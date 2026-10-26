"""The command line, run on the saved sample. Nothing here touches the network."""

import json
import re
from types import SimpleNamespace

import httpx
import pytest
from html_tools import inspect_page
from price_tools import chart, prices_with_returns
from typer.testing import CliRunner

from defi_risk_tools import costs, data
from defi_risk_tools.cli import app
from defi_risk_tools.costs import COST_TABLE

runner = CliRunner()

TABLE_HEADER = (
    "chain  project  symbol  TVL  APY (base+reward)  costs  net APY  break-even size  flags"
)


@pytest.fixture
def sample(fixture_path):
    return ["--from-file", str(fixture_path)]


def table_rows(output: str) -> list[str]:
    """The data rows of the text table: everything between the rule and the blank line."""
    lines = output.splitlines()
    rule = next(i for i, line in enumerate(lines) if line.startswith("-----"))
    rows = []
    for line in lines[rule + 1 :]:
        if not line.strip():
            break
        rows.append(line)
    return rows


def sample_pools(sample_payload, *, chains=None, advertising_apy=None) -> list[dict]:
    """Raw pools of the saved sample, optionally only some chains / only those with (no) APY."""
    pools = sample_payload["data"]
    if chains is not None:
        pools = [pool for pool in pools if pool["chain"] in chains]
    if advertising_apy is not None:
        pools = [pool for pool in pools if (pool["apy"] > 0) is advertising_apy]
    return pools


def scan_json(*args) -> dict:
    result = runner.invoke(app, ["scan", "--json", *args])
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)


# Text output


def test_scan_prints_a_table_from_the_sample(sample, sample_payload):
    result = runner.invoke(app, ["scan", *sample, "--limit", "10"])

    assert result.exit_code == 0, result.stderr
    header = next(line for line in result.stdout.splitlines() if line.startswith("chain"))
    assert re.sub(r"\s{2,}", "  ", header) == TABLE_HEADER
    rows = table_rows(result.stdout)
    assert len(rows) == 10
    assert all("COST_NEGATIVE" in row for row in rows)  # the worst ten are all cost-negative
    analysed = len(sample_pools(sample_payload, advertising_apy=True))
    assert f"Pools analysed: {analysed} on Arbitrum, OP Mainnet, Base, Solana." in result.stdout
    assert "not financial advice" in result.stdout
    assert "Position size" in result.stdout


def test_default_limit_is_fifty(sample):
    result = runner.invoke(app, ["scan", *sample])

    assert len(table_rows(result.stdout)) == 50
    assert "Shown: 50." in result.stdout


def test_a_run_from_a_file_makes_no_network_calls(sample, http_mock):
    result = runner.invoke(app, ["scan", *sample])

    assert result.exit_code == 0
    assert http_mock.calls.call_count == 0
    assert "Offline run" in result.stdout


def test_only_negative_shows_only_cost_negative_pools(sample):
    result = runner.invoke(app, ["scan", *sample, "--only-negative", "--limit", "100"])

    rows = table_rows(result.stdout)
    assert 0 < len(rows) < 61
    assert all("COST_NEGATIVE" in row for row in rows)


def test_chain_option_filters_ignores_case_and_repeats(sample, sample_payload):
    document = scan_json(*sample, "--chain", "solana", "--chain", "op mainnet", "--limit", "100")

    assert {pool["chain"] for pool in document["pools"]} == {"Solana", "OP Mainnet"}
    assert list(document["chain_costs"]) == ["Solana", "OP Mainnet"]
    chains = {"Solana", "OP Mainnet"}
    assert document["counts"]["analysed"] == len(
        sample_pools(sample_payload, chains=chains, advertising_apy=True)
    )


def test_a_chain_given_twice_is_scanned_once(sample, sample_payload):
    document = scan_json(*sample, "--chain", "base", "--chain", "Base", "--limit", "100")

    assert list(document["chain_costs"]) == ["Base"]
    assert document["counts"]["analysed"] == len(
        sample_pools(sample_payload, chains={"Base"}, advertising_apy=True)
    )


