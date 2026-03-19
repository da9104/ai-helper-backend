"""
main.py — FastAPI application entry point
"""

import os
import logging
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from routers import agent, tasks, slack_history, oauth

logger = logging.getLogger(__name__)

app = FastAPI(title="AI Helper API", version="1.0.0")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """
    Catch-all for unhandled exceptions so they return a proper JSON 500
    *inside* CORSMiddleware — ensuring CORS headers are always present.
    """
    logger.exception("Unhandled exception on %s %s", request.method, request.url)
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {exc}"},
    )

# CORS — allow one or more frontend origins (comma-separated in FRONTEND_URL)
_frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3000")
allowed_origins = [o.strip() for o in _frontend_url.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(agent.router)
app.include_router(tasks.router)
app.include_router(slack_history.router)
app.include_router(oauth.router)


@app.get("/")
async def root():
    return {"status": "ok", "service": "ai-helper-api"}
