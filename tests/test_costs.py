"""The cost table and the chain cost lookups. HTTP is mocked; nothing here uses the network."""

import httpx
import pytest

from defi_risk_tools import costs
from defi_risk_tools.costs import COST_TABLE, DEFAULT_CHAINS, DataError, resolve_tx_costs
from defi_risk_tools.data import make_client

ARBITRUM_RPC = COST_TABLE["Arbitrum"].rpc_url
OP_RPC = COST_TABLE["OP Mainnet"].rpc_url
BASE_RPC = COST_TABLE["Base"].rpc_url

PRICES_RESPONSE = {
    "coins": {
        "coingecko:ethereum": {"price": 3000.0, "symbol": "ETH"},
        "coingecko:solana": {"price": 150.0, "symbol": "SOL"},
    }
}


def rpc_result(wei: int) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "result": hex(wei)}


# The table


def test_the_table_covers_the_four_v1_chains():
    assert DEFAULT_CHAINS == ("Arbitrum", "OP Mainnet", "Base", "Solana")


def test_the_rpc_endpoints_are_the_ones_in_the_design():
    assert ARBITRUM_RPC == "https://arb1.arbitrum.io/rpc"
    assert OP_RPC == "https://mainnet.optimism.io"
    assert BASE_RPC == "https://mainnet.base.org"
    assert COST_TABLE["Solana"].rpc_url is None


def test_l2s_assume_250k_gas_and_a_2_cent_l1_fee():
    for chain in ("Arbitrum", "OP Mainnet", "Base"):
        assert COST_TABLE[chain].gas_units == 250_000
        assert COST_TABLE[chain].l1_fee_usd == 0.02


@pytest.mark.parametrize(
    ("typed", "known"),
    [
        ("arbitrum", "Arbitrum"),
        ("OP MAINNET", "OP Mainnet"),
        ("  base ", "Base"),
        ("Solana", "Solana"),
    ],
)
def test_chain_names_are_matched_ignoring_case(typed, known):
    assert costs.canonical_chain(typed) == known


def test_an_unknown_chain_says_which_chains_are_supported():
    with pytest.raises(ValueError, match="no cost data for chain 'Ethereum'.*Arbitrum.*Solana"):
        costs.canonical_chain("Ethereum")


# Offline: defaults only


def test_offline_runs_use_the_defaults_and_say_so():
    tx_costs, notes = resolve_tx_costs(DEFAULT_CHAINS, client=None)

    # 250,000 gas * 0.02 gwei = 5e-6 ETH; at $2,300 that is $0.0115, plus the $0.02 L1 fee
    assert tx_costs["Arbitrum"].usd == pytest.approx(0.0315)
    # 250,000 gas * 0.001 gwei = 2.5e-7 ETH = $0.000575, plus $0.02
    assert tx_costs["OP Mainnet"].usd == pytest.approx(0.020575)
    # 250,000 gas * 0.006 gwei = 1.5e-6 ETH = $0.00345, plus $0.02
    assert tx_costs["Base"].usd == pytest.approx(0.02345)
    # a fixed 0.00005 SOL at $100
    assert tx_costs["Solana"].usd == pytest.approx(0.005)
    assert not any(c.native_price_live or c.gas_price_live for c in tx_costs.values())
    assert len(notes) == 1
    assert "Offline run" in notes[0]
    assert "no network calls" in notes[0]


# Online: live lookups


def test_live_prices_and_gas_prices_are_used_when_they_arrive(http_mock):
    http_mock.get(costs.NATIVE_PRICES_URL).respond(json=PRICES_RESPONSE)
    http_mock.post(ARBITRUM_RPC).respond(json=rpc_result(1_000_000_000))  # 1 gwei
    http_mock.post(OP_RPC).respond(json=rpc_result(2_000_000_000))  # 2 gwei
    http_mock.post(BASE_RPC).respond(json=rpc_result(500_000_000))  # 0.5 gwei

    with make_client() as client:
        tx_costs, notes = resolve_tx_costs(DEFAULT_CHAINS, client)

    # 250,000 gas * 1 gwei = 0.00025 ETH * $3,000 = $0.75, plus the $0.02 L1 fee
    assert tx_costs["Arbitrum"].usd == pytest.approx(0.77)
    assert tx_costs["OP Mainnet"].usd == pytest.approx(1.52)
    assert tx_costs["Base"].usd == pytest.approx(0.395)
    assert tx_costs["Solana"].usd == pytest.approx(0.00005 * 150)
    assert all(c.native_price_live for c in tx_costs.values())
    assert all(tx_costs[chain].gas_price_live for chain in ("Arbitrum", "OP Mainnet", "Base"))
    assert notes == []


