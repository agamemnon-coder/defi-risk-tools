"""Shared test helpers. Every test runs offline."""

import json
from pathlib import Path

import pytest
import respx

from defi_risk_tools.data import Pool

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "pools_sample.json"


@pytest.fixture(autouse=True)
def http_mock():
    """Any HTTP request that a test did not mock raises, so no test can reach the network.

    Tests that want a response add routes to this router.
    """
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
def fixture_path() -> Path:
    return FIXTURE_PATH


@pytest.fixture
def sample_payload() -> dict:
    """The real DeFiLlama response saved on 2026-10-26: 61 pools."""
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def make_pool():
    """Build a `Pool`. The default is a plain single-asset stablecoin pool paying 5% base APY."""

    def make(**overrides) -> Pool:
        fields = {
            "pool_id": "pool-1",
            "chain": "Arbitrum",
            "project": "demo-protocol",
            "symbol": "USDC",
            "tvl_usd": 5_000_000.0,
            "apy_base": 5.0,
            "apy_reward": 0.0,
            "apy_mean_30d": None,
            "stablecoin": True,
            "il_risk": False,
            "exposure": "single",
            "il_7d": None,
            "outlier": False,
            "reward_tokens": (),
            "underlying_tokens": (),
        }
        fields.update(overrides)
        fields.setdefault("apy", fields["apy_base"] + fields["apy_reward"])
        return Pool(**fields)

    return make
