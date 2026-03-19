"""
tools.py — Slack + Notion integration (per-user token version)

Instead of reading tokens from env vars at import time, this module
exposes a factory function `build_tools(...)` that accepts per-user
credentials and returns (TOOL_FUNCTIONS, TOOL_SPECS).
"""

import json
import requests
from notion_client import Client
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _parse_page(page: dict) -> dict:
    """Extract all relevant fields from a Notion page safely."""
    props = page.get("properties", {})
    title_parts = props.get("Title", {}).get("title", [])
    desc_parts  = props.get("Description", {}).get("rich_text", [])
    status_obj  = props.get("Status", {}).get("status") or {}
    category    = [c["name"] for c in props.get("Category", {}).get("multi_select", [])]
    return {
        "id":          page["id"],
        "title":       title_parts[0]["plain_text"] if title_parts else "(제목 없음)",
        "status":      status_obj.get("name", "알 수 없음"),
        "assignee":    props.get("Created by", {}).get("created_by", {}).get("name", "미배정"),
        "category":    category,
        "description": desc_parts[0]["plain_text"] if desc_parts else "",
        "date":        (props.get("Date", {}).get("date") or {}).get("start", ""),
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

    def get_notion_tasks(status: str = "In progress") -> str:
        try:
            response = notion.databases.query(
                database_id=ds_id,
                filter={"property": "Status", "status": {"equals": status}},
            )
            results = response.get("results", [])
            tasks = [_parse_page(p) for p in results]
            return json.dumps(tasks, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"Notion 오류: {e}"

    def search_notion_tasks(
        category: str = "",
        keyword: str = "",
        date_on_or_after: str = "",
        date_on_or_before: str = "",
    ) -> str:
        filters = []
        if category:
            filters.append({"property": "Category", "multi_select": {"contains": category}})
        if keyword:
            filters.append({"property": "Title",       "title":     {"contains": keyword}})
            filters.append({"property": "Description", "rich_text": {"contains": keyword}})
        if date_on_or_after:
            filters.append({"property": "Date", "date": {"on_or_after": date_on_or_after}})
        if date_on_or_before:
            filters.append({"property": "Date", "date": {"on_or_before": date_on_or_before}})

        if not filters:
            query_args = {}
        elif len(filters) == 1:
            query_args = {"filter": filters[0]}
        else:
            query_args = {"filter": {"and": filters}}
        response = notion.databases.query(database_id=ds_id, **query_args)
        tasks = [_parse_page(p) for p in response.get("results", [])]
        return json.dumps(tasks, ensure_ascii=False, indent=2)

    def update_notion_task_status(page_id: str, new_status: str) -> str:
        try:
            notion.pages.update(
                page_id=page_id,
                properties={"Status": {"status": {"name": new_status}}},
            )
            return f"상태 업데이트 완료: {new_status}"
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
        "slack_post_message":        slack_post_message,
        "slack_read_messages":       slack_read_messages,
    }

    tool_specs = [
        {
            "type": "function",
            "function": {
                "name": "get_notion_tasks",
                "description": "Notion DB에서 상태별 작업 목록을 가져옵니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["All", "Not started", "In progress", "To-do", "Done", "Complete"],
                            "description": "가져올 작업 상태. 'All'은 전체 목록을 반환합니다.",
                        }
                    },
                    "required": ["status"],
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
