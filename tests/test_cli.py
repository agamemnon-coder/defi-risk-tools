"""The command line, run on the saved sample. Nothing here touches the network."""

import json
import re

import pytest
from html_tools import inspect_page
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


def scan_json(*args) -> dict:
    result = runner.invoke(app, ["scan", "--json", *args])
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)


# Text output


def test_scan_prints_a_table_from_the_sample(sample):
    result = runner.invoke(app, ["scan", *sample, "--limit", "10"])

    assert result.exit_code == 0, result.stderr
    header = next(line for line in result.stdout.splitlines() if line.startswith("chain"))
    assert re.sub(r"\s{2,}", "  ", header) == TABLE_HEADER
    rows = table_rows(result.stdout)
    assert len(rows) == 10
    assert all("COST_NEGATIVE" in row for row in rows)  # the worst ten are all cost-negative
    assert "Pools analysed: 61 on Arbitrum, OP Mainnet, Base, Solana." in result.stdout
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


def test_chain_option_filters_ignores_case_and_repeats(sample):
    document = scan_json(*sample, "--chain", "solana", "--chain", "op mainnet", "--limit", "100")

    assert {pool["chain"] for pool in document["pools"]} == {"Solana", "OP Mainnet"}
    assert list(document["chain_costs"]) == ["Solana", "OP Mainnet"]
    assert document["counts"]["analysed"] == 16 + 15


def test_a_chain_given_twice_is_scanned_once(sample):
    document = scan_json(*sample, "--chain", "base", "--chain", "Base", "--limit", "100")

    assert list(document["chain_costs"]) == ["Base"]
    assert document["counts"]["analysed"] == 15


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
    assert counts["analysed"] + counts["below_min_tvl"] == 61


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


def test_json_output_is_pure_json_with_the_documented_shape(sample):
    result = runner.invoke(app, ["scan", *sample, "--json", "--limit", "5"])

    assert result.exit_code == 0
    document = json.loads(result.stdout)  # the whole of stdout is one JSON document
    assert set(document) == {
        "tool", "version", "disclaimer", "source", "assumptions", "chain_costs", "counts",
        "notes", "pools",
    }  # fmt: skip
    assert len(document["pools"]) == 5
    assert document["counts"] == {
        "analysed": 61,
        "cost_negative": document["counts"]["cost_negative"],
        "shown": 5,
        "below_min_tvl": 0,
        "skipped_entries": 0,
    }


def test_json_is_sorted_cost_negative_first_then_lowest_net_apy_first(sample):
    pools = scan_json(*sample, "--limit", "100")["pools"]

    keys = [("COST_NEGATIVE" not in pool["flags"], pool["net_apy"]) for pool in pools]
    assert keys == sorted(keys)
    assert pools[0]["flags"][0] == "COST_NEGATIVE"
    assert "COST_NEGATIVE" not in pools[-1]["flags"]


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
    pools = scan_json(*sample, "--limit", "100")["pools"]

    il_risk = [pool for pool in pools if "IL_RISK" in pool["flags"]]
    assert len(il_risk) == 28
    assert all("IL_UNKNOWN" in pool["flags"] and pool["il_usd"] == 0 for pool in il_risk)


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


def test_report_makes_no_network_calls_from_a_file(sample, tmp_path, http_mock):
    runner.invoke(app, ["report", *sample, "--out", str(tmp_path / "r.html")])

    assert http_mock.calls.call_count == 0


def test_report_cannot_write_into_a_file_path_that_is_a_directory(sample, tmp_path):
    result = runner.invoke(app, ["report", *sample, "--out", str(tmp_path)])

    assert result.exit_code == 1
    assert "cannot write" in result.stderr


# Live path, with the network mocked


@pytest.fixture
def live(http_mock, sample_payload):
    """The four public endpoints, answering."""
    http_mock.get(data.YIELDS_URL).respond(json=sample_payload)
    http_mock.get(costs.NATIVE_PRICES_URL).respond(
        json={
            "coins": {
                "coingecko:ethereum": {"price": 3000.0},
                "coingecko:solana": {"price": 150.0},
            }
        }
    )
    routes = {}
    for chain in ("Arbitrum", "OP Mainnet", "Base"):
        routes[chain] = http_mock.post(COST_TABLE[chain].rpc_url).respond(
            json={"jsonrpc": "2.0", "id": 1, "result": hex(1_000_000_000)}
        )
    return routes


def test_without_a_file_the_pools_and_costs_come_from_the_public_endpoints(live, http_mock):
    document = scan_json("--limit", "3")

    assert document["source"] == "DeFiLlama yields API (live)"
    assert document["notes"] == []
    arbitrum = document["chain_costs"]["Arbitrum"]
    assert arbitrum["gas_price_source"] == "live"
    assert arbitrum["native_price_usd"] == 3000.0
    assert arbitrum["usd_per_tx"] == pytest.approx(0.77)
    assert http_mock.calls.call_count == 5  # pools, prices, three RPC reads
    assert {call.request.method for call in http_mock.calls} == {"GET", "POST"}


def test_a_failing_rpc_does_not_stop_the_scan_and_the_output_says_so(live):
    live["Base"].respond(503)

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
