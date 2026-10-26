"""Shared test helpers. Every test runs offline."""

import json
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
import respx

from defi_risk_tools import prices
from defi_risk_tools.data import Pool

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "pools_sample.json"
PRICES_PATH = Path(__file__).parent / "fixtures" / "prices_sample.json"


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
def prices_path() -> Path:
    return PRICES_PATH


@pytest.fixture
def prices_payload() -> dict:
    """A real chart response saved on 2026-10-26: 30 daily prices of WETH, USDC, WBTC and cbBTC."""
    return json.loads(PRICES_PATH.read_text(encoding="utf-8"))


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


@dataclass
class ChartServer:
    route: respx.Route
    requests: list[list[str]] = field(default_factory=list)  # the coin keys of each request


@pytest.fixture
def chart_server(http_mock):
    """Answer chart requests the way DeFiLlama does: with the known coins that were asked for.

    `known` maps a coin key to its entry in a chart response (`{"symbol": ..., "prices": [...]}`).
    A request that holds a coin in `reject` gets HTTP 400, one that holds a coin in `fail` gets
    HTTP 500. The server records every request.
    """

    def serve(known: dict[str, dict], *, reject=(), fail=()) -> ChartServer:
        server = ChartServer(route=http_mock.get(url__startswith=prices.CHART_URL))

        def handler(request: httpx.Request) -> httpx.Response:
            keys = request.url.path.removeprefix("/chart/").split(",")
            server.requests.append(keys)
            if any(key in reject for key in keys):
                return httpx.Response(400, json={"message": "bad coin"})
            if any(key in fail for key in keys):
                return httpx.Response(500)
            return httpx.Response(200, json={"coins": {k: known[k] for k in keys if k in known}})

        server.route.mock(side_effect=handler)
        return server

    return serve
