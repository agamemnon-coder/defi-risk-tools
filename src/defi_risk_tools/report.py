"""Rendering a scan: the text table, the JSON document and the static HTML page."""

import json
import textwrap
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date
from html import escape
from urllib.parse import quote

from defi_risk_tools import __version__
from defi_risk_tools.costs import TxCost
from defi_risk_tools.model import (
    BREAK_EVEN_MAX_DAYS,
    COST_NEGATIVE,
    FLAG_DESCRIPTIONS,
    IL_ESTIMATED,
    IL_RISK,
    IL_UNKNOWN,
    Assumptions,
    PoolResult,
)

DISCLAIMER = (
    "Estimates only, not financial advice. This tool reads public data and never touches a wallet."
)
DEFILLAMA_POOL_URL = "https://defillama.com/yields/pool/"
SORT_NOTE = (
    "Sorted with cost-negative pools first, the highest advertised APY leading, "
    "then the other pools by net APY, highest first."
)

LIMITATIONS = (
    "These numbers come from a simple model. They are estimates, not predictions, and not "
    "financial advice.",
    "The pool data comes from DeFiLlama and is only as good as what protocols and DeFiLlama "
    "report. APYs change every day.",
    "Impermanent loss is estimated, not measured. DeFiLlama's own 7-day figure (il7d) was empty "
    "for every pool when this tool was written, so for pools with IL risk and exactly two tokens "
    "it is estimated from 30 days of daily prices: volatility squared times T/365 over 8, the "
    "small-move result for a 50/50 constant-product pool. For concentrated-liquidity pools "
    "(Uniswap v3 and v4, Velodrome v3 and similar) it is a lower bound. Pools with another "
    "number of tokens, or without prices, stay IL_UNKNOWN and their net APY is optimistic.",
    "Prices are only looked up for the pools shown (after the filters and the limit), so a pool "
    "that loses money only once impermanent loss is counted can be missing from a short list. "
    "A larger limit widens it.",
    "Gas and L1 data fees are rough estimates. Swap cost is a flat rate, not a price-impact model.",
    "The reward haircut is a rule of thumb, not a forecast of any token's price.",
    "Smart contract risk, depegs, taxes, bridge costs and withdrawal limits are not modelled.",
)


@dataclass(frozen=True)
class Scan:
    """Everything a renderer needs: the rows to show and the context they were computed in."""

    rows: list[PoolResult]  # sorted, filtered and limited
    assumptions: Assumptions
    min_tvl_usd: float
    include_zero_apy: bool
    tx_costs: dict[str, TxCost]  # one entry per scanned chain
    source: str
    price_source: str  # where token prices for impermanent loss came from, or that there were none
    analysed: int  # pools the model ran on: scanned chains, at or above the TVL floor
    cost_negative: int  # how many of those are cost-negative
    below_min_tvl: int  # pools on the scanned chains that were left out for low TVL
    zero_apy: int  # pools above the floor that were left out for advertising an APY of 0 or less
    skipped_entries: int  # unreadable entries in the source
    notes: list[str]


# Number formatting


def format_money(value: float) -> str:
    """Dollars and cents, with the sign in front of the dollar sign."""
    return f"-${-value:,.2f}" if value < 0 else f"${value:,.2f}"


def format_compact_money(value: float) -> str:
    """Short dollar amounts for wide numbers: $73, $1.2k, $554.3M."""
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if value >= limit * 0.99995:  # so that 999,990 reads $1.0M, not $1000.0k
            return f"${value / limit:.1f}{suffix}"
    return f"${value:.2f}" if value < 10 else f"${value:.0f}"


def format_plain_money(value: float) -> str:
    return f"${value:,.0f}" if value == int(value) else f"${value:,.2f}"


def format_net_apy(value: float) -> str:
    """Signed percent. Two decimals under 1%, so a tiny loss does not read as -0.0%."""
    return f"{value:+,.2f}%" if abs(value) < 1 else f"{value:+,.1f}%"


def format_apy_parts(result: PoolResult) -> str:
    return f"{result.pool.apy_base:,.2f}+{result.pool.apy_reward:,.2f}"


