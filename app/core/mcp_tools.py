from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from app.agents.behaviour import BehaviourAgent
from app.agents.grocery import GroceryAgent
from app.agents.inventory import InventoryAgent
from app.agents.nutrition import NutritionAgent
from app.agents.recipe import RecipeAgent
from app.agents.utility import UtilityAgent
from app.core.confirmation_manager import ConfirmationManager, PendingConfirmation
from app.core.conversation_manager import ConversationManager
from app.core.context_store import ContextStore
from app.core.decision_engine import DecisionEngine
from app.core.diagnostics import DiagnosticsEngine
from app.core.heartbeat_service import HeartbeatService
from app.core.integration_debug import IntegrationDebugLog
from app.core.llm_gateway import LLMGatewayService
from app.core.recipe_discovery_service import RecipeDiscoveryService
from app.core.search_models import ALLOWED_SEARCH_MODELS, DEFAULT_SEARCH_MODEL, is_valid_search_model
from app.core.runtime_state import RuntimeStateAggregator
from app.core.time_utils import utc_now
from app.core.tracing import record_tool_call, record_tools_exposed
from app.models.api import RecipeInput
from app.models.domain import GroceryLine, InventoryItem


class MCPToolService:
    PROTECTED_TOOLS = {
        "clear_inventory",
        "remove_inventory_item",
        "order_groceries_for_recipe",
        "order_staple_restock",
    }

    def __init__(
        self,
        *,
        store: ContextStore,
        inventory_agent: InventoryAgent,
        recipe_agent: RecipeAgent,
        grocery_agent: GroceryAgent,
        nutrition_agent: NutritionAgent,
        behaviour_agent: BehaviourAgent,
        utility_agent: UtilityAgent,
        conversation_manager: ConversationManager,
        recipe_discovery_service: RecipeDiscoveryService,
        runtime_state_aggregator: RuntimeStateAggregator,
        diagnostics_engine: DiagnosticsEngine,
        heartbeat_service: HeartbeatService,
        confirmation_manager: ConfirmationManager,
        decision_engine: DecisionEngine,
        llm_gateway_service: LLMGatewayService,
    ) -> None:
        self.store = store
        self.inventory_agent = inventory_agent
        self.recipe_agent = recipe_agent
        self.grocery_agent = grocery_agent
        self.nutrition_agent = nutrition_agent
        self.behaviour_agent = behaviour_agent
        self.utility_agent = utility_agent
        self.conversation_manager = conversation_manager
        self.recipe_discovery_service = recipe_discovery_service
        self.runtime_state_aggregator = runtime_state_aggregator
        self.diagnostics_engine = diagnostics_engine
        self.heartbeat_service = heartbeat_service
        self.confirmation_manager = confirmation_manager
        self.decision_engine = decision_engine
        self.llm_gateway_service = llm_gateway_service
        self.debug_log = IntegrationDebugLog()
        self._registry = self._build_registry()

    def list_tools(self) -> list[dict[str, object]]:
        keys = (
            "name",
            "description",
            "arguments",
            "policy",
            "when_to_use",
            "when_not_to_use",
            "authoritative_source",
        )
        return [{key: tool[key] for key in keys} for tool in self._registry]

    def responses_api_tools(self) -> list[dict[str, object]]:
        tools = [
            {
                "type": "function",
                "name": str(tool["name"]),
                "description": str(tool["description"]),
                "parameters": tool["parameters"],
            }
            for tool in self._registry
        ]
        record_tools_exposed([str(tool["name"]) for tool in tools])
        return tools

    def prompt_tool_registry(self) -> str:
        lines: list[str] = []
        for tool in self.list_tools():
            lines.append(
                f"- {tool['name']}: {tool['description']} "
                f"Policy={tool['policy']}. Use={tool['when_to_use']}. "
                f"Avoid={tool['when_not_to_use']}. Source={tool['authoritative_source']}."
            )
        return "\n".join(lines)

    def gateway_access_summary(self) -> str:
        return self.llm_gateway_service.prompt_summary()

    def call_tool(self, tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
        clean_arguments = {key: value for key, value in arguments.items() if not str(key).startswith("_")}
        if tool_name in self.PROTECTED_TOOLS:
            result = self._request_confirmation(tool_name, clean_arguments)
        else:
            result = self._call_tool_impl(tool_name, clean_arguments)
        record_tool_call(name=tool_name, arguments=clean_arguments, result=result)
        self.debug_log.record(
            service="mcp",
            direction="internal",
            status="success",
            summary=f"MCP tool executed: {tool_name}.",
            metadata={"tool_name": tool_name, "requires_confirmation": result.get("requires_confirmation", False)},
        )
        return result

    def confirm_pending_action(self, confirmation_id: str, user_id: str | None = None) -> dict[str, object]:
        target_id = self._resolve_confirmation_id(confirmation_id, user_id)

        def executor(confirmation: PendingConfirmation) -> dict[str, object]:
            return self._call_tool_impl(confirmation.action, confirmation.arguments)

        result = self.confirmation_manager.confirm(target_id, executor)
        return {"tool_name": "confirm_pending_action", **result}

    def cancel_pending_action(self, confirmation_id: str, user_id: str | None = None) -> dict[str, object]:
        target_id = self._resolve_confirmation_id(confirmation_id, user_id)
        result = self.confirmation_manager.cancel(target_id)
        return {"tool_name": "cancel_pending_action", **result}

    def debug_snapshot(self) -> dict[str, object]:
        return {
            "tool_count": len(self.list_tools()),
            "protected_tools": sorted(self.PROTECTED_TOOLS),
            "pending_confirmations": self.confirmation_manager.pending_actions(),
            "recent_events": self.debug_log.dump(),
        }

    def _call_tool_impl(self, tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
        if tool_name == "get_memory_snapshot":
            user_id = str(arguments.get("user_id") or "").strip()
            snapshot = self.store.snapshot()
            return {
                "tool_name": tool_name,
                "version": snapshot.version,
                "inventory_count": len(snapshot.inventory),
                "inventory_batch_count": len(snapshot.inventory_batches),
                "recipe_count": len(snapshot.recipe_catalog),
                "shopping_list_count": len(snapshot.pending_grocery_list),
                "grocery_order_count": len(snapshot.grocery_orders),
                "utilities": snapshot.utilities.model_dump(),
                "recent_events": [event.model_dump(mode="json") for event in snapshot.recent_events[:10]],
                "session_status": self.conversation_manager.session_status(user_id) if user_id else None,
            }

        if tool_name == "get_runtime_state":
            return {"tool_name": tool_name, "runtime_state": self.runtime_state_aggregator.build(last_user_message=str(arguments.get("last_user_message") or ""), user_id=str(arguments.get("user_id") or "") or None)}

        if tool_name == "diagnostics_report":
            return {"tool_name": tool_name, **self.diagnostics_engine.diagnostics_report(last_user_message=str(arguments.get("last_user_message") or ""), user_id=str(arguments.get("user_id") or "") or None)}

        if tool_name == "run_heartbeat_check":
            return {"tool_name": tool_name, **self.heartbeat_service.run_once()}

        if tool_name == "get_inventory":
            return {
                "tool_name": tool_name,
                "items": [item.model_dump(mode="json") for item in self.inventory_agent.get_inventory()],
                "batches": [batch.model_dump(mode="json") for batch in self.store.list_inventory_batches()],
                "expiring_soon": [item.model_dump(mode="json") for item in self.inventory_agent.expiring_soon(days=3)],
                "low_stock": [item.model_dump(mode="json") for item in self.inventory_agent.low_stock_items()],
            }

        if tool_name == "get_inventory_batches":
            return {
                "tool_name": tool_name,
                "batches": [batch.model_dump(mode="json") for batch in self.store.list_inventory_batches(include_inactive=True)],
            }

        if tool_name == "get_expiring_items":
            days = self._coerce_int(arguments.get("days"), default=1)
            return {"tool_name": tool_name, "days": days, "items": [item.model_dump(mode="json") for item in self.inventory_agent.expiring_soon(days=days)]}

        if tool_name == "compare_current_inventory_to_expected_inventory":
            runtime_state = self.runtime_state_aggregator.build(last_user_message=str(arguments.get("last_user_message") or ""), user_id=str(arguments.get("user_id") or "") or None)
            mismatches = runtime_state.get("inventory_confidence_mismatches")
            return {
                "tool_name": tool_name,
                "low_stock_mismatches": [item.model_dump(mode="json") for item in self.inventory_agent.low_stock_items()],
                "sensor_mismatches": mismatches if isinstance(mismatches, list) else [],
                "confidence": "simulated",
                "note": "Expected inventory currently means configured restock thresholds plus local simulated mismatch data.",
            }

        if tool_name == "add_inventory_item":
            item = InventoryItem(
                name=str(arguments.get("name") or "").strip(),
                quantity=self._coerce_float(arguments.get("quantity"), default=0.0),
                unit=str(arguments.get("unit") or "unit"),
                purchased_at=self._optional_datetime(arguments.get("purchased_at")),
                expires_on=self._optional_date(arguments.get("expires_on")),
                category=str(arguments.get("category") or "general"),
                min_desired_quantity=self._coerce_float(arguments.get("min_desired_quantity"), default=1.0),
            )
            if not item.name or item.quantity <= 0:
                raise ValueError("name and a positive quantity are required.")
            stored = self.inventory_agent.add_or_refresh_item(item)
            return {"tool_name": tool_name, "item": stored.model_dump(mode="json")}

        if tool_name == "remove_inventory_item":
            return {"tool_name": tool_name, **self.inventory_agent.remove_item(str(arguments.get("name") or "").strip())}

        if tool_name == "clear_inventory":
            return {"tool_name": tool_name, **self.inventory_agent.clear_inventory()}

        if tool_name == "add_to_shopping_list":
            name = str(arguments.get("name") or "").strip()
            quantity = self._coerce_float(arguments.get("quantity"), default=1.0)
            unit = str(arguments.get("unit") or "unit")
            reason = str(arguments.get("reason") or "manual request")
            if not name or quantity <= 0:
                raise ValueError("name and a positive quantity are required.")
            line = GroceryLine(name=name, quantity=quantity, unit=unit, reason=reason)

            def mutator(state):
                state.pending_grocery_list.append(line)
                return {"item": name, "quantity": quantity, "unit": unit}

            updated = self.store.update(
                agent="mcp_tool_service",
                action="add_to_shopping_list",
                summary=f"Added {name} to the pending shopping list.",
                mutator=mutator,
            )
            return {"tool_name": tool_name, "item": line.model_dump(mode="json"), "shopping_list_count": len(updated.pending_grocery_list)}

        if tool_name == "list_recipes":
            return {"tool_name": tool_name, "recipes": [recipe.model_dump(mode="json") for recipe in self.recipe_agent.list_recipes()]}

        if tool_name == "search_recipes_online":
            query = str(arguments.get("query") or "").strip()
            max_results = self._coerce_int(arguments.get("max_results"), default=3)
            user_id = str(arguments.get("user_id") or "").strip() or None
            recipes = self.recipe_discovery_service.search_online_recipes(query=query, max_results=max_results, user_id=user_id)
            return {"tool_name": tool_name, "query": query, "results": [recipe.model_dump(mode="json") for recipe in recipes]}

        if tool_name == "import_recipe":
            recipe_payload = arguments.get("recipe")
            if not isinstance(recipe_payload, dict):
                raise ValueError("recipe must be an object.")
            stored_recipe = self.recipe_agent.add_recipe(RecipeInput(**recipe_payload).to_domain())
            return {"tool_name": tool_name, "recipe": stored_recipe.model_dump(mode="json")}

        if tool_name == "search_and_import_recipe":
            query = str(arguments.get("query") or "").strip()
            max_results = self._coerce_int(arguments.get("max_results"), default=3)
            selection_index = self._coerce_int(arguments.get("selection_index"), default=0)
            user_id = str(arguments.get("user_id") or "").strip() or None
            recipes = self.recipe_discovery_service.search_online_recipes(query=query, max_results=max_results, user_id=user_id)
            if not recipes:
                raise ValueError("No recipes were returned from online search.")
            if selection_index < 0 or selection_index >= len(recipes):
                raise ValueError("selection_index is out of range.")
            stored_recipe = self.recipe_agent.add_recipe(recipes[selection_index])
            return {"tool_name": tool_name, "selected_index": selection_index, "recipe": stored_recipe.model_dump(mode="json")}

        if tool_name == "get_utilities":
            return {"tool_name": tool_name, **self.utility_agent.get_status()}

        if tool_name == "update_utilities":
            water_level = arguments.get("water_level_percent")
            ice_level = arguments.get("ice_level_percent")
            return {
                "tool_name": tool_name,
                **self.utility_agent.update_levels(
                    water_level_percent=self._coerce_int(water_level) if water_level is not None else None,
                    ice_level_percent=self._coerce_int(ice_level) if ice_level is not None else None,
                ),
            }

        if tool_name == "get_nutrition_summary":
            return {"tool_name": tool_name, **self.nutrition_agent.get_summary()}

        if tool_name == "get_behaviour_summary":
            return {"tool_name": tool_name, **self.behaviour_agent.get_summary()}

        if tool_name == "order_groceries_for_recipe":
            recipe_id = str(arguments.get("recipe_id") or "").strip()
            return {"tool_name": tool_name, **self.grocery_agent.order_missing_for_recipe(recipe_id)}

        if tool_name == "place_custom_grocery_order":
            raw_items = arguments.get("items")
            if not isinstance(raw_items, list):
                raise ValueError("items must be a list.")
            items = [GroceryLine(**item) for item in raw_items if isinstance(item, dict)]
            source = str(arguments.get("source") or "manual_api")
            return {"tool_name": tool_name, **self.grocery_agent.place_order(items, source=source)}

        if tool_name == "order_staple_restock":
            return {"tool_name": tool_name, **self.grocery_agent.order_staple_restock()}

        if tool_name == "get_session_status":
            user_id = str(arguments.get("user_id") or "").strip()
            if not user_id:
                raise ValueError("user_id is required.")
            return {"tool_name": tool_name, **self.conversation_manager.session_status(user_id)}

        if tool_name == "get_heartbeat_status":
            user_id = str(arguments.get("user_id") or "").strip()
            if not user_id:
                raise ValueError("user_id is required.")
            return {"tool_name": tool_name, **self.heartbeat_service.status_for_user(user_id)}

        if tool_name == "set_heartbeat_status":
            user_id = str(arguments.get("user_id") or "").strip()
            if not user_id:
                raise ValueError("user_id is required.")
            enabled_raw = arguments.get("enabled")
            enabled = None
            if enabled_raw is not None and str(enabled_raw).strip() != "":
                enabled = str(enabled_raw).strip().lower() in {"1", "true", "yes", "on"}
            dinner_time = str(arguments.get("dinner_time") or "").strip() or None
            interval_minutes = arguments.get("interval_minutes")
            return {
                "tool_name": tool_name,
                **self.heartbeat_service.configure(
                    user_id,
                    enabled=enabled,
                    dinner_time=dinner_time,
                    interval_minutes=self._coerce_int(interval_minutes) if interval_minutes not in (None, "") else None,
                ),
            }

        if tool_name == "set_heartbeat_interval":
            user_id = str(arguments.get("user_id") or "").strip()
            if not user_id:
                raise ValueError("user_id is required.")
            interval_minutes = self._coerce_int(arguments.get("interval_minutes"), default=0)
            if interval_minutes < 1 or interval_minutes > 1440:
                raise ValueError("interval_minutes must be between 1 and 1440.")
            return {
                "tool_name": tool_name,
                **self.heartbeat_service.configure(
                    user_id,
                    interval_minutes=interval_minutes,
                ),
            }

        if tool_name == "gateway_get_access":
            path = str(arguments.get("path") or "").strip() or None
            return {"tool_name": tool_name, **self.llm_gateway_service.access_snapshot(path)}

        if tool_name == "fs_list_dir":
            path = str(arguments.get("path") or "").strip()
            if not path:
                raise ValueError("path is required.")
            return {"tool_name": tool_name, **self.llm_gateway_service.list_dir(path)}

        if tool_name == "fs_tree":
            path = str(arguments.get("path") or "").strip()
            if not path:
                raise ValueError("path is required.")
            max_depth = self._coerce_int(arguments.get("max_depth"), default=3)
            return {"tool_name": tool_name, **self.llm_gateway_service.tree(path, max_depth=max_depth)}

        if tool_name == "fs_read_text":
            path = str(arguments.get("path") or "").strip()
            if not path:
                raise ValueError("path is required.")
            return {"tool_name": tool_name, **self.llm_gateway_service.read_text(path)}

        if tool_name == "fs_search_text":
            query = str(arguments.get("query") or "").strip()
            if not query:
                raise ValueError("query is required.")
            path = str(arguments.get("path") or ".").strip() or "."
            max_results = self._coerce_int(arguments.get("max_results"), default=20)
            return {
                "tool_name": tool_name,
                **self.llm_gateway_service.search_text(query, path=path, max_results=max_results),
            }

        if tool_name == "fs_write_text":
            path = str(arguments.get("path") or "").strip()
            if not path:
                raise ValueError("path is required.")
            content = str(arguments.get("content") or "")
            return {"tool_name": tool_name, **self.llm_gateway_service.write_text(path, content)}

        if tool_name == "fs_append_text":
            path = str(arguments.get("path") or "").strip()
            if not path:
                raise ValueError("path is required.")
            content = str(arguments.get("content") or "")
            return {"tool_name": tool_name, **self.llm_gateway_service.append_text(path, content)}

        if tool_name == "terminal_exec":
            command = str(arguments.get("command") or "").strip()
            if not command:
                raise ValueError("command is required.")
            cwd = str(arguments.get("cwd") or ".").strip() or "."
            mode = str(arguments.get("mode") or "read_only").strip() or "read_only"
            return {
                "tool_name": tool_name,
                **self.llm_gateway_service.terminal_exec(command, cwd=cwd, mode=mode),
            }

        if tool_name == "get_decision_state":
            user_id = str(arguments.get("user_id") or "").strip()
            if not user_id:
                raise ValueError("user_id is required.")
            return {"tool_name": tool_name, **self.decision_engine.public_state(user_id)}

        if tool_name == "run_decision":
            user_id = str(arguments.get("user_id") or "").strip()
            if not user_id:
                raise ValueError("user_id is required.")
            force = str(arguments.get("force") or "").strip().lower() in {"1", "true", "yes", "on"}
            result = self.decision_engine.run_for_user(user_id, force=force)
            return {"tool_name": tool_name, **result.model_dump(mode="json")}

        if tool_name == "get_user_preferences":
            user_id = str(arguments.get("user_id") or "").strip()
            if not user_id:
                raise ValueError("user_id is required.")
            preferences = self.store.user_preferences(user_id)
            return {"tool_name": tool_name, **preferences.model_dump(mode="json")}

        if tool_name == "set_user_preferences":
            user_id = str(arguments.get("user_id") or "").strip()
            if not user_id:
                raise ValueError("user_id is required.")
            dietary_preferences = arguments.get("dietary_preferences")
            if dietary_preferences is not None and not isinstance(dietary_preferences, list):
                raise ValueError("dietary_preferences must be a list of strings.")
            essentials_items = arguments.get("essentials_items")
            if essentials_items is not None and not isinstance(essentials_items, list):
                raise ValueError("essentials_items must be a list of strings.")
            dairy_items = arguments.get("dairy_items")
            if dairy_items is not None and not isinstance(dairy_items, list):
                raise ValueError("dairy_items must be a list of strings.")
            search_model = str(arguments.get("search_model") or "").strip() or None
            if search_model is not None and not is_valid_search_model(search_model):
                raise ValueError(
                    f"search_model must be one of: {', '.join(ALLOWED_SEARCH_MODELS)}."
                )
            preferences = self.store.set_user_preferences(
                user_id,
                mode=str(arguments.get("mode") or "").strip() or None,
                meal_window_start=str(arguments.get("meal_window_start") or "").strip() or None,
                meal_window_end=str(arguments.get("meal_window_end") or "").strip() or None,
                late_night_window_start=str(arguments.get("late_night_window_start") or "").strip() or None,
                late_night_window_end=str(arguments.get("late_night_window_end") or "").strip() or None,
                max_prep_minutes=self._coerce_int(arguments.get("max_prep_minutes")) if arguments.get("max_prep_minutes") not in (None, "") else None,
                notification_frequency=str(arguments.get("notification_frequency") or "").strip() or None,
                dietary_preferences=[str(value) for value in dietary_preferences] if isinstance(dietary_preferences, list) else None,
                essentials_items=[str(value) for value in essentials_items] if isinstance(essentials_items, list) else None,
                dairy_items=[str(value) for value in dairy_items] if isinstance(dairy_items, list) else None,
                search_model=search_model,
            )
            return {"tool_name": tool_name, **preferences.model_dump(mode="json")}

        if tool_name == "get_user_state":
            user_id = str(arguments.get("user_id") or "").strip()
            if not user_id:
                raise ValueError("user_id is required.")
            return {
                "tool_name": tool_name,
                "user_id": user_id,
                "temporary_states": [state.model_dump(mode="json") for state in self.store.temporary_states(user_id)],
            }

        if tool_name == "set_user_state":
            user_id = str(arguments.get("user_id") or "").strip()
            state_name = str(arguments.get("state") or "").strip()
            if not user_id or not state_name:
                raise ValueError("user_id and state are required.")
            duration_hours = self._coerce_int(arguments.get("duration_hours"), default=24)
            state = self.store.set_temporary_state(
                user_id,
                state=state_name,
                value=str(arguments.get("value") or "active"),
                expires_at=utc_now() + timedelta(hours=duration_hours),
                source="mcp_tool",
                note=str(arguments.get("note") or ""),
            )
            return {"tool_name": tool_name, "state": state.model_dump(mode="json")}

        if tool_name == "record_decision_feedback":
            user_id = str(arguments.get("user_id") or "").strip()
            status = str(arguments.get("status") or "").strip()
            if not user_id or not status:
                raise ValueError("user_id and status are required.")
            result = self.decision_engine.record_feedback(
                user_id=user_id,
                status=status,
                intervention_id=str(arguments.get("intervention_id") or "").strip() or None,
                thread_key=str(arguments.get("thread_key") or "").strip() or None,
                detail=str(arguments.get("detail") or ""),
            )
            return {"tool_name": tool_name, "feedback": result.model_dump(mode="json") if result else None}

        if tool_name == "update_user_status":
            user_id = str(arguments.get("user_id") or "").strip()
            status = str(arguments.get("status") or "").strip()
            if not user_id or not status:
                raise ValueError("user_id and status are required.")
            memory = self.conversation_manager.update_current_status(user_id, status)
            return {"tool_name": tool_name, "user_id": user_id, "current_status": memory.current_status}

        if tool_name == "confirm_pending_action":
            return self.confirm_pending_action(
                str(arguments.get("confirmation_id") or "").strip(),
                user_id=str(arguments.get("user_id") or "").strip() or None,
            )

        if tool_name == "cancel_pending_action":
            return self.cancel_pending_action(
                str(arguments.get("confirmation_id") or "").strip(),
                user_id=str(arguments.get("user_id") or "").strip() or None,
            )

        raise LookupError(f"MCP tool {tool_name} was not found.")

    def _request_confirmation(self, tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
        user_id = str(arguments.get("user_id") or "unknown").strip() or "unknown"
        return {
            "tool_name": tool_name,
            **self.confirmation_manager.request_confirmation(
                user_id=user_id,
                action=tool_name,
                arguments=arguments,
                summary=self._confirmation_summary(tool_name, arguments),
            ),
        }

    def _resolve_confirmation_id(self, confirmation_id: str, user_id: str | None) -> str:
        target_id = confirmation_id.strip()
        if target_id:
            return target_id
        pending = self.confirmation_manager.pending_actions(user_id)
        if len(pending) != 1:
            raise ValueError("confirmation_id is required when there is not exactly one pending action.")
        return str(pending[0]["confirmation_id"])

    @staticmethod
    def _confirmation_summary(tool_name: str, arguments: dict[str, object]) -> str:
        if tool_name == "clear_inventory":
            return "clear all inventory items"
        if tool_name == "remove_inventory_item":
            return f"remove inventory item {arguments.get('name') or 'unknown'}"
        if tool_name == "order_groceries_for_recipe":
            return f"place a grocery order for recipe {arguments.get('recipe_id') or 'unknown'}"
        if tool_name == "order_staple_restock":
            return "place a grocery order for staple restock"
        return tool_name

    def _build_registry(self) -> list[dict[str, object]]:
        tools = [
            ("get_memory_snapshot", "Read the current shared FridgeMate memory snapshot.", {"user_id": "string"}, "read", "Need a compact operational memory summary.", "Need full inventory details; use get_inventory.", "ContextStore"),
            ("get_runtime_state", "Read current runtime metadata and simulated sensor state.", {"user_id": "string", "last_user_message": "string"}, "read", "Need situational state, connectivity, or pending actions.", "Need authoritative inventory quantities; use get_inventory.", "RuntimeStateAggregator"),
            ("diagnostics_report", "Return health status, issues, and recommended actions.", {"user_id": "string", "last_user_message": "string"}, "read", "Need to explain uncertainty or check system health.", "Only answering a simple recipe request where diagnostics are irrelevant.", "DiagnosticsEngine"),
            ("run_heartbeat_check", "Run the periodic self-check immediately.", {}, "read", "Need an immediate self-check or alert summary.", "Need only current inventory; use get_inventory.", "HeartbeatService"),
            ("get_inventory", "Read the current inventory, expiry, and low-stock state.", {}, "read", "Asked about stock, fridge contents, expiry, or low items.", "Changing inventory; use add/remove/clear tools.", "ContextStore inventory"),
            ("get_inventory_batches", "Read batch-level fridge stock including purchase dates and expiry.", {}, "read", "Need detailed fridge batch history or purchase timing.", "Only need aggregate stock totals.", "SQL inventory batches"),
            ("get_expiring_items", "Return inventory items expiring within a requested number of days.", {"days": "integer"}, "read", "Asked what must be used soon.", "Asked for all stock; use get_inventory.", "InventoryAgent"),
            ("compare_current_inventory_to_expected_inventory", "Compare current stock against expected thresholds and simulated mismatch signals.", {"user_id": "string", "last_user_message": "string"}, "read", "Asked about unusual, missing, or mismatched items.", "Need a simple list of items only; use get_inventory.", "Inventory thresholds + runtime state"),
            ("add_inventory_item", "Add or increment an inventory item in shared memory.", {"name": "string", "quantity": "number", "unit": "string", "purchased_at": "string", "expires_on": "string", "category": "string", "min_desired_quantity": "number"}, "write", "User asks to add stock or correct a non-destructive count upward.", "Destructive updates.", "InventoryAgent"),
            ("remove_inventory_item", "Remove one inventory item by name after confirmation.", {"name": "string", "user_id": "string"}, "destructive", "User asks to remove/delete one item.", "User only asks if the item exists.", "InventoryAgent"),
            ("clear_inventory", "Delete all inventory items after confirmation.", {"user_id": "string"}, "destructive", "User explicitly asks to clear/delete all inventory.", "Any ambiguous inventory request.", "InventoryAgent"),
            ("add_to_shopping_list", "Add one item to the pending shopping list without checkout.", {"name": "string", "quantity": "number", "unit": "string", "reason": "string"}, "write", "User asks to remember an item to buy.", "User asks to place an order.", "ContextStore pending_grocery_list"),
            ("list_recipes", "Read the recipe catalog.", {}, "read", "Asked for available recipes.", "Need online discovery.", "RecipeAgent"),
            ("search_recipes_online", "Search the web for recipes and return importable recipe candidates. Prefer this when the user wants to review options before saving one. Include user_id when available so the user's search_model applies.", {"query": "string", "max_results": "integer", "user_id": "string"}, "read", "Asked to find new recipes online and review candidates first.", "Asked only for local catalog or wants immediate top-result import.", "RecipeDiscoveryService"),
            ("import_recipe", "Add a recipe to the shared recipe catalog.", {"recipe": "object"}, "write", "User asks to save a selected recipe.", "User only wants suggestions.", "RecipeAgent"),
            ("search_and_import_recipe", "Search recipes online and immediately import one result into the catalog. For conversational online recipe requests, prefer selection_index 0 as the default top-result import. Include user_id when available so the user's search_model applies.", {"query": "string", "selection_index": "integer", "max_results": "integer", "user_id": "string"}, "write", "User asks to find an online or new recipe in one step.", "User wants to review candidates first.", "RecipeDiscoveryService + RecipeAgent"),
            ("get_utilities", "Read current fridge water and ice levels.", {}, "read", "Asked about water, ice, or fridge utilities.", "Need diagnostics across all components.", "UtilityAgent"),
            ("update_utilities", "Update fridge water and or ice levels.", {"water_level_percent": "integer", "ice_level_percent": "integer"}, "write", "User gives a new water or ice level.", "User asks for current level only.", "UtilityAgent"),
            ("get_nutrition_summary", "Read the current nutrition summary.", {}, "read", "Asked about nutrition, calories, protein, or meal patterns.", "Asked only about stock.", "NutritionAgent"),
            ("get_behaviour_summary", "Read the learned behaviour and preference summary.", {}, "read", "Need learned preferences or restock prediction.", "Need authoritative user.md profile.", "BehaviourAgent"),
            ("order_groceries_for_recipe", "Create a grocery order for missing ingredients in a recipe after confirmation.", {"recipe_id": "string", "user_id": "string"}, "destructive", "User asks to buy/order ingredients for a recipe.", "User asks to preview missing items.", "GroceryAgent mock provider"),
            ("order_staple_restock", "Create a grocery order for staple restock candidates after confirmation.", {"user_id": "string"}, "destructive", "User asks to order staple restock.", "User only wants a shopping list.", "GroceryAgent mock provider"),
            ("get_session_status", "Read the current Telegram user's session status.", {"user_id": "string"}, "read", "Need session metadata or current status.", "Need fridge inventory.", "ConversationManager"),
            ("get_heartbeat_status", "Read a user's heartbeat preference and next check.", {"user_id": "string"}, "read", "Need the user's dinner heartbeat settings.", "Need to change settings.", "HeartbeatService"),
            ("set_heartbeat_status", "Update a user's heartbeat enabled state, dinner time, or interval.", {"user_id": "string", "enabled": "string", "dinner_time": "string", "interval_minutes": "integer"}, "write", "User wants to change heartbeat behavior.", "Need only current status.", "HeartbeatService"),
            ("set_heartbeat_interval", "Update only a user's heartbeat interval in minutes.", {"user_id": "string", "interval_minutes": "integer"}, "write", "User explicitly changes how often heartbeat checks run.", "Need to change enabled state or dinner time too; use set_heartbeat_status.", "HeartbeatService"),
            ("gateway_get_access", "Read the repo gateway policy and effective access for a path. Access requirement: none.", {"path": "string"}, "read", "Need to know whether a file or directory is readable or writable before using gateway tools.", "Already know the exact access level and do not need policy context.", "LLMGatewayService"),
            ("fs_list_dir", "List directory entries under a readable repo path. Access requirement: read_only or read_write.", {"path": "string"}, "read", "Need to traverse the repo and inspect directories.", "Need file contents rather than directory entries.", "LLMGatewayService"),
            ("fs_tree", "Return a bounded recursive tree for a readable repo path. Access requirement: read_only or read_write.", {"path": "string", "max_depth": "integer"}, "read", "Need a compact recursive view of part of the repo.", "Need a single directory listing only.", "LLMGatewayService"),
            ("fs_read_text", "Read a UTF-8 text file from a readable repo path. Access requirement: read_only or read_write.", {"path": "string"}, "read", "Need the contents of a source file or markdown file.", "Need to modify the file.", "LLMGatewayService"),
            ("fs_search_text", "Search text within readable repo files. Access requirement: read_only or read_write.", {"query": "string", "path": "string", "max_results": "integer"}, "read", "Need to find symbols, strings, or references across files.", "Already know the exact file to read.", "LLMGatewayService"),
            ("fs_write_text", "Create or replace a UTF-8 text file in a read_write repo path. Access requirement: read_write.", {"path": "string", "content": "string"}, "write", "Need to create or fully rewrite a writable file.", "Need to append only.", "LLMGatewayService"),
            ("fs_append_text", "Append UTF-8 text to a file in a read_write repo path. Access requirement: read_write.", {"path": "string", "content": "string"}, "write", "Need to add content without replacing the existing file.", "Need to overwrite the file.", "LLMGatewayService"),
            ("terminal_exec", "Run a guarded shell command inside the repo with explicit read_only or read_write mode. Access requirement: cwd must be readable, and read_write mode requires a read_write cwd.", {"command": "string", "cwd": "string", "mode": "string"}, "write", "Need CLI-style traversal or inspection similar to a repo shell.", "A file tool already covers the task more safely.", "LLMGatewayService"),
            ("get_decision_state", "Read the public steering state: preferences, temporary states, session status, and recent interventions.", {"user_id": "string"}, "read", "Need current user steering state before deciding whether to intervene.", "Need hidden learned profile internals.", "DecisionEngine public state"),
            ("run_decision", "Run the adaptive decision engine once for a user without sending a Telegram message.", {"user_id": "string", "force": "string"}, "read", "Need to preview the current best intervention.", "Need to actually notify a user.", "DecisionEngine"),
            ("get_user_preferences", "Read the user-editable preference layer, including the per-user recipe search model.", {"user_id": "string"}, "read", "Need explicit preferences like mode, windows, effort, dietary settings, or recipe search model.", "Need learned hidden profile values.", "UserPreferences"),
            ("set_user_preferences", f"Update the user-editable preference layer, including search_model. Allowed search_model values: {', '.join(ALLOWED_SEARCH_MODELS)}.", {"user_id": "string", "mode": "string", "meal_window_start": "string", "meal_window_end": "string", "late_night_window_start": "string", "late_night_window_end": "string", "max_prep_minutes": "integer", "notification_frequency": "string", "dietary_preferences": "array", "essentials_items": "array", "dairy_items": "array", "search_model": "string"}, "write", "User explicitly changes preference settings.", "Need temporary one-off context; use set_user_state.", "UserPreferences"),
            ("get_user_state", "Read active temporary state overrides for a user.", {"user_id": "string"}, "read", "Need the current temporary context like tired, commuting, or not_home.", "Need persistent preferences.", "TemporaryStateOverride"),
            ("set_user_state", "Set a temporary state override for a user.", {"user_id": "string", "state": "string", "duration_hours": "integer", "value": "string", "note": "string"}, "write", "User gives short-term context like being tired or not home.", "Need durable preferences.", "TemporaryStateOverride"),
            ("record_decision_feedback", "Record feedback for an assistant intervention.", {"user_id": "string", "intervention_id": "string", "thread_key": "string", "status": "string", "detail": "string"}, "write", "Need to learn from a user's response to a nudge.", "No intervention exists to update.", "DecisionEngine"),
            ("update_user_status", "Update the current Telegram user's status note in shared memory.", {"user_id": "string", "status": "string"}, "write", "User tells FridgeMate a current status note.", "Changing durable profile preferences.", "ConversationManager"),
            ("confirm_pending_action", "Confirm and execute one pending protected action.", {"confirmation_id": "string", "user_id": "string"}, "destructive", "User confirms a pending action.", "There is no pending action.", "ConfirmationManager"),
            ("cancel_pending_action", "Cancel one pending protected action.", {"confirmation_id": "string", "user_id": "string"}, "write", "User cancels a pending action.", "User is confirming.", "ConfirmationManager"),
        ]
        return [
            self._tool(name, description, arguments, self._parameters(name, arguments), policy, use, avoid, source)
            for name, description, arguments, policy, use, avoid, source in tools
        ]

    @staticmethod
    def _tool(
        name: str,
        description: str,
        arguments: dict[str, str],
        parameters: dict[str, object],
        policy: str,
        when_to_use: str,
        when_not_to_use: str,
        authoritative_source: str,
    ) -> dict[str, object]:
        return {
            "name": name,
            "description": description,
            "arguments": arguments,
            "parameters": parameters,
            "policy": policy,
            "when_to_use": when_to_use,
            "when_not_to_use": when_not_to_use,
            "authoritative_source": authoritative_source,
        }

    @classmethod
    def _parameters(cls, name: str, arguments: dict[str, str]) -> dict[str, object]:
        if name == "import_recipe":
            return cls._recipe_schema()
        if name == "set_user_preferences":
            return cls._user_preferences_schema()
        required = {
            "add_inventory_item": ["name", "quantity"],
            "get_heartbeat_status": ["user_id"],
            "set_heartbeat_status": ["user_id"],
            "set_heartbeat_interval": ["user_id", "interval_minutes"],
            "fs_list_dir": ["path"],
            "fs_tree": ["path"],
            "fs_read_text": ["path"],
            "fs_search_text": ["query"],
            "fs_write_text": ["path", "content"],
            "fs_append_text": ["path", "content"],
            "terminal_exec": ["command"],
            "get_decision_state": ["user_id"],
            "run_decision": ["user_id"],
            "get_user_preferences": ["user_id"],
            "set_user_preferences": ["user_id"],
            "get_user_state": ["user_id"],
            "set_user_state": ["user_id", "state"],
            "record_decision_feedback": ["user_id", "status"],
            "remove_inventory_item": ["name"],
            "add_to_shopping_list": ["name"],
            "search_recipes_online": ["query"],
            "search_and_import_recipe": ["query"],
            "order_groceries_for_recipe": ["recipe_id"],
            "get_session_status": ["user_id"],
            "update_user_status": ["user_id", "status"],
        }.get(name, [])
        type_map = {
            "string": {"type": "string"},
            "number": {"type": "number"},
            "integer": {"type": "integer"},
            "object": {"type": "object", "additionalProperties": True},
            "array": {"type": "array", "items": {"type": "string"}},
        }
        return {
            "type": "object",
            "properties": {key: type_map.get(value, {"type": "string"}) for key, value in arguments.items()},
            "required": required,
        }

    @staticmethod
    def _user_preferences_schema() -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "mode": {"type": "string"},
                "meal_window_start": {"type": "string"},
                "meal_window_end": {"type": "string"},
                "late_night_window_start": {"type": "string"},
                "late_night_window_end": {"type": "string"},
                "max_prep_minutes": {"type": "integer"},
                "notification_frequency": {"type": "string"},
                "dietary_preferences": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "essentials_items": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "dairy_items": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "search_model": {
                    "type": "string",
                    "enum": list(ALLOWED_SEARCH_MODELS),
                    "default": DEFAULT_SEARCH_MODEL,
                },
            },
            "required": ["user_id"],
        }

    @staticmethod
    def _recipe_schema() -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "recipe": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "ingredients": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "quantity": {"type": "number"},
                                    "unit": {"type": "string"},
                                    "optional": {"type": "boolean"},
                                },
                                "required": ["name", "quantity"],
                            },
                        },
                        "instructions": {"type": "array", "items": {"type": "string"}},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "calories": {"type": "integer"},
                        "protein_g": {"type": "integer"},
                        "cuisine": {"type": "string"},
                        "source_url": {"type": "string"},
                        "source_title": {"type": "string"},
                    },
                    "required": ["name", "description", "ingredients", "instructions"],
                }
            },
            "required": ["recipe"],
        }

    @staticmethod
    def _optional_date(raw: object):
        if raw in (None, "", "null"):
            return None
        return date.fromisoformat(str(raw))

    @staticmethod
    def _optional_datetime(raw: object):
        if raw in (None, "", "null"):
            return None
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return value

    @staticmethod
    def _coerce_int(raw: object, *, default: int | None = None) -> int:
        if raw in (None, "", "null"):
            if default is None:
                raise ValueError("Expected an integer value.")
            return default
        if isinstance(raw, bool):
            return int(raw)
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float):
            return int(raw)
        if isinstance(raw, str):
            return int(raw.strip())
        raise ValueError("Expected an integer-compatible value.")

    @staticmethod
    def _coerce_float(raw: object, *, default: float | None = None) -> float:
        if raw in (None, "", "null"):
            if default is None:
                raise ValueError("Expected a numeric value.")
            return default
        if isinstance(raw, bool):
            return float(raw)
        if isinstance(raw, int | float):
            return float(raw)
        if isinstance(raw, str):
            return float(raw.strip())
        raise ValueError("Expected a numeric-compatible value.")
