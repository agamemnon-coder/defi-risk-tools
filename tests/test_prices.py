"""Token prices and the impermanent-loss estimate. HTTP is mocked; nothing here uses the network."""

import httpx
import pytest
from price_tools import DAY, START, chart

from defi_risk_tools import prices
from defi_risk_tools.data import DataError, make_client, parse_pools

WETH = "arbitrum:0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
USDC = "arbitrum:0xaf88d065e77c8cc2239327c5edb3a432268e5831"
WBTC = "arbitrum:0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f"
ETH_WETH = "ethereum:0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
ETH_USDC = "ethereum:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
ETH_WBTC = "ethereum:0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"
ETH_USDT = "ethereum:0xdac17f958d2ee523a2206206994597c13d831ec7"


@pytest.fixture
def serve_chart(chart_server):
    """`chart_server` for coins given as plain lists of daily prices."""

    def serve(known: dict[str, list[float]], **kwargs):
        return chart_server(chart(known)["coins"], **kwargs)

    return serve


# Which pools can be estimated


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, ("arbitrum:0xaaa", "arbitrum:0xbbb")),
        ({"chain": "Ethereum"}, ("ethereum:0xaaa", "ethereum:0xbbb")),
        ({"chain": "OP Mainnet"}, ("optimism:0xaaa", "optimism:0xbbb")),
        ({"chain": "Base"}, ("base:0xaaa", "base:0xbbb")),
        ({"underlying_tokens": ("0xAAA", "0xBbB")}, ("arbitrum:0xaaa", "arbitrum:0xbbb")),
        ({"il_risk": False}, None),  # no impermanent loss risk
        ({"il_7d": -0.4}, None),  # DeFiLlama has a figure, so there is nothing to estimate
        ({"underlying_tokens": ("0xaaa",)}, None),
        ({"underlying_tokens": ("0xaaa", "0xbbb", "0xccc")}, None),
        ({"underlying_tokens": ()}, None),
        ({"underlying_tokens": ("0xaaa", "0xAAA")}, None),  # the same token twice
        ({"chain": "Polygon"}, None),  # no coin prefix known
    ],
)
def test_only_pools_with_il_risk_and_exactly_two_tokens_can_be_estimated(
    make_pool, overrides, expected
):
    fields = {"il_risk": True, "underlying_tokens": ("0xaaa", "0xbbb"), **overrides}

    assert prices.coin_keys(make_pool(**fields)) == expected


def test_solana_mints_keep_their_case_because_they_are_case_sensitive(make_pool):
    pool = make_pool(
        chain="Solana", il_risk=True, underlying_tokens=("EPjFWdd5AufqSSqeM2", "So1111Abc")
    )

    assert prices.coin_keys(pool) == ("solana:EPjFWdd5AufqSSqeM2", "solana:So1111Abc")


def test_the_same_evm_token_in_two_cases_is_one_coin():
    assert prices.normalise_key("Arbitrum:0xABCdef") == prices.normalise_key("arbitrum:0xabcDEF")
    assert prices.normalise_key("solana:AbC") != prices.normalise_key("solana:abc")


# Reading a chart response


def test_the_real_chart_response_is_read(prices_payload):
    series = prices.parse_chart(prices_payload)

    assert len(series) == 13  # nine on the L2s, four on Ethereum
    assert all(len(points) == 30 for points in series.values())
    assert all(points == sorted(points) for points in series.values())
    assert set(series) >= {WETH, USDC, WBTC, ETH_WETH, ETH_USDC, ETH_WBTC, ETH_USDT}
    assert all(key == prices.normalise_key(key) for key in series)
    weth = series[WETH]
    assert 1_000 < weth[0][1] < 10_000  # a plausible price of ETH in dollars
    assert 0.9 < series[USDC][0][1] < 1.1  # and of a dollar stablecoin


def test_a_saved_chart_response_can_be_loaded_from_a_file(prices_path):
    assert len(prices.load_series(prices_path)) == 13


