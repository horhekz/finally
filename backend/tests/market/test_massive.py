"""Tests for MassiveDataSource.

The SDK client is mocked, but everything it returns is a REAL massive model
(TickerSnapshot / GroupedDailyAgg built with from_dict). A MagicMock snapshot
accepts any attribute, which is how the old `last_trade.timestamp` bug hid.
"""

import asyncio
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from massive.exceptions import BadResponse
from massive.rest.models import GroupedDailyAgg, TickerSnapshot

from app.market.cache import PriceCache
from app.market.massive_client import (
    SNAPSHOT_CHUNK,
    MassiveDataSource,
    parse_snapshot,
    to_epoch_seconds,
)
from app.market.tickers import InvalidTickerError

AAPL_JSON = {  # the sample from MASSIVE_API.md §4.1
    "ticker": "AAPL",
    "updated": 1605195918306274000,
    "day": {"o": 119.62, "h": 120.53, "l": 118.81, "c": 120.4229, "v": 28727868},
    "prevDay": {"o": 117.19, "h": 119.63, "l": 116.44, "c": 119.49, "v": 110597265},
    "min": {"c": 120.4201, "t": 1684428720000},
    "lastTrade": {"p": 120.47, "s": 236, "x": 10, "t": 1605195918306274000},
}

MSFT_JSON = {
    "ticker": "MSFT",
    "updated": 1605195918306274000,
    "prevDay": {"c": 420.0},
    "lastTrade": {"p": 425.5, "t": 1605195918306274000},
}

NOT_AUTHORIZED = '{"status":"NOT_AUTHORIZED","message":"You are not entitled to this data."}'


def snap(data: dict) -> TickerSnapshot:
    return TickerSnapshot.from_dict(data)


def make_source(tickers, client):
    cache = PriceCache()
    src = MassiveDataSource(api_key="test", price_cache=cache, poll_interval=60)
    src._tickers = list(tickers)
    src._client = client  # the SDK client is mocked; its RESULTS are real models
    return src, cache


# --- Pure helpers -------------------------------------------------------------


def test_parse_snapshot_uses_last_trade_and_ns_timestamp():
    price, ts, ref = parse_snapshot(snap(AAPL_JSON))
    assert price == 120.47
    assert abs(ts - 1605195918.306274) < 1e-3  # ns → s, not ms
    assert ref == 119.49


def test_parse_snapshot_falls_back_when_no_trade():
    data = {k: v for k, v in AAPL_JSON.items() if k != "lastTrade"}
    data["day"] = {"o": 0, "c": 0}  # pre-market: zeroed day bar
    price, ts, _ = parse_snapshot(snap(data))
    assert price == 120.4201  # min.close
    assert abs(ts - 1605195918.306274) < 1e-3  # falls back to `updated` (ns)


def test_parse_snapshot_falls_back_to_prev_day_close():
    data = {"ticker": "AAPL", "day": {"c": 0}, "prevDay": {"c": 119.49}}
    price, ts, ref = parse_snapshot(snap(data))
    assert price == 119.49 and ref == 119.49
    assert ts is None


def test_parse_snapshot_uses_minute_timestamp_in_ms():
    data = {"ticker": "AAPL", "min": {"c": 120.0, "t": 1684428720000}}
    price, ts, ref = parse_snapshot(snap(data))
    assert price == 120.0
    assert ts == 1684428720.0
    assert ref is None


def test_parse_snapshot_returns_none_without_any_price():
    assert parse_snapshot(snap({"ticker": "ZZZZ", "day": {"c": 0}})) is None


def test_to_epoch_seconds_units():
    assert to_epoch_seconds(1_700_000_000) == 1_700_000_000
    assert to_epoch_seconds(1_700_000_000_000) == 1_700_000_000
    assert to_epoch_seconds(1_700_000_000_000_000) == 1_700_000_000
    assert to_epoch_seconds(1_700_000_000_000_000_000) == 1_700_000_000
    assert to_epoch_seconds(None) is None
    assert to_epoch_seconds(0) is None


# --- Live polling -------------------------------------------------------------


