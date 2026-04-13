from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.bootstrap import build_initial_context
from app.core.context_store import ContextStore
from app.core.settings import get_settings


def main() -> None:
    settings = get_settings()
    store = ContextStore(
        build_initial_context(),
        database_url=settings.database_url,
        sql_echo=settings.sql_echo,
        storage_path=settings.memory_store_path,
        seed_history_on_startup=False,
        seed_history_days=settings.seed_history_days,
        seed_history_seed=settings.seed_history_seed,
    )
    days = int(os.getenv("SEED_HISTORY_DAYS", str(settings.seed_history_days)))
    seed = int(os.getenv("SEED_HISTORY_SEED", str(settings.seed_history_seed)))
    store.seed_synthetic_history(days=days, seed=seed, initial_state=build_initial_context())
    print(f"Seeded {days} days of synthetic history into {settings.database_url}")
    print(store.database_summary())


if __name__ == "__main__":
    main()