@pytest.mark.parametrize("content", ["{not json", '{"coins": []}', "[]", '{"price": 1}'])
def test_a_file_that_is_not_a_chart_response_is_a_data_error(tmp_path, content):
    path = tmp_path / "prices.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(DataError):
        prices.load_series(path)


def test_a_missing_prices_file_is_a_data_error(tmp_path):
    with pytest.raises(DataError, match="cannot read"):
        prices.load_series(tmp_path / "nope.json")


def test_unusable_coins_and_points_are_dropped_not_fatal():
    payload = {
        "coins": {
            "Arbitrum:0xABC": {
                "prices": [
                    {"timestamp": 300, "price": 3.0},
                    {"timestamp": 100, "price": 1.0},
                    {"timestamp": 200, "price": "2"},  # a text, not a number
                    {"timestamp": 400, "price": 0},  # not a price
                    {"timestamp": 500, "price": -1.0},
                    {"timestamp": True, "price": 1.0},
                    {"timestamp": 600, "price": float("nan")},
                    {"price": 1.0},
                    "junk",
                ]
            },
            "arbitrum:0xno-prices": {"prices": []},
            "arbitrum:0xnot-a-list": {"prices": "none"},
            "arbitrum:0xnot-a-dict": 5,
        }
    }

    assert prices.parse_chart(payload) == {"arbitrum:0xabc": [(100.0, 1.0), (300.0, 3.0)]}


@pytest.mark.parametrize("payload", [None, [], {"coins": None}, {"coins": [1]}, "text"])
def test_a_response_without_coins_is_a_data_error(payload):
    with pytest.raises(DataError, match="'coins' object"):
        prices.parse_chart(payload)


# Fetching


def test_the_coins_go_in_the_path_with_a_30_day_daily_window(http_mock):
    route = http_mock.get(url__startswith=prices.CHART_URL).respond(json=chart({WETH: [1.0]}))

    with make_client() as client:
        series, notes = prices.fetch_series(client, [WETH, USDC])

    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == f"/chart/{WETH},{USDC}"
    assert dict(request.url.params) == {"span": "30", "period": "1d"}
    assert series == {WETH: [(float(START), 1.0)]}
    assert notes == []


def test_at_most_ten_coins_go_in_one_request(serve_chart):
    keys = [f"arbitrum:0x{i:040x}" for i in range(25)]
    server = serve_chart({key: [1.0] for key in keys})

    with make_client() as client:
        series, notes = prices.fetch_series(client, keys)

    assert [len(request) for request in server.requests] == [10, 10, 5]
    assert len(series) == 25
    assert notes == []


def test_a_coin_is_only_asked_for_once(serve_chart):
    server = serve_chart({WETH: [1.0], USDC: [1.0]})

    with make_client() as client:
        prices.fetch_series(client, [WETH, USDC, WETH, USDC, WETH])

    assert server.requests == [[WETH, USDC]]


def test_no_coins_means_no_requests(serve_chart):
    server = serve_chart({})

    with make_client() as client:
        assert prices.fetch_series(client, []) == ({}, [])

    assert server.requests == []


def test_a_server_error_is_retried_once_and_then_the_coins_are_skipped_with_a_note(serve_chart):
    server = serve_chart({WETH: [1.0], USDC: [1.0]}, fail={WETH})

    with make_client() as client:
        series, notes = prices.fetch_series(client, [WETH, USDC])

    assert len(server.requests) == 2  # the request and one retry
    assert series == {}
    assert notes == ["Token prices: 2 of 2 coins could not be fetched (HTTP 500)."]


def test_a_failing_request_does_not_stop_the_others(serve_chart):
    keys = [f"arbitrum:0x{i:040x}" for i in range(15)]
    serve_chart({key: [1.0] for key in keys}, fail={keys[0]})

    with make_client() as client:
        series, notes = prices.fetch_series(client, keys)

    assert set(series) == set(keys[10:])  # the second request worked
    assert notes == ["Token prices: 10 of 15 coins could not be fetched (HTTP 500)."]


