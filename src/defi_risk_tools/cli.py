"""The `yield-scan` command line."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from defi_risk_tools import costs, data, model
from defi_risk_tools.report import Scan, render_html, render_json, render_text

DEFAULTS = model.Assumptions()
DEFAULT_MIN_TVL_USD = 100_000.0
DEFAULT_LIMIT = 50

app = typer.Typer(
    help=(
        "Cost-Negative Yield Scanner: what a DeFi yield pays after gas, swap fees and "
        "reward-token decay. Estimates only, not financial advice. Read-only: it never "
        "touches a wallet."
    ),
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

ChainsOption = Annotated[
    list[str] | None,
    typer.Option(
        "--chain",
        help="DeFiLlama chain name, for example 'OP Mainnet'. Repeat for several. "
        f"Default: {', '.join(costs.DEFAULT_CHAINS)}.",
    ),
]
SizeOption = Annotated[
    float, typer.Option("--size", min=0.01, help="Position size in USD.", show_default=True)
]
DaysOption = Annotated[
    int, typer.Option("--days", min=1, help="Holding period in days.", show_default=True)
]
HaircutOption = Annotated[
    float,
    typer.Option(
        "--haircut",
        min=0.0,
        max=1.0,
        help="Share of the reward APY assumed lost as reward tokens fall (0 to 1).",
        show_default=True,
    ),
]
MinTvlOption = Annotated[
    float,
    typer.Option(
        "--min-tvl", min=0.0, help="Leave out pools with less TVL (USD).", show_default=True
    ),
]
LimitOption = Annotated[
    int, typer.Option("--limit", min=1, help="Show at most this many pools.", show_default=True)
]
OnlyNegativeOption = Annotated[
    bool, typer.Option("--only-negative", help="Show only cost-negative pools.")
]
IncludeZeroApyOption = Annotated[
    bool,
    typer.Option(
        "--include-zero-apy",
        help="Also show pools that advertise an APY of 0 or less (they are left out by default).",
    ),
]
FromFileOption = Annotated[
    Path | None,
    typer.Option(
        "--from-file",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Read a saved pools JSON instead of calling the API. Makes no network calls at all: "
        "gas and token prices are the defaults from the cost table.",
    ),
]


@app.command()
def scan(
    chains: ChainsOption = None,
    size: SizeOption = DEFAULTS.size_usd,
    days: DaysOption = DEFAULTS.days,
    haircut: HaircutOption = DEFAULTS.haircut,
    min_tvl: MinTvlOption = DEFAULT_MIN_TVL_USD,
    limit: LimitOption = DEFAULT_LIMIT,
    only_negative: OnlyNegativeOption = False,
    include_zero_apy: IncludeZeroApyOption = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON instead of a table.")] = False,
    from_file: FromFileOption = None,
) -> None:
    """Show which yields lose money once costs are counted, worst first."""
    result = build_scan(
        chains=chains,
        size=size,
        days=days,
        haircut=haircut,
        min_tvl=min_tvl,
        limit=limit,
        only_negative=only_negative,
        include_zero_apy=include_zero_apy,
        from_file=from_file,
    )
    typer.echo(render_json(result) if as_json else render_text(result))


@app.command("report")
def write_report(
    out: Annotated[Path, typer.Option("--out", help="Where to write the HTML page.")] = Path(
        "report.html"
    ),
    chains: ChainsOption = None,
    size: SizeOption = DEFAULTS.size_usd,
    days: DaysOption = DEFAULTS.days,
    haircut: HaircutOption = DEFAULTS.haircut,
    min_tvl: MinTvlOption = DEFAULT_MIN_TVL_USD,
    limit: LimitOption = DEFAULT_LIMIT,
    only_negative: OnlyNegativeOption = False,
    include_zero_apy: IncludeZeroApyOption = False,
    from_file: FromFileOption = None,
) -> None:
    """Write the same scan as one self-contained HTML page (no scripts, no CDN)."""
    result = build_scan(
        chains=chains,
        size=size,
        days=days,
        haircut=haircut,
        min_tvl=min_tvl,
        limit=limit,
        only_negative=only_negative,
        include_zero_apy=include_zero_apy,
        from_file=from_file,
    )
    page = render_html(result, generated_on=datetime.now(UTC).date())
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page, encoding="utf-8")
    except OSError as error:
        fail(f"cannot write {out}: {error}")
    typer.echo(
        f"Wrote {out}: {len(result.rows)} pools shown, "
        f"{result.cost_negative} of {result.analysed} analysed are cost-negative."
    )


def build_scan(
    *,
    chains: list[str] | None,
    size: float,
    days: int,
    haircut: float,
    min_tvl: float,
    limit: int,
    only_negative: bool,
    include_zero_apy: bool,
    from_file: Path | None,
) -> Scan:
    """Fetch (or read) the pools, run the model on the ones in scope and rank the results."""
    try:
        wanted = [costs.canonical_chain(name) for name in chains or costs.DEFAULT_CHAINS]
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint="--chain") from error
    wanted = list(dict.fromkeys(wanted))  # drop repeats, keep the order
    assumptions = model.Assumptions(size_usd=size, days=days, haircut=haircut)

    try:
        if from_file is None:
            with data.make_client() as client:
                parsed = data.fetch_pools(client)
                tx_costs, notes = costs.resolve_tx_costs(wanted, client)
            source = "DeFiLlama yields API (live)"
        else:
            parsed = data.load_pools_file(from_file)
            tx_costs, notes = costs.resolve_tx_costs(wanted, None)
            source = f"file {from_file}"
    except data.DataError as error:
        hint = "" if from_file else " Use --from-file with a saved pools JSON to work offline."
        fail(f"{error}.{hint}")

    selection = model.select_pools(
        parsed.pools, chains=wanted, min_tvl_usd=min_tvl, include_zero_apy=include_zero_apy
    )
    ranked = model.rank(
        [
            model.evaluate_pool(pool, assumptions, tx_costs[pool.chain].usd)
            for pool in selection.pools
        ]
    )
    cost_negative = [result for result in ranked if result.cost_negative]
    shown = (cost_negative if only_negative else ranked)[:limit]
    return Scan(
        rows=shown,
        assumptions=assumptions,
        min_tvl_usd=min_tvl,
        include_zero_apy=include_zero_apy,
        tx_costs=tx_costs,
        source=source,
        analysed=len(ranked),
        cost_negative=len(cost_negative),
        below_min_tvl=selection.below_min_tvl,
        zero_apy=selection.zero_apy,
        skipped_entries=parsed.skipped,
        notes=notes,
    )


def fail(message: str) -> NoReturn:
    """Print an error and stop with a non-zero exit code."""
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code=1)
