from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.http_client import post_json
from app.core.settings import get_settings


def main() -> int:
    settings = get_settings()
    if not settings.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN is not configured.")
        return 1
    if not settings.telegram_webhook_url:
        print("TELEGRAM_WEBHOOK_URL is not configured.")
        return 1

    payload: dict[str, object] = {
        "url": settings.telegram_webhook_url,
        "allowed_updates": ["message"],
        "drop_pending_updates": False,
    }
    if settings.telegram_webhook_secret:
        payload["secret_token"] = settings.telegram_webhook_secret

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook"
    result = post_json(url=url, headers={"Content-Type": "application/json"}, payload=payload)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
