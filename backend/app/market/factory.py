"""Pick the market data source from the environment."""

from __future__ import annotations

import logging
import os

from .cache import PriceCache
from .interface import MarketDataSource
from .massive_client import MassiveDataSource
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
        return default


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """Massive/Polygon key non-empty → Massive; otherwise the GBM simulator. Returns it unstarted.

    The key is MASSIVE_API_KEY, or POLYGON_API_KEY as an alias (Massive is Polygon.io's
    new name; the same key works). MASSIVE_API_KEY wins if both are set.

    Optional env vars (a bad value is logged and replaced by the default):
      MASSIVE_POLL_INTERVAL  live-mode poll seconds (default 15, minimum 1)
      SIM_EVENT_PROBABILITY  per-ticker, per-tick jump chance (default 0.001)
      SIM_SEED               integer → deterministic simulator
    """
    api_key = (
        os.environ.get("MASSIVE_API_KEY", "").strip()
        or os.environ.get("POLYGON_API_KEY", "").strip()
    )
    if api_key:
        interval = max(_env_float("MASSIVE_POLL_INTERVAL", 15.0), 1.0)
        logger.info("Market data source: Massive/Polygon API (poll every %.0fs)", interval)
        return MassiveDataSource(api_key=api_key, price_cache=price_cache, poll_interval=interval)

    seed_raw = os.environ.get("SIM_SEED", "").strip()
    seed = int(seed_raw) if seed_raw.isdigit() else None
    if seed_raw and seed is None:
        logger.warning("Ignoring invalid SIM_SEED=%r; using a random seed", seed_raw)
    logger.info("Market data source: GBM simulator (seed=%s)", seed)
    return SimulatorDataSource(
        price_cache=price_cache,
        event_probability=_env_float("SIM_EVENT_PROBABILITY", 0.001),
        seed=seed,
    )
