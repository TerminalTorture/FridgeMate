from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Callable

from app.core.history_seed import SyntheticHistorySeeder
from app.core.sql_repository import SQLRepository
from app.core.time_utils import normalize_shared_context_datetimes, utc_now
from app.models.domain import ContextEvent, SharedContext


class ContextStore:
    def __init__(
        self,
        initial_state: SharedContext,
        *,
        database_url: str,
        sql_echo: bool = False,
        storage_path: str | None = None,
        seed_history_on_startup: bool = False,
        seed_history_days: int = 180,
        seed_history_seed: int = 4052,
    ) -> None:
        self._storage_path = Path(storage_path) if storage_path else None
        self.repository = SQLRepository(database_url=database_url, echo=sql_echo)
        self._lock = RLock()
        self._ensure_bootstrapped(
            initial_state=initial_state,
            seed_history_on_startup=seed_history_on_startup,
            seed_history_days=seed_history_days,
            seed_history_seed=seed_history_seed,
        )

    def snapshot(self) -> SharedContext:
        with self._lock:
            return self.repository.load_snapshot().model_copy(deep=True)

    def update(
        self,
        *,
        agent: str,
        action: str,
        summary: str,
        mutator: Callable[[SharedContext], dict[str, object] | None],
    ) -> SharedContext:
        with self._lock:
            state = self.repository.load_snapshot()
            changes = mutator(state) or {}
            state.version += 1
            state.recent_events.insert(
                0,
                ContextEvent(
                    timestamp=utc_now(),
                    agent=agent,
                    action=action,
                    summary=summary,
                    changes=changes,
                ),
            )
            state.recent_events = state.recent_events[:50]
            self.repository.save_snapshot(state)
            return state.model_copy(deep=True)

    def list_inventory_batches(self, *, include_inactive: bool = False):
        with self._lock:
            return self.repository.list_inventory_batches(include_inactive=include_inactive)

    def heartbeat_preference(self, user_id: str) -> dict[str, object]:
        with self._lock:
            return self.repository.get_heartbeat_preference(user_id)

    def set_heartbeat_preference(self, user_id: str, **kwargs) -> dict[str, object]:
        with self._lock:
            return self.repository.set_heartbeat_preference(user_id, **kwargs)

    def due_heartbeat_preferences(self) -> list[dict[str, object]]:
        with self._lock:
            return self.repository.list_due_heartbeat_preferences(now=utc_now())

    def record_diagnostics_snapshot(
        self,
        *,
        user_id: str | None,
        overall_status: str,
        issues: list[dict[str, object]],
        recommended_actions: list[str],
    ) -> None:
        with self._lock:
            self.repository.record_diagnostics_snapshot(
                user_id=user_id,
                overall_status=overall_status,
                issues=issues,
                recommended_actions=recommended_actions,
            )

    def database_summary(self) -> dict[str, object]:
        with self._lock:
            return self.repository.database_summary()

    def user_preferences(self, user_id: str):
        with self._lock:
            return self.repository.get_user_preferences(user_id)

    def set_user_preferences(self, user_id: str, **kwargs):
        with self._lock:
            return self.repository.set_user_preferences(user_id, **kwargs)

    def temporary_states(self, user_id: str):
        with self._lock:
            return self.repository.list_active_temporary_states(user_id)

    def set_temporary_state(self, user_id: str, **kwargs):
        with self._lock:
            return self.repository.set_temporary_state(user_id, **kwargs)

    def clear_temporary_state(self, user_id: str, state: str) -> int:
        with self._lock:
            return self.repository.clear_temporary_state(user_id, state)

    def decision_profile(self, user_id: str):
        with self._lock:
            return self.repository.get_decision_profile(user_id)

    def set_decision_profile(self, user_id: str, **kwargs):
        with self._lock:
            return self.repository.set_decision_profile(user_id, **kwargs)

    def create_assistant_intervention(self, intervention):
        with self._lock:
            return self.repository.create_assistant_intervention(intervention)

    def list_assistant_interventions(self, user_id: str, **kwargs):
        with self._lock:
            return self.repository.list_assistant_interventions(user_id, **kwargs)

    def latest_assistant_intervention(self, user_id: str, thread_key: str):
        with self._lock:
            return self.repository.get_latest_intervention_for_thread(user_id, thread_key)

    def assistant_intervention(self, intervention_id: str):
        with self._lock:
            return self.repository.get_assistant_intervention(intervention_id)

    def record_intervention_feedback(self, *, user_id: str, **kwargs):
        with self._lock:
            return self.repository.record_intervention_feedback(user_id=user_id, **kwargs)

    def seed_synthetic_history(self, *, days: int, seed: int, initial_state: SharedContext) -> None:
        with self._lock:
            seeder = SyntheticHistorySeeder(self.repository)
            seeder.seed(days=days, seed=seed, initial_state=initial_state)

    def close(self) -> None:
        with self._lock:
            self.repository.dispose()

    def _ensure_bootstrapped(
        self,
        *,
        initial_state: SharedContext,
        seed_history_on_startup: bool,
        seed_history_days: int,
        seed_history_seed: int,
    ) -> None:
        if not self.repository.is_empty():
            return

        if seed_history_on_startup:
            SyntheticHistorySeeder(self.repository).seed(
                days=seed_history_days,
                seed=seed_history_seed,
                initial_state=initial_state,
            )
            return

        legacy_state = self._load_legacy_state()
        self.repository.import_snapshot(legacy_state or initial_state)

    def _load_legacy_state(self) -> SharedContext | None:
        if not self._storage_path or not self._storage_path.exists():
            return None
        try:
            payload = json.loads(self._storage_path.read_text(encoding="utf-8"))
            return normalize_shared_context_datetimes(SharedContext(**payload))
        except Exception:
            return None
