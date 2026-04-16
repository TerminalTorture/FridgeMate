from __future__ import annotations

import asyncio
import json
import os
import platform
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.container import build_container
from app.core.llm_gateway import LLMGatewayService
from app.core.llm_service import LLMReplyResult
from app.core.recipe_discovery_service import RecipeDiscoveryService, RecipeSearchChatCompletionError
from app.core.settings import get_settings
from app.core.time_utils import utc_now
from app.models.domain import AssistantIntervention, Recipe, RecipeIngredient


class MCPToolSmokeTest(unittest.TestCase):
    @staticmethod
    def _read_only_terminal_command() -> str:
        if platform.system().lower() == "windows":
            return "Get-Location | Select-Object -ExpandProperty Path"
        return "pwd"

    @staticmethod
    def _blocked_write_command() -> str:
        if platform.system().lower() == "windows":
            return "Set-Content README.md hacked"
        return "touch README.md"

    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.repo_root = Path(__file__).resolve().parents[1]
        self.gateway_write_path = self.repo_root / "data" / "test_gateway" / "mcp_gateway_file.txt"
        self.gateway_write_path.parent.mkdir(parents=True, exist_ok=True)
        self.gateway_policy_path = Path(self.temp_dir.name) / "llm_gateway_policy.json"
        self.gateway_policy_path.write_text(
            json.dumps(
                {
                    "read_only": ["README.md", "app/**", "memory/**", "data/runtime_logs.json", "data/test_gateway/**"],
                    "read_write": ["app/core/**", "tests/**", "data/test_gateway/**"],
                    "terminal": {"enabled": True, "default_cwd": "."},
                }
            ),
            encoding="utf-8",
        )
        os.environ["MEMORY_STORE_PATH"] = str(Path(self.temp_dir.name) / "fridge_memory.json")
        os.environ["LOG_STORE_PATH"] = str(Path(self.temp_dir.name) / "runtime_logs.json")
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(self.temp_dir.name) / 'fridgemate.db'}"
        os.environ["SQL_ECHO"] = "0"
        os.environ["SEED_HISTORY_ON_STARTUP"] = "0"
        os.environ["SEED_HISTORY_DAYS"] = "30"
        os.environ["TELEGRAM_BOT_TOKEN"] = ""
        os.environ["TELEGRAM_CHAT_ID"] = ""
        os.environ["LLM_API_KEY"] = "test-key"
        os.environ["TRACE_MODE"] = "1"
        os.environ["TRACE_LOG_PATH"] = str(Path(self.temp_dir.name) / "traces")
        os.environ["FRIDGEMATE_MEMORY_ROOT"] = self.temp_dir.name
        os.environ["LLM_GATEWAY_POLICY_PATH"] = str(self.gateway_policy_path)
        get_settings.cache_clear()
        self.container = build_container()
        self.discovery_calls = 0

        def fake_search(query: str, max_results: int = 3, *, user_id: str | None = None) -> list[Recipe]:
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
        self.container.llm_service.generate_online_recipe_preview = lambda **kwargs: (
            "I found a recipe online: Test Online Recipe from Example Recipe.\n"
            "Link: https://example.com/recipe\n"
            "Key ingredients: Egg.\n\n"
            "Reply Yes to import it into your recipe list, or No to cancel."
        )

    def tearDown(self) -> None:
        self.container.store.close()
        if self.gateway_write_path.exists():
            self.gateway_write_path.unlink()
        self.temp_dir.cleanup()
        os.environ.pop("FRIDGEMATE_MEMORY_ROOT", None)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("SQL_ECHO", None)
        os.environ.pop("SEED_HISTORY_ON_STARTUP", None)
        os.environ.pop("SEED_HISTORY_DAYS", None)
        os.environ.pop("TRACE_MODE", None)
        os.environ.pop("TRACE_LOG_PATH", None)
        os.environ.pop("LLM_GATEWAY_POLICY_PATH", None)
        get_settings.cache_clear()

    def test_all_mcp_tools_execute(self) -> None:
        recipe_id = self.container.recipe_agent.list_recipes()[0].id
        tool_args = {
            "get_memory_snapshot": {"user_id": "u1"},
            "get_runtime_state": {"user_id": "u1", "last_user_message": "status"},
            "diagnostics_report": {"user_id": "u1"},
            "run_heartbeat_check": {},
            "get_inventory": {},
            "get_inventory_batches": {},
            "get_expiring_items": {"days": 1},
            "compare_current_inventory_to_expected_inventory": {"user_id": "u1"},
            "add_inventory_item": {
                "name": "Spinach",
                "quantity": 2,
                "unit": "bag",
                "purchased_at": "2026-03-28T10:00:00+08:00",
                "expires_on": "2026-03-30",
                "category": "produce",
                "min_desired_quantity": 1,
            },
            "remove_inventory_item": {"name": "Spinach"},
            "clear_inventory": {},
            "add_to_shopping_list": {"name": "eggs", "quantity": 6, "unit": "pcs", "reason": "test"},
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
            "get_heartbeat_status": {"user_id": "u1"},
            "set_heartbeat_status": {"user_id": "u1", "enabled": "true", "dinner_time": "18:30", "interval_minutes": 15},
            "set_heartbeat_interval": {"user_id": "u1", "interval_minutes": 5},
            "gateway_get_access": {"path": "app/core/mcp_tools.py"},
            "fs_list_dir": {"path": "app/core"},
            "fs_tree": {"path": "app", "max_depth": 1},
            "fs_read_text": {"path": "README.md"},
            "fs_search_text": {"query": "HeartbeatService", "path": "app/core", "max_results": 5},
            "fs_write_text": {"path": "data/test_gateway/mcp_gateway_file.txt", "content": "hello"},
            "fs_append_text": {"path": "data/test_gateway/mcp_gateway_file.txt", "content": "\nworld"},
            "terminal_exec": {"command": self._read_only_terminal_command(), "cwd": ".", "mode": "read_only"},
            "get_decision_state": {"user_id": "u1"},
            "run_decision": {"user_id": "u1", "force": "true"},
            "get_user_preferences": {"user_id": "u1"},
            "set_user_preferences": {"user_id": "u1", "mode": "strict", "max_prep_minutes": 8, "notification_frequency": "active", "dietary_preferences": ["avoid dairy"], "search_model": "gpt-4o-mini-search-preview"},
            "get_user_state": {"user_id": "u1"},
            "set_user_state": {"user_id": "u1", "state": "tired", "duration_hours": 24, "value": "active", "note": "test"},
            "record_decision_feedback": {"user_id": "u1", "status": "ignored", "thread_key": "cook:test"},
            "update_user_status": {"user_id": "u1", "status": "clearing and restocking inventory"},
            "confirm_pending_action": {"user_id": "u1"},
            "cancel_pending_action": {"user_id": "u1"},
        }

        protected_tools = {
            "remove_inventory_item",
            "clear_inventory",
            "order_groceries_for_recipe",
            "order_staple_restock",
        }

        for tool in self.container.mcp_tool_service.list_tools():
            name = tool["name"]
            if name == "confirm_pending_action":
                pending = self.container.mcp_tool_service.call_tool(
                    "clear_inventory",
                    {"user_id": "u1"},
                )
                tool_args[name]["confirmation_id"] = pending["pending_action"]["confirmation_id"]
            if name == "cancel_pending_action":
                pending = self.container.mcp_tool_service.call_tool(
                    "clear_inventory",
                    {"user_id": "u1"},
                )
                tool_args[name]["confirmation_id"] = pending["pending_action"]["confirmation_id"]

            result = self.container.mcp_tool_service.call_tool(name, tool_args[name])
            self.assertEqual(result["tool_name"], name)
            if name in protected_tools:
                self.assertTrue(result["requires_confirmation"])
                confirmation_id = result["pending_action"]["confirmation_id"]
                confirmed = self.container.mcp_tool_service.confirm_pending_action(confirmation_id)
                self.assertTrue(confirmed["confirmed"])

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
            if len(calls) == 2:
                output = payload["input"][0]["output"]
                import json

                pending = json.loads(output)["pending_action"]
                return {
                    "id": "resp_2",
                    "output": [
                        {
                            "type": "function_call",
                            "id": "fc_2",
                            "call_id": "call_2",
                            "name": "confirm_pending_action",
                            "arguments": json.dumps(
                                {"confirmation_id": pending["confirmation_id"], "user_id": "u2"}
                            ),
                        }
                    ],
                }
            return {
                "id": "resp_3",
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
        self.assertEqual(len(calls), 3)

    def test_prompt_builder_includes_design_layers(self) -> None:
        prompt = self.container.prompt_builder.build_instructions(
            user_id="u1",
            user_message="what can I cook tonight",
            conversation_context="Recent conversation context.",
        )

        self.assertIn("FridgeMate", prompt)
        self.assertIn("## Identity", prompt)
        self.assertIn("## Behaviour", prompt)
        self.assertIn("## User", prompt)
        self.assertIn("## Current Runtime State", prompt)
        self.assertIn("## Gateway Access", prompt)
        self.assertIn("## Available Tools", prompt)
        self.assertIn("## Long-term Memory", prompt)
        self.assertIn("## Recent Memory", prompt)
        self.assertIn("confirm_pending_action", prompt)
        self.assertIn("fs_read_text", prompt)
        self.assertIn("Asia/Singapore", prompt)
        self.assertIn("get_inventory_batches", prompt)

    def test_llm_instructions_prefer_online_recipe_import_for_explicit_request(self) -> None:
        instructions = self.container.llm_service._build_instructions(
            user_id="u1",
            user_message="search online for a pasta recipe",
            conversation_context="Recent conversation context.",
        )

        self.assertIn("search_and_import_recipe", instructions)
        self.assertIn("selection_index=0", instructions)
        self.assertIn("came from the web", instructions)

    def test_llm_instructions_keep_local_recipes_first_for_normal_request(self) -> None:
        instructions = self.container.llm_service._build_instructions(
            user_id="u1",
            user_message="what can I cook tonight?",
            conversation_context="Recent conversation context.",
        )

        self.assertIn("Do not use online recipe search for ordinary recipe suggestions", instructions)

    def test_gateway_service_reports_missing_policy(self) -> None:
        service = LLMGatewayService(
            repo_root=self.repo_root,
            policy_path=Path(self.temp_dir.name) / "missing-policy.json",
        )

        self.assertFalse(service.is_configured())
        snapshot = service.access_snapshot("README.md")
        self.assertFalse(snapshot["configured"])
        with self.assertRaises(RuntimeError):
            service.read_text("README.md")

    def test_gateway_tools_enforce_access_and_terminal_guardrails(self) -> None:
        access = self.container.mcp_tool_service.call_tool(
            "gateway_get_access",
            {"path": "data/test_gateway/mcp_gateway_file.txt"},
        )
        self.assertEqual(access["access"], "read_write")

        write_result = self.container.mcp_tool_service.call_tool(
            "fs_write_text",
            {"path": "data/test_gateway/mcp_gateway_file.txt", "content": "alpha"},
        )
        append_result = self.container.mcp_tool_service.call_tool(
            "fs_append_text",
            {"path": "data/test_gateway/mcp_gateway_file.txt", "content": "\nbeta"},
        )
        read_result = self.container.mcp_tool_service.call_tool(
            "fs_read_text",
            {"path": "data/test_gateway/mcp_gateway_file.txt"},
        )
        self.assertEqual(write_result["tool_name"], "fs_write_text")
        self.assertEqual(append_result["tool_name"], "fs_append_text")
        self.assertEqual(read_result["content"], "alpha\nbeta")

        with self.assertRaises(PermissionError):
            self.container.mcp_tool_service.call_tool(
                "fs_write_text",
                {"path": "README.md", "content": "forbidden"},
            )

        terminal_result = self.container.mcp_tool_service.call_tool(
            "terminal_exec",
            {"command": self._read_only_terminal_command(), "cwd": ".", "mode": "read_only"},
        )
        self.assertEqual(terminal_result["tool_name"], "terminal_exec")
        self.assertEqual(terminal_result["returncode"], 0)

        with self.assertRaises(PermissionError):
            self.container.mcp_tool_service.call_tool(
                "terminal_exec",
                {"command": self._blocked_write_command(), "cwd": ".", "mode": "read_only"},
            )

    def test_diagnostics_reports_degraded_components(self) -> None:
        self.container.runtime_state_aggregator.runtime_state_path = Path(self.temp_dir.name) / "runtime_state.json"
        self.container.runtime_state_aggregator.runtime_state_path.write_text(
            '{"camera_status": "healthy", "weight_sensor_status": "degraded", "fridge_last_scan": "2026-01-01T00:00:00+08:00"}',
            encoding="utf-8",
        )
        self.container.utility_agent.update_levels(water_level_percent=20)

        report = self.container.diagnostics_engine.diagnostics_report()

        self.assertEqual(report["overall_status"], "degraded")
        components = {issue["component"] for issue in report["issues"]}
        self.assertIn("weight_sensor", components)
        self.assertIn("fridge_scan", components)
        self.assertIn("water_reservoir", components)

    def test_heartbeat_dedupes_repeated_alerts(self) -> None:
        self.container.heartbeat_service.configure("u-heart", enabled=True, dinner_time="23:59", chat_id="chat-1")
        first = self.container.heartbeat_service.run_for_user("u-heart", force=True, notify=False)
        second = self.container.heartbeat_service.run_for_user("u-heart", force=False, notify=False)

        self.assertTrue(first["status_changed"])
        self.assertFalse(second["status_changed"])

    def test_telegram_heartbeat_commands(self) -> None:
        on_reply = self.container.telegram_service.build_reply_for_user("u3", "/heartbeat on", chat_id="chat-3")
        time_reply = self.container.telegram_service.build_reply_for_user("u3", "/heartbeat time 18:30", chat_id="chat-3")
        interval_reply = self.container.telegram_service.build_reply_for_user("u3", "/heartbeat every 5", chat_id="chat-3")
        now_reply = self.container.telegram_service.build_reply_for_user("u3", "/heartbeat now", chat_id="chat-3")

        self.assertIn("Heartbeat is on", on_reply)
        self.assertIn("18:30", time_reply)
        self.assertIn("every 5 minutes", interval_reply)
        self.assertTrue(len(now_reply) > 0)

    def test_telegram_searchmodel_commands(self) -> None:
        status_reply = self.container.telegram_service.build_reply_for_user("u-search", "/searchmodel", chat_id="chat-search")
        set_reply = self.container.telegram_service.build_reply_for_user(
            "u-search",
            "/searchmodel gpt-4o-search-preview",
            chat_id="chat-search",
        )
        invalid_reply = self.container.telegram_service.build_reply_for_user(
            "u-search",
            "/searchmodel invalid-model",
            chat_id="chat-search",
        )

        self.assertIn("Current recipe search model", status_reply)
        self.assertIn("Recipe search model set to gpt-4o-search-preview", set_reply)
        self.assertIn("Unsupported recipe search model", invalid_reply)
        self.assertEqual(
            self.container.store.user_preferences("u-search").search_model,
            "gpt-4o-search-preview",
        )

    def test_telegram_online_recipe_search_yes_imports_and_recipes_lists_catalog(self) -> None:
        search_reply = self.container.telegram_service.build_reply_for_user(
            "u-online-import",
            "search for nasi lemak recipe",
            chat_id="chat-online-import",
        )
        before_import_names = {recipe.name for recipe in self.container.recipe_agent.list_recipes()}

        self.assertIn("reply yes to import", search_reply.lower())
        self.assertNotIn("Test Online Recipe", before_import_names)

        confirm_reply = self.container.telegram_service.build_reply_for_user(
            "u-online-import",
            "Yes",
            chat_id="chat-online-import",
        )
        recipes_reply = self.container.telegram_service.build_reply_for_user(
            "u-online-import",
            "/recipes",
            chat_id="chat-online-import",
        )
        suggestions_reply = self.container.telegram_service.build_reply_for_user(
            "u-online-import",
            "/suggestions",
            chat_id="chat-online-import",
        )

        self.assertIn("Imported Test Online Recipe", confirm_reply)
        self.assertIn("Saved recipes:", recipes_reply)
        self.assertIn("Test Online Recipe", recipes_reply)
        self.assertIn("Top recipe ideas right now", suggestions_reply)

    def test_telegram_online_recipe_search_no_cancels_import(self) -> None:
        search_reply = self.container.telegram_service.build_reply_for_user(
            "u-online-cancel",
            "search for nasi lemak recipe",
            chat_id="chat-online-cancel",
        )

        self.assertIn("reply yes to import", search_reply.lower())

        cancel_reply = self.container.telegram_service.build_reply_for_user(
            "u-online-cancel",
            "No",
            chat_id="chat-online-cancel",
        )
        recipes_reply = self.container.telegram_service.build_reply_for_user(
            "u-online-cancel",
            "/recipes",
            chat_id="chat-online-cancel",
        )

        self.assertIn("did not import", cancel_reply.lower())
        self.assertNotIn("Test Online Recipe", recipes_reply)

    def test_online_recipe_preview_fallback_includes_link_and_ingredients(self) -> None:
        def fail_preview(**kwargs):
            raise RuntimeError("preview failed")

        self.container.llm_service.generate_online_recipe_preview = fail_preview

        reply = self.container.telegram_service.build_reply_for_user(
            "u-online-preview-fallback",
            "search for nasi lemak recipe",
            chat_id="chat-online-preview-fallback",
        )

        self.assertIn("https://example.com/recipe", reply)
        self.assertIn("Key ingredients: Egg", reply)

    def test_mcp_tool_can_set_search_model(self) -> None:
        result = self.container.mcp_tool_service.call_tool(
            "set_user_preferences",
            {"user_id": "u-search-mcp", "search_model": "gpt-5-search-api"},
        )

        self.assertEqual(result["tool_name"], "set_user_preferences")
        self.assertEqual(result["search_model"], "gpt-5-search-api")
        status = self.container.mcp_tool_service.call_tool(
            "get_user_preferences",
            {"user_id": "u-search-mcp"},
        )
        self.assertEqual(status["search_model"], "gpt-5-search-api")

    def test_mcp_tool_can_set_heartbeat_interval(self) -> None:
        result = self.container.mcp_tool_service.call_tool(
            "set_heartbeat_interval",
            {"user_id": "u-heart-interval", "interval_minutes": 5},
        )

        self.assertEqual(result["tool_name"], "set_heartbeat_interval")
        self.assertEqual(result["interval_minutes"], 5)

        status = self.container.mcp_tool_service.call_tool(
            "get_heartbeat_status",
            {"user_id": "u-heart-interval"},
        )
        self.assertEqual(status["interval_minutes"], 5)

    def test_natural_language_override_updates_state(self) -> None:
        reply = self.container.telegram_service.build_reply_for_user(
            "u-override",
            "I'm exhausted today",
            chat_id="chat-override",
        )

        states = self.container.store.temporary_states("u-override")
        self.assertIn("tired", {state.state for state in states})
        self.assertIn("treat you as tired", reply.lower())

    def test_online_recipe_request_fallback_is_honest(self) -> None:
        def fail_search(*args, **kwargs):
            raise RuntimeError("unauthorized")

        self.container.recipe_discovery_service.search_online_recipes = fail_search

        reply = self.container.telegram_service.build_reply_for_user(
            "u-online-fallback",
            "find a new chicken recipe on the web",
            chat_id="chat-online-fallback",
        )

        self.assertIn("couldn’t search online recipes right now", reply.lower())
        self.assertIn("have not imported anything from the web", reply.lower())

    def test_online_recipe_fallback_log_includes_request_fingerprint(self) -> None:
        def fail_search(*args, **kwargs):
            raise RecipeSearchChatCompletionError(
                "Online recipe search failed: HTTP request failed with status 400",
                request_fingerprint="abc123retry",
                original_error=RuntimeError("HTTP request failed with status 400"),
            )

        self.container.recipe_discovery_service.search_online_recipes = fail_search

        self.container.telegram_service.build_reply_for_user(
            "u-online-fingerprint",
            "find a new nasi lemak recipe on the web",
            chat_id="chat-online-fingerprint",
        )

        recent_events = self.container.telegram_service.debug_snapshot()["recent_events"]
        fallback_event = next(
            event for event in recent_events if event["status"] == "fallback_error"
        )
        self.assertEqual(
            fallback_event["metadata"].get("request_fingerprint"),
            "abc123retry",
        )

    def test_online_recipe_fallback_mentions_malformed_recipe_response(self) -> None:
        def fail_search(*args, **kwargs):
            raise RecipeSearchChatCompletionError(
                "Online recipe search returned invalid JSON: Unterminated string starting at: line 1 column 10",
                request_fingerprint="jsonbad123",
                original_error=ValueError("bad json"),
            )

        self.container.recipe_discovery_service.search_online_recipes = fail_search

        reply = self.container.telegram_service.build_reply_for_user(
            "u-online-malformed",
            "find a new nasi lemak recipe on the web",
            chat_id="chat-online-malformed",
        )

        self.assertIn("recipe search response was malformed", reply.lower())

    def test_silent_mode_suppresses_non_urgent_decision(self) -> None:
        def mutator(state):
            for item in state.inventory:
                item.expires_on = None
                item.quantity = max(item.quantity, item.min_desired_quantity + 2)
            return {"mutated": True}

        self.container.store.update(
            agent="test",
            action="stabilise_inventory",
            summary="Remove urgency from inventory for silent mode test.",
            mutator=mutator,
        )
        self.container.store.set_user_preferences("u-silent", mode="silent")
        result = self.container.decision_engine.run_for_user("u-silent", force=False)

        self.assertFalse(result.intervene)
        self.assertTrue(result.intervention_type is None or result.intervention_type in {"cook_now", "late_night_rescue"})

    def test_callback_query_updates_intervention_feedback(self) -> None:
        stored = self.container.store.create_assistant_intervention(
            AssistantIntervention(
                id="intv_test_callback",
                user_id="u-callback",
                thread_key="cook:veggie_omelette",
                sequence_index=1,
                context_hash="ctx123",
                decision_type="cook_now",
                sent_at=utc_now(),
                message="You have a quick dinner ready: Veggie Omelette.",
                recommended_action="Cook Veggie Omelette",
                score=0.8,
                confidence=0.8,
            )
        )

        update = {
            "callback_query": {
                "id": "cb-1",
                "from": {"id": "u-callback"},
                "data": f"fm:ignore:{stored.id}",
                "message": {"chat": {"id": "chat-callback"}},
            }
        }

        calls: list[tuple[str | int, str, object]] = []

        async def fake_send_message_async(chat_id, text, reply_markup=None):
            calls.append((chat_id, text, reply_markup))
            return {"ok": True}

        async def fake_answer_callback_query_async(callback_query_id, text=None):
            return {"ok": True, "id": callback_query_id, "text": text}

        self.container.telegram_service.send_message_async = fake_send_message_async
        self.container.telegram_service.answer_callback_query_async = fake_answer_callback_query_async

        result = self.container.telegram_service.process_update(
            update,
        )

        self.assertEqual(result["status"], "sent")
        updated = self.container.store.assistant_intervention(stored.id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, "ignored")
        self.assertEqual(calls[0][0], "chat-callback")

    def test_general_text_uses_llm_first_instead_of_fixed_orchestrator_reply(self) -> None:
        self.container.llm_service.generate_reply = lambda **_: (
            "Today is Monday. You have eggs, chicken breast, rice, spinach, tomatoes, and milk. "
            "Buy a fresh vegetable and some aromatics if you want a fuller meal."
        )

        reply = self.container.telegram_service.build_reply_for_user(
            "u-smart",
            "What day is it today and what ingredients do I have in the fridge and what should I get to prepare a meal for today?",
            chat_id="chat-smart",
        )

        self.assertIn("Today is Monday.", reply)
        self.assertNotIn("Current inventory:", reply)

    def test_nutrition_query_does_not_trigger_utility_check_from_rice(self) -> None:
        reply = self.container.telegram_service.build_reply_for_user(
            "u4",
            "Calories and macros for chicken breast and egg fried rice portion for 1",
            chat_id="chat-4",
        )

        self.assertNotIn("Water:", reply)
        self.assertNotIn("Ice:", reply)
        self.assertIn("calories and macros", reply.lower())

    def test_inventory_reply_is_numbered_list(self) -> None:
        reply = self.container.telegram_service.build_reply_for_user(
            "u-inventory-format",
            "/inventory",
            chat_id="chat-inventory-format",
        )

        self.assertIn("Current inventory:", reply)
        self.assertIn("1. ", reply)
        self.assertIn("2. ", reply)
        self.assertNotIn("- bananas:", reply)

    def test_trace_mode_emits_request_trace_with_memory_and_tools(self) -> None:
        def fake_create_response(payload: dict[str, object]) -> dict[str, object]:
            return {
                "id": "resp_trace_1",
                "output_text": "Trace test reply.",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Trace test reply."}],
                    }
                ],
            }

        self.container.llm_service.create_response = fake_create_response
        update = {
            "update_id": 9001,
            "message": {
                "chat": {"id": "chat-trace"},
                "from": {"id": "u-trace"},
                "text": "what can i cook tonight",
            },
        }

        async def fake_send_message_async(chat_id, text, reply_markup=None):
            return {"ok": True, "chat_id": chat_id, "text": text, "reply_markup": reply_markup}

        self.container.telegram_service.send_message_async = fake_send_message_async
        result = self.container.telegram_service.process_update(update)
        reply = result["reply"]

        self.assertEqual(reply, "Trace test reply.")
        trace_dir = Path(os.environ["TRACE_LOG_PATH"])
        trace_files = sorted(trace_dir.glob("*.json"))
        self.assertTrue(trace_files)
        payload = json.loads(trace_files[-1].read_text(encoding="utf-8"))

        self.assertEqual(payload["channel"], "telegram")
        self.assertIn("memory_files", payload)
        self.assertIn("prompt_sections", payload)
        self.assertIn("tools_exposed", payload)
        self.assertIn("decision_rules", payload)
        self.assertIn("final", payload)
        self.assertTrue(any(item.get("section") == "identity" for item in payload["memory_files"]))
        self.assertTrue(any(item.get("section") == "identity" for item in payload["prompt_sections"]))

    def test_llm_generate_reply_streaming_uses_sse_deltas(self) -> None:
        progress_updates: list[str] = []

        def fake_stream_json_sse(url, headers, payload, timeout=30, disable_proxies=True):
            self.assertTrue(payload["stream"])
            yield {"type": "response.output_text.delta", "delta": "Hello"}
            yield {"type": "response.output_text.delta", "delta": " there"}
            yield {
                "type": "response.completed",
                "response": {
                    "id": "resp_stream_1",
                    "output_text": "Hello there",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "Hello there"}],
                        }
                    ],
                },
            }

        import app.core.llm_service as llm_service_module

        original_stream_json_sse = llm_service_module.stream_json_sse
        llm_service_module.stream_json_sse = fake_stream_json_sse
        try:
            reply = self.container.llm_service.generate_reply_streaming(
                user_id="u-sse",
                user_message="hello",
                conversation_context="Recent context.",
                on_progress=progress_updates.append,
            )
        finally:
            llm_service_module.stream_json_sse = original_stream_json_sse

        self.assertEqual(reply, "Hello there")
        self.assertIn("Thinking...", progress_updates)
        self.assertIn("Hello", progress_updates)
        self.assertIn("Hello there", progress_updates)

    def test_recipe_discovery_service_parses_web_search_sources(self) -> None:
        service = RecipeDiscoveryService(llm_service=self.container.llm_service)
        self.container.store.set_user_preferences("u-search-model", search_model="gpt-5-search-api")

        def fake_create_chat_completion(payload: dict[str, object]) -> dict[str, object]:
            self.assertEqual(payload["model"], "gpt-5-search-api")
            self.assertEqual(payload["web_search_options"], {})
            self.assertEqual(payload["response_format"]["type"], "json_schema")
            self.assertEqual(payload["response_format"]["json_schema"]["name"], "recipe_search_results")
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "recipes": [
                                        {
                                            "id": "web-pasta",
                                            "name": "Web Pasta",
                                            "description": "Found online.",
                                            "ingredients": [{"name": "Pasta", "quantity": 1, "unit": "box"}],
                                            "instructions": ["Boil pasta.", "Serve."],
                                            "tags": ["italian"],
                                            "calories": 520,
                                            "protein_g": 18,
                                            "prep_minutes": 15,
                                            "step_count": 2,
                                            "effort_score": 0.3,
                                            "suitable_when_tired": True,
                                            "cuisine": "italian",
                                            "source_url": " https://example.com/web-pasta ",
                                            "source_title": " Example Pasta ",
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            }

        self.container.llm_service.create_chat_completion = fake_create_chat_completion
        recipes = service.search_online_recipes("pasta", max_results=1, user_id="u-search-model")

        self.assertEqual(len(recipes), 1)
        self.assertEqual(recipes[0].name, "Web Pasta")
        self.assertEqual(recipes[0].source_url, "https://example.com/web-pasta")
        self.assertEqual(recipes[0].source_title, "Example Pasta")

    def test_recipe_discovery_service_clamps_effort_score(self) -> None:
        service = RecipeDiscoveryService(llm_service=self.container.llm_service)

        def fake_create_chat_completion(payload: dict[str, object]) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "recipes": [
                                        {
                                            "id": "heavy_recipe",
                                            "name": "Heavy Recipe",
                                            "description": "Found online.",
                                            "ingredients": [{"name": "Rice", "quantity": 1, "unit": "cup"}],
                                            "instructions": ["Cook rice."],
                                            "tags": ["test"],
                                            "calories": 400,
                                            "protein_g": 8,
                                            "prep_minutes": 20,
                                            "step_count": 1,
                                            "effort_score": 3.0,
                                            "suitable_when_tired": False,
                                            "cuisine": "malay",
                                            "source_url": "https://example.com/heavy",
                                            "source_title": "Heavy Recipe Source",
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            }

        self.container.llm_service.create_chat_completion = fake_create_chat_completion
        recipes = service.search_online_recipes("nasi lemak", max_results=1)

        self.assertEqual(len(recipes), 1)
        self.assertEqual(recipes[0].effort_score, 1.0)
        self.assertEqual(recipes[0].cuisine, "malay")

    def test_recipe_discovery_payload_validator_accepts_documented_shape(self) -> None:
        service = RecipeDiscoveryService(llm_service=self.container.llm_service)
        payload = service._build_chat_completion_payload(
            query="nasi lemak",
            max_results=2,
            search_model="gpt-4o-mini-search-preview",
        )

        service._validate_chat_completion_payload(payload)
        self.assertEqual(payload["response_format"]["type"], "json_schema")

    def test_recipe_discovery_payload_validator_rejects_malformed_messages(self) -> None:
        service = RecipeDiscoveryService(llm_service=self.container.llm_service)
        payload = {
            "model": "gpt-4o-mini-search-preview",
            "web_search_options": {},
            "response_format": {"type": "json_schema"},
            "messages": [{"role": "user", "content": 123}],
        }

        with self.assertRaises(ValueError):
            service._validate_chat_completion_payload(payload)

    def test_recipe_discovery_retries_chat_completion_once_before_success(self) -> None:
        service = RecipeDiscoveryService(llm_service=self.container.llm_service)
        attempts = 0

        def flaky_create_chat_completion(payload: dict[str, object]) -> dict[str, object]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError(
                    "HTTP request failed with status 400: We could not parse the JSON body of your request."
                )
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "recipes": [
                                        {
                                            "id": "retry-pasta",
                                            "name": "Retry Pasta",
                                            "ingredients": [{"name": "Pasta", "quantity": 1, "unit": "box"}],
                                            "instructions": ["Boil pasta."],
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            }

        self.container.llm_service.create_chat_completion = flaky_create_chat_completion
        recipes = service.search_online_recipes("pasta", max_results=1, user_id="u-retry")

        self.assertEqual(attempts, 2)
        self.assertEqual(recipes[0].name, "Retry Pasta")

    def test_recipe_discovery_repeated_failure_retries_once_then_raises_with_fingerprint(self) -> None:
        service = RecipeDiscoveryService(llm_service=self.container.llm_service)
        attempts = 0

        def fail_create_chat_completion(payload: dict[str, object]) -> dict[str, object]:
            nonlocal attempts
            attempts += 1
            raise RuntimeError(
                "HTTP request failed with status 400: We could not parse the JSON body of your request."
            )

        self.container.llm_service.create_chat_completion = fail_create_chat_completion

        with self.assertRaises(RecipeSearchChatCompletionError) as context:
            service.search_online_recipes("pasta", max_results=1, user_id="u-retry-fail")

        self.assertEqual(attempts, 2)
        self.assertTrue(bool(context.exception.request_fingerprint))
        self.assertIn("Online recipe search failed", str(context.exception))

    def test_recipe_discovery_service_raises_on_invalid_json(self) -> None:
        service = RecipeDiscoveryService(llm_service=self.container.llm_service)

        def fake_create_chat_completion(payload: dict[str, object]) -> dict[str, object]:
            return {"choices": [{"message": {"content": "not valid json"}}]}

        self.container.llm_service.create_chat_completion = fake_create_chat_completion

        with self.assertRaises(RecipeSearchChatCompletionError) as context:
            service.search_online_recipes("pasta", max_results=1)
        self.assertIn("invalid JSON", str(context.exception))
        recent_events = self.container.llm_service.debug_snapshot()["recent_events"]
        parse_event = next(
            entry for entry in recent_events if entry["summary"] == "Recipe search response JSON parse failed."
        )
        self.assertEqual(
            parse_event["metadata"].get("request_fingerprint"),
            context.exception.request_fingerprint,
        )
        self.assertIn("not valid json", parse_event["metadata"].get("response_preview_head", ""))

    def test_chat_completion_logging_records_redacted_request_metadata(self) -> None:
        import app.core.llm_service as llm_service_module

        original_post_json = llm_service_module.post_json

        def fake_post_json(url: str, headers: dict[str, str], payload: dict[str, object], timeout: int = 30, disable_proxies: bool = True) -> dict[str, object]:
            return {"choices": [{"message": {"content": "ok"}}]}

        llm_service_module.post_json = fake_post_json
        try:
            payload = {
                "model": "gpt-4o-mini-search-preview",
                "web_search_options": {},
                "messages": [
                    {"role": "system", "content": "Return JSON only."},
                    {"role": "user", "content": "Find nasi lemak recipes online."},
                ],
            }
            self.container.llm_service.create_chat_completion(payload)
        finally:
            llm_service_module.post_json = original_post_json

        recent_events = self.container.llm_service.debug_snapshot()["recent_events"]
        event = next(
            entry for entry in recent_events if entry["summary"] == "Chat Completions API call succeeded."
        )
        metadata = event["metadata"]
        self.assertEqual(metadata["model"], "gpt-4o-mini-search-preview")
        self.assertTrue(bool(metadata.get("request_fingerprint")))
        self.assertEqual(metadata["message_roles"], ["system", "user"])
        self.assertEqual(metadata["content_lengths"], [17, 31])
        self.assertTrue(metadata["has_web_search_options"])
        self.assertNotIn("nasi lemak", json.dumps(metadata).lower())

    def test_telegram_draft_success_then_final_send(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def fake_telegram_api_call_async(method_name: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((method_name, payload))
            if method_name == "sendMessage":
                return {"ok": True, "method": method_name, "result": {"message_id": 321}}
            return {"ok": True, "method": method_name}

        async def fake_build_reply_for_user_async(user_id, text, chat_id=None, draft_callback=None) -> str:
            if draft_callback is not None:
                await draft_callback("Working on your reply...")
            return "Final reply."

        self.container.telegram_service._telegram_api_call_async = fake_telegram_api_call_async
        self.container.telegram_service.build_reply_for_user_async = fake_build_reply_for_user_async
        self.container.telegram_service.settings = self.container.telegram_service.settings.__class__(
            **{
                **self.container.telegram_service.settings.__dict__,
                "telegram_send_retries": 1,
            }
        )

        result = self.container.telegram_service.process_update(
            {
                "update_id": 1001,
                "message": {
                    "chat": {"id": 12345},
                    "from": {"id": "u-draft-success"},
                    "text": "hello there",
                },
            }
        )

        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["reply"], "Final reply.")
        self.assertEqual(calls[0][0], "sendMessage")
        self.assertEqual(calls[-1][0], "editMessageText")
        self.assertTrue(self.container.telegram_service._draft_streaming_supported)

    def test_set_my_commands_registers_full_command_list(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def fake_telegram_api_call_async(method_name: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((method_name, payload))
            return {"ok": True, "method": method_name}

        self.container.telegram_service._telegram_api_call_async = fake_telegram_api_call_async
        self.container.telegram_service.settings = self.container.telegram_service.settings.__class__(
            **{
                **self.container.telegram_service.settings.__dict__,
                "telegram_bot_token": "test-token",
            }
        )

        result = self.container.telegram_service.set_my_commands()

        self.assertEqual(result["method"], "setMyCommands")
        self.assertEqual(calls[0][0], "setMyCommands")
        commands = calls[0][1]["commands"]
        command_names = [item["command"] for item in commands]
        self.assertIn("recipes", command_names)
        self.assertIn("suggestions", command_names)
        self.assertIn("inventory", command_names)
        self.assertIn("groceries", command_names)
        self.assertIn("cook", command_names)
        self.assertIn("utilities", command_names)
        self.assertIn("heartbeat", command_names)
        self.assertIn("searchmodel", command_names)
        self.assertIn("new", command_names)

    def test_register_webhook_also_sets_my_commands(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def fake_telegram_api_call_async(method_name: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((method_name, payload))
            return {"ok": True, "method": method_name}

        self.container.telegram_service._telegram_api_call_async = fake_telegram_api_call_async
        self.container.telegram_service.settings = self.container.telegram_service.settings.__class__(
            **{
                **self.container.telegram_service.settings.__dict__,
                "telegram_bot_token": "test-token",
                "telegram_webhook_url": "https://example.com/telegram/webhook",
            }
        )

        result = self.container.telegram_service.register_webhook(
            url="https://example.com/telegram/webhook",
            drop_pending_updates=True,
        )

        self.assertEqual(result["method"], "setWebhook")
        self.assertEqual(calls[0][0], "setWebhook")
        self.assertEqual(calls[1][0], "setMyCommands")

    def test_send_message_splits_long_final_reply(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def fake_telegram_api_call_async(method_name: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((method_name, payload))
            return {"ok": True, "method": method_name, "text": payload.get("text")}

        self.container.telegram_service._telegram_api_call_async = fake_telegram_api_call_async
        long_text = ("A" * 2500) + "\n\n" + ("B" * 2500)

        result = self.container.telegram_service.send_message(
            chat_id="chat-long",
            text=long_text,
            reply_markup={"inline_keyboard": []},
        )

        send_calls = [payload for method, payload in calls if method == "sendMessage"]
        self.assertEqual(len(send_calls), 2)
        self.assertLessEqual(len(str(send_calls[0]["text"])), 4000)
        self.assertLessEqual(len(str(send_calls[1]["text"])), 4000)
        self.assertEqual(send_calls[0].get("reply_markup"), {"inline_keyboard": []})
        self.assertNotIn("reply_markup", send_calls[1])
        self.assertEqual(result["chunks_sent"], 2)

    def test_telegram_draft_falls_back_after_rejection(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []
        failed_once = False

        async def fake_telegram_api_call_async(method_name: str, payload: dict[str, object]) -> dict[str, object]:
            nonlocal failed_once
            calls.append((method_name, payload))
            if method_name == "sendMessage" and not failed_once:
                failed_once = True
                raise RuntimeError("HTTP request failed with status 404: method not found")
            return {"ok": True, "method": method_name}

        async def fake_build_reply_for_user_async(user_id, text, chat_id=None, draft_callback=None) -> str:
            if draft_callback is not None:
                await draft_callback("Working on your reply...")
            return "Fallback final reply."

        self.container.telegram_service._telegram_api_call_async = fake_telegram_api_call_async
        self.container.telegram_service.build_reply_for_user_async = fake_build_reply_for_user_async
        self.container.telegram_service.settings = self.container.telegram_service.settings.__class__(
            **{
                **self.container.telegram_service.settings.__dict__,
                "telegram_send_retries": 1,
            }
        )

        result = self.container.telegram_service.process_update(
            {
                "update_id": 1002,
                "message": {
                    "chat": {"id": 12345},
                    "from": {"id": "u-draft-fallback"},
                    "text": "hello fallback",
                },
            }
        )

        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["reply"], "Fallback final reply.")
        self.assertEqual(calls[0][0], "sendMessage")
        self.assertEqual(calls[-1][0], "sendMessage")
        self.assertFalse(self.container.telegram_service._draft_streaming_supported)

    def test_llm_streaming_updates_drafts_before_final_send(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def fake_telegram_api_call_async(method_name: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((method_name, payload))
            if method_name == "sendMessage":
                return {"ok": True, "method": method_name, "result": {"message_id": 789}}
            return {"ok": True, "method": method_name}

        def fake_generate_reply_streaming_result(
            *,
            user_id: str,
            user_message: str,
            conversation_context: str | None = None,
            on_progress=None,
        ) -> str:
            if on_progress is not None:
                on_progress("Thinking...")
                on_progress("Checking fridge data...")
                on_progress("Finalizing reply...")
            return LLMReplyResult(text="Streamed final reply.")

        self.container.telegram_service._telegram_api_call_async = fake_telegram_api_call_async
        self.container.llm_service.generate_reply_streaming_result = fake_generate_reply_streaming_result

        result = self.container.telegram_service.process_update(
            {
                "update_id": 1003,
                "message": {
                    "chat": {"id": 12345},
                    "from": {"id": "u-stream"},
                    "text": "what should I eat today?",
                },
            }
        )

        draft_calls = [payload for method, payload in calls if method in {"sendMessage", "editMessageText"}]
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["reply"], "Streamed final reply.")
        self.assertTrue(draft_calls)
        self.assertEqual(calls[-1][0], "editMessageText")
        self.assertTrue(
            any(
                str(payload.get("text")).startswith(("Working on it", "Thinking", "Checking fridge", "Finalizing"))
                for payload in draft_calls
            )
        )

    def test_telegram_streaming_ignores_message_not_modified_error(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def fake_telegram_api_call_async(method_name: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((method_name, payload))
            if method_name == "sendMessage":
                return {"ok": True, "method": method_name, "result": {"message_id": 456}}
            if method_name == "editMessageText" and payload.get("text") == "Final reply.":
                raise RuntimeError(
                    'HTTP request failed with status 400: {"ok":false,"error_code":400,'
                    '"description":"Bad Request: message is not modified: specified new message '
                    'content and reply markup are exactly the same as a current content and reply markup of the message"}'
                )
            return {"ok": True, "method": method_name}

        async def fake_build_reply_for_user_async(user_id, text, chat_id=None, draft_callback=None) -> str:
            if draft_callback is not None:
                await draft_callback("Final reply.")
            return "Final reply."

        self.container.telegram_service._telegram_api_call_async = fake_telegram_api_call_async
        self.container.telegram_service.build_reply_for_user_async = fake_build_reply_for_user_async

        result = self.container.telegram_service.process_update(
            {
                "update_id": 1004,
                "message": {
                    "chat": {"id": 12345},
                    "from": {"id": "u-not-modified"},
                    "text": "hello",
                },
            }
        )

        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["reply"], "Final reply.")
        self.assertTrue(self.container.telegram_service._draft_streaming_supported)
        self.assertEqual(calls[0][0], "sendMessage")
        self.assertEqual(calls[-1][0], "editMessageText")

    def test_telegram_bulk_inventory_add_is_deterministic_and_persists_items(self) -> None:
        reply = self.container.telegram_service.build_reply_for_user(
            user_id="u-inventory-added",
            text="Add these into inventory\nFresh Produce\nXiao bai cai (bok choy)\nGai lan (Chinese broccoli)\nEggs",
        )

        self.assertIn("Added 3 item(s) to inventory", reply)
        inventory_names = [item.name.lower() for item in self.container.inventory_agent.get_inventory()]
        self.assertIn("xiao bai cai", inventory_names)
        self.assertIn("gai lan", inventory_names)
        self.assertIn("eggs", inventory_names)

    def test_telegram_bulk_inventory_add_skips_ambiguous_lines(self) -> None:
        reply = self.container.telegram_service.build_reply_for_user(
            user_id="u-inventory-ambiguous",
            text="Add these into inventory\nProtein\nPork belly slices or minced pork\nChicken thighs or whole chicken\nEggs",
        )

        self.assertIn("Added 1 item(s) to inventory", reply)
        self.assertIn("Skipped ambiguous lines", reply)
        inventory_names = [item.name.lower() for item in self.container.inventory_agent.get_inventory()]
        self.assertIn("eggs", inventory_names)
        self.assertNotIn("pork belly slices or minced pork", inventory_names)

    def test_telegram_inventory_follow_up_uses_pending_bulk_context(self) -> None:
        user_id = "u-inventory-followup"
        candidates = [
            {"name": "xiao bai cai", "quantity": 1.0, "unit": "bunch", "category": "produce", "source_line": "Xiao bai cai", "ambiguous": False},
            {"name": "gai lan", "quantity": 1.0, "unit": "bunch", "category": "produce", "source_line": "Gai lan", "ambiguous": False},
        ]
        self.container.store.set_temporary_state(
            user_id,
            state="pending_bulk_inventory_import",
            value="pending",
            expires_at=utc_now() + timedelta(hours=1),
            note=json.dumps({"candidates": candidates, "skipped_lines": []}),
        )

        reply = self.container.telegram_service.build_reply_for_user(
            user_id=user_id,
            text="Yes add this in a sensible way",
        )

        self.assertIn("Added 2 item(s) to inventory", reply)
        inventory_names = [item.name.lower() for item in self.container.inventory_agent.get_inventory()]
        self.assertIn("xiao bai cai", inventory_names)
        self.assertIn("gai lan", inventory_names)

    def test_telegram_inventory_add_does_not_write_to_shopping_list(self) -> None:
        before_count = len(self.container.store.snapshot().pending_grocery_list)

        self.container.telegram_service.build_reply_for_user(
            user_id="u-no-shopping-list",
            text="Add all these items into inventory.\nFresh Produce\nGarlic\nGinger\nTomatoes",
        )

        after_count = len(self.container.store.snapshot().pending_grocery_list)
        self.assertEqual(before_count, after_count)


if __name__ == "__main__":
    unittest.main()
