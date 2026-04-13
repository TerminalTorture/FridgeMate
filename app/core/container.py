from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.agents.behaviour import BehaviourAgent
from app.agents.grocery import GroceryAgent
from app.agents.inventory import InventoryAgent
from app.agents.nutrition import NutritionAgent
from app.agents.recipe import RecipeAgent
from app.agents.utility import UtilityAgent
from app.core.bootstrap import build_initial_context
from app.core.confirmation_manager import ConfirmationManager
from app.core.conversation_manager import ConversationManager
from app.core.context_store import ContextStore
from app.core.decision_engine import DecisionEngine
from app.core.diagnostics import DiagnosticsEngine
from app.core.heartbeat_service import HeartbeatService
from app.core.llm_gateway import LLMGatewayService
from app.core.llm_service import LLMService
from app.core.memory_manager import MemoryManager
from app.core.mcp_tools import MCPToolService
from app.core.orchestrator import MCPFridgeOrchestrator
from app.core.override_parser import OverrideParser
from app.core.prompt_builder import PromptBuilder
from app.core.recipe_discovery_service import RecipeDiscoveryService
from app.core.runtime_state import RuntimeStateAggregator
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
    llm_gateway_service: LLMGatewayService
    recipe_discovery_service: RecipeDiscoveryService
    mcp_tool_service: MCPToolService
    telegram_service: TelegramService
    telegram_runner: TelegramPollingRunner
    conversation_manager: ConversationManager
    memory_manager: MemoryManager
    runtime_state_aggregator: RuntimeStateAggregator
    diagnostics_engine: DiagnosticsEngine
    heartbeat_service: HeartbeatService
    confirmation_manager: ConfirmationManager
    prompt_builder: PromptBuilder
    decision_engine: DecisionEngine


def build_container() -> AppContainer:
    settings = get_settings()
    memory_manager = MemoryManager(os.getenv("FRIDGEMATE_MEMORY_ROOT", "."))
    memory_manager.ensure_bootstrap_files()
    store = ContextStore(
        build_initial_context(),
        database_url=settings.database_url,
        sql_echo=settings.sql_echo,
        storage_path=settings.memory_store_path,
        seed_history_on_startup=settings.seed_history_on_startup,
        seed_history_days=settings.seed_history_days,
        seed_history_seed=settings.seed_history_seed,
    )
    confirmation_manager = ConfirmationManager()
    runtime_state_aggregator = RuntimeStateAggregator(store=store)
    runtime_state_aggregator.telegram_connected = settings.telegram_configured
    runtime_state_aggregator.set_pending_actions_provider(confirmation_manager.pending_actions)
    diagnostics_engine = DiagnosticsEngine(
        store=store,
        runtime_state_aggregator=runtime_state_aggregator,
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
    conversation_manager = ConversationManager(store=store)
    override_parser = OverrideParser()
    decision_engine = DecisionEngine(
        store=store,
        recipe_agent=recipe_agent,
        inventory_agent=inventory_agent,
        grocery_agent=grocery_agent,
        behaviour_agent=behaviour_agent,
        conversation_manager=conversation_manager,
        override_parser=override_parser,
    )
    heartbeat_service = HeartbeatService(
        store=store,
        diagnostics_engine=diagnostics_engine,
        memory_manager=memory_manager,
        decision_engine=decision_engine,
    )
    orchestrator = MCPFridgeOrchestrator(
        inventory_agent=inventory_agent,
        recipe_agent=recipe_agent,
        grocery_agent=grocery_agent,
        nutrition_agent=nutrition_agent,
        behaviour_agent=behaviour_agent,
        utility_agent=utility_agent,
    )
    prompt_builder = PromptBuilder(
        store=store,
        memory_manager=memory_manager,
        runtime_state_aggregator=runtime_state_aggregator,
    )
    llm_gateway_service = LLMGatewayService(
        repo_root=Path(__file__).resolve().parents[2],
        policy_path=Path(settings.llm_gateway_policy_path),
    )
    llm_service = LLMService(store=store, prompt_builder=prompt_builder)
    recipe_discovery_service = RecipeDiscoveryService(llm_service=llm_service)
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
        runtime_state_aggregator=runtime_state_aggregator,
        diagnostics_engine=diagnostics_engine,
        heartbeat_service=heartbeat_service,
        confirmation_manager=confirmation_manager,
        decision_engine=decision_engine,
        llm_gateway_service=llm_gateway_service,
    )
    llm_service.bind_mcp_tool_service(mcp_tool_service)
    decision_engine.bind_llm_service(llm_service)
    telegram_service = TelegramService(
        orchestrator=orchestrator,
        llm_service=llm_service,
        conversation_manager=conversation_manager,
        heartbeat_service=heartbeat_service,
        decision_engine=decision_engine,
        settings=settings,
    )
    heartbeat_service.set_notifier(telegram_service.send_message)
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
        llm_gateway_service=llm_gateway_service,
        recipe_discovery_service=recipe_discovery_service,
        mcp_tool_service=mcp_tool_service,
        telegram_service=telegram_service,
        telegram_runner=telegram_runner,
        conversation_manager=conversation_manager,
        memory_manager=memory_manager,
        runtime_state_aggregator=runtime_state_aggregator,
        diagnostics_engine=diagnostics_engine,
        heartbeat_service=heartbeat_service,
        confirmation_manager=confirmation_manager,
        prompt_builder=prompt_builder,
        decision_engine=decision_engine,
    )
