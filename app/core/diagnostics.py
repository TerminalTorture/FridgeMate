from __future__ import annotations

from datetime import datetime, timedelta

from app.core.context_store import ContextStore
from app.core.runtime_state import RuntimeStateAggregator
from app.core.time_utils import get_timezone


class DiagnosticsEngine:
    def __init__(
        self,
        *,
        store: ContextStore,
        runtime_state_aggregator: RuntimeStateAggregator,
    ) -> None:
        self.store = store
        self.runtime_state_aggregator = runtime_state_aggregator

    def diagnostics_report(
        self,
        *,
        last_user_message: str = "",
        user_id: str | None = None,
    ) -> dict[str, object]:
        runtime_state = self.runtime_state_aggregator.build(
            last_user_message=last_user_message,
            user_id=user_id,
        )
        snapshot = self.store.snapshot()
        issues: list[dict[str, object]] = []
        recommended_actions: list[str] = []

        self._check_component(
            issues,
            recommended_actions,
            component="camera",
            status=str(runtime_state.get("camera_status") or "unknown"),
            degraded_action="Use inventory memory or ask for visual confirmation before relying on image detection.",
        )
        self._check_component(
            issues,
            recommended_actions,
            component="weight_sensor",
            status=str(runtime_state.get("weight_sensor_status") or "unknown"),
            degraded_action="Ask for confirmation before acting on light-item count changes.",
        )

        stale_scan = self._scan_is_stale(str(runtime_state.get("fridge_last_scan") or ""))
        if stale_scan:
            issues.append(
                {
                    "component": "fridge_scan",
                    "status": "stale",
                    "impact": "Current inventory may not reflect the latest shelf state.",
                }
            )
            recommended_actions.append("Run a fresh fridge scan or verify items with the user.")

        if snapshot.utilities.water_level_percent <= 35:
            issues.append(
                {
                    "component": "water_reservoir",
                    "status": "low",
                    "impact": "Water utility may need attention.",
                }
            )
            recommended_actions.append("Refill or check the water reservoir.")
        if snapshot.utilities.ice_level_percent <= 35:
            issues.append(
                {
                    "component": "ice_bin",
                    "status": "low",
                    "impact": "Ice may run out soon.",
                }
            )
            recommended_actions.append("Refill or monitor the ice bin.")

        mismatches = runtime_state.get("inventory_confidence_mismatches")
        if isinstance(mismatches, list) and mismatches:
            issues.append(
                {
                    "component": "inventory_confidence",
                    "status": "mismatch",
                    "impact": "Some item counts may be inconsistent across simulated sensor and memory state.",
                    "details": mismatches[:5],
                }
            )
            recommended_actions.append("Compare current inventory to expected inventory before changing stock.")

        pending_confirmations = runtime_state.get("pending_confirmations")
        if isinstance(pending_confirmations, list) and pending_confirmations:
            issues.append(
                {
                    "component": "pending_confirmations",
                    "status": "waiting",
                    "impact": "One or more irreversible actions are waiting for user confirmation.",
                }
            )
            recommended_actions.append("Ask the user to confirm or cancel pending actions.")

        overall_status = "healthy"
        if issues:
            overall_status = "degraded"
        if any(issue.get("status") in {"offline", "error"} for issue in issues):
            overall_status = "critical"

        return {
            "overall_status": overall_status,
            "issues": issues,
            "recommended_actions": self._dedupe(recommended_actions),
            "runtime_state": runtime_state,
        }

    @staticmethod
    def _check_component(
        issues: list[dict[str, object]],
        recommended_actions: list[str],
        *,
        component: str,
        status: str,
        degraded_action: str,
    ) -> None:
        normalized = status.strip().lower()
        if normalized in {"healthy", "ok", "online"}:
            return
        issues.append(
            {
                "component": component,
                "status": normalized or "unknown",
                "impact": f"{component} data should be treated as uncertain.",
            }
        )
        recommended_actions.append(degraded_action)

    @staticmethod
    def _scan_is_stale(raw_value: str) -> bool:
        if not raw_value:
            return True
        try:
            value = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
            singapore = get_timezone("Asia/Singapore")
            if value.tzinfo is None:
                value = value.replace(tzinfo=singapore)
            now = datetime.now(singapore)
            return now - value.astimezone(singapore) > timedelta(hours=2)
        except ValueError:
            return True

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result