def test_the_rpc_call_is_a_read_only_eth_gasprice(http_mock):
    http_mock.get(costs.NATIVE_PRICES_URL).respond(json=PRICES_RESPONSE)
    route = http_mock.post(ARBITRUM_RPC).respond(json=rpc_result(1_000_000_000))

    with make_client() as client:
        resolve_tx_costs(["Arbitrum"], client)

    request = route.calls.last.request
    assert request.method == "POST"
    assert b'"method":"eth_gasPrice"' in request.content.replace(b" ", b"")
    assert http_mock.calls.call_count == 2  # the prices and one RPC call: nothing else


def test_only_the_chains_asked_for_are_queried(http_mock):
    http_mock.get(costs.NATIVE_PRICES_URL).respond(json=PRICES_RESPONSE)
    base_route = http_mock.post(BASE_RPC).respond(json=rpc_result(1_000_000_000))
    other_routes = [http_mock.post(ARBITRUM_RPC), http_mock.post(OP_RPC)]

    with make_client() as client:
        tx_costs, _ = resolve_tx_costs(["Base"], client)

    assert list(tx_costs) == ["Base"]
    assert base_route.called
    assert not any(route.called for route in other_routes)


def test_a_failed_rpc_call_falls_back_to_the_chain_default_and_says_so(http_mock):
    http_mock.get(costs.NATIVE_PRICES_URL).respond(json=PRICES_RESPONSE)
    http_mock.post(ARBITRUM_RPC).respond(500)
    http_mock.post(OP_RPC).respond(json=rpc_result(1_000_000_000))

    with make_client() as client:
        tx_costs, notes = resolve_tx_costs(["Arbitrum", "OP Mainnet"], client)

    arbitrum = tx_costs["Arbitrum"]
    assert arbitrum.gas_price_gwei == COST_TABLE["Arbitrum"].default_gas_price_gwei
    assert not arbitrum.gas_price_live
    assert arbitrum.native_price_live  # the price call worked
    assert tx_costs["OP Mainnet"].gas_price_live
    assert len(notes) == 1
    assert notes[0].startswith("Arbitrum: gas price unavailable (")
    assert "HTTP 500" in notes[0]
    assert "default of 0.02 gwei" in notes[0]


def test_a_failed_price_call_falls_back_to_default_prices_and_says_so(http_mock):
    http_mock.get(costs.NATIVE_PRICES_URL).mock(side_effect=httpx.ConnectError("no route"))
    http_mock.post(ARBITRUM_RPC).respond(json=rpc_result(1_000_000_000))

    with make_client() as client:
        tx_costs, notes = resolve_tx_costs(["Arbitrum", "Solana"], client)

    assert tx_costs["Arbitrum"].native_price_usd == costs.DEFAULT_NATIVE_PRICES_USD["ETH"]
    assert tx_costs["Solana"].native_price_usd == costs.DEFAULT_NATIVE_PRICES_USD["SOL"]
    assert not tx_costs["Arbitrum"].native_price_live
    assert tx_costs["Arbitrum"].gas_price_live  # the gas call still worked
    assert any(note.startswith("Native token prices unavailable") for note in notes)


def test_a_missing_coin_in_the_price_response_uses_that_default(http_mock):
    only_eth = {"coins": {"coingecko:ethereum": {"price": 3000.0}}}
    http_mock.get(costs.NATIVE_PRICES_URL).respond(json=only_eth)

    with make_client() as client:
        tx_costs, notes = resolve_tx_costs(["Solana"], client)

    assert tx_costs["Solana"].native_price_usd == costs.DEFAULT_NATIVE_PRICES_USD["SOL"]
    assert not tx_costs["Solana"].native_price_live
    assert notes == ["No live price for SOL; using the default."]


