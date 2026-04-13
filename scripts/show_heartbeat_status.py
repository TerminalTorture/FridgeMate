from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.container import build_container


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python scripts/show_heartbeat_status.py <user_id>")
    user_id = sys.argv[1]
    container = build_container()
    print(container.heartbeat_service.status_for_user(user_id))


if __name__ == "__main__":
    main()
