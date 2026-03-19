"""
db.py — Supabase client and data access helpers
Uses the service role key for server-side operations.
"""

import os
from datetime import datetime, timezone
from fastapi import HTTPException
from supabase import create_client, Client

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        _client = create_client(url, key)
    return _client


def get_user_integrations(user_id: str) -> dict | None:
    """Return the user's stored Notion + Slack tokens, or None if not found."""
    client = get_client()
    result = (
        client.table("user_integrations")
        .select("*")
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    return result.data


def save_integration(user_id: str, **fields) -> None:
    """Upsert integration fields for a user."""
    client = get_client()
    payload = {
        "user_id": user_id,
        **fields,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    result = client.table("user_integrations").upsert(payload, on_conflict="user_id").execute()
    if result.data is None:
        raise HTTPException(status_code=500, detail=f"Failed to save integration for user {user_id}")


def save_conversation_turn(user_id: str, role: str, content: str) -> None:
    client = get_client()
    client.table("agent_conversations").insert(
        {"user_id": user_id, "role": role, "content": content}
    ).execute()


def get_conversation_history(user_id: str, limit: int = 20) -> list[dict]:
    client = get_client()
    result = (
        client.table("agent_conversations")
        .select("role, content")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    rows = result.data or []
    # reverse so oldest is first
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def save_slack_post(user_id: str, channel: str, title: str, body: str) -> None:
    client = get_client()
    client.table("slack_post_history").insert(
        {"user_id": user_id, "channel": channel, "title": title, "body": body}
    ).execute()


def get_slack_history(user_id: str, limit: int = 20) -> list[dict]:
    client = get_client()
    result = (
        client.table("slack_post_history")
        .select("*")
        .eq("user_id", user_id)
        .order("posted_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []
