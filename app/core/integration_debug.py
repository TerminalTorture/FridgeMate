from __future__ import annotations

from collections import deque
from app.core.json_log_store import append_json_log
from app.core.time_utils import utc_now


class IntegrationDebugLog:
    def __init__(self, max_entries: int = 25) -> None:
        self._entries: deque[dict[str, object]] = deque(maxlen=max_entries)

    def record(
        self,
        *,
        service: str,
        direction: str,
        status: str,
        summary: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        entry = {
            "timestamp": utc_now().isoformat(),
            "service": service,
            "direction": direction,
            "status": status,
            "summary": summary,
            "metadata": metadata or {},
        }
        self._entries.appendleft(entry)
        append_json_log(entry)

    def dump(self) -> list[dict[str, object]]:
        return list(self._entries)
