# FridgeMate MCP Fridge Prototype

`FridgeMate` is a FastAPI-based household assistant with a SQL-first runtime store, batch-level fridge inventory, recipe matching, conversation memory, diagnostics, and a push-first decision engine for low-effort eating.



## Persistence Model

FridgeMate separates prompt/bootstrap assets from operational data:

- Markdown bootstrap: assistant identity, tone, user profile, durable notes, daily memory
- SQL runtime store: inventory, batches, recipes, meals, grocery orders, sessions, diagnostics, heartbeat preferences, runtime events
- Legacy JSON path: `MEMORY_STORE_PATH` is only kept for one-time import compatibility

Main SQL tables:

- `inventory_items`
- `inventory_batches`
- `recipes`
- `recipe_ingredients`
- `meal_records`
- `grocery_orders`
- `grocery_order_lines`
- `pending_grocery_items`
- `conversation_sessions`
- `conversation_turns`
- `conversation_summaries`
- `heartbeat_preferences`
- `user_preferences`
- `temporary_state_overrides`
- `decision_profiles`
- `assistant_interventions`
- `diagnostics_snapshots`
- `runtime_events`

## Setup

1. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and set your secrets.
Minimumly, fill TELEGRAM_BOT_TOKEN,LLM_API_KEY,LLM_MODEL,LLM_BASE_URL

3. Create the schema:

```bash
alembic upgrade head
```

4. Optionally seed six months of history:

```bash
python scripts/seed_history.py
```

5. Start the app:

```bash
uvicorn app.main:app --reload
```

Default SQL settings:

```bash
DATABASE_URL=sqlite:///data/fridgemate.db
SQL_ECHO=0
SEED_HISTORY_ON_STARTUP=1
SEED_HISTORY_DAYS=180
SEED_HISTORY_SEED=4052
```

`SEED_HISTORY_ON_STARTUP=1` means an empty database will auto-bootstrap with synthetic usage history. Set it to `0` if you want a minimal import-only startup.

## Historical Data

The synthetic history generator is deterministic. With the same `SEED_HISTORY_SEED`, you get the same purchase dates, expiry dates, meal cadence, low-stock events, and restock patterns.

The seeded data is intended to look like a fridge used daily for months:

- weekly milk purchases
- eggs dropping below threshold and being restocked
- vegetables bought in bursts
- leftovers and expiry pressure
- recurring recipes and meal logging
- routine grocery orders and occasional misses

You can reseed through the API too:

```bash
curl -X POST http://127.0.0.1:8000/seed/history \
  -H "Content-Type: application/json" \
  -d "{\"days\": 180, \"seed\": 4052}"
```

## Key API Checks

Basic health and config:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/config/status
curl http://127.0.0.1:8000/memory
```

Inventory and batch visibility:

```bash
curl http://127.0.0.1:8000/inventory
curl http://127.0.0.1:8000/inventory/batches
curl -X POST http://127.0.0.1:8000/inventory/items \
  -H "Content-Type: application/json" \
  -d "{\"id\":\"milk_demo\",\"name\":\"Milk\",\"category\":\"dairy\",\"quantity\":1,\"unit\":\"carton\",\"expires_on\":\"2026-04-15T00:00:00+08:00\",\"purchased_at\":\"2026-04-11T18:00:00+08:00\"}"
```

Recipes and cooking:

```bash
curl http://127.0.0.1:8000/recipes
curl http://127.0.0.1:8000/recipes/suggestions
curl -X POST http://127.0.0.1:8000/recipes/online/search \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"high protein chicken dinner\",\"max_results\":3}"
curl -X POST http://127.0.0.1:8000/recipes/chicken_rice_bowl/cook
```

Diagnostics and runtime:

```bash
curl http://127.0.0.1:8000/runtime/state
curl http://127.0.0.1:8000/diagnostics
curl http://127.0.0.1:8000/decision/state/demo-user
curl -X POST http://127.0.0.1:8000/decision/run/demo-user
curl -X POST http://127.0.0.1:8000/heartbeat/check
curl http://127.0.0.1:8000/heartbeat/settings/demo-user
```

Heartbeat settings through HTTP:

```bash
curl -X POST http://127.0.0.1:8000/heartbeat/settings/demo-user \
  -H "Content-Type: application/json" \
  -d "{\"enabled\":true,\"interval_minutes\":60,\"dinner_time\":\"18:30\",\"chat_id\":\"demo-user\"}"

curl -X POST "http://127.0.0.1:8000/heartbeat/check?user_id=demo-user"
```

User steering through HTTP:

```bash
curl http://127.0.0.1:8000/users/demo-user/preferences
curl -X POST http://127.0.0.1:8000/users/demo-user/preferences \
  -H "Content-Type: application/json" \
  -d "{\"mode\":\"lazy\",\"max_prep_minutes\":7,\"notification_frequency\":\"quiet\",\"dietary_preferences\":[\"avoid dairy\"]}"

