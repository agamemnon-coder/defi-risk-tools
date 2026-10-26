"""Rendering: number formats, the JSON document, and the HTML page."""

import json
import re
from datetime import date

import pytest
from html_tools import inspect_page

from defi_risk_tools import report
from defi_risk_tools.costs import resolve_tx_costs
from defi_risk_tools.model import Assumptions, evaluate_pool, rank
from defi_risk_tools.report import Scan

DEFILLAMA = "https://defillama.com/"
FORBIDDEN_TAGS = {"script", "link", "img", "iframe", "object", "embed", "video", "audio", "source"}


@pytest.fixture
def build_scan(make_pool):
    """Turn pools into a `Scan` the way the CLI does, with the default costs."""

    def build(pools, notes=None) -> Scan:
        assumptions = Assumptions()
        tx_costs, cost_notes = resolve_tx_costs(["Arbitrum"], client=None)
        results = rank(
            [evaluate_pool(pool, assumptions, tx_costs["Arbitrum"].usd) for pool in pools]
        )
        return Scan(
            rows=results,
            assumptions=assumptions,
            min_tvl_usd=100_000.0,
            tx_costs=tx_costs,
            source="a test",
            analysed=len(results),
            cost_negative=sum(result.cost_negative for result in results),
            below_min_tvl=0,
            skipped_entries=0,
            notes=cost_notes if notes is None else notes,
        )

    return build


# Number formats


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0.0, "$0.00"),
        (4.654, "$4.65"),
        (73.0, "$73"),
        (999.4, "$999"),
        (1_234.0, "$1.2k"),
        (458_171.0, "$458.2k"),
        (67_518_464.0, "$67.5M"),
        (886_390_610.0, "$886.4M"),
        (999_949.0, "$999.9k"),
        (999_990.0, "$1.0M"),
        (3_200_000_000.0, "$3.2B"),
    ],
)
def test_compact_money(value, text):
    assert report.format_compact_money(value) == text


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (6.0, "$6.00"),
        (0.0315, "$0.03"),
        (1234.5, "$1,234.50"),
        (-6.09, "-$6.09"),
        (-0.001, "-$0.00"),
    ],
)
def test_money_puts_the_sign_before_the_dollar(value, text):
    assert report.format_money(value) == text


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (-7.4128, "-7.4%"),
        (3.685, "+3.7%"),
        (-0.0185, "-0.02%"),
        (0.4, "+0.40%"),
        (1234.56, "+1,234.6%"),
    ],
)
def test_net_apy_uses_two_decimals_below_one_percent(value, text):
    assert report.format_net_apy(value) == text


def test_plain_money_drops_cents_only_when_there_are_none():
    assert report.format_plain_money(1000.0) == "$1,000"
    assert report.format_plain_money(2500.5) == "$2,500.50"


def test_clip_shortens_long_names():
    assert report.clip("short", 18) == "short"
    assert report.clip("a-very-long-pool-name-indeed", 18) == "a-very-long-poo..."
    assert len(report.clip("a-very-long-pool-name-indeed", 18)) == 18


# Text


def test_text_report_has_the_columns_in_the_design_order(build_scan, make_pool):
    text = report.render_text(build_scan([make_pool()]))

    header = next(line for line in text.splitlines() if line.startswith("chain"))
    columns = re.split(r"\s{2,}", header.strip())
    assert columns == [
        "chain",
        "project",
        "symbol",
        "TVL",
        "APY (base+reward)",
        "costs",
        "net APY",
        "break-even size",
        "flags",
    ]


def test_text_report_prints_assumptions_disclaimer_and_notes(build_scan, make_pool):
    text = report.render_text(build_scan([make_pool()], notes=["Something fell back."]))

    assert "Estimates only, not financial advice" in text
    assert "Position size" in text
    assert "$1,000" in text
    assert "30 days" in text
    assert "50% of the reward APY is assumed lost" in text
    assert "0.30% each way" in text
    assert "capped at 50% of the position" in text
    assert "Cost of one transaction" in text
    assert "Something fell back." in text


def test_text_report_without_rows_says_so(build_scan):
    assert "No pools match." in report.render_text(build_scan([]))


def test_long_names_are_clipped_in_the_text_table(build_scan, make_pool):
    pool = make_pool(project="p" * 40, symbol="s" * 40)

    text = report.render_text(build_scan([pool]))

    assert "p" * 40 not in text
    assert "ppppppppppppppp..." in text


# JSON


def test_json_document_holds_assumptions_costs_counts_and_pools(build_scan, make_pool):
    document = json.loads(report.render_json(build_scan([make_pool(), make_pool(pool_id="b")])))

    assert document["tool"] == "yield-scan"
    assert "not financial advice" in document["disclaimer"]
    assert document["assumptions"] == {
        "size_usd": 1000.0,
        "days": 30,
        "haircut": 0.5,
        "swap_rate": 0.003,
        "enter_txs": 2,
        "exit_txs": 1,
        "claim_every_days": 30,
        "il_cap": 0.5,
        "min_tvl_usd": 100000.0,
    }
    assert document["chain_costs"]["Arbitrum"]["gas_price_source"] == "default"
    assert document["counts"]["analysed"] == 2
    assert document["notes"]
    pool = document["pools"][0]
    assert set(pool) == {
        "pool", "chain", "project", "symbol", "tvl_usd", "apy", "apy_base", "apy_reward",
        "gross_usd", "gas_usd", "swap_usd", "il_usd", "costs_usd", "net_usd", "net_apy",
        "break_even_size_usd", "break_even_days", "flags", "reward_tokens", "underlying_tokens",
    }  # fmt: skip


