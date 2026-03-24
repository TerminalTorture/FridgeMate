from __future__ import annotations

from typing import Any

from app.agents.behaviour import BehaviourAgent
from app.agents.grocery import GroceryAgent
from app.agents.inventory import InventoryAgent
from app.agents.nutrition import NutritionAgent
from app.agents.recipe import RecipeAgent
from app.agents.utility import UtilityAgent
from app.core.conversation_manager import ConversationManager
from app.core.context_store import ContextStore
from app.core.integration_debug import IntegrationDebugLog
from app.core.recipe_discovery_service import RecipeDiscoveryService
from app.models.api import RecipeInput
from app.models.domain import InventoryItem


class MCPToolService:
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
        self.debug_log = IntegrationDebugLog()

    def list_tools(self) -> list[dict[str, object]]:
        return [
            {
                "name": "get_memory_snapshot",
                "description": "Read the current shared MCP Fridge memory snapshot.",
                "arguments": {"user_id": "string"},
            },
            {
                "name": "get_inventory",
                "description": "Read the current inventory and expiry state.",
                "arguments": {},
            },
            {
                "name": "add_inventory_item",
                "description": "Add or increment an inventory item in shared memory.",
                "arguments": {
                    "name": "string",
                    "quantity": "number",
                    "unit": "string",
                    "expires_on": "string",
                    "category": "string",
                    "min_desired_quantity": "number",
                },
            },
            {
                "name": "remove_inventory_item",
                "description": "Remove one inventory item by name.",
                "arguments": {"name": "string"},
            },
            {
                "name": "clear_inventory",
                "description": "Delete all inventory items from shared memory.",
                "arguments": {},
            },
            {
                "name": "list_recipes",
                "description": "Read the recipe catalog.",
                "arguments": {},
            },
            {
                "name": "search_recipes_online",
                "description": "Search the web for recipes and return importable recipe candidates.",
                "arguments": {"query": "string", "max_results": "integer"},
            },
            {
                "name": "import_recipe",
                "description": "Add a recipe to the shared MCP Fridge recipe catalog.",
                "arguments": {"recipe": "RecipeInput"},
            },
            {
                "name": "search_and_import_recipe",
                "description": "Search recipes online and immediately import one result into the catalog.",
                "arguments": {"query": "string", "selection_index": "integer", "max_results": "integer"},
            },
            {
                "name": "get_utilities",
                "description": "Read current fridge water and ice levels.",
                "arguments": {},
            },
            {
                "name": "update_utilities",
                "description": "Update fridge water and or ice levels.",
                "arguments": {"water_level_percent": "integer", "ice_level_percent": "integer"},
            },
            {
                "name": "get_nutrition_summary",
                "description": "Read the current nutrition summary.",
                "arguments": {},
            },
            {
                "name": "get_behaviour_summary",
                "description": "Read the learned behaviour and preference summary.",
                "arguments": {},
            },
            {
                "name": "order_groceries_for_recipe",
                "description": "Create a grocery order for missing ingredients in a recipe.",
                "arguments": {"recipe_id": "string"},
            },
            {
                "name": "order_staple_restock",
                "description": "Create a grocery order for staple restock candidates.",
                "arguments": {},
            },
            {
                "name": "get_session_status",
                "description": "Read the current Telegram user's session status.",
                "arguments": {"user_id": "string"},
            },
            {
                "name": "update_user_status",
                "description": "Update the current Telegram user's status note in shared memory.",
                "arguments": {"user_id": "string", "status": "string"},
            },
        ]

    def responses_api_tools(self) -> list[dict[str, object]]:
        return [
            self._function_tool(
                "get_memory_snapshot",
                "Read the current shared MCP Fridge memory snapshot.",
                {"type": "object", "properties": {"user_id": {"type": "string"}}, "required": []},
            ),
            self._function_tool(
                "get_inventory",
                "Read the current inventory and expiry state.",
                {"type": "object", "properties": {}, "required": []},
            ),
            self._function_tool(
                "add_inventory_item",
                "Add or increment an inventory item in shared memory.",
                {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "quantity": {"type": "number"},
                        "unit": {"type": "string"},
                        "expires_on": {"type": "string"},
                        "category": {"type": "string"},
                        "min_desired_quantity": {"type": "number"},
                    },
                    "required": ["name", "quantity"],
                },
            ),
            self._function_tool(
                "remove_inventory_item",
                "Remove one inventory item by name.",
                {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            ),
            self._function_tool(
                "clear_inventory",
                "Delete all inventory items from shared memory.",
                {"type": "object", "properties": {}, "required": []},
            ),
            self._function_tool(
                "list_recipes",
                "Read the recipe catalog.",
                {"type": "object", "properties": {}, "required": []},
            ),
            self._function_tool(
                "search_recipes_online",
                "Search the web for recipes and return importable recipe candidates.",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            ),
            self._function_tool(
                "import_recipe",
                "Add a recipe to the shared MCP Fridge recipe catalog.",
                {
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
                },
            ),
            self._function_tool(
                "search_and_import_recipe",
                "Search recipes online and immediately import one result into the catalog.",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "selection_index": {"type": "integer"},
                        "max_results": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            ),
            self._function_tool(
                "get_utilities",
                "Read current fridge water and ice levels.",
                {"type": "object", "properties": {}, "required": []},
            ),
            self._function_tool(
                "update_utilities",
                "Update fridge water and or ice levels.",
                {
                    "type": "object",
                    "properties": {
                        "water_level_percent": {"type": "integer"},
                        "ice_level_percent": {"type": "integer"},
                    },
                    "required": [],
                },
            ),
            self._function_tool(
                "get_nutrition_summary",
                "Read the current nutrition summary.",
                {"type": "object", "properties": {}, "required": []},
            ),
            self._function_tool(
                "get_behaviour_summary",
                "Read the learned behaviour and preference summary.",
                {"type": "object", "properties": {}, "required": []},
            ),
            self._function_tool(
                "order_groceries_for_recipe",
                "Create a grocery order for missing ingredients in a recipe.",
                {
                    "type": "object",
                    "properties": {"recipe_id": {"type": "string"}},
                    "required": ["recipe_id"],
                },
            ),
            self._function_tool(
                "order_staple_restock",
                "Create a grocery order for staple restock candidates.",
                {"type": "object", "properties": {}, "required": []},
            ),
            self._function_tool(
                "get_session_status",
                "Read the current Telegram user's session status.",
                {
                    "type": "object",
                    "properties": {"user_id": {"type": "string"}},
                    "required": ["user_id"],
                },
            ),
            self._function_tool(
                "update_user_status",
                "Update the current Telegram user's status note in shared memory.",
                {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string"},
                        "status": {"type": "string"},
                    },
                    "required": ["user_id", "status"],
                },
            ),
        ]

    def call_tool(self, tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
        result = self._call_tool_impl(tool_name, arguments)
        self.debug_log.record(
            service="mcp",
            direction="internal",
            status="success",
            summary=f"MCP tool executed: {tool_name}.",
            metadata={"tool_name": tool_name},
        )
        return result

    def debug_snapshot(self) -> dict[str, object]:
        return {
            "tool_count": len(self.list_tools()),
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
                "recipe_count": len(snapshot.recipe_catalog),
                "shopping_list_count": len(snapshot.pending_grocery_list),
                "grocery_order_count": len(snapshot.grocery_orders),
                "utilities": snapshot.utilities.model_dump(),
                "recent_events": [event.model_dump(mode="json") for event in snapshot.recent_events[:10]],
                "session_status": (
                    self.conversation_manager.session_status(user_id)
                    if user_id
                    else None
                ),
            }

        if tool_name == "get_inventory":
            return {
                "tool_name": tool_name,
                "items": [item.model_dump(mode="json") for item in self.inventory_agent.get_inventory()],
                "expiring_soon": [
                    item.model_dump(mode="json") for item in self.inventory_agent.expiring_soon(days=3)
                ],
                "low_stock": [
                    item.model_dump(mode="json") for item in self.inventory_agent.low_stock_items()
                ],
            }

        if tool_name == "add_inventory_item":
            item = InventoryItem(
                name=str(arguments.get("name") or "").strip(),
                quantity=float(arguments.get("quantity") or 0),
                unit=str(arguments.get("unit") or "unit"),
                expires_on=self._optional_date(arguments.get("expires_on")),
                category=str(arguments.get("category") or "general"),
                min_desired_quantity=float(arguments.get("min_desired_quantity") or 1.0),
            )
            if not item.name or item.quantity <= 0:
                raise ValueError("name and a positive quantity are required.")
            stored = self.inventory_agent.add_or_refresh_item(item)
            return {"tool_name": tool_name, "item": stored.model_dump(mode="json")}

        if tool_name == "remove_inventory_item":
            name = str(arguments.get("name") or "").strip()
            return {
                "tool_name": tool_name,
                **self.inventory_agent.remove_item(name),
            }

        if tool_name == "clear_inventory":
            return {"tool_name": tool_name, **self.inventory_agent.clear_inventory()}

        if tool_name == "list_recipes":
            return {
                "tool_name": tool_name,
                "recipes": [recipe.model_dump(mode="json") for recipe in self.recipe_agent.list_recipes()],
            }

        if tool_name == "search_recipes_online":
            query = str(arguments.get("query") or "").strip()
            max_results = int(arguments.get("max_results") or 3)
            recipes = self.recipe_discovery_service.search_online_recipes(
                query=query,
                max_results=max_results,
            )
            return {
                "tool_name": tool_name,
                "query": query,
                "results": [recipe.model_dump(mode="json") for recipe in recipes],
            }

        if tool_name == "import_recipe":
            recipe_payload = arguments.get("recipe")
            if not isinstance(recipe_payload, dict):
                raise ValueError("recipe must be an object.")
            recipe = RecipeInput(**recipe_payload).to_domain()
            stored_recipe = self.recipe_agent.add_recipe(recipe)
            return {
                "tool_name": tool_name,
                "recipe": stored_recipe.model_dump(mode="json"),
            }

        if tool_name == "search_and_import_recipe":
            query = str(arguments.get("query") or "").strip()
            max_results = int(arguments.get("max_results") or 3)
            selection_index = int(arguments.get("selection_index") or 0)
            recipes = self.recipe_discovery_service.search_online_recipes(
                query=query,
                max_results=max_results,
            )
            if not recipes:
                raise ValueError("No recipes were returned from online search.")
            if selection_index < 0 or selection_index >= len(recipes):
                raise ValueError("selection_index is out of range.")
            stored_recipe = self.recipe_agent.add_recipe(recipes[selection_index])
            return {
                "tool_name": tool_name,
                "selected_index": selection_index,
                "recipe": stored_recipe.model_dump(mode="json"),
            }

        if tool_name == "get_utilities":
            return {"tool_name": tool_name, **self.utility_agent.get_status()}

        if tool_name == "update_utilities":
            water_level = arguments.get("water_level_percent")
            ice_level = arguments.get("ice_level_percent")
            return {
                "tool_name": tool_name,
                **self.utility_agent.update_levels(
                    water_level_percent=int(water_level) if water_level is not None else None,
                    ice_level_percent=int(ice_level) if ice_level is not None else None,
                ),
            }

        if tool_name == "get_nutrition_summary":
            return {"tool_name": tool_name, **self.nutrition_agent.get_summary()}

        if tool_name == "get_behaviour_summary":
            return {"tool_name": tool_name, **self.behaviour_agent.get_summary()}

        if tool_name == "order_groceries_for_recipe":
            recipe_id = str(arguments.get("recipe_id") or "").strip()
            return {"tool_name": tool_name, **self.grocery_agent.order_missing_for_recipe(recipe_id)}

        if tool_name == "order_staple_restock":
            return {"tool_name": tool_name, **self.grocery_agent.order_staple_restock()}

        if tool_name == "get_session_status":
            user_id = str(arguments.get("user_id") or "").strip()
            if not user_id:
                raise ValueError("user_id is required.")
            return {
                "tool_name": tool_name,
                **self.conversation_manager.session_status(user_id),
            }

        if tool_name == "update_user_status":
            user_id = str(arguments.get("user_id") or "").strip()
            status = str(arguments.get("status") or "").strip()
            if not user_id or not status:
                raise ValueError("user_id and status are required.")
            memory = self.conversation_manager.update_current_status(user_id, status)
            return {
                "tool_name": tool_name,
                "user_id": user_id,
                "current_status": memory.current_status,
            }

        raise LookupError(f"MCP tool {tool_name} was not found.")

    @staticmethod
    def _function_tool(name: str, description: str, parameters: dict[str, object]) -> dict[str, object]:
        return {
            "type": "function",
            "name": name,
            "description": description,
            "parameters": parameters,
        }

    @staticmethod
    def _optional_date(raw: object):
        if raw in (None, "", "null"):
            return None
        from datetime import date

        return date.fromisoformat(str(raw))
