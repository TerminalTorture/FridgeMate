from __future__ import annotations

import json
from typing import TYPE_CHECKING

from app.core.context_store import ContextStore
from app.core.memory_manager import MemoryManager
from app.core.runtime_state import RuntimeStateAggregator
from app.core.tracing import record_prompt_section

if TYPE_CHECKING:
    from app.core.mcp_tools import MCPToolService


class PromptBuilder:
    def __init__(
        self,
        *,
        store: ContextStore,
        memory_manager: MemoryManager,
        runtime_state_aggregator: RuntimeStateAggregator,
    ) -> None:
        self.store = store
        self.memory_manager = memory_manager
        self.runtime_state_aggregator = runtime_state_aggregator
        self.mcp_tool_service: MCPToolService | None = None

    def bind_mcp_tool_service(self, mcp_tool_service: "MCPToolService") -> None:
        self.mcp_tool_service = mcp_tool_service

    def build_instructions(
        self,
        *,
        user_id: str,
        user_message: str,
        conversation_context: str | None = None,
    ) -> str:
        sections = self.memory_manager.prompt_sections()
        runtime_state = self.runtime_state_aggregator.build(
            last_user_message=user_message,
            user_id=user_id,
        )
        tool_list = (
            self.mcp_tool_service.prompt_tool_registry()
            if self.mcp_tool_service is not None
            else "No MCP tools are currently bound."
        )
        gateway_access = (
            self.mcp_tool_service.gateway_access_summary()
            if self.mcp_tool_service is not None
            else "Gateway policy unavailable."
        )
        record_prompt_section(section="identity", chars=len(sections["identity"]))
        record_prompt_section(section="soul", chars=len(sections["soul"]))
        record_prompt_section(section="user", chars=len(sections["user"]))
        record_prompt_section(section="bootstrap", chars=len(sections["bootstrap"]))
        record_prompt_section(section="heartbeat", chars=len(sections["heartbeat"]))
        record_prompt_section(section="long_term_memory", chars=len(sections["long_term_memory"]))
        record_prompt_section(section="recent_memory", chars=len(sections["recent_memory"]))
        record_prompt_section(section="runtime_state", chars=len(json.dumps(runtime_state, default=str)))
        record_prompt_section(section="conversation_context", chars=len(conversation_context.strip() if conversation_context else ""))
        return (
            "You are FridgeMate, a household inventory assistant.\n\n"
            "## Identity\n"
            f"{sections['identity']}\n\n"
            "## Behaviour\n"
            f"{sections['soul']}\n\n"
            "## User\n"
            f"{sections['user']}\n\n"
            "## Startup Bootstrap\n"
            f"{sections['bootstrap']}\n\n"
            "## Current Runtime State\n"
            f"{json.dumps(runtime_state, indent=2, default=str)}\n\n"
            "## Gateway Access\n"
            f"{gateway_access}\n\n"
            "## Available Tools\n"
            f"{tool_list}\n\n"
            "## Long-term Memory\n"
            f"{sections['long_term_memory'] or 'No long-term memory.'}\n\n"
            "## Recent Memory\n"
            f"{sections['recent_memory']}\n\n"
            "## Conversation Context\n"
            f"{conversation_context.strip() if conversation_context else 'No prior session context.'}\n\n"
            "## Heartbeat Policy\n"
            f"{sections['heartbeat']}\n\n"
            "## Rules\n"
            "- Be useful, concise, and honest.\n"
            "- Prefer verification over guessing.\n"
            "- Mention uncertainty when sensor confidence is low or diagnostics are degraded.\n"
            "- Ask for confirmation before modifying shopping lists through checkout or performing destructive inventory actions.\n"
            "- If diagnostics indicate degraded hardware, mention it when relevant.\n"
            "- You are responsible for inventory awareness, expiry awareness, and sensible next-step suggestions.\n"
            "- Stay within the FridgeMate domain: food, nutrition, grocery planning, kitchen inventory, and fridge utilities.\n"
            "- Do not invent inventory, preferences, meals, orders, scans, or sensor readings.\n"
            "- Reply for Telegram chat, not for a dashboard or email.\n"
            "- Use plain text only. Do not use markdown bold, italics, headings, or decorative bullets.\n"
            "- Keep most replies to 1 to 4 short lines.\n"
            "- Do not repeat the full inventory unless the user explicitly asks for a full inventory check.\n"
            "- Mention only the most relevant items for the question.\n"
            "- Avoid emoji unless the user used emoji first.\n"
            "- Ask at most one short follow-up question when useful.\n\n"
            "## Decision Policy\n"
            "- When asked about stock, use current inventory tools.\n"
            "- When asked about unusual or missing items, compare expected inventory with detected inventory.\n"
            "- When confidence is low, say why.\n"
            "- When multiple actions are possible, recommend the highest-value one.\n"
            "- When a tool returns requires_confirmation, explain the pending action and ask the user to confirm or cancel.\n"
        )

    def build_user_input(
        self,
        *,
        user_id: str,
        user_message: str,
    ) -> str:
        snapshot = self.store.snapshot()
        inventory = ", ".join(
            f"{item.name} ({item.quantity:g} {item.unit})"
            for item in sorted(snapshot.inventory, key=lambda item: item.name.lower())[:12]
        )
        low_stock = ", ".join(
            item.name
            for item in snapshot.inventory
            if item.quantity < item.min_desired_quantity
        ) or "none"
        expiring = ", ".join(
            item.name for item in snapshot.inventory if item.expires_on is not None
        ) or "none"
        return (
            f"Telegram user id: {user_id}\n"
            f"User message: {user_message}\n\n"
            "Current operational snapshot:\n"
            f"- Inventory: {inventory or 'none'}\n"
            f"- Expiring-tracked items: {expiring}\n"
            f"- Low stock items: {low_stock}\n"
            f"- Water level: {snapshot.utilities.water_level_percent}%\n"
            f"- Ice level: {snapshot.utilities.ice_level_percent}%\n"
        )
