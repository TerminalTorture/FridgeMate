from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query, Request

from app.core.bootstrap import build_initial_context
from app.core.container import build_container
from app.core.json_log_store import append_json_log
from app.core.settings import get_settings
from app.core.time_utils import utc_now
from app.core.tracing import record_json_consult, trace_scope
from app.models.api import (
    DecisionFeedbackRequest,
    HeartbeatSettingsRequest,
    GroceryOrderRequest,
    InventoryItemInput,
    MCPToolCallRequest,
    OnlineRecipeSearchRequest,
    RecipeImportRequest,
    SeedHistoryRequest,
    TelegramSendTestRequest,
    TelegramMessageRequest,
    TelegramWebhookRegistrationRequest,
    TemporaryStateRequest,
    UserPreferencesRequest,
    UtilityUpdateRequest,
)
from app.models.domain import GroceryLine, InventoryItem

app = FastAPI(
    title="FridgeMate MCP Fridge Prototype",
    version="0.1.0",
    description="A FridgeMate MCP-style multi-agent household assistant prototype.",
)

settings = get_settings()
container = build_container()


@app.middleware("http")
async def trace_http_requests(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid4().hex
    with trace_scope(
        channel="http",
        request_id=request_id,
        metadata={
            "method": request.method,
            "path": request.url.path,
            "query": dict(request.query_params),
        },
    ):
        return await call_next(request)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": "FridgeMate MCP Fridge Prototype",
        "docs": "/docs",
        "health": "/health",
        "config_status": "/config/status",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config/status")
def config_status() -> dict[str, object]:
    return {
        "telegram_configured": settings.telegram_configured,
        "llm_configured": settings.llm_configured,
        "llm_model": settings.llm_model,
        "telegram_mode": settings.telegram_mode,
        "database_url": settings.database_url,
        "sql_echo": settings.sql_echo,
        "seed_history_on_startup": settings.seed_history_on_startup,
        "seed_history_days": settings.seed_history_days,
        "seed_history_seed": settings.seed_history_seed,
        "memory_store_path": settings.memory_store_path,
        "log_store_path": settings.log_store_path,
        "session_timeout_minutes": settings.session_timeout_minutes,
        "notes": [
            "Secrets are loaded from environment variables or a repo-root .env file.",
            "If TELEGRAM_CHAT_ID is empty, the app uses Telegram polling mode.",
            "If TELEGRAM_CHAT_ID is set, the app expects Telegram webhook mode.",
            "Operational state persists to SQL; MEMORY_STORE_PATH is kept for one-time legacy import compatibility.",
            "Conversation sessions roll over after inactivity and persist to SQL.",
        ],
    }


@app.get("/debug/integrations")
def integration_debug() -> dict[str, object]:
    return {
        "telegram": container.telegram_service.debug_snapshot(),
        "telegram_runner": container.telegram_runner.status(),
        "heartbeat": container.heartbeat_service.status(),
        "llm": container.llm_service.debug_snapshot(),
        "mcp": container.mcp_tool_service.debug_snapshot(),
        "proxy_env": {
            "HTTP_PROXY": os.getenv("HTTP_PROXY"),
            "HTTPS_PROXY": os.getenv("HTTPS_PROXY"),
            "ALL_PROXY": os.getenv("ALL_PROXY"),
            "NO_PROXY": os.getenv("NO_PROXY"),
        },
    }


@app.get("/debug/logs")
def debug_logs(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, object]:
    path = Path(settings.log_store_path)
    if not path.exists():
        record_json_consult(
            name="runtime_logs",
            path=str(path),
            operation="read_missing",
            records=0,
            chars=0,
        )
        return {"log_store_path": settings.log_store_path, "entries": []}

    try:
        raw_text = path.read_text(encoding="utf-8")
        payload = json.loads(raw_text)
        entries = payload if isinstance(payload, list) else []
        record_json_consult(
            name="runtime_logs",
            path=str(path),
            operation="read",
            records=len(entries),
            chars=len(raw_text),
        )
    except Exception as exc:
        record_json_consult(
            name="runtime_logs",
            path=str(path),
            operation="read_error",
        )
        raise HTTPException(status_code=500, detail=f"Failed to read log file: {exc}") from exc

    return {
        "log_store_path": settings.log_store_path,
        "entries": entries[-limit:],
    }


@app.on_event("startup")
async def startup_event() -> None:
    append_json_log(
        {
            "timestamp": utc_now().isoformat(),
            "service": "app",
            "direction": "internal",
            "status": "startup",
            "summary": "Application startup completed.",
            "metadata": {
                "telegram_mode": settings.telegram_mode,
                "memory_store_path": settings.memory_store_path,
                "log_store_path": settings.log_store_path,
            },
        }
    )
    if settings.telegram_configured and settings.telegram_mode == "polling":
        await container.telegram_service.set_my_commands_async()
        container.telegram_runner.start()
    elif settings.telegram_configured:
        await container.telegram_service.set_my_commands_async()
    container.heartbeat_service.start()


@app.on_event("shutdown")
def shutdown_event() -> None:
    append_json_log(
        {
            "timestamp": utc_now().isoformat(),
            "service": "app",
            "direction": "internal",
            "status": "shutdown",
            "summary": "Application shutdown initiated.",
            "metadata": {
                "reason": "uvicorn shutdown or reload",
            },
        }
    )
    container.telegram_runner.stop()
    container.heartbeat_service.stop()


@app.get("/context")
def get_context():
    return container.store.snapshot()


@app.get("/memory")
def get_memory():
    snapshot = container.store.snapshot()
    return {
        "version": snapshot.version,
        "memory_store_path": settings.memory_store_path,
        "database": container.store.database_summary(),
        "inventory_count": len(snapshot.inventory),
        "inventory_batch_count": len(snapshot.inventory_batches),
        "recipe_count": len(snapshot.recipe_catalog),
        "shopping_list_count": len(snapshot.pending_grocery_list),
        "conversation_users": len(snapshot.conversation_memory),
        "conversation_memory": snapshot.conversation_memory,
        "recent_events": snapshot.recent_events[:10],
        "markdown_memory": container.memory_manager.metadata(),
    }


@app.get("/runtime/state")
def runtime_state(user_id: str | None = None):
    return container.runtime_state_aggregator.build(user_id=user_id)


@app.get("/diagnostics")
def diagnostics(user_id: str | None = None):
    return container.diagnostics_engine.diagnostics_report(user_id=user_id)


@app.post("/heartbeat/check")
def heartbeat_check(user_id: str | None = None):
    if user_id:
        return container.heartbeat_service.run_for_user(user_id, force=True, notify=False)
    return container.heartbeat_service.run_once()


@app.get("/decision/state/{user_id}")
def decision_state(user_id: str):
    return container.decision_engine.public_state(user_id)


@app.post("/decision/run/{user_id}")
def decision_run(user_id: str, force: bool = False):
    return container.decision_engine.run_for_user(user_id, force=force).model_dump(mode="json")


@app.post("/decision/feedback")
def decision_feedback(payload: DecisionFeedbackRequest):
    result = container.decision_engine.record_feedback(
        user_id=payload.user_id,
        status=payload.status,
        intervention_id=payload.intervention_id,
        thread_key=payload.thread_key,
        detail=payload.detail,
    )
    return {"feedback": result.model_dump(mode="json") if result else None}


@app.get("/users/{user_id}/preferences")
def get_user_preferences(user_id: str):
    return container.store.user_preferences(user_id)


@app.post("/users/{user_id}/preferences")
def set_user_preferences(user_id: str, payload: UserPreferencesRequest):
    return container.store.set_user_preferences(
        user_id,
        mode=payload.mode,
        meal_window_start=payload.meal_window_start,
        meal_window_end=payload.meal_window_end,
        late_night_window_start=payload.late_night_window_start,
        late_night_window_end=payload.late_night_window_end,
        max_prep_minutes=payload.max_prep_minutes,
        notification_frequency=payload.notification_frequency,
        dietary_preferences=payload.dietary_preferences,
        search_model=payload.search_model,
    )


@app.get("/users/{user_id}/state")
def get_user_state(user_id: str):
    return {"user_id": user_id, "temporary_states": container.store.temporary_states(user_id)}


@app.post("/users/{user_id}/state")
def set_user_state(user_id: str, payload: TemporaryStateRequest):
    duration = payload.duration_hours or {
        "tired": 24,
        "busy": 6,
        "stressed": 24,
        "commuting": 3,
        "at_home": 6,
        "not_home": 12,
    }.get(payload.state, 24)
    state = container.store.set_temporary_state(
        user_id,
        state=payload.state,
        value=payload.value,
        expires_at=utc_now() + timedelta(hours=duration),
        source="api",
        note=payload.note,
    )
    return {"state": state}


@app.get("/heartbeat/settings/{user_id}")
def heartbeat_status(user_id: str):
    return container.heartbeat_service.status_for_user(user_id)


@app.post("/heartbeat/settings/{user_id}")
def heartbeat_settings(user_id: str, payload: HeartbeatSettingsRequest):
    return container.heartbeat_service.configure(
        user_id,
        enabled=payload.enabled,
        interval_minutes=payload.interval_minutes,
        dinner_time=payload.dinner_time,
        chat_id=payload.chat_id,
    )


@app.post("/confirmations/{confirmation_id}/confirm")
def confirm_action(confirmation_id: str, user_id: str | None = None):
    try:
        return container.mcp_tool_service.confirm_pending_action(
            confirmation_id=confirmation_id,
            user_id=user_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/confirmations/{confirmation_id}/cancel")
def cancel_action(confirmation_id: str, user_id: str | None = None):
    try:
        return container.mcp_tool_service.cancel_pending_action(
            confirmation_id=confirmation_id,
            user_id=user_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/sessions/{user_id}")
def get_session_status(user_id: str):
    return container.conversation_manager.session_status(user_id)


@app.get("/inventory")
def get_inventory(days: int = Query(default=3, ge=1, le=30)):
    return {
        "items": container.inventory_agent.get_inventory(),
        "batches": container.store.list_inventory_batches(),
        "expiring_soon": container.inventory_agent.expiring_soon(days=days),
        "low_stock": container.inventory_agent.low_stock_items(),
    }


@app.get("/inventory/batches")
def get_inventory_batches(include_inactive: bool = Query(default=False)):
    return {"batches": container.store.list_inventory_batches(include_inactive=include_inactive)}


@app.post("/inventory/items")
def add_inventory_item(payload: InventoryItemInput):
    item = InventoryItem(**payload.model_dump())
    updated_item = container.inventory_agent.add_or_refresh_item(item)
    return {
        "message": f"Inventory updated for {updated_item.name}.",
        "item": updated_item,
        "low_stock": container.inventory_agent.low_stock_items(),
    }


@app.post("/seed/history")
def seed_history(payload: SeedHistoryRequest):
    container.store.seed_synthetic_history(
        days=payload.days,
        seed=payload.seed,
        initial_state=build_initial_context(),
    )
    return {
        "message": f"Seeded {payload.days} days of synthetic history.",
        "database": container.store.database_summary(),
    }


@app.get("/recipes")
def list_recipes():
    return {"recipes": container.recipe_agent.list_recipes()}


@app.get("/recipes/suggestions")
def suggest_recipes(limit: int = Query(default=3, ge=1, le=10)):
    return {"suggestions": container.recipe_agent.suggest_recipes(limit=limit)}


@app.post("/recipes/online/search")
def search_online_recipes(payload: OnlineRecipeSearchRequest):
    try:
        recipes = container.recipe_discovery_service.search_online_recipes(
            query=payload.query,
            max_results=payload.max_results,
        )
        return {"query": payload.query, "results": recipes}
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/recipes/import")
def import_recipe(payload: RecipeImportRequest):
    try:
        recipe = container.recipe_agent.add_recipe(payload.recipe.to_domain())
        return {"recipe": recipe}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/recipes/{recipe_id}/cook")
def cook_recipe(recipe_id: str):
    try:
        return container.orchestrator.cook_recipe(recipe_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/groceries/pending")
def preview_groceries():
    return {"suggested_items": container.grocery_agent.preview_staple_restock()}


@app.post("/groceries/order")
def create_grocery_order(payload: GroceryOrderRequest, user_id: str = "api-user"):
    try:
        if payload.recipe_id:
            return container.mcp_tool_service.call_tool(
                "order_groceries_for_recipe",
                {"recipe_id": payload.recipe_id, "user_id": user_id},
            )

        if payload.items:
            lines = [GroceryLine(**item.model_dump()) for item in payload.items]
            return {
                "tool_name": "place_custom_grocery_order",
                **container.confirmation_manager.request_confirmation(
                    user_id=user_id,
                    action="place_custom_grocery_order",
                    arguments={
                        "items": [line.model_dump(mode="json") for line in lines],
                        "source": payload.source,
                    },
                    summary=f"place a custom grocery order with {len(lines)} item(s)",
                ),
            }

        return container.mcp_tool_service.call_tool(
            "order_staple_restock",
            {"user_id": user_id},
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/groceries/order/recipe/{recipe_id}")
def order_recipe_gap(recipe_id: str):
    try:
        return container.mcp_tool_service.call_tool(
            "order_groceries_for_recipe",
            {"recipe_id": recipe_id, "user_id": "api-user"},
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/nutrition/summary")
def nutrition_summary():
    return container.nutrition_agent.get_summary()


@app.get("/behaviour/summary")
def behaviour_summary():
    return container.behaviour_agent.get_summary()


@app.get("/utilities")
def utility_status():
    return container.utility_agent.get_status()


@app.post("/utilities")
def update_utilities(payload: UtilityUpdateRequest):
    return container.utility_agent.update_levels(
        water_level_percent=payload.water_level_percent,
        ice_level_percent=payload.ice_level_percent,
    )


@app.post("/telegram/mock")
async def telegram_mock(payload: TelegramMessageRequest):
    reply = await container.telegram_service.build_reply_for_user_async(
        user_id=payload.user_id,
        text=payload.message,
        chat_id=payload.user_id,
    )
    return {
        "user_id": payload.user_id,
        "reply": reply,
        "session": container.conversation_manager.session_status(payload.user_id),
        "context": container.store.snapshot(),
    }


@app.post("/telegram/webhook")
async def telegram_webhook(
    update: dict[str, object],
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
):
    try:
        return await container.telegram_service.handle_webhook_async(
            update=update,
            secret_token=x_telegram_bot_api_secret_token,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/telegram/webhook/info")
async def telegram_webhook_info():
    try:
        return await container.telegram_service.get_webhook_info_async()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/telegram/webhook/register")
async def register_telegram_webhook(payload: TelegramWebhookRegistrationRequest):
    try:
        return await container.telegram_service.register_webhook_async(
            url=payload.url,
            drop_pending_updates=payload.drop_pending_updates,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/telegram/send-test")
async def telegram_send_test(payload: TelegramSendTestRequest):
    chat_id = payload.chat_id or settings.telegram_chat_id
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required.")
    try:
        return await container.telegram_service.send_message_async(chat_id=chat_id, text=payload.text)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/mcp/tools")
def list_mcp_tools():
    return {"tools": container.mcp_tool_service.list_tools()}


@app.post("/mcp/call")
def call_mcp_tool(payload: MCPToolCallRequest):
    try:
        return container.mcp_tool_service.call_tool(
            tool_name=payload.tool_name,
            arguments=payload.arguments,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/mcp/rpc")
def mcp_rpc(payload: dict[str, object]):
    request_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {
                    "name": "mcp-fridge",
                    "version": "0.1.0",
                },
                "capabilities": {
                    "tools": {},
                },
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}

    if method == "tools/list":
        tools = [
            {
                "name": str(tool.get("name") or ""),
                "description": str(tool.get("description") or ""),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        key: {"type": value}
                        for key, value in cast(dict[str, str], tool.get("arguments") or {}).items()
                    },
                },
            }
            for tool in container.mcp_tool_service.list_tools()
        ]
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}}

    if method == "tools/call":
        try:
            arguments = params.get("arguments", {}) if isinstance(params, dict) else {}
            tool_name = params.get("name") if isinstance(params, dict) else None
            if not tool_name:
                raise ValueError("Tool name is required.")
            result = container.mcp_tool_service.call_tool(
                tool_name=str(tool_name),
                arguments=arguments if isinstance(arguments, dict) else {},
            )
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": str(result),
                        }
                    ]
                },
            }
        except (LookupError, ValueError, RuntimeError) as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": str(exc)},
            }

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method {method} not found."},
    }
