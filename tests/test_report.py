"""Rendering: number formats, the JSON document, and the HTML page."""

import dataclasses
import json
import re
from datetime import date

import pytest
from html_tools import inspect_page

from defi_risk_tools import report
from defi_risk_tools.costs import resolve_tx_costs
from defi_risk_tools.model import (
    Assumptions,
    ChainSummary,
    evaluate_pool,
    rank,
    summarise_chains,
)
from defi_risk_tools.report import Scan

DEFILLAMA = "https://defillama.com/"
FORBIDDEN_TAGS = {"script", "link", "img", "iframe", "object", "embed", "video", "audio", "source"}


@pytest.fixture
def build_scan(make_pool):
    """Turn pools into a `Scan` the way the CLI does, with the default costs.

    All the pools are counted; only the first `limit` of them (best first) are shown.
    """

    def build(pools, notes=None, chains=("Arbitrum",), limit=None) -> Scan:
        assumptions = Assumptions()
        tx_costs, cost_notes = resolve_tx_costs(list(chains), client=None)
        results = rank(
            [
                evaluate_pool(pool, assumptions, tx_costs.get(pool.chain, tx_costs[chains[0]]).usd)
                for pool in pools
            ]
        )
        return Scan(
            rows=results[:limit],
            assumptions=assumptions,
            min_tvl_usd=100_000.0,
            include_zero_apy=False,
            tx_costs=tx_costs,
            source="a test",
            price_source="a prices test",
            analysed=len(results),
            cost_negative=sum(result.cost_negative for result in results),
            chain_summaries=summarise_chains(results, list(chains)),
            below_min_tvl=0,
            zero_apy=0,
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
    flat = " ".join(text.split())  # long lines are wrapped, so compare without the line breaks

    assert "Estimates only, not financial advice" in flat
    assert "Position size" in flat
    assert "$1,000" in flat
    assert "30 days" in flat
    assert "50% of the reward APY is assumed lost" in flat
    assert "none for a single-asset pool: you are assumed to hold its asset" in flat
    assert "swaps 50% of the position on the way in and back on the way out" in flat
    assert "at 0.30% (swap fee + slippage)" in flat
    assert "unknown exposure" in flat
    assert "volatility^2 x T/365 / 8 of the position" in flat
    assert "Capped at 50% of the position" in flat
    assert "Cost of one transaction" in flat
    assert "Something fell back." in flat


def test_long_lines_under_the_table_wrap_with_a_hanging_indent(build_scan, make_pool):
    text = report.render_text(build_scan([make_pool()], notes=["word " * 60]))
    lines = text.splitlines()

    start = lines.index("Assumptions")
    section = lines[start : lines.index("Cost of one transaction")]
    assert all(len(line) <= report.TEXT_WIDTH for line in section)
    impermanent = next(i for i, line in enumerate(section) if "Impermanent loss" in line)
    assert section[impermanent + 1].startswith(" " * 20)  # the text continues under the text
    notes = lines[lines.index("Notes") + 1 : -3]
    assert len(notes) > 1
    assert all(line.startswith("  ") and len(line) <= report.TEXT_WIDTH for line in notes)


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
        "multi_asset_swap_share": 0.5,
        "enter_txs": 2,
        "exit_txs": 1,
        "claim_every_days": 30,
        "il_cap": 0.5,
        "min_tvl_usd": 100000.0,
        "include_zero_apy": False,
    }
    assert document["chain_costs"]["Arbitrum"]["gas_price_source"] == "default"
    assert document["counts"]["analysed"] == 2
    assert document["notes"]
    pool = document["pools"][0]
    assert set(pool) == {
        "pool", "chain", "project", "symbol", "tvl_usd", "apy", "apy_base", "apy_reward",
        "gross_usd", "gas_usd", "swap_usd", "il_usd", "costs_usd", "net_usd", "net_apy",
        "break_even_size_usd", "break_even_days", "il_volatility", "flags", "reward_tokens",
        "underlying_tokens",
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
        rows=[], assumptions=Assumptions(), min_tvl_usd=0.0, include_zero_apy=True,
        tx_costs=tx_costs, source="t", price_source="p", analysed=0, cost_negative=0,
        chain_summaries=[], below_min_tvl=0, zero_apy=0, skipped_entries=0, notes=[],
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


# Impermanent loss that was estimated from prices


@pytest.fixture
def mixed_il_scan(build_scan, make_pool):
    """One pool whose IL was estimated, one with IL risk and no figure, one without IL risk."""
    lp = {"stablecoin": False, "exposure": "multi", "il_risk": True}
    estimated = make_pool(pool_id="estimated", il_volatility=0.4, **lp)
    unknown = make_pool(pool_id="unknown", **lp)
    plain = make_pool(pool_id="plain")
    return build_scan([estimated, unknown, plain])


def test_the_il_coverage_counts_what_was_estimated_among_the_pools_with_il_risk(mixed_il_scan):
    assert report.il_coverage(mixed_il_scan) == (2, 1, 1)  # at risk, estimated, unknown


def test_the_text_report_says_how_many_pools_have_an_estimated_il(mixed_il_scan):
    text = report.render_text(mixed_il_scan)

    assert (
        "Impermanent loss: estimated from token prices for 1 of the 2 analysed pools with IL risk."
        in text
    )
    assert "Prices: a prices test" in text


def test_the_il_line_is_left_out_when_no_pool_shown_has_il_risk(build_scan, make_pool):
    assert "Impermanent loss: estimated" not in report.render_text(build_scan([make_pool()]))


def test_json_counts_the_estimates_and_gives_each_pool_its_volatility(mixed_il_scan):
    document = json.loads(report.render_json(mixed_il_scan))

    assert document["price_source"] == "a prices test"
    assert document["counts"]["il_estimated"] == 1
    assert document["counts"]["il_unknown"] == 1
    volatilities = {pool["pool"]: pool["il_volatility"] for pool in document["pools"]}
    assert volatilities == {"estimated": 0.4, "unknown": None, "plain": None}


def test_html_labels_an_estimated_il_as_an_estimate(mixed_il_scan):
    page = report.render_html(mixed_il_scan, date(2026, 10, 26))

    assert page.count("IL est.") == 1  # only the pool that has one
    assert "IL_ESTIMATED" in page
    assert "a lower bound if concentrated" in page  # the legend explains the flag
    assert "Prices: a prices test" in page


def test_the_limitations_say_what_the_estimate_is_and_is_not():
    text = " ".join(report.LIMITATIONS)

    assert "a single-asset pool is assumed to need no swap" in text
    assert "if you must buy it first, that swap is not counted" in text

    assert "estimated, not measured" in text
    assert "50/50 constant-product pool" in text
    assert "lower bound" in text
    assert "Uniswap v3 and v4, Velodrome v3" in text
    assert "looked up for every pool analysed, not only the pools shown" in text
    assert "the counts and the order do not depend on the limit" in text
    assert "cost-negative counts are lower bounds" in text


# The summary by chain


@pytest.fixture
def three_chain_scan(build_scan, make_pool):
    """Six pools on two of the three chains scanned, of which only the first two are shown.

    By hand, for $1,000 held 30 days (gas is $0.0945 on Arbitrum and $0.07035 on Base for three
    transactions, a multi-asset pool swaps $3.00, and IL at a volatility of 0.4 is
    1000 * 0.4**2 * (30/365) / 8 = $1.64):
    a1 pays 5% on one asset: +$4.02.  a2 pays 2% and swaps: 1.64 - 0.09 - 3.00 = -$1.45.
    a3 pays 20%: 16.44 - 0.09 - 3.00 - 1.64 = +$11.70, with an estimated IL.
    a4 is a2 with IL risk and no estimate: -$1.45.  b1 pays 1% and swaps: -$2.25.  b2 pays 1% on
    one asset: +$0.75.
    """
    multi = {"stablecoin": False, "exposure": "multi"}
    pools = [
        make_pool(pool_id="a1", apy_base=5.0),
        make_pool(pool_id="a2", apy_base=2.0, **multi),
        make_pool(pool_id="a3", apy_base=20.0, il_risk=True, il_volatility=0.4, **multi),
        make_pool(pool_id="a4", apy_base=2.0, il_risk=True, **multi),
        make_pool(pool_id="b1", chain="Base", apy_base=1.0, **multi),
        make_pool(pool_id="b2", chain="Base", apy_base=1.0),
    ]
    return build_scan(pools, chains=("Arbitrum", "Base", "Solana"), limit=2)


def test_the_shares_are_worked_out_from_the_counts():
    assert report.format_share(ChainSummary("x", 3, 1, 0, 0, 0)) == "33.3%"
    assert report.format_share(ChainSummary("x", 4, 4, 0, 0, 0)) == "100.0%"
    assert report.format_share(ChainSummary("x", 0, 0, 0, 0, 0)) == "-"  # nothing to divide


def test_the_total_adds_the_chains_up():
    total = report.total_summary(
        [ChainSummary("a", 4, 2, 2, 1, 1), ChainSummary("b", 2, 1, 0, 0, 0)]
    )

    assert (total.analysed, total.cost_negative, total.il_risk) == (6, 3, 2)
    assert (total.il_estimated, total.il_unknown) == (1, 1)
    assert total.cost_negative_share == 0.5


def test_the_text_report_counts_every_pool_by_chain_whatever_is_shown(three_chain_scan):
    il_line = (
        "Impermanent loss: estimated from token prices for 1 of the 2 analysed pools with IL risk."
    )

    lines = report.render_text(three_chain_scan).splitlines()

    assert "Pools analysed: 6 on Arbitrum, Base, Solana. Cost-negative: 3. Shown: 2." in lines
    start = lines.index("By chain")
    assert lines[start - 2] == il_line  # the table follows the counts it breaks down
    table = lines[start + 1 : start + 7]
    cells = [re.split(r"\s{2,}", line.strip()) for line in table]
    assert cells[0] == ["chain", "analysed", "cost-negative", "share", "IL estimated"]
    assert set("".join(cells[1])) == {"-"}  # the rule
    assert cells[2:] == [
        ["Arbitrum", "4", "2", "50.0%", "1 of 2"],
        ["Base", "2", "1", "50.0%", "0 of 0"],
        ["Solana", "0", "0", "-", "0 of 0"],
        ["all chains", "6", "3", "50.0%", "1 of 2"],
    ]
    assert len({len(line) for line in table}) == 1  # the numbers line up on the right
    assert lines[start + 7] == ""  # and a blank line ends the table


def test_the_text_report_has_no_by_chain_table_without_summaries(build_scan):
    scan = build_scan([])
    without = dataclasses.replace(scan, chain_summaries=[])

    assert "By chain" in report.render_text(scan)
    assert "By chain" not in report.render_text(without)


def test_json_has_a_summary_for_each_chain_and_counts_the_il_of_every_pool(three_chain_scan):
    document = json.loads(report.render_json(three_chain_scan))

    assert document["counts"] == {
        "analysed": 6,
        "cost_negative": 3,
        "shown": 2,
        "below_min_tvl": 0,
        "zero_apy": 0,
        "skipped_entries": 0,
        "il_risk": 2,
        "il_estimated": 1,
        "il_unknown": 1,
    }
    assert list(document["chain_summary"]) == ["Arbitrum", "Base", "Solana"]
    assert document["chain_summary"] == {
        "Arbitrum": {
            "analysed": 4,
            "cost_negative": 2,
            "cost_negative_share": 0.5,
            "il_risk": 2,
            "il_estimated": 1,
            "il_unknown": 1,
        },
        "Base": {
            "analysed": 2,
            "cost_negative": 1,
            "cost_negative_share": 0.5,
            "il_risk": 0,
            "il_estimated": 0,
            "il_unknown": 0,
        },
        "Solana": {
            "analysed": 0,
            "cost_negative": 0,
            "cost_negative_share": None,
            "il_risk": 0,
            "il_estimated": 0,
            "il_unknown": 0,
        },
    }
    assert len(document["pools"]) == 2


def test_json_rounds_a_share_that_does_not_divide_evenly(build_scan, make_pool):
    multi = {"stablecoin": False, "exposure": "multi"}
    pools = [make_pool(pool_id=f"p{i}", apy_base=2.0, **multi) for i in range(2)]
    pools.append(make_pool(pool_id="fine", apy_base=5.0))

    document = json.loads(report.render_json(build_scan(pools)))

    assert document["chain_summary"]["Arbitrum"]["cost_negative_share"] == 0.666667  # 2 of 3


def html_cells(html: str, row_name: str) -> list[str]:
    """The numbers in the row of the by-chain table that starts with `row_name`."""
    match = re.search(rf'<th scope="row">{row_name}</th>(.*?)</tr>', html)
    assert match, row_name
    return re.findall(r'<td class="num">([^<]*)</td>', match.group(1))


def test_html_puts_the_summary_by_chain_above_the_table_of_pools(three_chain_scan):
    page = report.render_html(three_chain_scan, date(2026, 10, 26))

    summary = page.index("<h2>Summary</h2>")
    by_chain = page.index("<h2>By chain</h2>")
    pools = page.index("<h2>Pools</h2>")
    pool_table = page.index('<caption class="sr">Pools, cost-negative first')
    assert summary < by_chain < pools < pool_table
    assert "Cost-negative: 3. Shown: 2." in page[summary:by_chain]
    section = page[by_chain:pools]
    assert re.findall(r'<th scope="col"[^>]*>([^<]*)</th>', section) == [
        "Chain",
        "Analysed",
        "Cost-negative",
        "Share",
        "IL estimated",
    ]
    assert html_cells(section, "Arbitrum") == ["4", "2", "50.0%", "1 of 2"]
    assert html_cells(section, "Base") == ["2", "1", "50.0%", "0 of 0"]
    assert html_cells(section, "Solana") == ["0", "0", "-", "0 of 0"]
    assert html_cells(section[section.index("<tfoot>") :], "all chains") == [
        "6",
        "3",
        "50.0%",
        "1 of 2",
    ]
    assert page.count('<tr class="cost-negative">') == 2  # the table of pools holds two rows


def test_html_escapes_the_chain_names_of_the_summary(build_scan):
    scan = dataclasses.replace(
        build_scan([]),
        chain_summaries=[ChainSummary("<script>alert(1)</script>", 1, 1, 0, 0, 0)],
    )

    page = report.render_html(scan, date(2026, 10, 26))

    assert "script" not in inspect_page(page).tags
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_html_has_no_by_chain_section_without_summaries(build_scan):
    scan = dataclasses.replace(build_scan([]), chain_summaries=[])

    page = report.render_html(scan, date(2026, 10, 26))

    assert "By chain" not in page
    assert "<h2>Pools</h2>" in page