def test_an_unsupported_chain_is_a_usage_error_that_lists_the_supported_ones(sample):
    result = runner.invoke(app, ["scan", *sample, "--chain", "Ethereum"])

    assert result.exit_code == 2
    assert "no cost data for chain 'Ethereum'" in result.stderr
    assert "Arbitrum" in result.stderr


def test_min_tvl_leaves_small_pools_out_and_counts_them(sample):
    document = scan_json(*sample, "--min-tvl", "10000000", "--limit", "100")

    assert all(pool["tvl_usd"] >= 10_000_000 for pool in document["pools"])
    counts = document["counts"]
    assert counts["below_min_tvl"] > 0
    assert counts["analysed"] + counts["below_min_tvl"] + counts["zero_apy"] == 61


def test_pools_under_the_floor_are_left_out_unless_the_floor_is_lowered(sample_payload, tmp_path):
    tiny = dict(sample_payload["data"][0], pool="tiny-pool", tvlUsd=50_000)
    path = tmp_path / "tiny.json"
    path.write_text(json.dumps({"data": [tiny]}), encoding="utf-8")

    default = scan_json("--from-file", str(path))
    lowered = scan_json("--from-file", str(path), "--min-tvl", "0")

    assert default["pools"] == []
    assert default["counts"]["below_min_tvl"] == 1
    assert [pool["pool"] for pool in lowered["pools"]] == ["tiny-pool"]
    assert "LOW_TVL" in lowered["pools"][0]["flags"]


def test_size_days_and_haircut_change_the_result(sample):
    base = scan_json(*sample, "--limit", "100")
    bigger = scan_json(
        *sample, "--limit", "100", "--size", "100000", "--days", "365", "--haircut", "0"
    )

    assert bigger["assumptions"]["size_usd"] == 100000.0
    assert bigger["assumptions"]["days"] == 365
    assert bigger["assumptions"]["haircut"] == 0.0
    assert bigger["counts"]["cost_negative"] < base["counts"]["cost_negative"]


@pytest.mark.parametrize(
    "option",
    [
        ["--haircut", "1.5"],
        ["--haircut", "-0.1"],
        ["--days", "0"],
        ["--size", "0"],
        ["--limit", "0"],
    ],
)
def test_nonsense_options_are_rejected(sample, option):
    result = runner.invoke(app, ["scan", *sample, *option])

    assert result.exit_code == 2


def test_a_missing_file_is_a_usage_error():
    result = runner.invoke(app, ["scan", "--from-file", "/no/such/file.json"])

    assert result.exit_code == 2


def test_a_file_that_is_not_a_pools_response_is_an_error(tmp_path):
    path = tmp_path / "other.json"
    path.write_text('{"hello": "world"}', encoding="utf-8")

    result = runner.invoke(app, ["scan", "--from-file", str(path)])

    assert result.exit_code == 1
    assert result.stderr.startswith("error: expected a JSON object with a 'data' list of pools")


def test_no_arguments_print_help():
    result = runner.invoke(app, [])

    assert "scan" in result.output
    assert "report" in result.output


# JSON output


def test_json_output_is_pure_json_with_the_documented_shape(sample, sample_payload):
    result = runner.invoke(app, ["scan", *sample, "--json", "--limit", "5"])

    assert result.exit_code == 0
    document = json.loads(result.stdout)  # the whole of stdout is one JSON document
    assert set(document) == {
        "tool", "version", "disclaimer", "source", "price_source", "assumptions", "chain_costs",
        "counts", "notes", "pools",
    }  # fmt: skip
    assert len(document["pools"]) == 5
    analysed = len(sample_pools(sample_payload, advertising_apy=True))
    assert document["counts"] == {
        "analysed": analysed,
        "cost_negative": document["counts"]["cost_negative"],
        "shown": 5,
        "below_min_tvl": 0,
        "zero_apy": 61 - analysed,
        "skipped_entries": 0,
        "il_estimated": 0,  # an offline run without --prices-file estimates nothing
        "il_unknown": document["counts"]["il_unknown"],
    }