def test_an_unreachable_server_gives_a_note_and_no_prices(http_mock):
    http_mock.get(url__startswith=prices.CHART_URL).mock(side_effect=httpx.ConnectError("down"))

    with make_client() as client:
        series, notes = prices.fetch_series(client, [WETH])

    assert series == {}
    assert notes == ["Token prices: 1 of 1 coins could not be fetched (ConnectError: down)."]


def test_a_rejected_request_is_split_so_one_bad_coin_costs_only_itself(serve_chart):
    keys = [f"arbitrum:0x{i:040x}" for i in range(10)]
    server = serve_chart({key: [1.0] for key in keys}, reject={keys[6]})

    with make_client() as client:
        series, notes = prices.fetch_series(client, keys)

    assert set(series) == set(keys) - {keys[6]}
    assert notes == ["Token prices: 1 of 10 coins could not be fetched (HTTP 400)."]
    # 10 -> 5 + 5; the second 5 -> 2 + 3; the 2 -> 1 + 1. Nothing is sent twice: a 400 is final.
    assert [len(request) for request in server.requests] == [10, 5, 5, 2, 1, 1, 3]


def test_a_response_that_is_not_a_chart_is_a_failure_and_not_split(http_mock):
    route = http_mock.get(url__startswith=prices.CHART_URL).respond(json={"coins": 5})

    with make_client() as client:
        series, notes = prices.fetch_series(client, [WETH, USDC])

    assert series == {}
    assert route.call_count == 1
    assert "'coins' object" in notes[0]


def test_five_failed_requests_in_a_row_make_it_give_up_on_the_coins_not_asked_for_yet(serve_chart):
    keys = [f"arbitrum:0x{i:040x}" for i in range(60)]  # six requests of ten
    server = serve_chart({key: [1.0] for key in keys}, fail=set(keys))

    with make_client() as client:
        series, notes = prices.fetch_series(client, keys)

    assert series == {}
    # five requests that failed, each sent twice (a 500 is retried once), and no sixth request
    assert [len(request) for request in server.requests] == [10] * 10
    assert notes == [
        "Token prices: 60 of 60 coins could not be fetched "
        "(HTTP 500, gave up after 5 failed requests in a row)."
    ]


def test_a_request_that_works_starts_the_count_again(serve_chart):
    keys = [f"arbitrum:0x{i:040x}" for i in range(70)]  # seven requests of ten
    # requests 1 to 4 fail, the 5th works, the 6th fails and the 7th works: never five in a row
    failing = set(keys[:40]) | set(keys[50:60])
    server = serve_chart({key: [1.0] for key in keys}, fail=failing)

    with make_client() as client:
        series, notes = prices.fetch_series(client, keys)

    assert set(series) == set(keys[40:50]) | set(keys[60:])
    assert len(server.requests) == 4 * 2 + 1 + 2 + 1  # a 500 is sent twice
    assert notes == ["Token prices: 50 of 70 coins could not be fetched (HTTP 500)."]


def test_a_bad_coin_in_every_request_does_not_make_it_give_up(serve_chart):
    keys = [f"arbitrum:0x{i:040x}" for i in range(60)]
    bad = {keys[i] for i in range(0, 60, 10)}  # one bad coin in each of the six requests
    server = serve_chart({key: [1.0] for key in keys}, reject=bad)

    with make_client() as client:
        series, notes = prices.fetch_series(client, keys)

    # Six requests in a row were rejected, but each was split, and the pieces without the bad coin
    # worked: so the server is up, and it went on to the end.
    assert set(series) == set(keys) - bad
    assert notes == ["Token prices: 6 of 60 coins could not be fetched (HTTP 400)."]
    assert sum(len(request) == 10 for request in server.requests) == 6


