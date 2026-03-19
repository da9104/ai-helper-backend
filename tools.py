"""
tools.py — Slack + Notion integration (per-user token version)

Instead of reading tokens from env vars at import time, this module
exposes a factory function `build_tools(...)` that accepts per-user
credentials and returns (TOOL_FUNCTIONS, TOOL_SPECS).
"""

import json
from typing import Any
from notion_client import Client
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _safe_dict(d: Any, key: str) -> dict:
    if isinstance(d, dict):
        val = d.get(key)
        if isinstance(val, dict):
            return val
    return {}

def _safe_list(d: Any, key: str) -> list:
    if isinstance(d, dict):
        val = d.get(key)
        if isinstance(val, list):
            return val
    return []

def _parse_page(page: dict) -> dict:
    """Extract all relevant fields from a Notion page safely without linter errors."""
    props = page.get("properties")
    if not isinstance(props, dict):
        props = {}
    
    # Safely get title
    title_parts = []
    for key in ("Title", "Name", "이름", "제목"):
        prop_val = _safe_dict(props, key)
        if prop_val:
            val = prop_val.get("title")
            if isinstance(val, list):
                title_parts = val
                break
    title = title_parts[0].get("plain_text", "(제목 없음)") if title_parts and isinstance(title_parts[0], dict) else "(제목 없음)"

    # Safely get description (rich_text or title)
    desc_prop = _safe_dict(props, "Description")
    desc_parts = _safe_list(desc_prop, "rich_text")
    if not desc_parts:
        desc_parts = _safe_list(desc_prop, "title")
    description = desc_parts[0].get("plain_text", "") if desc_parts and isinstance(desc_parts[0], dict) else ""

    # Safely get status (status or select)
    status_prop = _safe_dict(props, "Status")
    status_obj = _safe_dict(status_prop, "status")
    if not status_obj:
        status_obj = _safe_dict(status_prop, "select")
    status = str(status_obj.get("name", "알 수 없음")) if status_obj else "알 수 없음"

    # Safely get assignee
    assign_prop = _safe_dict(props, "Created by") or _safe_dict(props, "Assignee") or _safe_dict(props, "생성자")
    assignee = _safe_dict(assign_prop, "created_by").get("name")
    if not assignee:
        people = _safe_list(assign_prop, "people")
        if people and isinstance(people[0], dict):
            assignee = people[0].get("name")
    if not assignee:
        assignee = _safe_dict(assign_prop, "select").get("name")
    if not assignee:
        assignee = "미배정"
    assignee = str(assignee) if assignee else "미배정"

    # Safely get category
    cat_prop = _safe_dict(props, "Category")
    multi_select = _safe_list(cat_prop, "multi_select")
    category = [str(c.get("name", "")) for c in multi_select if isinstance(c, dict)]
    if not category:
        sel = _safe_dict(cat_prop, "select").get("name")
        if sel:
            category = [str(sel)]

    # Safely get date
    date_prop = _safe_dict(props, "Date") or _safe_dict(props, "날짜")
    date_val = _safe_dict(date_prop, "date").get("start", "")
    date_val = str(date_val) if date_val else ""

    return {
        "id":          str(page.get("id", "")),
        "title":       title,
        "status":      status,
        "assignee":    assignee,
        "category":    category,
        "description": description,
        "date":        date_val,
    }


# ══════════════════════════════════════════════════════════════════════════════
# TOOL FACTORY
# ══════════════════════════════════════════════════════════════════════════════