def format_break_even_size(result: PoolResult) -> str:
    size = result.break_even_size_usd
    return "never" if size is None else format_compact_money(size)


def format_break_even_days(result: PoolResult, a: Assumptions) -> str:
    days = result.break_even_days
    return f">{max(BREAK_EVEN_MAX_DAYS, a.days)} d" if days is None else f"{days} d"


def clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 3] + "..."


# Shared text


def assumption_rows(scan: Scan) -> list[tuple[str, str]]:
    """The assumptions in words. Every report prints them."""
    a = scan.assumptions
    return [
        ("Position size", format_plain_money(a.size_usd)),
        ("Holding period", format_days(a.days)),
        ("Reward haircut", f"{a.haircut:.0%} of the reward APY is assumed lost"),
        ("Yield", "simple, not compounded; the APY is assumed to stay constant"),
        (
            "Transactions",
            f"{a.enter_txs} to enter, {a.exit_txs} to exit, and 1 claim per started "
            f"{a.claim_every_days} days when the pool pays rewards",
        ),
        (
            "Swaps",
            f"{a.swap_rate:.2%} each way (swap fee + slippage); none for single-asset "
            "stablecoin pools",
        ),
        (
            "Impermanent loss",
            "DeFiLlama's 7-day figure if there is one. Otherwise, for pools with two tokens, "
            f"volatility^2 x T/365 / 8 of the position, from 30 days of daily prices (a 50/50 "
            "constant-product pool; a lower bound for concentrated liquidity). Capped at "
            f"{a.il_cap:.0%} of the position, and not counted when neither exists. Prices are "
            "looked up only for the pools shown.",
        ),
        ("TVL floor", f"pools under {format_plain_money(scan.min_tvl_usd)} are left out"),
        (
            "Zero-APY pools",
            "kept"
            if scan.include_zero_apy
            else "left out: they advertise nothing, so they mislead nobody (--include-zero-apy)",
        ),
    ]


def il_coverage(scan: Scan) -> tuple[int, int, int]:
    """Of the pools shown: how many have IL risk, how many of those were estimated and how many
    have no figure at all."""
    flags = [result.flags for result in scan.rows]
    return (
        sum(IL_RISK in f for f in flags),
        sum(IL_ESTIMATED in f for f in flags),
        sum(IL_UNKNOWN in f for f in flags),
    )


def counts_lines(scan: Scan) -> list[str]:
    lines = [
        f"Pools analysed: {scan.analysed} on {', '.join(scan.tx_costs)}. "
        f"Cost-negative: {scan.cost_negative}. Shown: {len(scan.rows)}.",
        f"Left out: {scan.below_min_tvl} under the TVL floor, {scan.zero_apy} advertising an APY "
        f"of 0 or less, {scan.skipped_entries} unreadable entries in the source.",
    ]
    at_risk, estimated, _ = il_coverage(scan)
    if at_risk:
        lines.append(
            f"Impermanent loss: estimated from token prices for {estimated} of the {at_risk} "
            "pools shown with IL risk."
        )
    return lines


def format_days(days: int) -> str:
    return f"{days} day" if days == 1 else f"{days} days"


# Text

TEXT_HEADER = (
    "chain",
    "project",
    "symbol",
    "TVL",
    "APY (base+reward)",
    "costs",
    "net APY",
    "break-even size",
    "flags",
)
TEXT_RIGHT_ALIGNED = {3, 4, 5, 6, 7}
TEXT_WIDTH = 100  # long lines under the table wrap here


def render_text(scan: Scan) -> str:
    lines = ["Cost-Negative Yield Scanner  -  estimates only, not financial advice", ""]
    lines.append(_text_table(scan.rows) if scan.rows else "No pools match.")
    lines += ["", *counts_lines(scan), f"Source: {scan.source}", f"Prices: {scan.price_source}"]
    lines += [SORT_NOTE, "", "Assumptions"]
    for label, text in assumption_rows(scan):
        lines += _hanging(label, text, 18)
    lines += ["", "Cost of one transaction"]
    for chain, tx_cost in scan.tx_costs.items():
        lines += _hanging(chain, tx_cost.describe(), 12)
    if scan.notes:
        lines += ["", "Notes"]
        for note in scan.notes:
            lines += _hanging("", note, 0)
    lines += ["", DISCLAIMER, "See the README for the model and its limits."]
    return "\n".join(lines)