def test_json_puts_cost_negative_pools_first_by_advertised_apy_then_the_rest_by_net_apy(sample):
    pools = scan_json(*sample, "--limit", "100")["pools"]

    negative = [pool for pool in pools if "COST_NEGATIVE" in pool["flags"]]
    rest = [pool for pool in pools if "COST_NEGATIVE" not in pool["flags"]]
    assert negative
    assert rest
    assert pools == negative + rest
    assert [pool["apy"] for pool in negative] == sorted(
        (pool["apy"] for pool in negative), reverse=True
    )
    assert [pool["net_apy"] for pool in rest] == sorted(
        (pool["net_apy"] for pool in rest), reverse=True
    )


def test_the_most_misleading_pools_of_the_sample_lead(sample):
    pools = scan_json(*sample, "--limit", "3")["pools"]

    # They advertise 8.24%, 6.77% and 4.67%, and each of them loses money after costs.
    assert [pool["symbol"] for pool in pools] == ["USD.INFRA", "ETH-USDC", "GROVE-STEAKUSDC"]
    assert [pool["apy"] for pool in pools] == pytest.approx([8.24, 6.77, 4.67], abs=0.005)
    assert all("COST_NEGATIVE" in pool["flags"] and pool["net_usd"] < 0 for pool in pools)


# Pools that advertise no APY


def test_pools_advertising_no_apy_are_left_out_and_counted(sample, sample_payload):
    zero = {pool["pool"] for pool in sample_pools(sample_payload, advertising_apy=False)}

    document = scan_json(*sample, "--limit", "100")

    assert zero
    assert not zero & {pool["pool"] for pool in document["pools"]}
    assert all(pool["apy"] > 0 for pool in document["pools"])
    assert document["counts"]["zero_apy"] == len(zero)
    assert document["assumptions"]["include_zero_apy"] is False


def test_include_zero_apy_brings_them_back(sample, sample_payload):
    zero = {pool["pool"] for pool in sample_pools(sample_payload, advertising_apy=False)}

    document = scan_json(*sample, "--limit", "100", "--include-zero-apy")

    pools = document["pools"]
    assert zero <= {pool["pool"] for pool in pools}
    assert document["counts"]["analysed"] == 61
    assert document["counts"]["zero_apy"] == 0
    assert document["assumptions"]["include_zero_apy"] is True
    # They advertise the least, so they come last among the cost-negative pools.
    negative = [pool for pool in pools if "COST_NEGATIVE" in pool["flags"]]
    assert {pool["pool"] for pool in negative[-len(zero) :]} == zero


def test_the_text_report_says_how_many_zero_apy_pools_were_left_out(sample, sample_payload):
    zero = len(sample_pools(sample_payload, advertising_apy=False))

    default = runner.invoke(app, ["scan", *sample]).stdout
    included = runner.invoke(app, ["scan", *sample, "--include-zero-apy"]).stdout

    assert f"{zero} advertising an APY of 0 or less" in default
    assert "--include-zero-apy" in default
    assert "0 advertising an APY of 0 or less" in included
    assert re.search(r"Zero-APY pools\s+kept", included)


def test_json_net_apy_matches_a_hand_calculation(sample):
    pools = {p["pool"]: p for p in scan_json(*sample, "--limit", "100")["pools"]}

    # Arbitrum sky-lending sUSDS: 3.8% base APY, a single stablecoin (no swap), three
    # transactions at the default $0.0315 each (250,000 gas * 0.02 gwei * $2,300 + $0.02).
    susds = pools["3637ce7b-529b-49c1-964c-710a50b2939c"]
    assert susds["gas_usd"] == pytest.approx(0.0945)
    assert susds["swap_usd"] == 0
    assert susds["net_apy"] == pytest.approx(3.8 - 0.0945 / 1000 * 365 / 30 * 100, abs=1e-5)
    assert susds["break_even_size_usd"] == pytest.approx(0.0945 / (0.038 * 30 / 365), rel=1e-5)
    assert susds["flags"] == []


