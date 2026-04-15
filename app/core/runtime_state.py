from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.core.context_store import ContextStore
from app.core.time_utils import singapore_now
from app.core.tracing import record_json_consult


class RuntimeStateAggregator:
    def __init__(
        self,
        *,
        store: ContextStore,
        runtime_state_path: str | Path = "data/runtime_state.json",
    ) -> None:
        self.store = store
        self.runtime_state_path = Path(runtime_state_path)
        self.telegram_connected = False
        self.dashboard_connected = True
        self._pending_actions_provider = None

    def set_pending_actions_provider(self, provider) -> None:
        self._pending_actions_provider = provider

    def build(
        self,
        *,
        last_user_message: str = "",
        user_id: str | None = None,
    ) -> dict[str, object]:
        now = singapore_now()
        snapshot = self.store.snapshot()
        persisted = self._load_persisted_state()
        pending_actions = self._pending_actions(user_id)
        derived_actions = [self._describe_pending_action(action) for action in pending_actions]
        heartbeat_status = self.store.heartbeat_preference(user_id) if user_id else None
        user_preferences = self.store.user_preferences(user_id).model_dump(mode="json") if user_id else None
        temporary_states = (
            [state.model_dump(mode="json") for state in self.store.temporary_states(user_id)]
            if user_id
            else []
        )

        state = {
            "time": now.isoformat(timespec="seconds"),
            "location": "home",
            "device_online": True,
            "fridge_last_scan": now.isoformat(timespec="seconds"),
            "camera_status": "missing",
            "weight_sensor_status": "not_ready",
            "telegram_connected": self.telegram_connected,
            "dashboard_connected": self.dashboard_connected,
            "last_user_message": last_user_message,
            "pending_actions": derived_actions,
            "pending_confirmations": pending_actions,
            "heartbeat_status": heartbeat_status,
            "user_preferences": user_preferences,
            "temporary_states": temporary_states,
            "simulated_state": True,
            "simulation_note": "Camera and weight sensor fields default to unavailable simulated state until real integrations are added.",
            "inventory_count": len(snapshot.inventory),
            "inventory_batch_count": len(snapshot.inventory_batches),
            "low_stock_count": len(
                [
                    item
                    for item in snapshot.inventory
                    if item.quantity < item.min_desired_quantity
                ]
            ),
            "shopping_list_count": len(snapshot.pending_grocery_list),
            "inventory_confidence_mismatches": [],
        }
        state.update(persisted)
        state["last_user_message"] = last_user_message
        state["telegram_connected"] = self.telegram_connected
        state["dashboard_connected"] = self.dashboard_connected
        state["pending_actions"] = derived_actions or state.get("pending_actions", [])
        state["pending_confirmations"] = pending_actions
        return state

    def _pending_actions(self, user_id: str | None) -> list[dict[str, object]]:
        if self._pending_actions_provider is None:
            return []
        return self._pending_actions_provider(user_id)

    def _load_persisted_state(self) -> dict[str, object]:
        if not self.runtime_state_path.exists():
            record_json_consult(
                name="runtime_state",
                path=str(self.runtime_state_path),
                operation="read_missing",
                records=0,
                chars=0,
            )
            return {}
        try:
            raw_text = self.runtime_state_path.read_text(encoding="utf-8")
            payload = json.loads(raw_text)
            record_json_consult(
                name="runtime_state",
                path=str(self.runtime_state_path),
                operation="read",
                chars=len(raw_text),
            )
            return payload if isinstance(payload, dict) else {}
        except Exception:
            record_json_consult(
                name="runtime_state",
                path=str(self.runtime_state_path),
                operation="read_error",
            )
            return {"runtime_state_error": "Failed to read local simulated runtime state."}

    @staticmethod
    def _describe_pending_action(action: dict[str, object]) -> str:
        summary = action.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        return str(action.get("action") or "pending action")