def _hanging(label: str, text: str, label_width: int) -> list[str]:
    """`  label  text`, with the text wrapped at 100 columns under its own first line."""
    indent = " " * (2 + label_width)
    wrapped = textwrap.wrap(text, width=TEXT_WIDTH - len(indent)) or [""]
    first = f"  {label:<{label_width}}{wrapped[0]}"
    return [first, *(indent + line for line in wrapped[1:])]


def _text_table(rows: list[PoolResult]) -> str:
    cells = [_text_cells(row) for row in rows]
    widths = [max(len(cell) for cell in column) for column in zip(TEXT_HEADER, *cells, strict=True)]

    def line(values: tuple[str, ...] | list[str]) -> str:
        padded = [
            value.rjust(width) if i in TEXT_RIGHT_ALIGNED else value.ljust(width)
            for i, (value, width) in enumerate(zip(values, widths, strict=True))
        ]
        return "  ".join(padded).rstrip()

    rule = line(["-" * width for width in widths])
    return "\n".join([line(TEXT_HEADER), rule, *(line(row) for row in cells)])


def _text_cells(result: PoolResult) -> list[str]:
    pool = result.pool
    return [
        pool.chain,
        clip(pool.project, 18),
        clip(pool.symbol, 18),
        format_compact_money(pool.tvl_usd),
        format_apy_parts(result),
        format_money(result.costs_usd),
        format_net_apy(result.net_apy),
        format_break_even_size(result),
        ",".join(result.flags) or "-",
    ]


# JSON


def render_json(scan: Scan) -> str:
    document = {
        "tool": "yield-scan",
        "version": __version__,
        "disclaimer": DISCLAIMER,
        "source": scan.source,
        "price_source": scan.price_source,
        "assumptions": {
            **asdict(scan.assumptions),
            "min_tvl_usd": scan.min_tvl_usd,
            "include_zero_apy": scan.include_zero_apy,
        },
        "chain_costs": {chain: _tx_cost_record(tx) for chain, tx in scan.tx_costs.items()},
        "counts": {
            "analysed": scan.analysed,
            "cost_negative": scan.cost_negative,
            "shown": len(scan.rows),
            "below_min_tvl": scan.below_min_tvl,
            "zero_apy": scan.zero_apy,
            "skipped_entries": scan.skipped_entries,
            "il_estimated": il_coverage(scan)[1],
            "il_unknown": il_coverage(scan)[2],
        },
        "notes": scan.notes,
        "pools": [_pool_record(result) for result in scan.rows],
    }
    return json.dumps(document, indent=2)


def _tx_cost_record(tx_cost: TxCost) -> dict:
    spec = tx_cost.spec
    evm = tx_cost.gas_price_gwei is not None
    return {
        "usd_per_tx": round(tx_cost.usd, 6),
        "native_symbol": spec.native_symbol,
        "native_price_usd": tx_cost.native_price_usd,
        "native_price_source": "live" if tx_cost.native_price_live else "default",
        "gas_units": spec.gas_units if evm else None,
        "gas_price_gwei": tx_cost.gas_price_gwei,
        "gas_price_source": ("live" if tx_cost.gas_price_live else "default") if evm else None,
        "l1_fee_usd": spec.l1_fee_usd if evm else None,
        "fixed_fee_native": spec.fixed_fee_native,
    }


