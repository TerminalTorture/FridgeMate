from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models.domain import SharedContext


def get_timezone(name: str = "UTC") -> timezone | ZoneInfo:
    if name.upper() == "UTC":
        return UTC
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == "Asia/Singapore":
            return timezone(timedelta(hours=8), name="Asia/Singapore")
        return UTC


def singapore_now() -> datetime:
    tz = get_timezone("Asia/Singapore")
    return datetime.now(tz)


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def normalize_shared_context_datetimes(state: SharedContext) -> SharedContext:
    for meal in state.meal_history:
        meal.cooked_at = ensure_utc(meal.cooked_at)

    for item in state.inventory:
        if item.purchased_at is not None:
            item.purchased_at = ensure_utc(item.purchased_at)

    for batch in state.inventory_batches:
        if batch.purchased_at is not None:
            batch.purchased_at = ensure_utc(batch.purchased_at)

    for order in state.grocery_orders:
        order.created_at = ensure_utc(order.created_at)

    for event in state.recent_events:
        event.timestamp = ensure_utc(event.timestamp)

    for memory in state.conversation_memory.values():
        memory.session_started_at = ensure_utc(memory.session_started_at)
        memory.last_activity_at = ensure_utc(memory.last_activity_at)
        for turn in memory.turns:
            turn.timestamp = ensure_utc(turn.timestamp)

    return state