def test_json_lists_reward_and_underlying_tokens(sample):
    pools = {p["pool"]: p for p in scan_json(*sample, "--limit", "100")["pools"]}

    susds = pools["3637ce7b-529b-49c1-964c-710a50b2939c"]
    assert susds["reward_tokens"] == []
    assert susds["underlying_tokens"] == ["0x6491c05a82219b8d1479057361ff1654749b876b"]
    gtusdgp = next(p for p in pools.values() if p["symbol"] == "GTUSDGP")
    assert gtusdgp["reward_tokens"] == ["0x912ce59144191c1204e64559fe8253a0e49e6548"]


def test_json_every_il_risk_pool_in_the_sample_is_also_il_unknown(sample):
    pools = scan_json(*sample, "--limit", "100", "--include-zero-apy")["pools"]

    il_risk = [pool for pool in pools if "IL_RISK" in pool["flags"]]
    assert len(il_risk) == 28
    assert all("IL_UNKNOWN" in pool["flags"] and pool["il_usd"] == 0 for pool in il_risk)


# Impermanent loss from token prices


def write_json(path, payload) -> str:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def raw_pool(sample_payload, **overrides) -> dict:
    """A raw pool for the tests below, based on a real pool of the sample."""
    fields = dict(
        sample_payload["data"][0],
        tvlUsd=5_000_000,
        stablecoin=False,
        exposure="multi",
        ilRisk="no",
        apyReward=None,
        outlier=False,
        apyMean30d=None,
        underlyingTokens=[],
    )
    return {**fields, **overrides}


def pools_by_name(document) -> dict:
    return {(pool["chain"], pool["symbol"]): pool for pool in document["pools"]}


def test_a_prices_file_gives_the_pools_shown_an_estimated_il(sample, prices_path):
    document = scan_json(*sample, "--prices-file", str(prices_path), "--limit", "10")

    pools = pools_by_name(document)
    eth_usdc = pools[("Arbitrum", "ETH-USDC")]
    assert "IL_ESTIMATED" in eth_usdc["flags"]
    assert "IL_UNKNOWN" not in eth_usdc["flags"]
    assert eth_usdc["il_volatility"] == pytest.approx(0.399567, abs=1e-6)  # WETH / USDC
    assert eth_usdc["il_usd"] == pytest.approx(1000 * 0.399567**2 * (30 / 365) / 8, abs=1e-4)
    assert eth_usdc["costs_usd"] == pytest.approx(
        eth_usdc["gas_usd"] + eth_usdc["swap_usd"] + eth_usdc["il_usd"]
    )
    wbtc_usdc = pools[("Arbitrum", "WBTC.B-USDC")]
    assert wbtc_usdc["il_volatility"] == pytest.approx(0.259566, abs=1e-6)  # WBTC / USDC
    assert wbtc_usdc["il_usd"] == pytest.approx(0.6922, abs=1e-4)
    assert document["price_source"] == f"file {prices_path}"
    assert document["counts"]["il_estimated"] == 2


def test_pools_that_cannot_be_estimated_stay_unknown(sample, prices_path):
    document = scan_json(*sample, "--prices-file", str(prices_path), "--limit", "10")

    pools = pools_by_name(document)
    one_token = pools[("Base", "GROVE-STEAKUSDC")]
    no_prices = pools[("Base", "VVV-DIEM")]  # two tokens, but the saved prices have neither
    for pool in (one_token, no_prices):
        assert "IL_RISK" in pool["flags"]
        assert "IL_UNKNOWN" in pool["flags"]
        assert pool["il_usd"] == 0
        assert pool["il_volatility"] is None
    assert document["counts"]["il_unknown"] == 3  # those two and KPK-EURE-YIELD (one token)
    assert any("not estimated for 1 pool" in note for note in document["notes"])