curl http://127.0.0.1:8000/users/demo-user/state
curl -X POST http://127.0.0.1:8000/users/demo-user/state \
  -H "Content-Type: application/json" \
  -d "{\"state\":\"tired\",\"duration_hours\":24,\"note\":\"long day\"}"

curl -X POST http://127.0.0.1:8000/decision/feedback \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"demo-user\",\"thread_key\":\"cook:veggie_omelette\",\"status\":\"ignored\",\"detail\":\"skip tonight\"}"
```

Grocery confirmation flow:

```bash
curl -X POST http://127.0.0.1:8000/groceries/order/recipe/chicken_rice_bowl
curl -X POST http://127.0.0.1:8000/confirmations/<confirmation_id>/confirm
curl -X POST http://127.0.0.1:8000/confirmations/<confirmation_id>/cancel
```

Telegram mock route:

```bash
curl -X POST http://127.0.0.1:8000/telegram/mock \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"demo-user\",\"message\":\"what can I cook tonight?\"}"
```

## Telegram Heartbeat Commands

Per-user heartbeat settings are stored in SQL and default to hourly checks in `Asia/Singapore`.

Supported commands:

- `/heartbeat`
- `/heartbeat status`
- `/heartbeat on`
- `/heartbeat off`
- `/heartbeat time 18:30`
- `/heartbeat now`

Examples:

```text
/heartbeat status
/heartbeat on
/heartbeat time 18:30
/heartbeat now
```

Heartbeat behavior:

- checks run every hour in the background
- the decision engine uses the user’s meal windows, mode, notification frequency, and temporary states
- before dinner, FridgeMate can ask if the user is coming home to eat
- if ingredients are available, it suggests one low-effort meal first instead of dumping options
- if staples are low, it can draft pickup items like milk or eggs
- repeated alerts are deduped until status materially changes

Natural-language steering examples:

```text
I'm exhausted today
Not home tonight
I'm commuting
Give me easier meals this week
I only want 5-minute meals
Stop messaging me at night
Switch to lazy mode
Be quiet today
```

Telegram callback actions:

- `Cook this`
- `Show easier option`
- `Draft shopping list`
- `Ignore tonight`
- `Not home`
- `Ordered food`

Helper script:

```bash
python scripts/show_heartbeat_status.py demo-user
```

## MCP Tool Surface

Tool metadata is centralized and exposed at `GET /mcp/tools`. Each tool describes:

- `policy`
- `when_to_use`
- `when_not_to_use`
- `authoritative_source`

Useful tool checks:

```bash
curl http://127.0.0.1:8000/mcp/tools
curl -X POST http://127.0.0.1:8000/mcp/call \
  -H "Content-Type: application/json" \
  -d "{\"tool_name\":\"get_inventory_batches\",\"arguments\":{}}"
curl -X POST http://127.0.0.1:8000/mcp/call \
  -H "Content-Type: application/json" \
  -d "{\"tool_name\":\"get_heartbeat_status\",\"arguments\":{\"user_id\":\"demo-user\"}}"
curl -X POST http://127.0.0.1:8000/mcp/call \
  -H "Content-Type: application/json" \
  -d "{\"tool_name\":\"run_decision\",\"arguments\":{\"user_id\":\"demo-user\",\"force\":\"true\"}}"
```

Protected actions return `requires_confirmation: true` first:

- `clear_inventory`
- `remove_inventory_item`
- `order_groceries_for_recipe`
- `order_staple_restock`

## Testing

Run the test suite:

```bash
python -m unittest discover -s tests
```

Useful targeted checks:

```bash
python -m unittest tests.test_mcp_tools
python -m unittest tests.test_sql_storage
python -m compileall app tests
```

What the tests cover:

- Alembic migration bootstrapping
- SQL repository CRUD and snapshot persistence
- six-month seed generation and reproducibility
- diagnostics and runtime state checks
- MCP tool coverage
- destructive confirmation flow
- Telegram `/heartbeat` command handling
- natural-language steering into structured preference and state updates
- callback-query feedback on proactive nudges
- adaptive decision gating for lazy and silent modes

## Notes

- SQLite is the default runtime store; the schema and repository layer are kept portable enough for Postgres later.
- `MEMORY_STORE_PATH` is deprecated as the live operational store.
- Online recipe discovery requires `LLM_API_KEY`.
- Polling mode starts automatically when `TELEGRAM_CHAT_ID` is empty and `TELEGRAM_BOT_TOKEN` is set.
