"""Tests for GBMSimulator."""

import numpy as np

from app.market.seed_prices import DEFAULT_PARAMS, SEED_PRICES
from app.market.simulator import GBMSimulator


class TestGBMSimulator:
    """Unit tests for the GBM price simulator."""

    def test_step_returns_all_tickers(self):
        """Test that step() returns prices for all tickers."""
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        result = sim.step()
        assert set(result.keys()) == {"AAPL", "GOOGL"}

    def test_prices_are_positive(self):
        """GBM prices can never go negative (exp() is always positive)."""
        sim = GBMSimulator(tickers=["AAPL"])
        for _ in range(10_000):
            prices = sim.step()
            assert prices["AAPL"] > 0

    def test_initial_prices_match_seeds(self):
        """Test that initial prices match seed prices."""
        sim = GBMSimulator(tickers=["AAPL"])
        # Before any step, price should be the seed price
        assert sim.get_price("AAPL") == SEED_PRICES["AAPL"]

    def test_add_ticker(self):
        """Test adding a ticker dynamically."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.add_ticker("TSLA")
        result = sim.step()
        assert "TSLA" in result

    def test_remove_ticker(self):
        """Test removing a ticker."""
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        sim.remove_ticker("GOOGL")
        result = sim.step()
        assert "GOOGL" not in result
        assert "AAPL" in result

    def test_add_duplicate_is_noop(self):
        """Test that adding a duplicate ticker is a no-op."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.add_ticker("AAPL")
        assert len(sim._tickers) == 1

    def test_remove_nonexistent_is_noop(self):
        """Test that removing a non-existent ticker is a no-op."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.remove_ticker("NOPE")  # Should not raise

    def test_unknown_ticker_gets_random_seed_price(self):
        """Test that unknown tickers get random seed prices."""
        sim = GBMSimulator(tickers=["ZZZZ"])
        price = sim.get_price("ZZZZ")
        assert price is not None
        assert 50.0 <= price <= 300.0

    def test_empty_step(self):
        """Test stepping with no tickers."""
        sim = GBMSimulator(tickers=[])
        result = sim.step()
        assert result == {}

    def test_prices_change_over_time(self):
        """After many steps, prices should have drifted from their seeds."""
        sim = GBMSimulator(tickers=["AAPL"])
        initial_price = sim.get_price("AAPL")

        for _ in range(1000):
            sim.step()

        final_price = sim.get_price("AAPL")
        # Price should have changed (extremely unlikely to be exactly the seed)
        assert final_price != initial_price

    def test_cholesky_rebuilds_on_add(self):
        """Test that Cholesky matrix is rebuilt when tickers are added."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim._cholesky is None  # Only 1 ticker, no correlation matrix
        sim.add_ticker("GOOGL")
        assert sim._cholesky is not None  # Now 2 tickers, matrix exists

    def test_cholesky_none_with_one_ticker(self):
        """Test that Cholesky is None with only one ticker."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim._cholesky is None

    def test_get_price_returns_none_for_unknown(self):
        """Test that get_price returns None for unknown ticker."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim.get_price("UNKNOWN") is None

    def test_pairwise_correlation_tech_stocks(self):
        """Test that tech stocks have high correlation."""
        corr = GBMSimulator._pairwise_correlation("AAPL", "GOOGL")
        assert corr == 0.6

    def test_pairwise_correlation_finance_stocks(self):
        """Test that finance stocks have moderate correlation."""
        corr = GBMSimulator._pairwise_correlation("JPM", "V")
        assert corr == 0.5

    def test_pairwise_correlation_tsla(self):
        """Test that TSLA has lower correlation with everything."""
        corr = GBMSimulator._pairwise_correlation("TSLA", "AAPL")
        assert corr == 0.3
        corr = GBMSimulator._pairwise_correlation("TSLA", "JPM")
        assert corr == 0.3

    def test_pairwise_correlation_cross_sector(self):
        """Test cross-sector correlation."""
        corr = GBMSimulator._pairwise_correlation("AAPL", "JPM")
        assert corr == 0.3

    def test_default_dt_is_reasonable(self):
        """Test that default dt is a reasonable small value."""
        assert 0 < GBMSimulator.DEFAULT_DT < 0.0001

    def test_prices_rounded_to_two_decimals(self):
        """Test that prices are rounded to 2 decimal places."""
        sim = GBMSimulator(tickers=["AAPL"])
        result = sim.step()
        price_str = str(result["AAPL"])
        # Check that we have at most 2 decimal places
        if "." in price_str:
            decimal_part = price_str.split(".")[1]
            assert len(decimal_part) <= 2


