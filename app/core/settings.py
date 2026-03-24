from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _load_dotenv(dotenv_path: str = ".env") -> None:
    path = Path(dotenv_path)
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    telegram_webhook_secret: str | None
    telegram_webhook_url: str | None
    telegram_poll_timeout_seconds: int
    telegram_send_retries: int
    telegram_worker_count: int
    session_timeout_minutes: int
    memory_store_path: str
    log_store_path: str
    llm_api_key: str | None
    llm_model: str
    llm_base_url: str | None

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token)

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def telegram_mode(self) -> str:
        return "webhook" if self.telegram_chat_id else "polling"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_dotenv()
    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        telegram_webhook_secret=os.getenv("TELEGRAM_WEBHOOK_SECRET"),
        telegram_webhook_url=os.getenv("TELEGRAM_WEBHOOK_URL"),
        telegram_poll_timeout_seconds=int(os.getenv("TELEGRAM_POLL_TIMEOUT_SECONDS", "20")),
        telegram_send_retries=int(os.getenv("TELEGRAM_SEND_RETRIES", "3")),
        telegram_worker_count=int(os.getenv("TELEGRAM_WORKER_COUNT", "4")),
        session_timeout_minutes=int(os.getenv("SESSION_TIMEOUT_MINUTES", "30")),
        memory_store_path=os.getenv("MEMORY_STORE_PATH", "data/fridge_memory.json"),
        log_store_path=os.getenv("LOG_STORE_PATH", "data/runtime_logs.json"),
        llm_api_key=os.getenv("LLM_API_KEY"),
        llm_model=os.getenv("LLM_MODEL", "gpt-5.1-mini"),
        llm_base_url=os.getenv("LLM_BASE_URL"),
    )
