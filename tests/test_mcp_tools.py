from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.container import build_container
from app.core.settings import get_settings
from app.models.domain import Recipe, RecipeIngredient


class MCPToolSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        os.environ["MEMORY_STORE_PATH"] = str(Path(self.temp_dir.name) / "fridge_memory.json")
        os.environ["LOG_STORE_PATH"] = str(Path(self.temp_dir.name) / "runtime_logs.json")
        os.environ["TELEGRAM_BOT_TOKEN"] = ""
        os.environ["TELEGRAM_CHAT_ID"] = ""
        os.environ["LLM_API_KEY"] = "test-key"
        get_settings.cache_clear()
        self.container = build_container()
        self.discovery_calls = 0

        def fake_search(query: str, max_results: int = 3) -> list[Recipe]:
            self.discovery_calls += 1
            return [
                Recipe(
                    id="test_online_recipe",
                    name="Test Online Recipe",
                    description=f"Imported for {query}",
                    ingredients=[
                        RecipeIngredient(name="Egg", quantity=2, unit="unit"),
                    ],
                    instructions=["Cook it."],
                    tags=["test"],
                    calories=300,
                    protein_g=20,
                    cuisine="global",
                    source_url="https://example.com/recipe",
                    source_title="Example Recipe",
                )
            ][:max_results]

        self.container.recipe_discovery_service.search_online_recipes = fake_search

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        get_settings.cache_clear()

    def test_all_mcp_tools_execute(self) -> None:
        recipe_id = self.container.recipe_agent.list_recipes()[0].id
        tool_args = {
            "get_memory_snapshot": {"user_id": "u1"},
            "get_inventory": {},
            "add_inventory_item": {
                "name": "Spinach",
                "quantity": 2,
                "unit": "bag",
                "expires_on": "2026-03-30",
                "category": "produce",
                "min_desired_quantity": 1,
            },
            "remove_inventory_item": {"name": "Spinach"},
            "clear_inventory": {},
            "list_recipes": {},
            "search_recipes_online": {"query": "omelette", "max_results": 1},
            "import_recipe": {
                "recipe": {
                    "name": "Direct Import Recipe",
                    "description": "Recipe imported directly.",
                    "ingredients": [{"name": "Milk", "quantity": 1, "unit": "cup"}],
                    "instructions": ["Mix and serve."],
                    "tags": ["quick"],
                    "calories": 120,
                    "protein_g": 8,
                    "cuisine": "global",
                }
            },
            "search_and_import_recipe": {"query": "protein eggs", "selection_index": 0, "max_results": 1},
            "get_utilities": {},
            "update_utilities": {"water_level_percent": 60, "ice_level_percent": 55},
            "get_nutrition_summary": {},
            "get_behaviour_summary": {},
            "order_groceries_for_recipe": {"recipe_id": recipe_id},
            "order_staple_restock": {},
            "get_session_status": {"user_id": "u1"},
            "update_user_status": {"user_id": "u1", "status": "clearing and restocking inventory"},
        }

        for tool in self.container.mcp_tool_service.list_tools():
            name = tool["name"]
            result = self.container.mcp_tool_service.call_tool(name, tool_args[name])
            self.assertEqual(result["tool_name"], name)

        inventory = self.container.inventory_agent.get_inventory()
        self.assertEqual(inventory, [])
        session = self.container.conversation_manager.session_status("u1")
        self.assertEqual(session["current_status"], "clearing and restocking inventory")
        self.assertGreaterEqual(self.discovery_calls, 2)

    def test_llm_tool_loop_can_clear_inventory(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_create_response(payload: dict[str, object]) -> dict[str, object]:
            calls.append(payload)
            if len(calls) == 1:
                return {
                    "id": "resp_1",
                    "output": [
                        {
                            "type": "function_call",
                            "id": "fc_1",
                            "call_id": "call_1",
                            "name": "clear_inventory",
                            "arguments": "{}",
                        }
                    ],
                }
            return {
                "id": "resp_2",
                "output_text": "Inventory cleared successfully.",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Inventory cleared successfully."}],
                    }
                ],
            }

        self.container.llm_service.create_response = fake_create_response
        reply = self.container.telegram_service.build_reply_for_user(
            user_id="u2",
            text="delete all inventory",
        )

        self.assertEqual(reply, "Inventory cleared successfully.")
        self.assertEqual(self.container.inventory_agent.get_inventory(), [])
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
