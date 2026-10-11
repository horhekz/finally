"""Tests for market data source factory."""

import os
from unittest.mock import patch

from app.market.cache import PriceCache
from app.market.factory import create_market_data_source
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource


class TestFactory:
    """Tests for create_market_data_source factory."""

    def test_creates_simulator_when_no_api_key(self):
        """Test that simulator is created when MASSIVE_API_KEY is not set."""
        cache = PriceCache()

        with patch.dict(os.environ, {}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)

    def test_creates_simulator_when_api_key_empty(self):
        """Test that simulator is created when MASSIVE_API_KEY is empty."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": ""}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)

    def test_creates_simulator_when_api_key_whitespace(self):
        """Test that simulator is created when MASSIVE_API_KEY is whitespace."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "   "}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)

    def test_creates_massive_when_api_key_set(self):
        """Test that Massive client is created when MASSIVE_API_KEY is set."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key"}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, MassiveDataSource)

    def test_massive_receives_api_key(self):
        """Test that Massive client receives the API key."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key-123"}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, MassiveDataSource)
        assert source._api_key == "test-key-123"

    def test_simulator_receives_cache(self):
        """Test that simulator receives the cache reference."""
        cache = PriceCache()

        with patch.dict(os.environ, {}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)
        assert source._cache is cache

    def test_massive_receives_cache(self):
        """Test that Massive client receives the cache reference."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key"}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, MassiveDataSource)
        assert source._cache is cache

    def test_massive_poll_interval_from_env(self):
        env = {"MASSIVE_API_KEY": "k", "MASSIVE_POLL_INTERVAL": "5"}
        with patch.dict(os.environ, env, clear=True):
            source = create_market_data_source(PriceCache())
        assert source._interval == 5.0

    def test_massive_poll_interval_has_minimum(self):
        env = {"MASSIVE_API_KEY": "k", "MASSIVE_POLL_INTERVAL": "0.1"}
        with patch.dict(os.environ, env, clear=True):
            source = create_market_data_source(PriceCache())
        assert source._interval == 1.0

    def test_invalid_poll_interval_falls_back_to_default(self):
        env = {"MASSIVE_API_KEY": "k", "MASSIVE_POLL_INTERVAL": "fast"}
        with patch.dict(os.environ, env, clear=True):
            source = create_market_data_source(PriceCache())
        assert source._interval == 15.0

    def test_massive_api_key_is_stripped(self):
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "  key  "}, clear=True):
            source = create_market_data_source(PriceCache())
        assert source._api_key == "key"

    def test_sim_event_probability_from_env(self):
        with patch.dict(os.environ, {"SIM_EVENT_PROBABILITY": "0.0002"}, clear=True):
            source = create_market_data_source(PriceCache())
        assert source._event_prob == 0.0002

    def test_invalid_sim_event_probability_falls_back(self):
        with patch.dict(os.environ, {"SIM_EVENT_PROBABILITY": "lots"}, clear=True):
            source = create_market_data_source(PriceCache())
        assert source._event_prob == 0.001

    async def test_sim_seed_makes_runs_identical(self):
        paths = []
        for _ in range(2):
            with patch.dict(os.environ, {"SIM_SEED": "42"}, clear=True):
                source = create_market_data_source(PriceCache())
            await source.start(["AAPL", "ZZZZ"])
            await source.stop()
            paths.append([source._sim.step() for _ in range(20)])
        assert paths[0] == paths[1]

    def test_invalid_sim_seed_is_ignored(self):
        with patch.dict(os.environ, {"SIM_SEED": "abc"}, clear=True):
            source = create_market_data_source(PriceCache())
        assert isinstance(source, SimulatorDataSource)
