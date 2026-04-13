from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import datetime, timedelta

from app.core.context_store import ContextStore
from app.core.decision_engine import DecisionEngine
from app.core.diagnostics import DiagnosticsEngine
from app.core.integration_debug import IntegrationDebugLog
from app.core.memory_manager import MemoryManager
from app.core.time_utils import utc_now
from app.core.tracing import add_event, trace_scope, update_trace_metadata


class HeartbeatService:
    def __init__(
        self,
        *,
        store: ContextStore,
        diagnostics_engine: DiagnosticsEngine,
        memory_manager: MemoryManager,
        decision_engine: DecisionEngine,
        interval_seconds: int = 60,
    ) -> None:
        self.store = store
        self.diagnostics_engine = diagnostics_engine
        self.memory_manager = memory_manager
        self.decision_engine = decision_engine
        self.interval_seconds = interval_seconds
        self.debug_log = IntegrationDebugLog()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_report: dict[str, object] | None = None
        self._notifier: Callable[[str, str, dict[str, object] | None], object] | None = None

    def set_notifier(self, notifier) -> None:
        self._notifier = notifier

    def register_chat(self, user_id: str, chat_id: str) -> None:
        self.store.set_heartbeat_preference(user_id, chat_id=chat_id)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="fridgemate-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def configure(
        self,
        user_id: str,
        *,
        enabled: bool | None = None,
        dinner_time: str | None = None,
        interval_minutes: int | None = None,
        chat_id: str | None = None,
    ) -> dict[str, object]:
        updated = self.store.set_heartbeat_preference(
            user_id,
            enabled=enabled,
            dinner_time=dinner_time,
            interval_minutes=interval_minutes,
            chat_id=chat_id,
        )
        if dinner_time:
            start = dinner_time
            anchor = datetime.strptime(dinner_time, "%H:%M")
            end = (anchor + timedelta(hours=3)).strftime("%H:%M")
            self.store.set_user_preferences(
                user_id,
                meal_window_start=start,
                meal_window_end=end,
            )
        return updated

    def status_for_user(self, user_id: str) -> dict[str, object]:
        preference = self.store.heartbeat_preference(user_id)
        next_check = self._next_check(preference)
        preferences = self.store.user_preferences(user_id)
        return {
            **preference,
            "next_check_at": next_check,
            "mode": preferences.mode,
            "max_prep_minutes": preferences.max_prep_minutes,
            "notification_frequency": preferences.notification_frequency,
        }

    def run_once(self) -> dict[str, object]:
        results = self.run_due_checks()
        report = {
            "checked_users": len(results),
            "results": results,
        }
        self._last_report = report
        return report

    def run_for_user(
        self,
        user_id: str,
        *,
        force: bool = False,
        notify: bool = False,
    ) -> dict[str, object]:
        with trace_scope(
            channel="heartbeat",
            request_id=f"hb_{user_id}_{utc_now().strftime('%Y%m%d%H%M%S')}",
            user_id=user_id,
            metadata={"force": force, "notify": notify},
        ):
            update_trace_metadata(user_id=user_id)
            add_event(name="heartbeat_run_for_user", detail={"force": force, "notify": notify})
            preference = self.store.heartbeat_preference(user_id)
            diagnostics = self.diagnostics_engine.diagnostics_report(user_id=user_id)
            diagnostics_status = str(diagnostics.get("overall_status") or "unknown")
            diagnostics_issues = diagnostics.get("issues")
            diagnostics_recommended_actions = diagnostics.get("recommended_actions")
            issues = diagnostics_issues if isinstance(diagnostics_issues, list) else []
            recommended_actions = (
                [str(item) for item in diagnostics_recommended_actions]
                if isinstance(diagnostics_recommended_actions, list)
                else []
            )
            decision = self.decision_engine.run_for_user(user_id, force=force)
            signature = json.dumps(
                {
                    "intervene": decision.intervene,
                    "type": decision.intervention_type,
                    "thread_key": decision.thread_key,
                    "context_hash": decision.context_hash,
                    "reason_codes": decision.reason_codes,
                    "diagnostics": diagnostics_status,
                },
                sort_keys=True,
            )
            status_changed = signature != str(preference.get("last_alert_signature") or "")
            should_notify = bool(preference.get("enabled")) and decision.intervene and (force or status_changed)

            self.store.set_heartbeat_preference(
                user_id,
                last_checked_at=utc_now(),
                last_alert_signature=signature,
            )

            materialized = decision
            if notify and should_notify and self._notifier is not None and preference.get("chat_id"):
                materialized = self.decision_engine.materialize_intervention(decision)
                reply_markup = self.decision_engine.build_reply_markup(materialized)
                self._notifier(str(preference["chat_id"]), materialized.message, reply_markup)
                self.store.set_heartbeat_preference(
                    user_id,
                    last_notified_at=utc_now(),
                )
                self.memory_manager.append_daily_event(
                    f"Sent decision heartbeat to {user_id}: {materialized.message}",
                    category="heartbeat",
                )

            interval_minutes = self._coerce_interval_minutes(preference.get("interval_minutes"))
            result = {
                "user_id": user_id,
                "enabled": bool(preference.get("enabled")),
                "interval_minutes": interval_minutes,
                "dinner_time": str(preference.get("dinner_time") or "19:00"),
                "timezone": str(preference.get("timezone") or "Asia/Singapore"),
                "status_changed": status_changed,
                "should_notify": should_notify,
                "message": materialized.message,
                "decision": materialized.model_dump(mode="json"),
                "diagnostics_status": diagnostics_status,
            }
            self._last_report = result
            self.store.record_diagnostics_snapshot(
                user_id=user_id,
                overall_status=diagnostics_status,
                issues=[item for item in issues if isinstance(item, dict)],
                recommended_actions=recommended_actions,
            )
            return result

    def run_due_checks(self) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        for preference in self.store.due_heartbeat_preferences():
            user_id = str(preference["user_id"])
            result = self.run_for_user(user_id, notify=True)
            decision_payload = result.get("decision")
            results.append(result)
            self.debug_log.record(
                service="heartbeat",
                direction="internal",
                status="sent" if result["should_notify"] else "checked",
                summary=f"Heartbeat checked {user_id}.",
                metadata={
                    "user_id": user_id,
                    "should_notify": result["should_notify"],
                    "intervention_type": decision_payload.get("intervention_type") if isinstance(decision_payload, dict) else None,
                },
            )
        return results

    def format_status_message(self, user_id: str) -> str:
        status = self.status_for_user(user_id)
        enabled_text = "on" if status["enabled"] else "off"
        return (
            f"Heartbeat is {enabled_text}.\n"
            f"Interval: every {status['interval_minutes']} minutes.\n"
            f"Dinner time: {status['dinner_time']} ({status['timezone']}).\n"
            f"Mode: {status['mode']}, max prep: {status['max_prep_minutes']} min, frequency: {status['notification_frequency']}.\n"
            f"Next check: {status['next_check_at'] or 'not scheduled'}."
        )

    def status(self) -> dict[str, object]:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "interval_seconds": self.interval_seconds,
            "last_report": self._last_report,
            "recent_events": self.debug_log.dump(),
        }

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_due_checks()
            except Exception as exc:
                self.debug_log.record(
                    service="heartbeat",
                    direction="internal",
                    status="error",
                    summary="Heartbeat check failed.",
                    metadata={"error": str(exc)},
                )
            self._stop_event.wait(self.interval_seconds)

    @staticmethod
    def _next_check(preference: dict[str, object]) -> str | None:
        last_checked = preference.get("last_checked_at")
        if not isinstance(last_checked, str) or not last_checked:
            return None
        last_checked_dt = datetime.fromisoformat(last_checked.replace("Z", "+00:00"))
        return (last_checked_dt + timedelta(minutes=HeartbeatService._coerce_interval_minutes(preference.get("interval_minutes")))).isoformat()

    @staticmethod
    def _coerce_interval_minutes(raw: object) -> int:
        if raw in (None, "", "null"):
            return 60
        if isinstance(raw, bool):
            return int(raw)
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float):
            return int(raw)
        if isinstance(raw, str):
            return int(raw.strip())
        return 60
