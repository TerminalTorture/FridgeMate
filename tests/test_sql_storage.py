from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.core.bootstrap import build_initial_context
from app.core.context_store import ContextStore


class SQLStorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "fridgemate.db"
        self.database_url = f"sqlite:///{self.database_path}"
        os.environ["DATABASE_URL"] = self.database_url
        os.environ["SEED_HISTORY_ON_STARTUP"] = "0"

    def tearDown(self) -> None:
        if hasattr(self, "store"):
            self.store.close()
        self.temp_dir.cleanup()
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("SEED_HISTORY_ON_STARTUP", None)

    def test_alembic_upgrade_creates_tables(self) -> None:
        config = Config("alembic.ini")
        command.upgrade(config, "head")

        engine = create_engine(self.database_url, future=True)
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        recipe_columns = {column["name"] for column in inspector.get_columns("recipes")}
        user_preference_columns = {column["name"] for column in inspector.get_columns("user_preferences")}
        engine.dispose()

        self.assertIn("inventory_items", table_names)
        self.assertIn("inventory_batches", table_names)
        self.assertIn("recipes", table_names)
        self.assertIn("heartbeat_preferences", table_names)
        self.assertIn("user_preferences", table_names)
        self.assertIn("temporary_state_overrides", table_names)
        self.assertIn("decision_profiles", table_names)
        self.assertIn("assistant_interventions", table_names)
        self.assertIn("prep_minutes", recipe_columns)
        self.assertIn("step_count", recipe_columns)
        self.assertIn("effort_score", recipe_columns)
        self.assertIn("suitable_when_tired", recipe_columns)
        self.assertIn("search_model", user_preference_columns)

    def test_seed_history_creates_six_months_of_activity(self) -> None:
        self.store = ContextStore(
            build_initial_context(),
            database_url=self.database_url,
            sql_echo=False,
            storage_path=None,
            seed_history_on_startup=False,
        )
        self.store.seed_synthetic_history(days=180, seed=4052, initial_state=build_initial_context())
        snapshot = self.store.snapshot()

        self.assertGreaterEqual(len(snapshot.inventory_batches), 50)
        self.assertGreaterEqual(len(snapshot.meal_history), 60)
        self.assertGreaterEqual(len(snapshot.grocery_orders), 10)
        self.assertTrue(any(item.purchased_at is not None for item in snapshot.inventory))

    def test_context_store_upgrades_older_recipe_schema_on_startup(self) -> None:
        engine = create_engine(self.database_url, future=True)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE recipes (
                    id VARCHAR(255) PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    description TEXT NOT NULL,
                    instructions JSON,
                    tags JSON,
                    calories INTEGER DEFAULT 0,
                    protein_g INTEGER DEFAULT 0,
                    cuisine VARCHAR(100) DEFAULT 'global',
                    source_url TEXT,
                    source_title TEXT
                )
                """
            )
            connection.exec_driver_sql(
                """
                CREATE TABLE recipe_ingredients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recipe_id VARCHAR(255) NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    quantity FLOAT DEFAULT 0.0,
                    unit VARCHAR(50) DEFAULT 'unit',
                    optional BOOLEAN DEFAULT 0,
                    FOREIGN KEY(recipe_id) REFERENCES recipes(id)
                )
                """
            )
        engine.dispose()

        self.store = ContextStore(
            build_initial_context(),
            database_url=self.database_url,
            sql_echo=False,
            storage_path=None,
            seed_history_on_startup=False,
        )

        engine = create_engine(self.database_url, future=True)
        inspector = inspect(engine)
        recipe_columns = {column["name"] for column in inspector.get_columns("recipes")}
        engine.dispose()

        self.assertIn("prep_minutes", recipe_columns)
        self.assertIn("step_count", recipe_columns)
        self.assertIn("effort_score", recipe_columns)
        self.assertIn("suitable_when_tired", recipe_columns)

    def test_context_store_upgrades_older_user_preferences_schema_on_startup(self) -> None:
        engine = create_engine(self.database_url, future=True)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE user_preferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id VARCHAR(255) NOT NULL UNIQUE,
                    mode VARCHAR(20) NOT NULL DEFAULT 'lazy',
                    meal_window_start VARCHAR(10) NOT NULL DEFAULT '18:00',
                    meal_window_end VARCHAR(10) NOT NULL DEFAULT '21:00',
                    late_night_window_start VARCHAR(10) NOT NULL DEFAULT '22:30',
                    late_night_window_end VARCHAR(10) NOT NULL DEFAULT '00:30',
                    max_prep_minutes INTEGER NOT NULL DEFAULT 10,
                    notification_frequency VARCHAR(20) NOT NULL DEFAULT 'normal',
                    dietary_preferences JSON NOT NULL DEFAULT '[]',
                    updated_at DATETIME NOT NULL
                )
                """
            )
        engine.dispose()

        self.store = ContextStore(
            build_initial_context(),
            database_url=self.database_url,
            sql_echo=False,
            storage_path=None,
            seed_history_on_startup=False,
        )

        engine = create_engine(self.database_url, future=True)
        inspector = inspect(engine)
        user_preference_columns = {column["name"] for column in inspector.get_columns("user_preferences")}
        engine.dispose()

        self.assertIn("search_model", user_preference_columns)


if __name__ == "__main__":
    unittest.main()
