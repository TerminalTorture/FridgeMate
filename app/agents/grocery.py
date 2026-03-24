from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from app.agents.behaviour import BehaviourAgent
from app.agents.inventory import InventoryAgent
from app.agents.recipe import RecipeAgent
from app.core.context_store import ContextStore
from app.models.domain import GroceryLine, GroceryOrder


class GroceryAgent:
    def __init__(
        self,
        store: ContextStore,
        inventory_agent: InventoryAgent,
        recipe_agent: RecipeAgent,
        behaviour_agent: BehaviourAgent,
    ) -> None:
        self.store = store
        self.inventory_agent = inventory_agent
        self.recipe_agent = recipe_agent
        self.behaviour_agent = behaviour_agent

    def preview_recipe_gap(self, recipe_id: str) -> list[GroceryLine]:
        recipe = self.recipe_agent.get_recipe(recipe_id)
        suggestion = self.recipe_agent.evaluate_recipe(recipe)
        return suggestion.missing_items

    def preview_staple_restock(self) -> list[GroceryLine]:
        low_stock_lines = [
            GroceryLine(
                name=item.name,
                quantity=round(max(item.min_desired_quantity - item.quantity, 1.0), 2),
                unit=item.unit,
                reason="low stock",
            )
            for item in self.inventory_agent.low_stock_items()
        ]

        predicted_lines = self.behaviour_agent.predict_restock_candidates(limit=3)

        merged: list[GroceryLine] = []
        seen: set[str] = set()
        for line in low_stock_lines + predicted_lines:
            key = line.name.lower()
            if key in seen:
                continue
            merged.append(line)
            seen.add(key)
        return merged

    def place_order(self, items: list[GroceryLine], source: str) -> dict[str, object]:
        if not items:
            return {
                "message": "No grocery order was created because there are no missing items.",
                "items": [],
                "order": None,
            }

        provider_response = self._mock_provider_checkout(items)
        order = GroceryOrder(
            id=provider_response["order_id"],
            items=items,
            created_at=datetime.utcnow(),
            status="confirmed",
            source=source,
            vendor=provider_response["vendor"],
            eta_minutes=provider_response["eta_minutes"],
        )

        def mutator(state):
            state.grocery_orders.insert(0, order)
            state.pending_grocery_list = []
            return {
                "order_id": order.id,
                "item_count": len(items),
                "source": source,
            }

        self.store.update(
            agent="grocery_agent",
            action="place_order",
            summary=f"Placed grocery order {order.id} from {source}.",
            mutator=mutator,
        )

        return {
            "message": f"Placed mock grocery order {order.id}.",
            "order": order,
            "items": items,
        }

    def order_missing_for_recipe(self, recipe_id: str) -> dict[str, object]:
        recipe = self.recipe_agent.get_recipe(recipe_id)
        missing_items = self.preview_recipe_gap(recipe_id)
        if not missing_items:
            return {
                "message": f"No order needed. Inventory already covers {recipe.name}.",
                "items": [],
                "order": None,
            }
        return self.place_order(missing_items, source=f"recipe:{recipe.id}")

    def order_staple_restock(self) -> dict[str, object]:
        items = self.preview_staple_restock()
        return self.place_order(items, source="staple_restock")

    @staticmethod
    def _mock_provider_checkout(items: list[GroceryLine]) -> dict[str, object]:
        return {
            "order_id": f"GRO-{uuid4().hex[:8].upper()}",
            "vendor": "PantryNow Mock",
            "eta_minutes": 45 + (5 * max(len(items) - 1, 0)),
        }