def test_estimating_il_does_not_change_which_pools_are_shown(sample, prices_path):
    without = scan_json(*sample, "--limit", "10")["pools"]
    with_prices = scan_json(*sample, "--prices-file", str(prices_path), "--limit", "10")["pools"]

    assert {pool["pool"] for pool in without} == {pool["pool"] for pool in with_prices}
    # and each pool's advertised APY is still what it was
    assert [pool["apy"] for pool in with_prices] == sorted(
        (pool["apy"] for pool in with_prices), reverse=True
    )


def test_the_text_report_says_how_much_il_was_estimated(sample, prices_path):
    result = runner.invoke(
        app, ["scan", *sample, "--prices-file", str(prices_path), "--limit", "10"]
    )

    assert (
        "Impermanent loss: estimated from token prices for 2 of the 5 pools shown" in result.stdout
    )
    assert f"Prices: file {prices_path}" in result.stdout
    assert "IL_ESTIMATED" in result.stdout


def test_an_offline_run_without_a_prices_file_leaves_il_unknown_and_says_why(sample, http_mock):
    document = scan_json(*sample, "--limit", "10")

    assert document["price_source"].startswith("none (offline")
    assert document["counts"]["il_estimated"] == 0
    assert all("IL_ESTIMATED" not in pool["flags"] for pool in document["pools"])
    assert any("--prices-file" in note for note in document["notes"])
    assert http_mock.calls.call_count == 0


def test_no_note_about_prices_when_no_pool_shown_needs_them(sample):
    document = scan_json(*sample, "--limit", "1")  # USD.INFRA has no impermanent loss risk

    assert not any("--prices-file" in note for note in document["notes"])


def test_a_prices_file_makes_no_network_calls(sample, prices_path, http_mock):
    runner.invoke(app, ["scan", *sample, "--prices-file", str(prices_path)])

    assert http_mock.calls.call_count == 0


@pytest.mark.parametrize("content", ['{"hello": "world"}', "not json", "[]"])
def test_a_prices_file_that_is_not_a_chart_response_is_an_error(sample, tmp_path, content):
    path = tmp_path / "prices.json"
    path.write_text(content, encoding="utf-8")

    result = runner.invoke(app, ["scan", *sample, "--prices-file", str(path)])

    assert result.exit_code == 1
    assert result.stderr.startswith("error: ")
    assert "--from-file" not in result.stderr  # that hint is for a failed live fetch


def test_a_missing_prices_file_is_a_usage_error(sample):
    result = runner.invoke(app, ["scan", *sample, "--prices-file", "/no/such/prices.json"])

    assert result.exit_code == 2


@pytest.fixture
def flip_files(tmp_path, sample_payload):
    """Two pools and prices for them, made so that the numbers can be worked out by hand.

    "lp" advertises 8% and holds two tokens, one of which swings by +-2% a day. "plain" advertises
    3% and has no impermanent loss risk.
    """
    lp = raw_pool(
        sample_payload,
        pool="lp",
        symbol="A-B",
        apy=8.0,
        apyBase=8.0,
        ilRisk="yes",
        underlyingTokens=["0xAAA", "0xbbb"],
    )
    plain = raw_pool(
        sample_payload, pool="plain", symbol="PLAIN", apy=3.0, apyBase=3.0, exposure="single"
    )
    swings = prices_with_returns([0.02, -0.02] * 14)  # 28 daily returns: 14 up and 14 down
    prices = chart({"arbitrum:0xaaa": swings, "arbitrum:0xbbb": [1.0] * 29})
    return [
        "--from-file",
        write_json(tmp_path / "pools.json", {"data": [lp, plain]}),
        "--prices-file",
        write_json(tmp_path / "prices.json", prices),
    ]


