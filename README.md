# MCP Fridge Prototype

`MCP Fridge` is a small FastAPI-based multi-agent prototype for food, nutrition, and household management. It uses a shared context store as MCP-style memory so each agent can read the same state and write back structured updates.

## Architecture Diagram

```text
                    +----------------------------------+
                    | Telegram / WhatsApp Interface    |
                    | (Telegram mocked via API route)  |
                    +----------------+-----------------+
                                     |
                                     v
                    +----------------------------------+
                    | FastAPI App + Orchestrator       |
                    | - routes                         |
                    | - intent handling                |
                    | - cross-agent workflows          |
                    +----------------+-----------------+
                                     |
                                     v
                    +----------------------------------+
                    | Shared Context Store             |
                    | - inventory                      |
                    | - recipes                        |
                    | - shopping list / orders         |
                    | - utilities                      |
                    | - meal history                   |
                    | - behaviour profile              |
                    | - conversation memory            |
                    | - recent events                  |
                    +----------------+-----------------+
                                     |
         +-------------+-------------+-------------+-------------+-------------+-------------+
         |             |             |             |             |             |             |
         v             v             v             v             v             v
+----------------+ +----------------+ +----------------+ +----------------+ +----------------+ +----------------+
| Inventory Agent| |  Recipe Agent  | | Grocery Agent  | | Nutrition Ag. | | Behaviour Ag. | | Utility Agent |
| stock/expiry   | | meal matching  | | order planning | | meal tracking  | | habit learning | | water / ice   |
+----------------+ +----------------+ +----------------+ +----------------+ +----------------+ +----------------+
```

## Folder Structure

```text
SaaS/
|-- README.md
|-- requirements.txt
|-- .gitignore
`-- app/
    |-- main.py
    |-- agents/
    |   |-- behaviour.py
    |   |-- grocery.py
    |   |-- inventory.py
    |   |-- nutrition.py
    |   |-- recipe.py
    |   `-- utility.py
    |-- core/
    |   |-- bootstrap.py
    |   |-- conversation_manager.py
    |   |-- container.py
    |   |-- context_store.py
    |   |-- telegram_runner.py
    |   `-- orchestrator.py
    `-- models/
        |-- api.py
        `-- domain.py
```

## Shared Context and Data Flow

- `ContextStore.snapshot()` gives agents a deep-copy read view of the current state.
- `ContextStore.update(...)` applies changes with agent attribution and appends an event log entry.
- Shared context persists to `data/fridge_memory.json` by default, so inventory, expiry dates, shopping list, recipes, behaviour, and conversation memory survive restarts.
- The orchestrator coordinates multi-agent workflows such as cooking a recipe or responding to a Telegram-style message.
- `ConversationManager` keeps one active Telegram session per user, compacts old conversations on `/new` or after inactivity, and feeds the carryover summary back into the LLM.

Example cook flow:

1. User calls `POST /recipes/{recipe_id}/cook`
2. `RecipeAgent` resolves the recipe
3. `InventoryAgent` checks and consumes ingredients
4. `NutritionAgent` logs the meal
5. `BehaviourAgent` updates preferences and usage patterns
6. `UtilityAgent` lowers water/ice levels
7. Shared context version and event log are updated after each write

## Key Files

- [app/main.py]
- [app/core/context_store.py]
- [app/core/conversation_manager.py]
- [app/core/orchestrator.py]
- [app/core/bootstrap.py]
- [app/agents/inventory.py]
- [app/agents/recipe.py]
- [app/agents/grocery.py]
## Example API Endpoints

- `GET /health`
- `GET /context`
- `GET /memory`
- `GET /sessions/{user_id}`
- `GET /inventory`
- `POST /inventory/items`
- `GET /recipes`
- `GET /recipes/suggestions`
- `POST /recipes/online/search`
- `POST /recipes/import`
- `POST /recipes/{recipe_id}/cook`
- `GET /groceries/pending`
- `POST /groceries/order`
- `POST /groceries/order/recipe/{recipe_id}`
- `GET /nutrition/summary`
- `GET /behaviour/summary`
- `GET /utilities`
- `POST /utilities`
- `POST /telegram/mock`
- `POST /telegram/webhook`
- `GET /telegram/webhook/info`
- `POST /telegram/webhook/register`
- `GET /mcp/tools`
- `POST /mcp/call`

Example commands:

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload

curl http://127.0.0.1:8000/recipes/suggestions
curl -X POST http://127.0.0.1:8000/recipes/online/search \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"high protein chicken dinner\", \"max_results\": 3}"
curl http://127.0.0.1:8000/inventory
curl -X POST http://127.0.0.1:8000/groceries/order/recipe/chicken_rice_bowl
curl -X POST http://127.0.0.1:8000/telegram/mock \
  -H "Content-Type: application/json" \
  -d "{\"message\": \"what can I cook?\"}"
```

