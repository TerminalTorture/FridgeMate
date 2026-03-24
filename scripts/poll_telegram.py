from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.container import build_container


def main() -> int:
    container = build_container()
    service = container.telegram_service
    runner = container.telegram_runner

    if not service.settings.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN is not configured.")
        return 1

    print(
        f"Starting Telegram polling with timeout={service.settings.telegram_poll_timeout_seconds}s. "
        "Press Ctrl+C to stop."
    )

    runner.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        runner.stop()
        print("Polling stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