def test_il_can_turn_a_pool_cost_negative_and_move_it_to_the_top(flip_files):
    # Arbitrum costs $0.0315 a transaction, so gas is 3 * 0.0315 = $0.0945 and the swaps $6.00.
    # "lp" pays 1000 * 8% * 30/365 = $6.5753: +$0.48 before impermanent loss.
    # The ratio of its tokens moves +-2% a day, 28 times: the mean is 0 and the sample variance is
    # 28 * 0.02**2 / 27, so over 30 days IL = 1000 * 0.02**2 * (28/27) * 30 / 8 = $1.5556.
    document = scan_json(*flip_files)

    lp, plain = document["pools"]
    assert (lp["pool"], plain["pool"]) == ("lp", "plain")  # it advertises more, so it leads
    assert lp["flags"] == ["COST_NEGATIVE", "IL_RISK", "IL_ESTIMATED"]
    assert lp["il_usd"] == pytest.approx(1000 * 0.02**2 * (28 / 27) * 30 / 8)
    assert lp["net_usd"] == pytest.approx(6.5753 - 0.0945 - 6.0 - 1.5556, abs=1e-4)
    assert document["counts"]["cost_negative"] == 2  # both lose money now


def test_without_the_prices_the_same_pool_is_not_cost_negative(flip_files):
    pools_only = flip_files[:2]

    plain, lp = scan_json(*pools_only)["pools"]

    assert (plain["pool"], lp["pool"]) == ("plain", "lp")  # "plain" is the one that loses money
    assert "COST_NEGATIVE" not in lp["flags"]
    assert lp["flags"] == ["IL_RISK", "IL_UNKNOWN"]


def test_only_negative_looks_at_the_pools_that_lose_money_before_il_is_known(flip_files):
    # Prices are only looked up for the pools that are going to be shown, and --only-negative
    # decides that before there is an IL estimate. A pool that only loses money once IL is counted
    # is therefore not found by it: that is a documented limit, not an accident.
    document = scan_json(*flip_files, "--only-negative")

    assert [pool["pool"] for pool in document["pools"]] == ["plain"]
    assert document["counts"]["cost_negative"] == 1


def test_a_limit_of_one_does_not_look_prices_up_for_the_pool_left_out(flip_files):
    document = scan_json(*flip_files, "--limit", "1")

    assert [pool["pool"] for pool in document["pools"]] == ["plain"]
    assert document["counts"]["il_estimated"] == 0


# HTML report


def test_report_writes_a_self_contained_page(sample, tmp_path):
    out = tmp_path / "nested" / "report.html"

    result = runner.invoke(app, ["report", *sample, "--out", str(out)])

    assert result.exit_code == 0, result.stderr
    assert "Wrote" in result.stdout
    page = out.read_text(encoding="utf-8")
    inspector = inspect_page(page)
    assert {"script", "link", "img", "iframe"}.isdisjoint(inspector.tags)
    assert {"src", "srcset"}.isdisjoint(inspector.attribute_names)
    assert inspector.hrefs
    assert all(href.startswith("https://defillama.com/") for href in inspector.hrefs)
    urls = re.findall(r"https?://[^\s\"'<>)]+", page)
    assert all(url.startswith("https://defillama.com/") for url in urls)
    assert "not financial advice" in page
    assert "Reward haircut" in page


def test_report_uses_the_same_options_as_scan(sample, tmp_path):
    out = tmp_path / "r.html"

    result = runner.invoke(
        app,
        ["report", *sample, "--out", str(out), "--chain", "Base", "--limit", "3", "--days", "7"],
    )

    assert result.exit_code == 0
    page = out.read_text(encoding="utf-8")
    assert "held 7 days" in page
    assert page.count("<tr") == 3 + 1  # three pools and the header row
    assert "Solana" not in page.split("<h2>Flags</h2>")[0]


