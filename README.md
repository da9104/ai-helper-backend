# AI Helper — Backend

FastAPI backend that powers the AI Helper dashboard. Connects Notion and Slack via OAuth, runs an OpenAI function-calling agent, and persists data in Supabase.

---

## Tech Stack

| Layer | Library | Version |
|-------|---------|---------|
| Web framework | FastAPI | 0.115.0 |
| ASGI server | Uvicorn | 0.30.6 |
| AI agent | OpenAI (gpt-4o-mini) | 1.51.0 |
| Database | Supabase (PostgreSQL) | 2.9.0 |
| Notion integration | notion-client | 2.2.1 |
| Slack integration | slack-sdk | 3.33.0 |
| HTTP client | httpx | 0.27.2 |
| Auth | PyJWT + cryptography | 2.9.0 / 43.0.1 |

---

## Project Structure

```
backend/
├── main.py              # App entry point, CORS, router registration
├── agent.py             # OpenAI agentic loop
├── tools.py             # Notion + Slack tool factory (build_tools)
├── db.py                # Supabase client and data access helpers
├── routers/
│   ├── agent.py         # POST /agent/run
│   ├── tasks.py         # GET /tasks, GET /tasks/debug
│   ├── slack_history.py # GET /slack/history
│   └── oauth.py         # Notion + Slack OAuth flows
├── middleware/
│   └── auth.py          # JWT verification, user_id extraction
├── requirements.txt
├── .env.example
├── Procfile             # Railway / Heroku process definition
└── railway.toml         # Railway deployment config
```

---

## Local Setup

### 1. Create a virtual environment

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

```env
# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
SUPABASE_JWT_SECRET=your-jwt-secret

# OpenAI
OPENAI_API_KEY=sk-...

# Notion OAuth app
NOTION_CLIENT_ID=your-notion-client-id
NOTION_CLIENT_SECRET=your-notion-client-secret

# Slack OAuth app
SLACK_CLIENT_ID=your-slack-client-id
SLACK_CLIENT_SECRET=your-slack-client-secret

# URLs (must match OAuth redirect URIs)
BACKEND_URL=http://localhost:8000
FRONTEND_URL=http://localhost:3000
```

### 4. Run the server

```bash
uvicorn main:app --reload
```

API available at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

---

## API Endpoints

### Agent
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/agent/run` | Run the AI agent with a user message |

**Request body:**
```json
{ "message": "Show me tasks in progress", "use_history": true }
```

**Response:**
```json
{ "response": "현재 진행 중인 작업은..." }
```

---

### Tasks (Notion)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/tasks` | Fetch all tasks from the user's Notion database |
| `GET` | `/tasks/debug` | Inspect Notion integration state (accessible databases, query count) |

---

### Slack
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/slack/history` | Fetch recent Slack posts logged in the DB (`?limit=20`) |

---

### OAuth
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/oauth/notion/init` | Start Notion OAuth — returns a short-lived redirect URL |
| `GET` | `/oauth/notion` | Consume one-time code → redirect to Notion consent page |
| `GET` | `/oauth/notion/callback` | Exchange auth code for token, save to DB |
| `POST` | `/oauth/slack/init` | Start Slack OAuth — returns a short-lived redirect URL |
| `GET` | `/oauth/slack` | Consume one-time code → redirect to Slack consent page |
| `GET` | `/oauth/slack/callback` | Exchange auth code for token, save to DB |
| `GET` | `/oauth/status` | Returns `{ notion: bool, slack: bool, notion_database_id: str }` |
| `PATCH` | `/oauth/notion/database` | Save the user's Notion database ID |
| `PATCH` | `/oauth/notion/apikey` | Save a Notion internal integration API key |

---

## Authentication

Every request (except OAuth callbacks) requires a valid Supabase JWT in the `Authorization` header:

```
Authorization: Bearer <supabase-access-token>
```

`middleware/auth.py` verifies the JWT using `SUPABASE_JWT_SECRET` and extracts the `user_id` (`sub` claim). This is injected into route handlers via `Depends(get_current_user_id)`.

---

## OAuth Security Model

The OAuth flows use a **two-step redirect pattern** to keep the user's JWT out of browser URLs and server logs:

1. **`POST /oauth/*/init`** — Frontend sends the JWT in the `Authorization` header. Backend validates it and issues a short-lived one-time code (random 32-byte token, 60 s TTL).
2. **`GET /oauth/*?code=...`** — Browser is redirected to this URL with only the opaque code. Backend consumes the code, retrieves `user_id`, and redirects to the provider's consent page with a CSRF `state` parameter.
3. **`GET /oauth/*/callback`** — Provider redirects back. Backend exchanges the auth code for a token and saves it to the DB. Browser is redirected to `/settings?notion=connected` (or `slack=connected`).

---

## AI Agent

`agent.py` implements a stateless OpenAI function-calling loop:

- **Model:** `gpt-4o-mini`
- **Language:** Responds in Korean
- **Max iterations:** 10
- **History:** Conversation turns are persisted in Supabase and reloaded per request

The agent has access to 6 tools (built per-user in `tools.py`):

| Tool | Description |
|------|-------------|
| `get_notion_tasks` | Fetch all entries from the user's Notion database |
| `search_notion_tasks` | Filter by category, keyword, or date range |
| `update_notion_task_status` | Change the status of an existing Notion page |
| `create_notion_task` | Create a new page in the Notion database |
| `slack_post_message` | Post a formatted message to a Slack channel |
| `slack_read_messages` | Read recent messages from a Slack channel |

### Per-User Tool Factory

`build_tools(notion_token, datasource_id, slack_token, slack_channel, on_slack_post)` creates closures scoped to a single user's credentials. No global tokens are used at runtime.

```python
tool_functions, tool_specs = build_tools(
    notion_token=...,
    datasource_id=...,
    slack_token=...,
    on_slack_post=lambda channel, title, body: save_slack_post(user_id, ...),
)
```

---

## Database (Supabase)

`db.py` uses the service role key for all server-side DB operations.

### Tables

| Table | Purpose |
|-------|---------|
| `user_integrations` | Per-user Notion + Slack tokens and config |
| `agent_conversations` | Conversation history (role + content per turn) |
| `slack_post_history` | Log of every Slack message posted by the agent |

### Key columns in `user_integrations`

| Column | Description |
|--------|-------------|
| `notion_access_token` | Notion OAuth access token |
| `notion_datasource_id` | Notion database ID (set manually in Settings) |
| `notion_workspace_id` | Notion workspace ID (from OAuth response) |
| `slack_bot_token` | Slack bot token (`xoxb-...`) |
| `slack_channel` | Default Slack channel (from webhook, default: `all-todo-list`) |

---

## Deployment

The backend is configured for **Railway** deployment.

`railway.toml`:
```toml
[build]
builder = "nixpacks"

[deploy]
startCommand = "uvicorn main:app --host 0.0.0.0 --port $PORT"
restartPolicyType = "on_failure"
```

Set all environment variables from `.env.example` in the Railway project settings. Ensure `BACKEND_URL` and `FRONTEND_URL` match the deployed domains, and that the OAuth redirect URIs registered with Notion and Slack match:
- Notion: `https://<BACKEND_URL>/oauth/notion/callback`
- Slack: `https://<BACKEND_URL>/oauth/slack/callback`

### Slack OAuth Scopes Required

```
chat:write
channels:history
channels:read
```
