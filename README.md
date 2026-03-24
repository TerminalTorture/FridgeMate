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
                    | - utilities                      |
                    | - meal history                   |
                    | - behaviour profile              |
                    | - grocery orders                 |
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
    |   |-- container.py
    |   |-- context_store.py
    |   `-- orchestrator.py
    `-- models/
        |-- api.py
        `-- domain.py
```

## Shared Context and Data Flow

- `ContextStore.snapshot()` gives agents a deep-copy read view of the current state.
- `ContextStore.update(...)` applies changes with agent attribution and appends an event log entry.
- The orchestrator coordinates multi-agent workflows such as cooking a recipe or responding to a Telegram-style message.

Example cook flow:

1. User calls `POST /recipes/{recipe_id}/cook`
2. `RecipeAgent` resolves the recipe
3. `InventoryAgent` checks and consumes ingredients
4. `NutritionAgent` logs the meal
5. `BehaviourAgent` updates preferences and usage patterns
6. `UtilityAgent` lowers water/ice levels
7. Shared context version and event log are updated after each write

## Key Files

- [app/main.py](/c:/Users/LeeJR/OneDrive%20-%20Nanyang%20Technological%20University/School/Year%204%20Semester%202/CZ4052%20Cloud%20Computing-ML-LP/SaaS/app/main.py)
- [app/core/context_store.py](/c:/Users/LeeJR/OneDrive%20-%20Nanyang%20Technological%20University/School/Year%204%20Semester%202/CZ4052%20Cloud%20Computing-ML-LP/SaaS/app/core/context_store.py)
- [app/core/orchestrator.py](/c:/Users/LeeJR/OneDrive%20-%20Nanyang%20Technological%20University/School/Year%204%20Semester%202/CZ4052%20Cloud%20Computing-ML-LP/SaaS/app/core/orchestrator.py)
- [app/core/bootstrap.py](/c:/Users/LeeJR/OneDrive%20-%20Nanyang%20Technological%20University/School/Year%204%20Semester%202/CZ4052%20Cloud%20Computing-ML-LP/SaaS/app/core/bootstrap.py)
- [app/agents/inventory.py](/c:/Users/LeeJR/OneDrive%20-%20Nanyang%20Technological%20University/School/Year%204%20Semester%202/CZ4052%20Cloud%20Computing-ML-LP/SaaS/app/agents/inventory.py)
- [app/agents/recipe.py](/c:/Users/LeeJR/OneDrive%20-%20Nanyang%20Technological%20University/School/Year%204%20Semester%202/CZ4052%20Cloud%20Computing-ML-LP/SaaS/app/agents/recipe.py)
- [app/agents/grocery.py](/c:/Users/LeeJR/OneDrive%20-%20Nanyang%20Technological%20University/School/Year%204%20Semester%202/CZ4052%20Cloud%20Computing-ML-LP/SaaS/app/agents/grocery.py)

## Example API Endpoints

- `GET /health`
- `GET /config/status`
- `GET /context`
- `GET /inventory`
- `POST /inventory/items`
- `GET /recipes`
- `GET /recipes/suggestions`
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

Example commands:

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload

curl http://127.0.0.1:8000/recipes/suggestions
curl http://127.0.0.1:8000/inventory
curl -X POST http://127.0.0.1:8000/groceries/order/recipe/chicken_rice_bowl
curl -X POST http://127.0.0.1:8000/telegram/mock \
  -H "Content-Type: application/json" \
  -d "{\"message\": \"what can I cook?\"}"
```

## Secrets Setup

Put your secrets in a repo-root `.env` file. Start from `.env.example`.

```bash
cp .env.example .env
```

Then set:

```env
TELEGRAM_BOT_TOKEN=your-real-telegram-bot-token
TELEGRAM_CHAT_ID=optional-default-chat-id
TELEGRAM_WEBHOOK_SECRET=optional-webhook-shared-secret
TELEGRAM_WEBHOOK_URL=https://your-public-domain.example.com/telegram/webhook

LLM_API_KEY=your-real-llm-api-key
LLM_MODEL=gpt-4.1-mini
LLM_BASE_URL=
```

Notes:

- The app reads `.env` automatically at startup through [app/core/settings.py](/c:/Users/LeeJR/OneDrive%20-%20Nanyang%20Technological%20University/School/Year%204%20Semester%202/CZ4052%20Cloud%20Computing-ML-LP/SaaS/app/core/settings.py).
- `.env` is ignored by git, so your secrets stay out of source control.
- `GET /config/status` confirms whether Telegram and LLM credentials were detected.
- Real Telegram webhook support is available at `POST /telegram/webhook`.
- Register the webhook with `POST /telegram/webhook/register` after your public URL is reachable from Telegram.
- `POST /telegram/mock` is still available for local testing without Telegram.

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
