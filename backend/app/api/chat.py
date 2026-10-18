"""Chat endpoints: POST /api/chat, GET /api/chat/history (team contract §5)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.llm import service

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4000

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: Any = None


@router.post("")
async def post_chat(body: ChatRequest) -> Any:
    message = body.message.strip() if isinstance(body.message, str) else ""
    if not message:
        return JSONResponse(status_code=400, content={"error": "Message must not be empty"})
    if len(message) > MAX_MESSAGE_LENGTH:
        return JSONResponse(
            status_code=400,
            content={"error": f"Message is too long (max {MAX_MESSAGE_LENGTH} characters)"},
        )
    try:
        return await service.handle_chat_message(message)
    except Exception:
        # Contract §3: chat never returns 500. Only reached if storage itself fails.
        logger.exception("Chat request failed")
        return {
            "id": str(uuid.uuid4()),
            "role": "assistant",
            "content": service.GENERIC_ERROR_MESSAGE,
            "actions": service.empty_actions(),
            "created_at": datetime.now(UTC).isoformat(),
        }


@router.get("/history")
async def get_chat_history() -> dict[str, Any]:
    return {"messages": service.get_history()}
