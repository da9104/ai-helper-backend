"""
routers/tasks.py — GET /tasks
Returns Notion tasks for the authenticated user.
"""

import json
from fastapi import APIRouter, Depends, HTTPException, Query

from middleware.auth import get_current_user_id
from db import get_user_integrations
from tools import build_tools

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/debug")
async def debug_tasks(
    user_id: str = Depends(get_current_user_id),
):
    from notion_client import Client
    integrations = get_user_integrations(user_id)
    if not integrations or not integrations.get("notion_access_token"):
        raise HTTPException(status_code=400, detail="Notion not connected.")
    if not integrations.get("notion_datasource_id"):
        raise HTTPException(status_code=400, detail="No database ID set.")

    notion = Client(auth=integrations["notion_access_token"])
    ds_id = integrations["notion_datasource_id"]

    # First: search all accessible pages/databases with this token
    try:
        search_resp = notion.search(filter={"value": "database", "property": "object"})
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Notion search error: {e}")

    accessible = [
        {"id": r["id"], "title": (r.get("title") or [{}])[0].get("plain_text", "(no title)")}
        for r in search_resp.get("results", [])
    ]

    # Then try querying the saved database ID
    db_result = None
    if ds_id:
        try:
            response = notion.databases.query(database_id=ds_id)
            results = response.get("results", [])
            db_result = {
                "count": len(results),
                "property_names": list(results[0].get("properties", {}).keys()) if results else [],
            }
        except Exception as e:
            db_result = {"error": str(e)}

    return {
        "saved_database_id": ds_id,
        "accessible_databases": accessible,
        "query_result": db_result,
    }


@router.get("")
async def get_tasks(
    status: str = Query(default="In progress"),
    user_id: str = Depends(get_current_user_id),
):
    integrations = get_user_integrations(user_id)
    if not integrations or not integrations.get("notion_access_token"):
        raise HTTPException(status_code=400, detail="Notion not connected.")
    if not integrations.get("notion_datasource_id"):
        raise HTTPException(status_code=400, detail="Notion database ID not set. Go to Settings and save your Notion Database ID.")

    tool_functions, _ = build_tools(
        notion_token=integrations["notion_access_token"],
        datasource_id=integrations["notion_datasource_id"],
        slack_token=integrations.get("slack_bot_token", ""),
        slack_channel=integrations.get("slack_channel", "all-todo-list"),
    )

    raw = tool_functions["get_notion_tasks"](status=status)
    try:
        tasks = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=502, detail=raw)

    return {"tasks": tasks}
