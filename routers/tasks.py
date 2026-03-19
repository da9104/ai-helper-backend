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

    out = {"saved_database_id": ds_id}

    # 1. Retrieve the database schema
    try:
        db = notion.databases.retrieve(database_id=ds_id)
        out["db_title"] = (db.get("title") or [{}])[0].get("plain_text", "(no title)")
        out["db_properties"] = list(db.get("properties", {}).keys())
    except Exception as e:
        out["db_retrieve_error"] = str(e)

    # 2. Query without filter — get first page's raw properties
    try:
        resp = notion.databases.query(database_id=ds_id, page_size=1)
        results = resp.get("results", [])
        if results:
            p = results[0]
            out["first_page_id"] = p["id"]
            out["first_page_props"] = list(p.get("properties", {}).keys())
            # Show title value of first page
            props = p.get("properties", {})
            for key in ("Title", "Name", "이름", "제목"):
                if key in props:
                    parts = props[key].get("title", [])
                    out["first_page_title"] = parts[0]["plain_text"] if parts else "(empty)"
                    break
        else:
            out["db_query_count"] = 0
    except Exception as e:
        out["db_query_error"] = str(e)

    return out


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