def test_a_server_that_rejects_every_coin_is_given_up_on_like_one_that_is_down(serve_chart):
    keys = [f"arbitrum:0x{i:040x}" for i in range(60)]
    server = serve_chart({key: [1.0] for key in keys}, reject=set(keys))

    with make_client() as client:
        series, notes = prices.fetch_series(client, keys)

    assert series == {}
    # Each request of ten is split down to single coins, which makes 19 requests that all fail, and
    # a 400 is not sent twice. After five such requests the sixth is not asked for.
    assert len(server.requests) == 5 * 19
    assert notes == [
        "Token prices: 60 of 60 coins could not be fetched "
        "(HTTP 400, gave up after 5 failed requests in a row)."
    ]


def test_a_chart_without_the_coins_asked_for_is_an_answer_and_not_a_failure(serve_chart):
    keys = [f"arbitrum:0x{i:040x}" for i in range(60)]
    server = serve_chart({})  # the server answers every request, and knows none of the coins

    with make_client() as client:
        series, notes = prices.fetch_series(client, keys)

    assert series == {}
    assert notes == []
    assert [len(request) for request in server.requests] == [10] * 6


# The estimate for a pool


def test_the_volatility_is_estimated_for_the_pools_with_two_priced_tokens(make_pool):
    eth_usdc = make_pool(pool_id="eth", il_risk=True, underlying_tokens=("0xaaa", "0xbbb"))
    no_risk = make_pool(pool_id="plain", il_risk=False, underlying_tokens=("0xaaa", "0xbbb"))
    three = make_pool(pool_id="three", il_risk=True, underlying_tokens=("0xaaa", "0xbbb", "0xccc"))
    series = {
        "arbitrum:0xaaa": [(START + i * DAY, 1.0 + 0.01 * (-1) ** i) for i in range(30)],
        "arbitrum:0xbbb": [(START + i * DAY, 1.0) for i in range(30)],
    }

    volatilities, notes = prices.estimate_volatilities(
        [eth_usdc, no_risk, three], lambda keys: (series, [])
    )

    assert set(volatilities) == {"eth"}
    assert volatilities["eth"] > 0
    assert notes == []


def test_prices_are_asked_for_once_per_token_and_only_for_estimable_pools(make_pool):
    pools = [
        make_pool(pool_id="a", il_risk=True, underlying_tokens=("0xaaa", "0xbbb")),
        make_pool(pool_id="b", il_risk=True, underlying_tokens=("0xAAA", "0xccc")),  # shares 0xaaa
        make_pool(pool_id="c", il_risk=False, underlying_tokens=("0xddd", "0xeee")),
        make_pool(pool_id="d", il_risk=True, underlying_tokens=("0xfff",)),
    ]
    asked = []

    def lookup(keys):
        asked.append(keys)
        return {}, []

    prices.estimate_volatilities(pools, lookup)

    assert asked == [["arbitrum:0xaaa", "arbitrum:0xbbb", "arbitrum:0xccc"]]


def test_the_coins_are_asked_for_in_the_order_of_the_pools(make_pool):
    first = make_pool(pool_id="a", il_risk=True, underlying_tokens=("0xaaa", "0xbbb"))
    second = make_pool(pool_id="b", il_risk=True, underlying_tokens=("0xAAA", "0xccc"))
    asked = []

    def lookup(keys):
        asked.append(keys)
        return {}, []

    prices.estimate_volatilities([first, second], lookup)
    prices.estimate_volatilities([second, first], lookup)

    # so that, if the lookup gives out partway, the pools that came first were estimated
    assert asked == [
        ["arbitrum:0xaaa", "arbitrum:0xbbb", "arbitrum:0xccc"],
        ["arbitrum:0xaaa", "arbitrum:0xccc", "arbitrum:0xbbb"],
    ]


