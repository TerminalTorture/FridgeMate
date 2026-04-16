# FridgeMate

FridgeMate is a FastAPI-based household assistant that helps you decide what to cook, what to buy, and when to act, using your fridge state plus adaptive behavior over time.

It is built to work in real chat workflows (Telegram and HTTP) while remaining testable and observable like an engineering system.

## Why FridgeMate Is Different

1. Multi-agent architecture with clear roles.
   Inventory, recipe, grocery, nutrition, behavior, and utility concerns are split into dedicated agents and orchestrated through one runtime.
2. SQL-first operational memory.
   Inventory, meal history, sessions, diagnostics, user preferences, and runtime events are persisted in SQL rather than scattered ad hoc files.
3. Deterministic historical seeding.
   You can boot an empty database with realistic six-month synthetic behavior for demos, grading, and reproducible testing.
4. Adaptive decision engine and heartbeat nudges.
   The system can proactively check in around meal times and suggest low-effort actions based on temporary user states.
5. Safer action workflow.
   Destructive or irreversible actions are gated behind explicit confirmation IDs.
6. Built-in observability.
   Debug logs, diagnostics, runtime state snapshots, and optional trace files let you inspect what happened and why.

## What You Can Do

- Track inventory with batch-level details and expiry pressure.
- Suggest and cook recipes from available ingredients.
- Search and import online recipes through the LLM-backed discovery service.
- Generate grocery drafts and confirm orders.
- Manage user preferences and temporary context states (for example, tired or not_home).
- Run FridgeMate through HTTP, Telegram polling, or Telegram webhook mode.

## Tech Stack

- Python 3.10+
- FastAPI + Uvicorn
- SQLAlchemy + Alembic
- SQLite by default (portable to other SQL backends)

---

## Quick Start (Local)

### 1. Create and activate a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 3. Configure environment variables

Copy `.env.example` to `.env`, then set at least:

- `TELEGRAM_BOT_TOKEN`
- `LLM_API_KEY`
- `LLM_MODEL`

`LLM_BASE_URL` is optional (useful for proxy or gateway setups).

### 4. Initialize schema

```bash
alembic upgrade head
```

### 5. Start the API

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### 6. Smoke-check the system

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/config/status
curl http://127.0.0.1:8000/debug/integrations
```

If these return valid JSON, the app is up and configured.

---

## Deployment Modes

FridgeMate can run in three practical modes:

1. API-only mode
   - Condition: Telegram token not configured.
   - Result: HTTP API works, no Telegram messaging worker.
2. Telegram polling mode
   - Condition: `TELEGRAM_BOT_TOKEN` is set and `TELEGRAM_CHAT_ID` is empty.
   - Result: Background polling runner starts and consumes Telegram updates.
3. Telegram webhook mode
   - Condition: `TELEGRAM_BOT_TOKEN` is set and `TELEGRAM_CHAT_ID` is set.
   - Result: App expects incoming webhook calls on `/telegram/webhook`.

For webhook mode, also configure:

- `TELEGRAM_WEBHOOK_SECRET`
- `TELEGRAM_WEBHOOK_URL`

Then register webhook:

```bash
python scripts/register_telegram_webhook.py
```

Check webhook status:

```bash
python scripts/check_telegram_webhook.py
```

---

## Configuration Reference

Key settings in `.env`:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_WEBHOOK_SECRET=
TELEGRAM_WEBHOOK_URL=

LLM_API_KEY=
LLM_MODEL=gpt-5.4-mini
LLM_BASE_URL=

DATABASE_URL=sqlite:///data/fridgemate.db
SQL_ECHO=0

SEED_HISTORY_ON_STARTUP=1
SEED_HISTORY_DAYS=180
SEED_HISTORY_SEED=4052

LOG_STORE_PATH=data/runtime_logs.json
TRACE_MODE=0
TRACE_LOG_PATH=data/traces

LLM_GATEWAY_POLICY_PATH=config/llm_gateway_policy.json
```

Notes:

- `SEED_HISTORY_ON_STARTUP=1` means an empty database auto-seeds realistic historical usage.
- `TRACE_MODE=1` enables per-request trace artifacts under `TRACE_LOG_PATH`.
- `MEMORY_STORE_PATH` is kept for legacy compatibility and one-time import behavior.

---

## Core API Checkpoints

