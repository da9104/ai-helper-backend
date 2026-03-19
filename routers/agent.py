"""
routers/agent.py — POST /agent/run
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from middleware.auth import get_current_user_id
from db import get_user_integrations, save_conversation_turn, get_conversation_history
from tools import build_tools
from agent import run_agent as _run_agent

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentRequest(BaseModel):
    message: str
    use_history: bool = True   # whether to include persisted history


class AgentResponse(BaseModel):
    response: str


@router.post("/run", response_model=AgentResponse)
async def run(body: AgentRequest, user_id: str = Depends(get_current_user_id)):
    integrations = get_user_integrations(user_id)
    if not integrations:
        raise HTTPException(
            status_code=400,
            detail="No integrations configured. Please connect Notion and Slack first.",
        )

    notion_token  = integrations.get("notion_access_token")
    datasource_id = integrations.get("notion_datasource_id")
    slack_token   = integrations.get("slack_bot_token")
    slack_channel = integrations.get("slack_channel", "all-todo-list")

    if not notion_token or not datasource_id:
        raise HTTPException(status_code=400, detail="Notion integration not configured.")
    if not slack_token:
        raise HTTPException(status_code=400, detail="Slack integration not configured.")

    # Lazy import to avoid circular deps
    from db import save_slack_post

    def on_slack_post(channel: str, title: str, body: str):
        save_slack_post(user_id, channel, title, body)

    tool_functions, tool_specs = build_tools(
        notion_token=notion_token,
        datasource_id=datasource_id,
        slack_token=slack_token,
        slack_channel=slack_channel,
        on_slack_post=on_slack_post,
    )

    history = get_conversation_history(user_id) if body.use_history else None

    response_text = _run_agent(
        user_message=body.message,
        tool_functions=tool_functions,
        tool_specs=tool_specs,
        history=history,
    )

    # Persist the new turns
    save_conversation_turn(user_id, "user", body.message)
    save_conversation_turn(user_id, "assistant", response_text)

    return AgentResponse(response=response_text)
