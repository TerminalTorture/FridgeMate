from __future__ import annotations

import json
from pathlib import Path
from threading import RLock

from app.core.settings import get_settings

_LOCK = RLock()


def append_json_log(entry: dict[str, object], limit: int = 500) -> None:
    settings = get_settings()
    path = Path(settings.log_store_path)

    with _LOCK:
        existing: list[dict[str, object]] = []
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, list):
                    existing = [item for item in payload if isinstance(item, dict)]
            except Exception:
                existing = []

        existing.append(entry)
        existing = existing[-limit:]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