## MCP Tools

The prototype now exposes an MCP-style tool surface for recipe discovery and import:

- `GET /mcp/tools`
- `POST /mcp/call`

Available tool names:

- `search_recipes_online`
- `import_recipe`
- `search_and_import_recipe`

Online recipe discovery uses the OpenAI Responses API with the built-in `web_search` tool and requires a working `LLM_API_KEY`.

## Telegram Modes

You can run Telegram in either mode:

- Webhook mode: used when `TELEGRAM_CHAT_ID` is set and requires a public HTTPS URL in `TELEGRAM_WEBHOOK_URL`
- Polling mode: used when `TELEGRAM_CHAT_ID` is empty and does not require a domain or webhook

Polling mode now runs through a background worker pool so one slow LLM call or one slow Telegram `sendMessage` does not stall the entire bot. The runner keeps polling, dispatches updates concurrently, and exposes `in_flight` worker counts in `GET /debug/integrations`.

Polling mode command:

```bash
python scripts/poll_telegram.py
```

If you run the FastAPI app with `uvicorn app.main:app --reload`, polling mode now starts automatically in the background when `TELEGRAM_CHAT_ID` is empty.

Useful `.env` settings:

```bash
TELEGRAM_WORKER_COUNT=4
SESSION_TIMEOUT_MINUTES=30
MEMORY_STORE_PATH=data/fridge_memory.json
LOG_STORE_PATH=data/runtime_logs.json
```

Session behavior:

- `/new` starts a new Telegram session immediately
- after 30 minutes of inactivity, the next message automatically rolls into a new session
- the previous session is compacted into a carryover summary so the fridge keeps relevant context without dragging the whole transcript forward

Runtime logs:

- integration and lifecycle events are written to `data/runtime_logs.json`
- inspect them with `GET /debug/logs`
- if you run `uvicorn app.main:app --reload`, source edits trigger an intentional shutdown/startup cycle; that is the reloader, not a crash

Useful helper commands:

```bash
python scripts/check_telegram_webhook.py
python scripts/register_telegram_webhook.py
```

If you use polling mode, `TELEGRAM_BOT_TOKEN` is enough. You do not need `TELEGRAM_WEBHOOK_URL`.

## Example Interaction Flow

`what can I cook?`

1. `BehaviourAgent` logs a recipe query
2. `RecipeAgent` scores recipes against inventory coverage and soon-to-expire items
3. The orchestrator returns top matches and missing ingredients if any

`check inventory`

1. `InventoryAgent` reads current stock
2. Expiring and low-stock items are summarized
3. Response is formatted for chat output

`order groceries for chicken rice bowl`

1. `RecipeAgent` identifies ingredient gaps
2. `GroceryAgent` calls a mock grocery provider
3. The order is stored in shared context and logged as an event

## Bonus Logic

- Rule-based behaviour learning from cooked meals and frequent commands
- Predicted restock candidates based on favourite ingredients
- Mock grocery ordering with order IDs and delivery ETA
