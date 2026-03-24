from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException, Query

from app.core.container import build_container
from app.core.settings import get_settings
from app.models.api import (
    GroceryOrderRequest,
    InventoryItemInput,
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
        "telegram_mode": "webhook_ready",
        "notes": [
            "Secrets are loaded from environment variables or a repo-root .env file.",
            "Telegram now supports a real webhook endpoint at /telegram/webhook.",
        ],
    }


@app.get("/context")
def get_context():
    return container.store.snapshot()


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
    return container.orchestrator.handle_telegram_message(
        user_id=payload.user_id,
        message=payload.message,
    )


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
