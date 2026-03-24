from __future__ import annotations

from dataclasses import dataclass

from app.agents.behaviour import BehaviourAgent
from app.agents.grocery import GroceryAgent
from app.agents.inventory import InventoryAgent
from app.agents.nutrition import NutritionAgent
from app.agents.recipe import RecipeAgent
from app.agents.utility import UtilityAgent
from app.core.bootstrap import build_initial_context
from app.core.conversation_manager import ConversationManager
from app.core.context_store import ContextStore
from app.core.llm_service import LLMService
from app.core.mcp_tools import MCPToolService
from app.core.orchestrator import MCPFridgeOrchestrator
from app.core.recipe_discovery_service import RecipeDiscoveryService
from app.core.settings import get_settings
from app.core.telegram_runner import TelegramPollingRunner
from app.core.telegram_service import TelegramService


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
    llm_service: LLMService
    recipe_discovery_service: RecipeDiscoveryService
    mcp_tool_service: MCPToolService
    telegram_service: TelegramService
    telegram_runner: TelegramPollingRunner
    conversation_manager: ConversationManager


def build_container() -> AppContainer:
    settings = get_settings()
    store = ContextStore(
        build_initial_context(),
        storage_path=settings.memory_store_path,
    )
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
    llm_service = LLMService(store=store)
    recipe_discovery_service = RecipeDiscoveryService(llm_service=llm_service)
    conversation_manager = ConversationManager(store=store)
    mcp_tool_service = MCPToolService(
        store=store,
        inventory_agent=inventory_agent,
        recipe_agent=recipe_agent,
        grocery_agent=grocery_agent,
        nutrition_agent=nutrition_agent,
        behaviour_agent=behaviour_agent,
        utility_agent=utility_agent,
        conversation_manager=conversation_manager,
        recipe_discovery_service=recipe_discovery_service,
    )
    llm_service.bind_mcp_tool_service(mcp_tool_service)
    telegram_service = TelegramService(
        orchestrator=orchestrator,
        llm_service=llm_service,
        conversation_manager=conversation_manager,
        settings=settings,
    )
    telegram_runner = TelegramPollingRunner(
        telegram_service=telegram_service,
        worker_count=settings.telegram_worker_count,
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
        llm_service=llm_service,
        recipe_discovery_service=recipe_discovery_service,
        mcp_tool_service=mcp_tool_service,
        telegram_service=telegram_service,
        telegram_runner=telegram_runner,
        conversation_manager=conversation_manager,
    )
