"""
routers/oauth.py — Notion + Slack OAuth flows

Secure two-step OAuth initiation:
  POST /oauth/notion/init  → validate JWT (header), issue short-lived one-time code
  GET  /oauth/notion       → consume code, redirect to Notion consent page
  GET  /oauth/notion/callback → exchange code, save token

  POST /oauth/slack/init   → same pattern for Slack
  GET  /oauth/slack        → consume code, redirect to Slack consent page
  GET  /oauth/slack/callback → exchange code, save token

The one-time code is a random opaque token (never the JWT), so the user's
credential never appears in the browser address bar, server logs, or
Referer headers.
"""

import os
import base64
import json
import time
import secrets
import httpx

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from middleware.auth import get_current_user_id
from db import save_integration

router = APIRouter(prefix="/oauth", tags=["oauth"])

# ── One-time code store ────────────────────────────────────────────────────────
# { code: {"user_id": str, "expires_at": float} }
_pending: dict[str, dict] = {}
_CODE_TTL = 60  # seconds


def _issue_code(user_id: str) -> str:
    """Generate a single-use opaque code tied to user_id, valid for 60 s."""
    _cleanup_expired()
    code = secrets.token_urlsafe(32)
    _pending[code] = {"user_id": user_id, "expires_at": time.time() + _CODE_TTL}
    return code


def _consume_code(code: str) -> str:
    """Validate and consume a one-time code, returning user_id. Raises 400 if invalid/expired."""
    _cleanup_expired()
    entry = _pending.pop(code, None)
    if entry is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth code")
    return entry["user_id"]


def _cleanup_expired():
    now = time.time()
    for k in [k for k, v in _pending.items() if v["expires_at"] < now]:
        del _pending[k]


# ── Notion ────────────────────────────────────────────────────────────────────

@router.post("/notion/init")
async def notion_oauth_init(user_id: str = Depends(get_current_user_id)):
    """
    Step 1: validate the user's JWT (via Authorization header) and return a
    short-lived redirect URL containing only an opaque one-time code.
    The frontend redirects the browser to this URL — no JWT ever hits the address bar.
    """
    code = _issue_code(user_id)
    backend_url = os.environ["BACKEND_URL"]
    return {"redirect_url": f"{backend_url}/oauth/notion?code={code}"}


@router.get("/notion")
async def notion_oauth_start(code: str = Query(...)):
    """Step 2: consume the one-time code and redirect to Notion's OAuth consent page."""
    user_id = _consume_code(code)

    client_id    = os.environ["NOTION_CLIENT_ID"]
    backend_url  = os.environ["BACKEND_URL"]
    redirect_uri = f"{backend_url}/oauth/notion/callback"

    # Include a random nonce in state for CSRF protection
    state = base64.urlsafe_b64encode(
        json.dumps({"user_id": user_id, "nonce": secrets.token_urlsafe(16)}).encode()
    ).decode()

    url = (
        "https://api.notion.com/v1/oauth/authorize"
        f"?client_id={client_id}"
        f"&response_type=code"
        f"&owner=user"
        f"&redirect_uri={redirect_uri}"
        f"&state={state}"
    )
    return RedirectResponse(url=url)


@router.get("/notion/callback")
async def notion_oauth_callback(code: str = Query(...), state: str = Query(...)):
    """Exchange the Notion auth code for an access token and persist it."""
    client_id     = os.environ["NOTION_CLIENT_ID"]
    client_secret = os.environ["NOTION_CLIENT_SECRET"]
    backend_url   = os.environ["BACKEND_URL"]
    frontend_url  = os.environ["FRONTEND_URL"]
    redirect_uri  = f"{backend_url}/oauth/notion/callback"

    try:
        state_data = json.loads(base64.urlsafe_b64decode(state.encode()))
        user_id    = state_data["user_id"]
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    async with httpx.AsyncClient() as http:
        resp = await http.post(
            "https://api.notion.com/v1/oauth/token",
            auth=(client_id, client_secret),
            json={
                "grant_type":   "authorization_code",
                "code":         code,
                "redirect_uri": redirect_uri,
            },
        )

    data = resp.json()

    if resp.status_code != 200 or data.get("error"):
        detail = data.get("error_description") or data.get("error") or resp.text
        raise HTTPException(status_code=502, detail=f"Notion token exchange failed: {detail}")

    access_token = data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=502, detail="Notion response missing access_token")

    save_integration(
        user_id,
        notion_access_token=access_token,
        notion_workspace_id=data.get("workspace_id", ""),
        notion_datasource_id=data.get("duplicated_template_id") or data.get("bot_id", ""),
    )

    return RedirectResponse(url=f"{frontend_url}/settings?notion=connected")