async def test_live_poll_updates_cache():
    client = MagicMock()
    client.get_snapshot_all.return_value = [snap(AAPL_JSON)]
    src, cache = make_source(["AAPL", "ZZZZ"], client)
    await src._poll_once()
    upd = cache.get("AAPL")
    assert upd.price == 120.47 and upd.reference_price == 119.49
    assert round(upd.day_change_percent, 2) == 0.82
    assert abs(upd.timestamp - 1605195918.306274) < 1e-3
    assert "ZZZZ" not in cache  # unknown symbol: never priced


async def test_live_poll_uses_one_call_for_all_tickers():
    client = MagicMock()
    client.get_snapshot_all.return_value = [snap(AAPL_JSON), snap(MSFT_JSON)]
    src, cache = make_source(["AAPL", "MSFT"], client)
    await src._poll_once()
    assert client.get_snapshot_all.call_count == 1
    assert client.get_snapshot_all.call_args.kwargs["tickers"] == ["AAPL", "MSFT"]
    assert cache.get_price("MSFT") == 425.5


async def test_snapshot_requests_are_chunked():
    client = MagicMock()
    client.get_snapshot_all.return_value = []
    tickers = [f"T{i}" for i in range(SNAPSHOT_CHUNK + 5)]
    src, _ = make_source(tickers, client)
    await src._poll_once()
    assert client.get_snapshot_all.call_count == 2
    sizes = [len(c.kwargs["tickers"]) for c in client.get_snapshot_all.call_args_list]
    assert sizes == [SNAPSHOT_CHUNK, 5]


async def test_result_for_ticker_removed_mid_poll_is_dropped():
    src, cache = make_source(["AAPL"], MagicMock())

    def fetch(tickers):
        src._tickers.remove("AAPL")  # simulate a removal while the thread is in flight
        return [snap(AAPL_JSON)]

    with patch.object(src, "_fetch_snapshots", side_effect=fetch):
        await src._poll_once()
    assert "AAPL" not in cache


async def test_empty_tickers_skips_poll():
    client = MagicMock()
    src, _ = make_source([], client)
    await src._poll_once()
    client.get_snapshot_all.assert_not_called()


async def test_network_error_does_not_raise():
    client = MagicMock()
    client.get_snapshot_all.side_effect = ConnectionError("network down")
    src, cache = make_source(["AAPL"], client)
    await src._poll_once()  # must not raise
    assert cache.get_price("AAPL") is None
    assert src.mode == "live"


async def test_other_errors_keep_last_price():
    client = MagicMock()
    client.get_snapshot_all.return_value = [snap(AAPL_JSON)]
    src, cache = make_source(["AAPL"], client)
    await src._poll_once()
    client.get_snapshot_all.side_effect = BadResponse('{"status":"ERROR"}')
    await src._poll_once()  # must not raise
    assert src.mode == "live"
    assert cache.get_price("AAPL") == 120.47


# --- End-of-day fallback ------------------------------------------------------


async def test_403_switches_to_eod_mode():
    client = MagicMock()
    client.get_snapshot_all.side_effect = BadResponse(NOT_AUTHORIZED)
    client.get_grouped_daily_aggs.side_effect = [
        [GroupedDailyAgg.from_dict({"T": "AAPL", "o": 230, "c": 231.4, "t": 1759708800000})],
        [GroupedDailyAgg.from_dict({"T": "AAPL", "o": 228, "c": 229.0, "t": 1759622400000})],
    ] + [[]] * 10
    src, cache = make_source(["AAPL"], client)
    await src._poll_once()
    assert src.mode == "eod"
    assert cache.get("AAPL").price == 231.4
    assert cache.get("AAPL").reference_price == 229.0
    assert cache.get("AAPL").timestamp == 1759708800.0
    assert src._current_interval() == MassiveDataSource.EOD_INTERVAL


async def test_eod_reference_falls_back_to_open_without_previous_day():
    client = MagicMock()
    client.get_snapshot_all.side_effect = BadResponse(NOT_AUTHORIZED)
    client.get_grouped_daily_aggs.side_effect = [
        [GroupedDailyAgg.from_dict({"T": "AAPL", "o": 230, "c": 231.4})],
    ] + [[]] * 10
    src, cache = make_source(["AAPL"], client)
    await src._poll_once()
    assert cache.get("AAPL").reference_price == 230.0


