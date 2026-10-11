"""Market data subsystem for FinAlly. Import everything from here.

Public API:
    PriceUpdate               - Immutable price snapshot (to_dict() is the wire format)
    PriceCache                - Thread-safe in-memory latest-price store
    MarketDataSource          - Abstract interface for data providers
    MarketDataService         - Owns the tracked set (watchlist ∪ held positions)
    create_market_data_source - Factory: Massive if MASSIVE_API_KEY is set, else simulator
    create_stream_router      - FastAPI router for GET /api/stream/prices (SSE)
    normalize_ticker          - Strip/uppercase/validate a symbol (InvalidTickerError)
    DEFAULT_TICKERS           - The ten default watchlist symbols
"""

from .cache import PriceCache
from .factory import create_market_data_source
from .interface import MarketDataSource
from .models import PriceUpdate
from .service import MarketDataService
from .stream import create_stream_router
from .tickers import DEFAULT_TICKERS, InvalidTickerError, normalize_ticker

__all__ = [
    "DEFAULT_TICKERS",
    "InvalidTickerError",
    "MarketDataService",
    "MarketDataSource",
    "PriceCache",
    "PriceUpdate",
    "create_market_data_source",
    "create_stream_router",
    "normalize_ticker",
]
