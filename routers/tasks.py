"""
routers/tasks.py — GET /tasks
Returns Notion tasks for the authenticated user.
"""

import json
from fastapi import APIRouter, Depends, HTTPException

from middleware.auth import get_current_user_id
from db import get_user_integrations
from tools import build_tools

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/debug")
async def debug_tasks(
    user_id: str = Depends(get_current_user_id),
):
    import requests as req
    integrations = get_user_integrations(user_id)
    if not integrations or not integrations.get("notion_access_token"):
        raise HTTPException(status_code=400, detail="Notion not connected.")

    token = integrations["notion_access_token"]
    ds_id = integrations.get("notion_datasource_id", "")
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    out: dict = {"saved_database_id": ds_id}

    # 1. Search for all databases accessible to this integration
    try:
        r = req.post(
            "https://api.notion.com/v1/search",
            headers=headers,
            json={"filter": {"value": "database", "property": "object"}, "page_size": 20},
            timeout=10,
        )
        search_data = r.json()
        out["accessible_databases"] = [
            {
                "id": db["id"],
                "title": (db.get("title") or [{}])[0].get("plain_text", "(no title)"),
            }
            for db in search_data.get("results", [])
        ]
    except Exception as e:
        out["search_error"] = str(e)

    # 2. If a database ID is saved, try to query it
    if ds_id:
        try:
            r = req.post(
                f"https://api.notion.com/v1/data_sources/{ds_id}/query",
                headers=headers,
                json={"page_size": 1},
                timeout=10,
            )
            data = r.json()
            if r.ok:
                results = data.get("results", [])
                out["db_query_count"] = len(results)
                if results:
                    out["first_page_props"] = list(results[0].get("properties", {}).keys())
            else:
                out["db_query_error"] = data.get("message") or r.text
        except Exception as e:
            out["db_query_error"] = str(e)

    return out


@router.get("")
async def get_tasks(
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

    raw = tool_functions["get_notion_tasks"]()
    try:
        tasks = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=502, detail=raw)

    return {"tasks": tasks}