def test_report_with_a_prices_file_shows_the_estimated_il(sample, prices_path, tmp_path):
    out = tmp_path / "r.html"

    result = runner.invoke(
        app,
        ["report", *sample, "--prices-file", str(prices_path), "--limit", "10", "--out", str(out)],
    )

    assert result.exit_code == 0, result.stderr
    page = out.read_text(encoding="utf-8")
    assert page.count("IL est.") == 2
    assert "IL_ESTIMATED" in page
    assert f"Prices: file {prices_path}" in page
    assert "lower bound" in page


def test_report_makes_no_network_calls_from_a_file(sample, tmp_path, http_mock):
    runner.invoke(app, ["report", *sample, "--out", str(tmp_path / "r.html")])

    assert http_mock.calls.call_count == 0


def test_report_cannot_write_into_a_file_path_that_is_a_directory(sample, tmp_path):
    result = runner.invoke(app, ["report", *sample, "--out", str(tmp_path)])

    assert result.exit_code == 1
    assert "cannot write" in result.stderr


# Live path, with the network mocked


@pytest.fixture
def live(http_mock, sample_payload, prices_payload, chart_server):
    """The public endpoints, answering: pools, native prices, gas prices and token prices."""
    pools = http_mock.get(data.YIELDS_URL).respond(json=sample_payload)
    http_mock.get(costs.NATIVE_PRICES_URL).respond(
        json={
            "coins": {
                "coingecko:ethereum": {"price": 3000.0},
                "coingecko:solana": {"price": 150.0},
            }
        }
    )
    rpc = {}
    for chain in ("Arbitrum", "OP Mainnet", "Base"):
        rpc[chain] = http_mock.post(COST_TABLE[chain].rpc_url).respond(
            json={"jsonrpc": "2.0", "id": 1, "result": hex(1_000_000_000)}
        )
    return SimpleNamespace(pools=pools, rpc=rpc, chart=chart_server(prices_payload["coins"]))


COIN_PREFIXES = {
    "Arbitrum": "arbitrum",
    "OP Mainnet": "optimism",
    "Base": "base",
    "Solana": "solana",
}


def coins_of_pools_with_il_risk(document) -> set[str]:
    """The coin keys of the shown pools that hold two tokens and have impermanent loss risk."""
    coins = set()
    for pool in document["pools"]:
        tokens = pool["underlying_tokens"]
        if "IL_RISK" in pool["flags"] and len(tokens) == 2:
            prefix = COIN_PREFIXES[pool["chain"]]
            coins |= {f"{prefix}:{t if prefix == 'solana' else t.lower()}" for t in tokens}
    return coins


def test_without_a_file_the_pools_and_costs_come_from_the_public_endpoints(live, http_mock):
    document = scan_json("--chain", "Arbitrum", "--limit", "5")

    assert document["source"] == "DeFiLlama yields API (live)"
    arbitrum = document["chain_costs"]["Arbitrum"]
    assert arbitrum["gas_price_source"] == "live"
    assert arbitrum["native_price_usd"] == 3000.0
    assert arbitrum["usd_per_tx"] == pytest.approx(0.77)
    assert {call.request.method for call in http_mock.calls} == {"GET", "POST"}
    hosts = {call.request.url.host for call in http_mock.calls}
    assert hosts == {"yields.llama.fi", "coins.llama.fi", "arb1.arbitrum.io"}


