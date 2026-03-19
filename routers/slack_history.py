"""
routers/slack_history.py — GET /slack/history
Returns recent Slack posts recorded in the DB for the authenticated user.
"""

from fastapi import APIRouter, Depends, Query

from middleware.auth import get_current_user_id
from db import get_slack_history

router = APIRouter(prefix="/slack", tags=["slack"])


@router.get("/history")
async def slack_history(
    limit: int = Query(default=20, ge=1, le=100),
    user_id: str = Depends(get_current_user_id),
):
    posts = get_slack_history(user_id, limit=limit)
    return {"posts": posts}