def _pool_record(result: PoolResult) -> dict:
    pool = result.pool
    return {
        "pool": pool.pool_id,
        "chain": pool.chain,
        "project": pool.project,
        "symbol": pool.symbol,
        "tvl_usd": pool.tvl_usd,
        "apy": pool.apy,
        "apy_base": pool.apy_base,
        "apy_reward": pool.apy_reward,
        "gross_usd": round(result.gross_usd, 6),
        "gas_usd": round(result.gas_usd, 6),
        "swap_usd": round(result.swap_usd, 6),
        "il_usd": round(result.il_usd, 6),
        "costs_usd": round(result.costs_usd, 6),
        "net_usd": round(result.net_usd, 6),
        "net_apy": round(result.net_apy, 6),
        "break_even_size_usd": _round_or_none(result.break_even_size_usd),
        "break_even_days": result.break_even_days,
        "il_volatility": _round_or_none(pool.il_volatility),
        "flags": list(result.flags),
        "reward_tokens": list(pool.reward_tokens),
        "underlying_tokens": list(pool.underlying_tokens),
    }


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


# HTML

STYLE = """
:root{color-scheme:light dark;--bg:#fff;--fg:#1b1f24;--muted:#57606a;--line:#d0d7de;
--panel:#f6f8fa;--neg:#b42318;--neg-bg:#fef3f2;--pos:#067647;--note:#7a2e0e;--note-bg:#fffaeb;
--link:#0550ae}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#9da7b3;
--line:#30363d;--panel:#161b22;--neg:#ff8b84;--neg-bg:#3d1a1a;--pos:#56d364;--note:#e3b341;
--note-bg:#2b2412;--link:#79c0ff}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.page{max-width:1280px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:1.75rem;line-height:1.2;margin:0 0 4px}
h2{font-size:1.15rem;margin:32px 0 8px}
p{margin:0 0 12px}
a{color:var(--link)}
.lede{color:var(--muted)}
.notice{background:var(--note-bg);color:var(--note);border:1px solid var(--line);
border-radius:8px;padding:10px 14px}
.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:.875rem}
th,td{padding:8px 10px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}
thead th{background:var(--panel);vertical-align:bottom}
tbody tr:last-child td{border-bottom:0}
tr.cost-negative td:first-child{box-shadow:inset 3px 0 0 var(--neg)}
.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
small{display:block;color:var(--muted);font-size:.75rem}
td:first-child,td a{white-space:nowrap}
td.flags{min-width:13rem}
.neg{color:var(--neg)}
.pos{color:var(--pos)}
.flag{display:inline-block;border:1px solid var(--line);border-radius:4px;padding:0 6px;
margin:0 4px 4px 0;font:600 .7rem/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;
white-space:nowrap}
.flag.cost-negative{color:var(--neg);background:var(--neg-bg);border-color:var(--neg)}
dl{display:grid;grid-template-columns:max-content 1fr;gap:6px 16px;margin:0}
dt{font-weight:600}
dd{margin:0}
ul{margin:0;padding-left:20px}
li{margin-bottom:6px}
footer{color:var(--muted);font-size:.85rem;margin-top:32px}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
@media (max-width:640px){dl{grid-template-columns:1fr}dd{margin-bottom:8px}}
"""

HTML_HEADERS = (
    ("Chain", False),
    ("Pool", False),
    ("TVL", True),
    ("APY (base+reward)", True),
    ("Costs", True),
    ("Net APY", True),
    ("Break-even size", True),
    ("Flags", False),
)


def render_html(scan: Scan, generated_on: date) -> str:
    """One self-contained page: inline CSS, no script, no font or image from elsewhere."""
    a = scan.assumptions
    flag_terms = _html_definitions(FLAG_DESCRIPTIONS.items())
    assumption_terms = _html_definitions(assumption_rows(scan))
    cost_terms = _html_definitions((chain, tx.describe()) for chain, tx in scan.tx_costs.items())
    limitations = _html_list(LIMITATIONS)
    position = f"Position {format_plain_money(a.size_usd)}, held {format_days(a.days)}."
    notes = f"<h2>Notes</h2>\n{_html_list(scan.notes)}" if scan.notes else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="What DeFi yields pay after gas, swap fees and reward decay.">