class TestGBMSimulatorGuarantees:
    """Behaviour guarantees from design doc §8.5."""

    def test_seeded_simulator_is_deterministic(self):
        a = GBMSimulator(["AAPL", "MSFT", "ZZZZ"], rng=np.random.default_rng(42))
        b = GBMSimulator(["AAPL", "MSFT", "ZZZZ"], rng=np.random.default_rng(42))
        assert [a.step() for _ in range(50)] == [b.step() for _ in range(50)]

    def test_different_seeds_differ(self):
        a = GBMSimulator(["AAPL"], rng=np.random.default_rng(1))
        b = GBMSimulator(["AAPL"], rng=np.random.default_rng(2))
        assert [a.step() for _ in range(20)] != [b.step() for _ in range(20)]

    def test_correlation_structure(self):
        sim = GBMSimulator(
            ["AAPL", "MSFT", "TSLA"], event_probability=0, rng=np.random.default_rng(1)
        )
        prev = {t: sim._prices[t] for t in sim.get_tickers()}
        rets = {t: [] for t in prev}
        for _ in range(20_000):
            sim.step()
            for t in prev:
                rets[t].append(np.log(sim._prices[t] / prev[t]))
                prev[t] = sim._prices[t]
        c = np.corrcoef([rets["AAPL"], rets["MSFT"], rets["TSLA"]])
        assert abs(c[0, 1] - 0.6) < 0.05
        assert abs(c[0, 2] - 0.3) < 0.05

    def test_events_always_move_at_probability_one(self):
        sim = GBMSimulator(["AAPL"], event_probability=1.0, rng=np.random.default_rng(3))
        p = sim._prices["AAPL"]
        for _ in range(100):
            sim.step()
            q = sim._prices["AAPL"]
            assert abs(q / p - 1) > 0.019
            p = q

    def test_no_events_at_probability_zero(self):
        sim = GBMSimulator(["AAPL"], event_probability=0.0, rng=np.random.default_rng(3))
        p = sim._prices["AAPL"]
        for _ in range(1000):
            sim.step()
            q = sim._prices["AAPL"]
            assert abs(q / p - 1) < 0.01  # pure diffusion: tiny moves only
            p = q

    def test_initial_prices_override_seed(self):
        sim = GBMSimulator(["NVDA", "PYPL"], initial_prices={"NVDA": 900.0, "PYPL": 70.0})
        assert sim.get_price("NVDA") == 900.0
        assert sim.get_price("PYPL") == 70.0
        assert sim.get_start_price("NVDA") == 900.0

    def test_add_ticker_with_initial_price(self):
        sim = GBMSimulator(["AAPL"])
        sim.add_ticker("PYPL", initial_price=71.2)
        assert sim.get_price("PYPL") == 71.2

    def test_start_price_is_seed(self):
        sim = GBMSimulator(["AAPL"])
        sim.step()
        assert sim.get_start_price("AAPL") == SEED_PRICES["AAPL"]
        assert sim.get_start_price("NOPE") is None

    def test_remove_clears_start_price(self):
        sim = GBMSimulator(["AAPL", "MSFT"])
        sim.remove_ticker("MSFT")
        assert sim.get_start_price("MSFT") is None
        assert sim.get_tickers() == ["AAPL"]

    def test_dt_scales_volatility(self):
        """A larger dt gives proportionally larger moves (dt is not hard-coded)."""
        small = GBMSimulator(["AAPL"], dt=1e-8, event_probability=0, rng=np.random.default_rng(5))
        large = GBMSimulator(["AAPL"], dt=1e-4, event_probability=0, rng=np.random.default_rng(5))
        assert abs(small.step()["AAPL"] - 190) < abs(large.step()["AAPL"] - 190)

    def test_params_are_copied_per_ticker(self):
        sim = GBMSimulator(["ZZZZ"])
        sim._params["ZZZZ"]["sigma"] = 9.9
        assert DEFAULT_PARAMS["sigma"] == 0.25

    def test_cholesky_failure_falls_back_to_independent(self, monkeypatch):
        def boom(_):
            raise np.linalg.LinAlgError("not PD")

        monkeypatch.setattr(np.linalg, "cholesky", boom)
        sim = GBMSimulator(["AAPL", "MSFT"])
        assert sim._cholesky is None
        assert set(sim.step()) == {"AAPL", "MSFT"}
