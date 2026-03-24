from __future__ import annotations

from dataclasses import dataclass

from app.agents.behaviour import BehaviourAgent
from app.agents.grocery import GroceryAgent
from app.agents.inventory import InventoryAgent
from app.agents.nutrition import NutritionAgent
from app.agents.recipe import RecipeAgent
from app.agents.utility import UtilityAgent
from app.core.bootstrap import build_initial_context
from app.core.context_store import ContextStore
from app.core.orchestrator import MCPFridgeOrchestrator


@dataclass
class AppContainer:
    store: ContextStore
    inventory_agent: InventoryAgent
    recipe_agent: RecipeAgent
    grocery_agent: GroceryAgent
    nutrition_agent: NutritionAgent
    behaviour_agent: BehaviourAgent
    utility_agent: UtilityAgent
    orchestrator: MCPFridgeOrchestrator


def build_container() -> AppContainer:
    store = ContextStore(build_initial_context())
    inventory_agent = InventoryAgent(store)
    recipe_agent = RecipeAgent(store)
    behaviour_agent = BehaviourAgent(store)
    nutrition_agent = NutritionAgent(store)
    utility_agent = UtilityAgent(store)
    grocery_agent = GroceryAgent(
        store=store,
        inventory_agent=inventory_agent,
        recipe_agent=recipe_agent,
        behaviour_agent=behaviour_agent,
    )
    orchestrator = MCPFridgeOrchestrator(
        inventory_agent=inventory_agent,
        recipe_agent=recipe_agent,
        grocery_agent=grocery_agent,
        nutrition_agent=nutrition_agent,
        behaviour_agent=behaviour_agent,
        utility_agent=utility_agent,
    )

    return AppContainer(
        store=store,
        inventory_agent=inventory_agent,
        recipe_agent=recipe_agent,
        grocery_agent=grocery_agent,
        nutrition_agent=nutrition_agent,
        behaviour_agent=behaviour_agent,
        utility_agent=utility_agent,
        orchestrator=orchestrator,
    )

