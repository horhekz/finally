"""FinAlly chat assistant: LiteLLM -> OpenRouter (Cerebras) with structured outputs."""

from .models import ChatResponse, TradeRequest, WatchlistChange, parse_response
from .service import get_history, handle_chat_message

__all__ = [
    "ChatResponse",
    "TradeRequest",
    "WatchlistChange",
    "get_history",
    "handle_chat_message",
    "parse_response",
]