def test_token_prices_come_from_the_chart_endpoint_and_nothing_is_noted(live, sample_payload):
    weth, usdc = (
        "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
        "0xAF88d065e77c8cC2239327C5EDb3A432268e5831",  # as DeFiLlama writes it: mixed case
    )
    eth_usdc = raw_pool(
        sample_payload, pool="eth-usdc", symbol="ETH-USDC", apy=8.0, apyBase=8.0, ilRisk="yes"
    )
    live.pools.respond(json={"data": [{**eth_usdc, "underlyingTokens": [weth, usdc]}]})

    document = scan_json()

    assert document["price_source"] == "DeFiLlama chart API (live)"
    assert document["notes"] == []
    (pool,) = document["pools"]
    assert "IL_ESTIMATED" in pool["flags"]
    assert pool["il_volatility"] == pytest.approx(0.399567, abs=1e-6)
    assert live.chart.requests == [[f"arbitrum:{weth}", f"arbitrum:{usdc.lower()}"]]
    assert live.chart.route.call_count == 1


def test_prices_are_only_looked_up_for_the_pools_shown(live):
    document = scan_json("--limit", "6")

    asked = [key for request in live.chart.requests for key in request]
    wanted = coins_of_pools_with_il_risk(document)
    assert wanted  # the pools shown include some with two tokens and impermanent loss risk
    assert set(asked) == wanted
    assert len(asked) == len(wanted)  # each coin once, although some are shared by several pools


def test_a_long_list_looks_up_prices_in_batches_of_ten(live):
    document = scan_json("--limit", "100", "--include-zero-apy")

    wanted = coins_of_pools_with_il_risk(document)
    assert len(wanted) == 26  # 24 pools, and the same token appears in several of them
    assert [len(request) for request in live.chart.requests] == [10, 10, 6]
    assert (
        document["counts"]["il_estimated"] == 10
    )  # the pools whose tokens are in the saved prices
    assert document["counts"]["il_unknown"] == 14 + 4  # no prices for them, or just one token


def test_a_saved_prices_file_replaces_the_chart_endpoint(live, prices_path):
    document = scan_json("--limit", "10", "--prices-file", str(prices_path))

    assert live.chart.requests == []
    assert document["price_source"] == f"file {prices_path}"
    assert document["counts"]["il_estimated"] >= 1


def test_a_failing_chart_endpoint_does_not_stop_the_scan_and_the_output_says_so(live):
    live.chart.route.mock(side_effect=lambda request: httpx.Response(503))

    document = scan_json("--limit", "2")

    assert document["counts"]["il_estimated"] == 0
    assert document["counts"]["il_unknown"] >= 1
    assert document["notes"] == ["Token prices: 2 of 2 coins could not be fetched (HTTP 503)."]
    assert live.chart.route.call_count == 2  # the request, and one retry


def test_a_chart_endpoint_that_rejects_a_coin_costs_only_that_coin(live, prices_payload):
    weth = "arbitrum:0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
    server = live.chart
    original = server.route.side_effect

    def reject_weth(request):
        if weth in request.url.path:
            return httpx.Response(400, json={"message": "bad coin"})
        return original(request)

    server.route.mock(side_effect=reject_weth)

    document = scan_json("--chain", "Arbitrum", "--limit", "10", "--include-zero-apy")

    # WETH is in the pools of Arbitrum with WBTC and USDC: those with WETH cannot be estimated.
    estimated = {p["symbol"] for p in document["pools"] if "IL_ESTIMATED" in p["flags"]}
    assert estimated == {"WBTC.B-USDC"}
    assert any("could not be fetched (HTTP 400)" in note for note in document["notes"])


def test_a_failing_rpc_does_not_stop_the_scan_and_the_output_says_so(live):
    live.rpc["Base"].respond(503)

    result = runner.invoke(app, ["scan", "--chain", "Base"])

    assert result.exit_code == 0
    assert "Base: gas price unavailable" in result.stdout
    assert "0.0060 gwei (default)" in result.stdout


def test_when_the_pools_endpoint_is_down_the_error_suggests_from_file(http_mock):
    http_mock.get(data.YIELDS_URL).respond(502)

    result = runner.invoke(app, ["scan"])

    assert result.exit_code == 1
    assert result.stderr.startswith("error: GET https://yields.llama.fi/pools failed (HTTP 502)")
    assert "--from-file" in result.stderr
    assert result.stdout == ""