def test_json_uses_null_for_no_break_even(build_scan, make_pool):
    document = json.loads(report.render_json(build_scan([make_pool(apy_base=0.0)])))

    pool = document["pools"][0]
    assert pool["break_even_size_usd"] is None
    assert pool["break_even_days"] is None
    assert pool["flags"] == ["COST_NEGATIVE"]


def test_json_leaves_gas_fields_empty_on_a_fixed_fee_chain(make_pool):
    tx_costs, _ = resolve_tx_costs(["Solana"], client=None)
    scan = Scan(
        rows=[], assumptions=Assumptions(), min_tvl_usd=0.0, tx_costs=tx_costs, source="t",
        analysed=0, cost_negative=0, below_min_tvl=0, skipped_entries=0, notes=[],
    )  # fmt: skip

    solana = json.loads(report.render_json(scan))["chain_costs"]["Solana"]

    assert solana["gas_price_gwei"] is None
    assert solana["gas_price_source"] is None
    assert solana["fixed_fee_native"] == 0.00005


# HTML


def test_html_page_is_self_contained(build_scan, make_pool):
    page = report.render_html(build_scan([make_pool(), make_pool(pool_id="b")]), date(2026, 10, 26))

    inspector = inspect_page(page)
    assert page.startswith("<!doctype html>")
    assert "style" in inspector.tags
    assert not inspector.tags & FORBIDDEN_TAGS
    assert "src" not in inspector.attribute_names
    assert "srcset" not in inspector.attribute_names
    assert "@import" not in page
    assert "url(" not in page
    assert inspector.hrefs
    assert all(href.startswith(DEFILLAMA) for href in inspector.hrefs)


def test_html_page_shows_the_data_and_the_assumptions(build_scan, make_pool):
    pool = make_pool(symbol="SUSDS", project="sky-lending", pool_id="3637ce7b")
    page = report.render_html(build_scan([pool]), date(2026, 10, 26))

    assert "<title>Cost-Negative Yield Scanner</title>" in page
    assert "Estimates only, not financial advice" in page
    assert "SUSDS" in page
    assert "sky-lending" in page
    assert "https://defillama.com/yields/pool/3637ce7b" in page
    assert "Reward haircut" in page
    assert "Impermanent loss" in page
    assert "Cost of one transaction" in page
    assert "Limitations" in page
    assert "2026-10-26" in page
    for flag in ("COST_NEGATIVE", "REWARD_DEPENDENT", "APY_OUTLIER", "LOW_TVL", "IL_RISK"):
        assert flag in page  # the legend explains every flag, shown or not
    assert "IL_UNKNOWN" in page


def test_html_page_has_a_phone_friendly_viewport_and_dark_mode(build_scan, make_pool):
    page = report.render_html(build_scan([make_pool()]), date(2026, 10, 26))

    assert 'name="viewport"' in page
    assert "prefers-color-scheme:dark" in page
    assert "overflow-x:auto" in page


def test_html_escapes_everything_that_comes_from_the_data(build_scan, make_pool):
    hostile = make_pool(
        symbol="<script>alert(1)</script>",
        project='"><img src=x onerror=alert(1)>',
        chain="Arbi<b>trum",
        pool_id='a"b&c/d',
    )
    scan = build_scan([hostile])

    page = report.render_html(scan, date(2026, 10, 26))

    inspector = inspect_page(page)
    assert "script" not in inspector.tags
    assert "img" not in inspector.tags
    assert "<b>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "https://defillama.com/yields/pool/a%22b%26c%2Fd" in page
    assert all(href.startswith(DEFILLAMA) for href in inspector.hrefs)


def test_html_page_lists_notes_when_there_are_some(build_scan, make_pool):
    with_notes = report.render_html(
        build_scan([make_pool()], notes=["Base: gas fell back."]), date(2026, 1, 1)
    )
    without = report.render_html(build_scan([make_pool()], notes=[]), date(2026, 1, 1))

    assert "Base: gas fell back." in with_notes
    assert "<h2>Notes</h2>" in with_notes
    assert "<h2>Notes</h2>" not in without


def test_html_cost_negative_rows_are_marked(build_scan, make_pool):
    page = report.render_html(
        build_scan([make_pool(pool_id="loses", apy_base=0.0), make_pool(pool_id="wins")]),
        date(2026, 10, 26),
    )

    assert page.count('<tr class="cost-negative">') == 1
    assert page.index("loses") < page.index("wins")  # and it sorts first


def test_html_without_rows_says_so(build_scan):
    assert "No pools match." in report.render_html(build_scan([]), date(2026, 10, 26))
