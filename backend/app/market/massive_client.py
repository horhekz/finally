"""Massive (formerly Polygon.io) REST poller for real market data."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from massive import RESTClient
from massive.exceptions import BadResponse
from massive.rest.models import GroupedDailyAgg, SnapshotMarketType, TickerSnapshot

from .cache import PriceCache
from .interface import MarketDataSource
from .tickers import normalize_ticker

logger = logging.getLogger(__name__)

NEW_YORK = ZoneInfo("America/New_York")
SNAPSHOT_CHUNK = 100  # tickers per snapshot call; keeps the URL a sane length
EOD_LOOKBACK_DAYS = 7  # how far back to search for two trading days


def to_epoch_seconds(ts: int | float | None) -> float | None:
    """Normalise a Massive timestamp (s, ms, µs or ns) to float seconds by magnitude."""
    if not ts:
        return None
    ts = float(ts)
    if ts > 1e17:  # nanoseconds (~1.7e18 today)
        return ts / 1e9
    if ts > 1e14:  # microseconds
        return ts / 1e6
    if ts > 1e11:  # milliseconds (~1.7e12)
        return ts / 1e3
    return ts


def _first_positive(*values: float | None) -> float | None:
    """First value that is a positive number. Pre-market bars are often 0."""
    for v in values:
        if v is not None and v > 0:
            return float(v)
    return None


def parse_snapshot(snap: TickerSnapshot) -> tuple[float, float | None, float | None] | None:
    """Extract (price, timestamp_s, reference_price) from one snapshot, or None."""
    last_trade, minute, day, prev_day = snap.last_trade, snap.min, snap.day, snap.prev_day
    price = _first_positive(
        last_trade.price if last_trade else None,
        minute.close if minute else None,
        day.close if day else None,
        prev_day.close if prev_day else None,
    )
    if price is None:
        return None
    timestamp = to_epoch_seconds(
        (last_trade.sip_timestamp if last_trade else None)
        or snap.updated
        or (minute.timestamp if minute else None)
    )
    reference = _first_positive(prev_day.close if prev_day else None)
    return price, timestamp, reference


class MassiveDataSource(MarketDataSource):
    """Polls Massive for every tracked ticker and writes into the PriceCache.

    Modes (auto-detected):
      live — get_snapshot_all every `poll_interval` s (paid plans; real-time or 15-min delayed)
      eod  — get_grouped_daily_aggs every EOD_INTERVAL s (free plan; snapshots return 403)
    """

    EOD_INTERVAL = 1800.0
    # After a failed EOD fetch (e.g. a 429 at startup) retry soon instead of waiting
    # 30 min with no prices. 60 s lets the free plan's 5-calls/minute window reset.
    EOD_RETRY_INTERVAL = 60.0

    def __init__(self, api_key: str, price_cache: PriceCache, poll_interval: float = 15.0) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._client: RESTClient | None = None
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()  # set by add_ticker → poll now instead of waiting
        self._mode: Literal["live", "eod"] = "live"
        # Last grouped-daily result, so add_ticker can price instantly in EOD mode
        self._eod_latest: dict[str, GroupedDailyAgg] = {}
        self._eod_previous: dict[str, GroupedDailyAgg] = {}
        self._eod_ok = False  # last EOD fetch succeeded → wait the full EOD_INTERVAL

    @property
    def mode(self) -> str:
        return self._mode

    # --- MarketDataSource ---

    async def start(
        self, tickers: list[str], initial_prices: dict[str, float] | None = None
    ) -> None:
        self._client = RESTClient(api_key=self._api_key)
        self._tickers = list(dict.fromkeys(normalize_ticker(t) for t in tickers))
        await self._poll_once()  # warm the cache before start() returns
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info(
            "Massive poller started: %d tickers, mode=%s, interval=%.0fs",
            len(self._tickers), self._mode, self._current_interval(),
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._client = None
        logger.info("Massive poller stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker in self._tickers:
            return
        self._tickers.append(ticker)
        if self._mode == "eod":
            self._apply_eod(ticker)  # data already in memory; the next fetch is 30 min away
        else:
            self._wake.set()  # live: poll right away (paid plans have no per-minute cap)
        logger.info("Massive: added %s", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)
        logger.info("Massive: removed %s", ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- Polling ---

    def _current_interval(self) -> float:
        if self._mode == "live":
            return self._interval
        return self.EOD_INTERVAL if self._eod_ok else self.EOD_RETRY_INTERVAL

    async def _poll_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._current_interval())
            except TimeoutError:
                pass
            self._wake.clear()
            await self._poll_once()

    async def _poll_once(self) -> None:
        """One cycle. Never raises: errors are logged and the last prices stay cached."""
        if not self._tickers or self._client is None:
            return
        tickers = list(self._tickers)  # copy: the worker thread must not see mutations
        try:
            if self._mode == "live":
                await self._poll_snapshots(tickers)
            else:
                await self._safe_poll_eod(tickers)
        except BadResponse as e:
            if self._mode == "live" and "NOT_AUTHORIZED" in str(e):
                logger.warning("Massive plan has no snapshot access; switching to end-of-day prices")
                self._mode = "eod"
                await self._safe_poll_eod(tickers)
            else:
                logger.error("Massive poll failed: %s", e)
        except Exception as e:  # network errors, timeouts, unexpected payloads
            logger.error("Massive poll failed: %s", e)

    async def _poll_snapshots(self, tickers: list[str]) -> None:
        snapshots = await asyncio.to_thread(self._fetch_snapshots, tickers)
        updated = 0
        for snap in snapshots:
            parsed = parse_snapshot(snap)
            if snap.ticker not in self._tickers or parsed is None:
                continue  # removed mid-poll, or no usable price
            price, timestamp, reference = parsed
            self._cache.update(snap.ticker, price, timestamp=timestamp, reference_price=reference)
            updated += 1
        logger.debug("Massive snapshot: %d/%d tickers priced", updated, len(tickers))

    def _fetch_snapshots(self, tickers: list[str]) -> list[TickerSnapshot]:
        """Blocking SDK call(s). Runs in a worker thread."""
        assert self._client is not None
        out: list[TickerSnapshot] = []
        for i in range(0, len(tickers), SNAPSHOT_CHUNK):
            out.extend(
                self._client.get_snapshot_all(
                    # Pass the plain string: the SDK f-strings the enum into the URL
                    # ("SnapshotMarketType.STOCKS") and gets locale "global" → 404.
                    market_type=SnapshotMarketType.STOCKS.value,
                    tickers=tickers[i : i + SNAPSHOT_CHUNK],
                )
            )
        return out

    async def _safe_poll_eod(self, tickers: list[str]) -> None:
        try:
            await self._poll_eod(tickers)
            self._eod_ok = bool(self._eod_latest)
        except Exception as e:
            self._eod_ok = False
            logger.error("Massive end-of-day poll failed: %s", e)
        if not self._eod_ok:
            logger.warning("Massive end-of-day prices unavailable; retrying in %.0fs",
                           self.EOD_RETRY_INTERVAL)

    async def _poll_eod(self, tickers: list[str]) -> None:
        self._eod_latest, self._eod_previous = await asyncio.to_thread(self._fetch_last_two_days)
        for ticker in tickers:
            if ticker in self._tickers:
                self._apply_eod(ticker)

    def _apply_eod(self, ticker: str) -> None:
        bar = self._eod_latest.get(ticker)
        if bar is None or not bar.close:
            return  # unknown symbol: never priced, UI shows "—"
        prev = self._eod_previous.get(ticker)
        reference = _first_positive(prev.close if prev else None, bar.open)
        self._cache.update(
            ticker, bar.close, timestamp=to_epoch_seconds(bar.timestamp), reference_price=reference
        )

    def _fetch_last_two_days(self) -> tuple[dict[str, GroupedDailyAgg], dict[str, GroupedDailyAgg]]:
        """Walk back from today (New York) to the two most recent days with data."""
        assert self._client is not None
        found: list[dict[str, GroupedDailyAgg]] = []
        day: date = datetime.now(NEW_YORK).date()
        for _ in range(EOD_LOOKBACK_DAYS):
            if day.weekday() < 5:  # skip weekends without spending a request
                bars = self._client.get_grouped_daily_aggs(date=day.isoformat(), adjusted=True)
                if bars:
                    found.append({b.ticker: b for b in bars if b.ticker})
                    if len(found) == 2:
                        break
            day -= timedelta(days=1)
        found += [{}, {}]
        return found[0], found[1]