def build_tools(
    notion_token: str,
    datasource_id: str,
    slack_token: str,
    slack_channel: str = "all-todo-list",
    *,
    on_slack_post=None,   # optional callback(channel, title, body) for DB logging
) -> tuple[dict, list]:
    """
    Build per-user tool functions and OpenAI tool specs.

    Args:
        notion_token:   Notion OAuth access token
        datasource_id:  Notion database / data source ID
        slack_token:    Slack bot token (xoxb-...)
        slack_channel:  Default Slack channel name (without #)
        on_slack_post:  Optional async callback invoked after a successful post.
                        Signature: on_slack_post(channel, title, body)

    Returns:
        (TOOL_FUNCTIONS dict, TOOL_SPECS list)
    """
    notion = Client(auth=notion_token)
    slack  = WebClient(token=slack_token)
    ds_id  = datasource_id

    # ── Notion tools ──────────────────────────────────────────────────────────

    def get_notion_tasks() -> str:
        try:
            response = notion.databases.query(database_id=ds_id)
            tasks = [_parse_page(p) for p in response["results"]]
            return json.dumps(tasks, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"Notion 오류: {e}"

    def search_notion_tasks(
        category: str = "",
        keyword: str = "",
        date_on_or_after: str = "",
        date_on_or_before: str = "",
    ) -> str:
        try:
            filters: list[dict[str, Any]] = []
            if category:
                filters.append({"property": "Category", "multi_select": {"contains": category}})
            if keyword:
                filters.append({
                    "or": [
                        {"property": "Title", "title": {"contains": keyword}},
                        {"property": "Description", "rich_text": {"contains": keyword}},
                    ]
                })
            if date_on_or_after:
                filters.append({"property": "Date", "date": {"on_or_after": date_on_or_after}})
            if date_on_or_before:
                filters.append({"property": "Date", "date": {"on_or_before": date_on_or_before}})

            query_args: dict = {"database_id": ds_id}
            if filters:
                query_args["filter"] = {"and": filters}
            response = notion.databases.query(**query_args)
            tasks = [_parse_page(p) for p in response.get("results", [])]
            return json.dumps(tasks, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"Notion 오류: {e}"

    def _notion_page_update(page_id: str, body: dict) -> dict:
        return notion.pages.update(page_id=page_id, **body)

    def _notion_page_create(body: dict) -> dict:
        return notion.pages.create(**body)

    def update_notion_task_status(page_id: str, new_status: str) -> str:
        try:
            _notion_page_update(page_id, {
                "properties": {"Status": {"status": {"name": new_status}}}
            })
            return f"상태 업데이트 완료: {new_status}"
        except Exception as e:
            return f"Notion 오류: {e}"

    def create_notion_task(title: str, content: str, status: str = "Not started") -> str:
        """
        Notion DB에 새 작업을 추가합니다.
        Returns: 생성된 페이지 URL
        """
        try:
            page = _notion_page_create({
                "parent": {"database_id": ds_id},
                "properties": {
                    "Title": {"title": [{"text": {"content": title}}]},
                    "Status": {"status": {"name": status}},
                },
                "children": [{
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": content}}]
                    },
                }],
            })
            return f"생성 완료 → {page['url']}"
        except Exception as e:
            return f"Notion 오류: {e}"

    # ── Slack tools ───────────────────────────────────────────────────────────

    def slack_post_message(channel: str, title: str, body: str, color: str = "#4A90E2") -> str:
        try:
            slack.chat_postMessage(
                channel=f"#{channel}",
                text=title,
                attachments=[{
                    "color": color,
                    "blocks": [
                        {"type": "header", "text": {"type": "plain_text", "text": title}},
                        {"type": "section", "text": {"type": "mrkdwn", "text": body}},
                        {"type": "context", "elements": [
                            {"type": "mrkdwn", "text": "🤖 AI Agent가 자동으로 발송했습니다"}
                        ]},
                    ],
                }],
            )
            if on_slack_post:
                on_slack_post(channel, title, body)
            return f"#{channel} 전송 완료"
        except SlackApiError as e:
            return f"Slack 오류: {e.response['error']}"

    def slack_read_messages(channel_id: str, limit: int = 5) -> str:
        try:
            response = slack.conversations_history(channel=channel_id, limit=limit)
            msgs = [m["text"] for m in response["messages"] if m.get("text")]
            return json.dumps(msgs, ensure_ascii=False, indent=2)
        except SlackApiError as e:
            return f"Slack 오류: {e.response['error']}"

    # ── Registry ──────────────────────────────────────────────────────────────

    tool_functions = {
        "get_notion_tasks":          get_notion_tasks,
        "search_notion_tasks":       search_notion_tasks,
        "update_notion_task_status": update_notion_task_status,
        "create_notion_task":        create_notion_task,
        "slack_post_message":        slack_post_message,
        "slack_read_messages":       slack_read_messages,
    }

    tool_specs = [
        {
            "type": "function",
            "function": {
                "name": "get_notion_tasks",
                "description": "Notion DB에서 전체 작업 목록을 가져옵니다.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_notion_tasks",
                "description": "카테고리, 키워드(제목/설명), 날짜로 Notion 작업을 검색합니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category":          {"type": "string"},
                        "keyword":           {"type": "string"},
                        "date_on_or_after":  {"type": "string"},
                        "date_on_or_before": {"type": "string"},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "update_notion_task_status",
                "description": "기존 Notion 작업의 상태를 변경합니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id":    {"type": "string"},
                        "new_status": {
                            "type": "string",
                            "enum": ["Not started", "In progress", "To-do", "Done", "Complete"],
                        },
                    },
                    "required": ["page_id", "new_status"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_notion_task",
                "description": "새로운 Notion 작업을 생성합니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title":   {"type": "string", "description": "작업 제목 (필수)"},
                        "content": {"type": "string", "description": "작업 상세 내용 (설명)"},
                        "status": {
                            "type": "string",
                            "enum": ["Not started", "In progress", "To-do", "Done", "Complete"],
                            "description": "초기 작업 상태. 기본값은 'Not started'."
                        },
                    },
                    "required": ["title", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "slack_post_message",
                "description": "Slack 채널에 제목과 본문이 있는 메시지를 보냅니다",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channel": {"type": "string"},
                        "title":   {"type": "string"},
                        "body":    {"type": "string"},
                        "color":   {"type": "string"},
                    },
                    "required": ["channel", "title", "body"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "slack_read_messages",
                "description": "Slack 채널의 최근 메시지를 읽습니다",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channel_id": {"type": "string"},
                        "limit":      {"type": "integer"},
                    },
                    "required": ["channel_id"],
                },
            },
        },
    ]

    return tool_functions, tool_specs