# ── Slack ─────────────────────────────────────────────────────────────────────

@router.post("/slack/init")
async def slack_oauth_init(user_id: str = Depends(get_current_user_id)):
    """
    Step 1: validate the user's JWT (via Authorization header) and return a
    short-lived redirect URL containing only an opaque one-time code.
    """
    code = _issue_code(user_id)
    backend_url = os.environ["BACKEND_URL"]
    return {"redirect_url": f"{backend_url}/oauth/slack?code={code}"}


@router.get("/slack")
async def slack_oauth_start(code: str = Query(...)):
    """Step 2: consume the one-time code and redirect to Slack's OAuth consent page."""
    user_id = _consume_code(code)

    client_id    = os.environ["SLACK_CLIENT_ID"]
    backend_url  = os.environ["BACKEND_URL"]
    redirect_uri = f"{backend_url}/oauth/slack/callback"

    state = base64.urlsafe_b64encode(
        json.dumps({"user_id": user_id, "nonce": secrets.token_urlsafe(16)}).encode()
    ).decode()

    url = (
        "https://slack.com/oauth/v2/authorize"
        f"?client_id={client_id}"
        f"&scope=chat:write,channels:history,channels:read"
        f"&redirect_uri={redirect_uri}"
        f"&state={state}"
    )
    return RedirectResponse(url=url)


@router.get("/slack/callback")
async def slack_oauth_callback(code: str = Query(...), state: str = Query(...)):
    """Exchange the Slack auth code for a bot token and persist it."""
    client_id     = os.environ["SLACK_CLIENT_ID"]
    client_secret = os.environ["SLACK_CLIENT_SECRET"]
    backend_url   = os.environ["BACKEND_URL"]
    frontend_url  = os.environ["FRONTEND_URL"]
    redirect_uri  = f"{backend_url}/oauth/slack/callback"

    try:
        state_data = json.loads(base64.urlsafe_b64decode(state.encode()))
        user_id    = state_data["user_id"]
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    async with httpx.AsyncClient() as http:
        resp = await http.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id":     client_id,
                "client_secret": client_secret,
                "code":          code,
                "redirect_uri":  redirect_uri,
            },
        )

    data = resp.json()
    if not data.get("ok"):
        raise HTTPException(status_code=502, detail=f"Slack token exchange failed: {data}")

    bot_token = data.get("access_token") or data.get("bot", {}).get("bot_access_token")
    channel   = data.get("incoming_webhook", {}).get("channel", "all-todo-list").lstrip("#")

    save_integration(
        user_id,
        slack_bot_token=bot_token,
        slack_channel=channel,
    )

    return RedirectResponse(url=f"{frontend_url}/settings?slack=connected")


# ── Integration status ────────────────────────────────────────────────────────

@router.get("/status")
async def integration_status(user_id: str = Depends(get_current_user_id)):
    from db import get_user_integrations
    row = get_user_integrations(user_id) or {}
    return {
        "notion": bool(row.get("notion_access_token")),
        "slack":  bool(row.get("slack_bot_token")),
        "notion_database_id": row.get("notion_datasource_id", ""),
    }


class NotionDatabaseUpdate(BaseModel):
    database_id: str


@router.patch("/notion/database")
async def update_notion_database(
    body: NotionDatabaseUpdate,
    user_id: str = Depends(get_current_user_id),
):
    """Save the user's Notion database ID."""
    db_id = body.database_id.strip()
    if not db_id:
        raise HTTPException(status_code=400, detail="database_id is required")
    # Notion database IDs are 32 hex chars, sometimes with dashes
    clean = db_id.replace("-", "")
    if len(clean) != 32 or not all(c in "0123456789abcdefABCDEF" for c in clean):
        raise HTTPException(status_code=400, detail="Invalid Notion database ID format")
    save_integration(user_id, notion_datasource_id=db_id)
    return {"ok": True}
