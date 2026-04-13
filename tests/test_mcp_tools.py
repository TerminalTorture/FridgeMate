from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.container import build_container
from app.core.llm_gateway import LLMGatewayService
from app.core.settings import get_settings
from app.core.time_utils import utc_now
from app.models.domain import AssistantIntervention, Recipe, RecipeIngredient


class MCPToolSmokeTest(unittest.TestCase):
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
            "terminal_exec": {"command": "Get-Location | Select-Object -ExpandProperty Path", "cwd": ".", "mode": "read_only"},
            "get_decision_state": {"user_id": "u1"},
            "run_decision": {"user_id": "u1", "force": "true"},
            "get_user_preferences": {"user_id": "u1"},
            "set_user_preferences": {"user_id": "u1", "mode": "strict", "max_prep_minutes": 8, "notification_frequency": "active", "dietary_preferences": ["avoid dairy"]},
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
            {"command": "Get-Location | Select-Object -ExpandProperty Path", "cwd": ".", "mode": "read_only"},
        )
        self.assertEqual(terminal_result["tool_name"], "terminal_exec")
        self.assertEqual(terminal_result["returncode"], 0)

        with self.assertRaises(PermissionError):
            self.container.mcp_tool_service.call_tool(
                "terminal_exec",
                {"command": "Set-Content README.md hacked", "cwd": ".", "mode": "read_only"},
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

    def test_telegram_draft_success_then_final_send(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def fake_telegram_api_call_async(method_name: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((method_name, payload))
            return {"ok": True, "method": method_name}

        async def fake_build_reply_for_user_async(user_id, text, chat_id=None, draft_callback=None) -> str:
            if draft_callback is not None:
                await draft_callback("Working on your reply...")
            return "Final reply."

        self.container.telegram_service._telegram_api_call_async = fake_telegram_api_call_async
        self.container.telegram_service.build_reply_for_user_async = fake_build_reply_for_user_async

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
        self.assertEqual(calls[0][0], "sendMessageDraft")
        self.assertEqual(calls[-1][0], "sendMessage")
        self.assertTrue(self.container.telegram_service._draft_streaming_supported)

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

        async def fake_telegram_api_call_async(method_name: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((method_name, payload))
            if method_name == "sendMessageDraft":
                raise RuntimeError("HTTP request failed with status 404: method not found")
            return {"ok": True, "method": method_name}

        async def fake_build_reply_for_user_async(user_id, text, chat_id=None, draft_callback=None) -> str:
            if draft_callback is not None:
                await draft_callback("Working on your reply...")
            return "Fallback final reply."

        self.container.telegram_service._telegram_api_call_async = fake_telegram_api_call_async
        self.container.telegram_service.build_reply_for_user_async = fake_build_reply_for_user_async

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
        self.assertEqual(calls[0][0], "sendMessageDraft")
        self.assertEqual(calls[-1][0], "sendMessage")
        self.assertFalse(self.container.telegram_service._draft_streaming_supported)

    def test_llm_streaming_updates_drafts_before_final_send(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def fake_telegram_api_call_async(method_name: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((method_name, payload))
            return {"ok": True, "method": method_name}

        def fake_generate_reply_streaming(
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
            return "Streamed final reply."

        self.container.telegram_service._telegram_api_call_async = fake_telegram_api_call_async
        self.container.llm_service.generate_reply_streaming = fake_generate_reply_streaming

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

        draft_calls = [payload for method, payload in calls if method == "sendMessageDraft"]
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["reply"], "Streamed final reply.")
        self.assertTrue(draft_calls)
        self.assertEqual(calls[-1][0], "sendMessage")
        self.assertTrue(
            any(
                str(payload.get("text")).startswith(("Working on it", "Thinking", "Checking fridge", "Finalizing"))
                for payload in draft_calls
            )
        )


if __name__ == "__main__":
    unittest.main()
