from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query

from app.core.container import build_container
from app.core.json_log_store import append_json_log
from app.core.settings import get_settings
from app.core.time_utils import utc_now
from app.models.api import (
    GroceryOrderRequest,
    InventoryItemInput,
    MCPToolCallRequest,
    OnlineRecipeSearchRequest,
    RecipeImportRequest,
    TelegramSendTestRequest,
    TelegramMessageRequest,
    TelegramWebhookRegistrationRequest,
    UtilityUpdateRequest,
)
from app.models.domain import GroceryLine, InventoryItem

app = FastAPI(
    title="MCP Fridge Prototype",
    version="0.1.0",
    description="A simple MCP-style multi-agent household assistant prototype.",
)

settings = get_settings()
container = build_container()


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": "MCP Fridge Prototype",
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
        "memory_store_path": settings.memory_store_path,
        "log_store_path": settings.log_store_path,
        "session_timeout_minutes": settings.session_timeout_minutes,
        "notes": [
            "Secrets are loaded from environment variables or a repo-root .env file.",
            "If TELEGRAM_CHAT_ID is empty, the app uses Telegram polling mode.",
            "If TELEGRAM_CHAT_ID is set, the app expects Telegram webhook mode.",
            "Conversation sessions roll over after inactivity and persist to a JSON memory file.",
        ],
    }


@app.get("/debug/integrations")
def integration_debug() -> dict[str, object]:
    return {
        "telegram": container.telegram_service.debug_snapshot(),
        "telegram_runner": container.telegram_runner.status(),
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
        return {"log_store_path": settings.log_store_path, "entries": []}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = payload if isinstance(payload, list) else []
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read log file: {exc}") from exc

    return {
        "log_store_path": settings.log_store_path,
        "entries": entries[-limit:],
    }


@app.on_event("startup")
def startup_event() -> None:
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
        container.telegram_runner.start()


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


@app.get("/context")
def get_context():
    return container.store.snapshot()


@app.get("/memory")
def get_memory():
    snapshot = container.store.snapshot()
    return {
        "version": snapshot.version,
        "memory_store_path": settings.memory_store_path,
        "inventory_count": len(snapshot.inventory),
        "recipe_count": len(snapshot.recipe_catalog),
        "shopping_list_count": len(snapshot.pending_grocery_list),
        "conversation_users": len(snapshot.conversation_memory),
        "conversation_memory": snapshot.conversation_memory,
        "recent_events": snapshot.recent_events[:10],
    }


@app.get("/sessions/{user_id}")
def get_session_status(user_id: str):
    return container.conversation_manager.session_status(user_id)


@app.get("/inventory")
def get_inventory(days: int = Query(default=3, ge=1, le=30)):
    return {
        "items": container.inventory_agent.get_inventory(),
        "expiring_soon": container.inventory_agent.expiring_soon(days=days),
        "low_stock": container.inventory_agent.low_stock_items(),
    }


@app.post("/inventory/items")
def add_inventory_item(payload: InventoryItemInput):
    item = InventoryItem(**payload.model_dump())
    updated_item = container.inventory_agent.add_or_refresh_item(item)
    return {
        "message": f"Inventory updated for {updated_item.name}.",
        "item": updated_item,
        "low_stock": container.inventory_agent.low_stock_items(),
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
def create_grocery_order(payload: GroceryOrderRequest):
    try:
        if payload.recipe_id:
            return container.grocery_agent.order_missing_for_recipe(payload.recipe_id)

        if payload.items:
            lines = [GroceryLine(**item.model_dump()) for item in payload.items]
            return container.grocery_agent.place_order(lines, source=payload.source)

        return container.grocery_agent.order_staple_restock()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/groceries/order/recipe/{recipe_id}")
def order_recipe_gap(recipe_id: str):
    try:
        return container.grocery_agent.order_missing_for_recipe(recipe_id)
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
def telegram_mock(payload: TelegramMessageRequest):
    reply = container.telegram_service.build_reply_for_user(
        user_id=payload.user_id,
        text=payload.message,
    )
    return {
        "user_id": payload.user_id,
        "reply": reply,
        "session": container.conversation_manager.session_status(payload.user_id),
        "context": container.store.snapshot(),
    }


@app.post("/telegram/webhook")
def telegram_webhook(
    update: dict[str, object],
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
):
    try:
        return container.telegram_service.handle_webhook(
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
def telegram_webhook_info():
    try:
        return container.telegram_service.get_webhook_info()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/telegram/webhook/register")
def register_telegram_webhook(payload: TelegramWebhookRegistrationRequest):
    try:
        return container.telegram_service.register_webhook(
            url=payload.url,
            drop_pending_updates=payload.drop_pending_updates,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/telegram/send-test")
def telegram_send_test(payload: TelegramSendTestRequest):
    chat_id = payload.chat_id or settings.telegram_chat_id
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required.")
    try:
        return container.telegram_service.send_message(chat_id=chat_id, text=payload.text)
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
                "name": tool["name"],
                "description": tool["description"],
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        key: {"type": value}
                        for key, value in tool["arguments"].items()
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
