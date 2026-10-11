"""Tests for ticker normalisation (design doc §6)."""

import pytest

from app.market import DEFAULT_TICKERS, InvalidTickerError, normalize_ticker


@pytest.mark.parametrize(
    "raw,expected",
    [
        (" aapl ", "AAPL"),
        ("AAPL", "AAPL"),
        ("BRK.B", "BRK.B"),
        ("bf-b", "BF-B"),
        ("V", "V"),
        ("ABCDEFGHIJ", "ABCDEFGHIJ"),  # exactly 10 chars
    ],
)
def test_normalize_ok(raw, expected):
    assert normalize_ticker(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "   ", None, "TOOLONGTICKER", "ABCDEFGHIJK", "$$$", "1ABC", "AA PL", ".A"]
)
def test_normalize_bad(raw):
    with pytest.raises(InvalidTickerError):
        normalize_ticker(raw)


def test_invalid_ticker_error_is_value_error():
    assert issubclass(InvalidTickerError, ValueError)
    with pytest.raises(InvalidTickerError, match="Invalid ticker symbol: '\\$\\$\\$'"):
        normalize_ticker("$$$")


def test_default_tickers_are_normalised_and_unique():
    assert len(DEFAULT_TICKERS) == 10
    assert len(set(DEFAULT_TICKERS)) == 10
    assert all(normalize_ticker(t) == t for t in DEFAULT_TICKERS)