def test_eod_fetch_skips_weekends_and_stops_after_two_days():
    client = MagicMock()
    client.get_grouped_daily_aggs.return_value = [
        GroupedDailyAgg.from_dict({"T": "AAPL", "o": 230, "c": 231.4})
    ]
    src, _ = make_source(["AAPL"], client)
    src._fetch_last_two_days()
    dates = [c.kwargs["date"] for c in client.get_grouped_daily_aggs.call_args_list]
    assert len(dates) == 2
    assert all(date.fromisoformat(d).weekday() < 5 for d in dates)


async def test_eod_error_after_403_does_not_raise():
    client = MagicMock()
    client.get_snapshot_all.side_effect = BadResponse(NOT_AUTHORIZED)
    client.get_grouped_daily_aggs.side_effect = ConnectionError("down")
    src, cache = make_source(["AAPL"], client)
    await src._poll_once()  # must not raise
    assert src.mode == "eod"
    assert "AAPL" not in cache


async def test_eod_add_ticker_prices_from_memory():
    src, cache = make_source(["AAPL"], MagicMock())
    src._mode = "eod"
    src._eod_latest = {"MSFT": GroupedDailyAgg.from_dict({"T": "MSFT", "o": 430, "c": 437.1})}
    await src.add_ticker("msft")
    assert cache.get_price("MSFT") == 437.1
    assert not src._wake.is_set()  # no extra request in EOD mode


async def test_eod_add_unknown_ticker_stays_unpriced():
    src, cache = make_source(["AAPL"], MagicMock())
    src._mode = "eod"
    await src.add_ticker("ZZZZ")
    assert "ZZZZ" in src.get_tickers() and "ZZZZ" not in cache


# --- Ticker management --------------------------------------------------------


async def test_add_ticker_normalises_and_wakes_poller_in_live_mode():
    src, _ = make_source([], MagicMock())
    await src.add_ticker("  aapl ")
    assert src.get_tickers() == ["AAPL"]
    assert src._wake.is_set()


async def test_add_ticker_twice_is_noop():
    src, _ = make_source(["AAPL"], MagicMock())
    await src.add_ticker("AAPL")
    assert src.get_tickers() == ["AAPL"]
    assert not src._wake.is_set()


async def test_add_invalid_ticker_raises():
    src, _ = make_source([], MagicMock())
    with pytest.raises(InvalidTickerError):
        await src.add_ticker("$$$")


async def test_remove_ticker_evicts_from_cache():
    src, cache = make_source(["AAPL", "GOOGL"], MagicMock())
    cache.update("AAPL", 190.0)
    await src.remove_ticker("aapl")
    assert src.get_tickers() == ["GOOGL"]
    assert "AAPL" not in cache


async def test_remove_unknown_ticker_is_noop():
    src, _ = make_source(["AAPL"], MagicMock())
    await src.remove_ticker("MSFT")
    assert src.get_tickers() == ["AAPL"]


# --- Lifecycle ----------------------------------------------------------------


async def test_start_polls_immediately_and_normalises():
    with patch("app.market.massive_client.RESTClient") as rest:
        rest.return_value.get_snapshot_all.return_value = [snap(AAPL_JSON)]
        cache = PriceCache()
        src = MassiveDataSource(api_key="k", price_cache=cache, poll_interval=60)
        await src.start(["aapl", "AAPL"])
    try:
        assert src.get_tickers() == ["AAPL"]
        assert cache.get_price("AAPL") == 120.47  # warm before start() returns
        assert src._task is not None and not src._task.done()
    finally:
        await src.stop()
    assert src._task is None


async def test_wake_triggers_immediate_poll():
    with patch("app.market.massive_client.RESTClient") as rest:
        rest.return_value.get_snapshot_all.side_effect = lambda **kw: [
            snap(AAPL_JSON if t == "AAPL" else MSFT_JSON) for t in kw["tickers"]
        ]
        cache = PriceCache()
        src = MassiveDataSource(api_key="k", price_cache=cache, poll_interval=60)
        await src.start(["AAPL"])
        try:
            await src.add_ticker("MSFT")
            for _ in range(50):
                if "MSFT" in cache:
                    break
                await asyncio.sleep(0.02)
            assert cache.get_price("MSFT") == 425.5  # well before the 60 s interval
        finally:
            await src.stop()


async def test_stop_is_idempotent():
    src = MassiveDataSource(api_key="k", price_cache=PriceCache())
    await src.stop()
    await src.stop()