<title>Cost-Negative Yield Scanner</title>
<style>{STYLE}</style>
</head>
<body>
<div class="page">
<header>
<h1>Cost-Negative Yield Scanner</h1>
<p class="lede">What a DeFi yield pays after the costs of using it: gas, swap fees, the expected
fall of reward tokens and impermanent loss.</p>
<p class="notice" role="note"><strong>{escape(DISCLAIMER)}</strong></p>
</header>
<main>
<h2>Summary</h2>
<p>{escape(" ".join(counts_lines(scan)))}</p>
<p>{escape(SORT_NOTE)} {escape(position)}</p>
{_html_table(scan.rows, a)}
<h2>Flags</h2>
<dl>
{flag_terms}
</dl>
<h2>Assumptions</h2>
<dl>
{assumption_terms}
</dl>
<h2>Cost of one transaction</h2>
<dl>
{cost_terms}
</dl>
{notes}
<h2>Limitations</h2>
{limitations}
</main>
<footer>
<p>Generated by yield-scan {escape(__version__)} on {generated_on.isoformat()}.
Source: {escape(scan.source)}. Prices: {escape(scan.price_source)}.
Pool links go to DeFiLlama.</p>
</footer>
</div>
</body>
</html>
"""


def _html_table(rows: list[PoolResult], a: Assumptions) -> str:
    if not rows:
        return "<p>No pools match.</p>"
    head = "".join(_th(name, numeric) for name, numeric in HTML_HEADERS)
    body = "\n".join(_html_row(result, a) for result in rows)
    return (
        '<div class="table-wrap"><table>\n'
        '<caption class="sr">Pools, cost-negative first</caption>\n'
        f"<thead><tr>{head}</tr></thead>\n<tbody>\n{body}\n</tbody>\n</table></div>"
    )


def _th(name: str, numeric: bool) -> str:
    css = ' class="num"' if numeric else ""
    return f'<th scope="col"{css}>{escape(name)}</th>'


def _html_row(result: PoolResult, a: Assumptions) -> str:
    pool = result.pool
    link = DEFILLAMA_POOL_URL + quote(pool.pool_id, safe="")
    net_class = "neg" if result.net_usd < 0 else "pos"
    row_class = ' class="cost-negative"' if result.cost_negative else ""
    il_label = "IL est." if IL_ESTIMATED in result.flags else "IL"
    breakdown = (
        f"gas {format_money(result.gas_usd)}, swaps {format_money(result.swap_usd)}, "
        f"{il_label} {format_money(result.il_usd)}"
    )
    net_note = f"{format_money(result.net_usd)} over {format_days(a.days)}"
    days_note = f"{format_break_even_days(result, a)} at {format_plain_money(a.size_usd)}"
    flags = "".join(_html_flag(flag) for flag in result.flags) or "-"
    cells = [
        f"<td>{escape(pool.chain)}</td>",
        f'<td><a href="{escape(link)}" rel="noopener noreferrer">{escape(pool.symbol)}</a>'
        f"<small>{escape(pool.project)}</small></td>",
        f'<td class="num" title="{escape(format_plain_money(pool.tvl_usd))}">'
        f"{escape(format_compact_money(pool.tvl_usd))}</td>",
        f'<td class="num">{escape(format_apy_parts(result))}</td>',
        f'<td class="num">{escape(format_money(result.costs_usd))}'
        f"<small>{escape(breakdown)}</small></td>",
        f'<td class="num {net_class}">{escape(format_net_apy(result.net_apy))}'
        f"<small>{escape(net_note)}</small></td>",
        f'<td class="num">{escape(format_break_even_size(result))}'
        f"<small>{escape(days_note)}</small></td>",
        f'<td class="flags">{flags}</td>',
    ]
    return f"<tr{row_class}>{''.join(cells)}</tr>"


def _html_flag(flag: str) -> str:
    css = " cost-negative" if flag == COST_NEGATIVE else ""
    return (
        f'<span class="flag{css}" title="{escape(FLAG_DESCRIPTIONS[flag])}">{escape(flag)}</span>'
    )


def _html_definitions(pairs: Iterable[tuple[str, str]]) -> str:
    return "\n".join(f"<dt>{escape(term)}</dt><dd>{escape(text)}</dd>" for term, text in pairs)


def _html_list(items: Iterable[str]) -> str:
    return "<ul>\n" + "\n".join(f"<li>{escape(item)}</li>" for item in items) + "\n</ul>"