def test_the_count_note_says_how_many_pools_were_estimated_and_how_many_were_not(make_pool):
    ids = ["a", "b", "c", "d"]
    pools = [
        make_pool(pool_id=name, il_risk=True, underlying_tokens=(f"0x{name}1", f"0x{name}2"))
        for name in ids
    ]
    # Pools a and b have 14 daily returns each; c has only one token with prices, d has none.
    moving = [(START + i * DAY, 1.0 + 0.01 * (-1) ** i) for i in range(15)]
    flat = [(START + i * DAY, 1.0) for i in range(15)]
    series = {
        "arbitrum:0xa1": moving,
        "arbitrum:0xa2": flat,
        "arbitrum:0xb1": moving,
        "arbitrum:0xb2": flat,
        "arbitrum:0xc1": moving,
    }

    volatilities, notes = prices.estimate_volatilities(pools, lambda keys: (series, []))

    assert set(volatilities) == {"a", "b"}
    assert notes == [
        "Impermanent loss is estimated for 2 of the 4 pools with IL risk and two tokens. The "
        "other 2 stay IL_UNKNOWN: no usable daily prices for one or both of their two tokens."
    ]


def test_there_is_no_count_note_when_every_pool_was_estimated(make_pool):
    pool = make_pool(il_risk=True, underlying_tokens=("0xaaa", "0xbbb"))
    series = {
        "arbitrum:0xaaa": [(START + i * DAY, 1.0 + 0.01 * (-1) ** i) for i in range(15)],
        "arbitrum:0xbbb": [(START + i * DAY, 1.0) for i in range(15)],
    }

    volatilities, notes = prices.estimate_volatilities([pool], lambda keys: (series, []))

    assert len(volatilities) == 1
    assert notes == []


def test_the_lookup_is_not_called_when_no_pool_can_be_estimated(make_pool):
    def lookup(keys):
        raise AssertionError("no prices should have been asked for")

    assert prices.estimate_volatilities([make_pool(il_risk=False)], lookup) == ({}, [])


def test_pools_with_too_little_price_history_are_left_unestimated_and_the_note_says_so(make_pool):
    pools = [make_pool(pool_id="young", il_risk=True, underlying_tokens=("0xaaa", "0xbbb"))]
    keys = ("arbitrum:0xaaa", "arbitrum:0xbbb")
    short = {key: [(START + i * DAY, 1.0 + i / 100) for i in range(5)] for key in keys}

    volatilities, notes = prices.estimate_volatilities(pools, lambda keys: (short, []))

    assert volatilities == {}
    assert notes == [
        "Impermanent loss is estimated for 0 of the 1 pool with IL risk and two tokens. The other "
        "1 stays IL_UNKNOWN: no usable daily prices for one or both of its two tokens."
    ]


def test_the_count_note_follows_what_the_lookup_said_about_itself(make_pool):
    pools = [make_pool(il_risk=True, underlying_tokens=("0xaaa", "0xbbb"))]

    _, notes = prices.estimate_volatilities(pools, lambda keys: ({}, ["The lookup failed."]))

    assert notes[0] == "The lookup failed."
    assert notes[1].startswith("Impermanent loss is estimated for 0 of the 1 pool")
    assert len(notes) == 2


# The real sample


def sample_pool(pools, chain, project, symbol, index=0):
    matches = [p for p in pools if (p.chain, p.project, p.symbol) == (chain, project, symbol)]
    return matches[index]


