from __future__ import annotations

from app.agents.behaviour import BehaviourAgent
from app.agents.grocery import GroceryAgent
from app.agents.inventory import InventoryAgent
from app.agents.nutrition import NutritionAgent
from app.agents.recipe import RecipeAgent
from app.agents.utility import UtilityAgent


class MCPFridgeOrchestrator:
    def __init__(
        self,
        *,
        inventory_agent: InventoryAgent,
        recipe_agent: RecipeAgent,
        grocery_agent: GroceryAgent,
        nutrition_agent: NutritionAgent,
        behaviour_agent: BehaviourAgent,
        utility_agent: UtilityAgent,
    ) -> None:
        self.inventory_agent = inventory_agent
        self.recipe_agent = recipe_agent
        self.grocery_agent = grocery_agent
        self.nutrition_agent = nutrition_agent
        self.behaviour_agent = behaviour_agent
        self.utility_agent = utility_agent

    def cook_recipe(self, recipe_id: str) -> dict[str, object]:
        recipe = self.recipe_agent.get_recipe(recipe_id)
        self.behaviour_agent.record_recipe_interest(recipe)
        inventory_result = self.inventory_agent.consume_for_recipe(recipe)

        if not inventory_result["success"]:
            return {
                "status": "blocked",
                "recipe": recipe.name,
                "missing_items": inventory_result["missing_items"],
                "suggested_order": self.grocery_agent.preview_recipe_gap(recipe.id),
            }

        nutrition_summary = self.nutrition_agent.record_meal(recipe)
        behaviour_summary = self.behaviour_agent.record_meal(recipe)
        utility_status = self.utility_agent.register_kitchen_activity(recipe)

        return {
            "status": "cooked",
            "recipe": recipe.name,
            "inventory": inventory_result["remaining_inventory"],
            "nutrition": nutrition_summary,
            "behaviour": behaviour_summary,
            "utilities": utility_status,
        }

    def handle_telegram_message(self, user_id: str, message: str) -> dict[str, object]:
        text = message.strip().lower()

        if "what can i cook" in text or "what should i cook" in text:
            self.behaviour_agent.record_command("recipe_query")
            suggestions = self.recipe_agent.suggest_recipes(limit=3)
            lines = ["Top recipe ideas right now:"]
            for suggestion in suggestions:
                if suggestion.can_make_now:
                    lines.append(f"- {suggestion.name}: ready now.")
                else:
                    missing = ", ".join(
                        f"{item.name} ({item.quantity:g} {item.unit})"
                        for item in suggestion.missing_items
                    )
                    lines.append(f"- {suggestion.name}: missing {missing}.")
            return {
                "user_id": user_id,
                "intent": "recipe_query",
                "reply": "\n".join(lines),
                "data": {"suggestions": suggestions},
            }

        if "inventory" in text or "fridge" in text:
            self.behaviour_agent.record_command("inventory_check")
            inventory = self.inventory_agent.get_inventory()
            expiring = self.inventory_agent.expiring_soon(days=3)
            lines = ["Current inventory:"]
            for item in inventory:
                expiry_text = f", expires {item.expires_on.isoformat()}" if item.expires_on else ""
                lines.append(f"- {item.name}: {item.quantity:g} {item.unit}{expiry_text}")
            if expiring:
                lines.append(
                    "Expiring soon: "
                    + ", ".join(item.name for item in expiring)
                    + "."
                )
            return {
                "user_id": user_id,
                "intent": "inventory_check",
                "reply": "\n".join(lines),
                "data": {"inventory": inventory, "expiring_soon": expiring},
            }

        if "order groceries" in text or "buy groceries" in text:
            self.behaviour_agent.record_command("grocery_order")
            recipe = self.recipe_agent.match_recipe_from_text(text)
            result = (
                self.grocery_agent.order_missing_for_recipe(recipe.id)
                if recipe
                else self.grocery_agent.order_staple_restock()
            )

            if result["order"] is None:
                reply = result["message"]
            else:
                order = result["order"]
                items = ", ".join(
                    f"{item.name} ({item.quantity:g} {item.unit})"
                    for item in result["items"]
                )
                reply = (
                    f"Placed order {order.id} with {order.vendor}. "
                    f"ETA {order.eta_minutes} minutes. Items: {items}."
                )

            return {
                "user_id": user_id,
                "intent": "grocery_order",
                "reply": reply,
                "data": result,
            }

        if text.startswith("cook "):
            self.behaviour_agent.record_command("cook_recipe")
            recipe = self.recipe_agent.match_recipe_from_text(text)
            if recipe is None:
                return {
                    "user_id": user_id,
                    "intent": "cook_recipe",
                    "reply": "I could not match that recipe name.",
                    "data": {},
                }

            result = self.cook_recipe(recipe.id)
            if result["status"] == "blocked":
                missing = ", ".join(
                    f"{item.name} ({item.quantity:g} {item.unit})"
                    for item in result["missing_items"]
                )
                reply = f"Cannot cook {recipe.name} yet. Missing: {missing}."
            else:
                reply = f"{recipe.name} cooked successfully. Inventory and nutrition context updated."

            return {
                "user_id": user_id,
                "intent": "cook_recipe",
                "reply": reply,
                "data": result,
            }

        if "water" in text or "ice" in text or "utilities" in text:
            self.behaviour_agent.record_command("utility_check")
            status = self.utility_agent.get_status()
            utilities = status["utilities"]
            alerts = status["alerts"]
            reply = (
                f"Water: {utilities.water_level_percent}%, "
                f"Ice: {utilities.ice_level_percent}%."
            )
            if alerts:
                reply += " Alerts: " + ", ".join(alerts)
            return {
                "user_id": user_id,
                "intent": "utility_check",
                "reply": reply,
                "data": status,
            }

        self.behaviour_agent.record_command("unknown")
        return {
            "user_id": user_id,
            "intent": "unknown",
            "reply": (
                "Try: 'what can I cook?', 'check inventory', "
                "'order groceries', or 'cook veggie omelette'."
            ),
            "data": {},
        }