### Health and configuration

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/config/status
```

### Runtime visibility

```bash
curl http://127.0.0.1:8000/debug/logs
curl http://127.0.0.1:8000/debug/integrations
curl http://127.0.0.1:8000/runtime/state
curl http://127.0.0.1:8000/diagnostics
curl http://127.0.0.1:8000/memory
```

### Inventory and recipes

```bash
curl http://127.0.0.1:8000/inventory
curl http://127.0.0.1:8000/inventory/batches
curl http://127.0.0.1:8000/recipes
curl http://127.0.0.1:8000/recipes/suggestions
```

### Decision engine and heartbeat

```bash
curl http://127.0.0.1:8000/decision/state/demo-user
curl -X POST http://127.0.0.1:8000/decision/run/demo-user
curl -X POST "http://127.0.0.1:8000/heartbeat/check?user_id=demo-user"
curl http://127.0.0.1:8000/heartbeat/settings/demo-user
```

### Telegram simulation without real webhook traffic

```bash
curl -X POST http://127.0.0.1:8000/telegram/mock \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"demo-user\",\"message\":\"what can I cook tonight?\"}"
```

---

## MCP Tool Surface and Confirmation Flow

List all tools:

```bash
curl http://127.0.0.1:8000/mcp/tools
```

Call a tool:

```bash
curl -X POST http://127.0.0.1:8000/mcp/call \
  -H "Content-Type: application/json" \
  -d "{\"tool_name\":\"get_inventory_batches\",\"arguments\":{}}"
```

Protected operations return `requires_confirmation: true` first. Complete or cancel with:

```bash
curl -X POST http://127.0.0.1:8000/confirmations/<confirmation_id>/confirm
curl -X POST http://127.0.0.1:8000/confirmations/<confirmation_id>/cancel
```

---

## Troubleshooting

### 1. Uvicorn fails at startup

Check:

- `python -m pip install -r requirements.txt` completed successfully
- `.env` exists and required keys are populated
- `alembic upgrade head` has run

Then inspect:

```bash
curl http://127.0.0.1:8000/config/status
curl http://127.0.0.1:8000/debug/logs
```

### 2. Telegram is not responding

Check mode first:

```bash
curl http://127.0.0.1:8000/config/status
```

- If mode is polling, run:

```bash
python scripts/poll_telegram.py
```

- If mode is webhook, verify registration and secret:

```bash
python scripts/check_telegram_webhook.py
curl http://127.0.0.1:8000/telegram/webhook/info
```

### 3. LLM-backed features fail (online recipe search or richer replies)

Check:

- `LLM_API_KEY`
- `LLM_MODEL`
- `LLM_BASE_URL` connectivity (if used)

Inspect:

```bash
curl http://127.0.0.1:8000/debug/integrations
curl http://127.0.0.1:8000/debug/logs
```

### 4. Inventory or decisions look wrong

Inspect current operational state:

```bash
curl http://127.0.0.1:8000/memory
curl http://127.0.0.1:8000/runtime/state
curl http://127.0.0.1:8000/diagnostics
curl http://127.0.0.1:8000/decision/state/demo-user
```

Common cause: no realistic history in a fresh database. Fix by seeding:

```bash
curl -X POST http://127.0.0.1:8000/seed/history \
  -H "Content-Type: application/json" \
  -d "{\"days\":180,\"seed\":4052}"
```

### 5. You cannot find why a response happened

Enable trace mode and restart app:

```env
TRACE_MODE=1
```

Then inspect generated trace artifacts under `data/traces` and logs in `data/runtime_logs.json`.

---

## Server Deployment Notes (VPS/Cloud)

A practical baseline:

1. Set environment variables securely (do not commit secrets).
2. Use a process manager (for example systemd or supervisor) for `uvicorn`.
3. Put a reverse proxy in front (Nginx/Caddy) if exposing public webhook endpoints.
4. Use HTTPS for Telegram webhook mode.
5. Restrict or protect debug routes in production (`/debug/logs`, `/debug/integrations`).
6. Keep `DATABASE_URL` on durable storage.

Suggested startup command:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## Testing

Run all tests:

```bash
python -m unittest discover -s tests
```

Targeted checks:

```bash
python -m unittest tests.test_mcp_tools
python -m unittest tests.test_sql_storage
python -m compileall app tests
```

---

## Suggested First Run Path

1. Start app locally.
2. Verify `/health`, `/config/status`, `/debug/integrations`.
3. Call `/telegram/mock` with a demo message.
4. Inspect `/memory` and `/diagnostics`.
5. Test one MCP call and one confirmation-protected action.

If these work, your deployment is in a healthy baseline state.