def test_volatilities_of_the_real_sample_match_a_hand_computed_reference(
    sample_payload, prices_path
):
    pools = parse_pools(sample_payload).pools
    series = prices.load_series(prices_path)

    volatilities, notes = prices.estimate_volatilities(pools, lambda keys: (series, []))

    # Reference values: annualised standard deviation of the daily log returns of the price
    # ratio, worked out separately from the saved prices (one price a day for each token).
    expected = {
        ("Arbitrum", "gmx-evm", "ETH-USDC", 0): 0.399567,  # WETH / USDC
        ("Arbitrum", "uniswap-v3", "WETH-USDC", 0): 0.399567,
        ("Arbitrum", "gmx-evm", "WBTC.B-USDC", 0): 0.259566,  # WBTC / USDC
        ("Arbitrum", "uniswap-v3", "WBTC-WETH", 0): 0.207355,  # WBTC / WETH
        ("OP Mainnet", "uniswap-v3", "USDC-WETH", 0): 0.416895,
        ("OP Mainnet", "uniswap-v3", "WETH-WBTC", 0): 0.229261,
        ("Base", "uniswap-v3", "WETH-USDC", 0): 0.402455,
        ("Base", "uniswap-v3", "WETH-CBBTC", 0): 0.210607,  # WETH / cbBTC
        ("Base", "uniswap-v3", "WETH-CBBTC", 1): 0.210607,
        ("Base", "uniswap-v3", "USDC-CBBTC", 0): 0.267180,
        ("Ethereum", "uniswap-v3", "USDC-WETH", 0): 0.412939,  # USDC / WETH
        ("Ethereum", "uniswap-v3", "WETH-USDT", 0): 0.412303,
        ("Ethereum", "uniswap-v3", "WBTC-WETH", 0): 0.218020,  # WBTC / WETH
    }
    assert len(volatilities) == len(expected)
    for (chain, project, symbol, index), want in expected.items():
        pool = sample_pool(pools, chain, project, symbol, index)
        assert volatilities[pool.pool_id] == pytest.approx(want, abs=5e-7), (chain, symbol)
    # Pools whose tokens are not in the saved prices stay unestimated, and the notes say how many.
    estimable = [p for p in pools if prices.coin_keys(p)]
    assert len(estimable) == 29  # 24 on the L2s and Solana, and five on Ethereum
    assert notes == [
        "Impermanent loss is estimated for 13 of the 29 pools with IL risk and two tokens. The "
        "other 16 stay IL_UNKNOWN: no usable daily prices for one or both of their two tokens."
    ]


def test_the_ethereum_pools_of_the_sample_are_priced_by_their_lowercase_ethereum_keys(
    sample_payload,
):
    pools = [p for p in parse_pools(sample_payload).pools if p.chain == "Ethereum"]
    asked = []

    def lookup(keys):
        asked.append(keys)
        return {}, []

    prices.estimate_volatilities(pools, lookup)

    # Of the 15 pools on Ethereum, five have IL risk and exactly two tokens: Uniswap v3 USDC-WETH,
    # WETH-USDT and WBTC-WETH, Uniswap v2 PEPE-WETH and Balancer v2 BAL-WETH. The pool with three
    # tokens, and the ones without IL risk, are not asked about.
    assert len(asked) == 1
    assert set(asked[0]) == {
        ETH_WETH,
        ETH_USDC,
        ETH_WBTC,
        ETH_USDT,
        "ethereum:0x6982508145454ce325ddbe47a25d4ec3d2311933",  # PEPE
        "ethereum:0xba100000625a3754423978a60c9317c58a424e3d",  # BAL
    }
    assert len(asked[0]) == 6  # each coin once, though WETH is in all five pools


def test_the_il_estimate_for_the_real_eth_usdc_pool_is_a_few_dollars_at_most(
    sample_payload, prices_path
):
    # sigma = 0.399567, so $1,000 for 30 days loses 1000 * 0.399567**2 * (30/365) / 8 = $1.64
    pools = parse_pools(sample_payload).pools
    pool = sample_pool(pools, "Arbitrum", "gmx-evm", "ETH-USDC")
    series = prices.load_series(prices_path)

    volatilities, _ = prices.estimate_volatilities([pool], lambda keys: (series, []))

    loss = 1000 * volatilities[pool.pool_id] ** 2 * (30 / 365) / 8
    assert loss == pytest.approx(1.6403, abs=1e-4)
