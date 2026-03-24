from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Callable

from app.models.domain import ContextEvent, SharedContext
from app.core.time_utils import normalize_shared_context_datetimes, utc_now


class ContextStore:
    def __init__(
        self,
        initial_state: SharedContext,
        storage_path: str | None = None,
    ) -> None:
        self._storage_path = Path(storage_path) if storage_path else None
        self._state = self._load_state(initial_state)
        self._lock = RLock()

    def snapshot(self) -> SharedContext:
        with self._lock:
            return self._state.model_copy(deep=True)

    def update(
        self,
        *,
        agent: str,
        action: str,
        summary: str,
        mutator: Callable[[SharedContext], dict[str, object] | None],
    ) -> SharedContext:
        with self._lock:
            changes = mutator(self._state) or {}
            self._state.version += 1
            self._state.recent_events.insert(
                0,
                ContextEvent(
                    timestamp=utc_now(),
                    agent=agent,
                    action=action,
                    summary=summary,
                    changes=changes,
                ),
            )
            self._state.recent_events = self._state.recent_events[:50]
            self._persist()
            return self._state.model_copy(deep=True)

    def _load_state(self, fallback_state: SharedContext) -> SharedContext:
        if not self._storage_path or not self._storage_path.exists():
            return fallback_state

        try:
            payload = json.loads(self._storage_path.read_text(encoding="utf-8"))
            return normalize_shared_context_datetimes(SharedContext(**payload))
        except Exception:
            return fallback_state

    def _persist(self) -> None:
        if not self._storage_path:
            return

        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._storage_path.write_text(
            self._state.model_dump_json(indent=2),
            encoding="utf-8",
        )