@pytest.mark.parametrize(
    "payload",
    [
        {"coins": {}},
        {"coins": []},
        {},
        [],
        {"coins": {"coingecko:ethereum": {"price": 0}, "coingecko:solana": {"price": -1}}},
        {"coins": {"coingecko:ethereum": {"price": "3000"}, "coingecko:solana": {"price": None}}},
        {"coins": {"coingecko:ethereum": {"price": True}}},
    ],
)
def test_a_price_response_without_usable_prices_is_an_error(http_mock, payload):
    http_mock.get(costs.NATIVE_PRICES_URL).respond(json=payload)

    with make_client() as client, pytest.raises(DataError):
        costs.fetch_native_prices(client)


@pytest.mark.parametrize(
    "payload",
    [
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "nope"}},
        {"result": "not hex"},
        {"result": None},
        {"result": 5},
        {"result": "-0x1"},
        [],
    ],
)
def test_an_rpc_response_without_a_usable_gas_price_is_an_error(http_mock, payload):
    http_mock.post(ARBITRUM_RPC).respond(json=payload)

    with make_client() as client, pytest.raises(DataError, match="eth_gasPrice"):
        costs.fetch_gas_price_gwei(client, ARBITRUM_RPC)


def test_the_gas_price_is_converted_from_wei_to_gwei(http_mock):
    http_mock.post(ARBITRUM_RPC).respond(json={"jsonrpc": "2.0", "id": 1, "result": "0x132b3a0"})

    with make_client() as client:
        assert costs.fetch_gas_price_gwei(client, ARBITRUM_RPC) == pytest.approx(0.0201)


def test_the_real_responses_seen_on_2026_10_26_are_understood(http_mock):
    # These are the bodies the public endpoints returned that day, copied as they came.
    http_mock.get(costs.NATIVE_PRICES_URL).respond(
        text='{"coins":{"coingecko:ethereum":{"price":2328.0192415168995,"symbol":"ETH",'
        '"timestamp":1793010361,"confidence":0.99},"coingecko:solana":{"price":101.35432277703522,'
        '"symbol":"SOL","timestamp":1793010361,"confidence":0.99}}}'
    )
    http_mock.post(ARBITRUM_RPC).respond(text='{"jsonrpc":"2.0","id":1,"result":"0x132b3a0"}')
    http_mock.post(OP_RPC).respond(text='{"jsonrpc":"2.0","result":"0xf435b","id":1}')
    http_mock.post(BASE_RPC).respond(text='{"jsonrpc":"2.0","id":1,"result":"0x5b8d80"}')

    with make_client() as client:
        tx_costs, notes = resolve_tx_costs(DEFAULT_CHAINS, client)

    assert notes == []
    assert tx_costs["Arbitrum"].gas_price_gwei == pytest.approx(0.0201)
    assert tx_costs["OP Mainnet"].gas_price_gwei == pytest.approx(0.001000283)
    assert tx_costs["Base"].gas_price_gwei == pytest.approx(0.006)
    # 250,000 gas * 0.0201 gwei = 5.025e-6 ETH, times $2,328.02, plus the $0.02 L1 fee
    assert tx_costs["Arbitrum"].usd == pytest.approx(0.031698, abs=1e-6)
    assert tx_costs["Solana"].usd == pytest.approx(0.00005 * 101.35432277703522)


# Describing a cost


def test_describe_shows_the_inputs_and_where_they_came_from():
    tx_costs, _ = resolve_tx_costs(["Arbitrum", "Solana"], client=None)

    assert tx_costs["Arbitrum"].describe() == (
        "$0.0315 per tx = 250,000 gas x 0.0200 gwei (default) x ETH $2,300.00 (default)"
        " + $0.02 L1 data fee (estimate)"
    )
    assert tx_costs["Solana"].describe() == (
        "$0.0050 per tx = 0.00005 SOL fixed fee x SOL $100.00 (default)"
    )
