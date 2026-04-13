from __future__ import annotations

import json
from pathlib import Path
from threading import RLock

from app.core.settings import get_settings
from app.core.tracing import record_json_consult

_LOCK = RLock()


def append_json_log(entry: dict[str, object], limit: int = 500) -> None:
    settings = get_settings()
    path = Path(settings.log_store_path)

    with _LOCK:
        existing: list[dict[str, object]] = []
        if path.exists():
            try:
                raw_text = path.read_text(encoding="utf-8")
                payload = json.loads(raw_text)
                if isinstance(payload, list):
                    existing = [item for item in payload if isinstance(item, dict)]
                record_json_consult(
                    name="runtime_logs",
                    path=str(path),
                    operation="append_read_existing",
                    records=len(existing),
                    chars=len(raw_text),
                )
            except Exception:
                existing = []
                record_json_consult(
                    name="runtime_logs",
                    path=str(path),
                    operation="append_read_error",
                )

        existing.append(entry)
        existing = existing[-limit:]
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(existing, indent=2)
        path.write_text(serialized, encoding="utf-8")
        record_json_consult(
            name="runtime_logs",
            path=str(path),
            operation="append_write",
            records=len(existing),
            chars=len(serialized),
        )
