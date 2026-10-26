"""The `yield-scan` command line."""

from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, NoReturn

import httpx
import typer

from defi_risk_tools import costs, data, model, prices
from defi_risk_tools.report import Scan, render_html, render_json, render_text

DEFAULTS = model.Assumptions()
DEFAULT_MIN_TVL_USD = 100_000.0
DEFAULT_LIMIT = 50
NO_PRICES_NOTE = (
    "Offline run without --prices-file: no token prices, so impermanent loss is not estimated "
    "and pools with IL risk stay IL_UNKNOWN."
)

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
PricesFileOption = Annotated[
    Path | None,
    typer.Option(
        "--prices-file",
        exists=True,
        dir_okay=False,
        readable=True,
        help="A saved chart response (GET https://coins.llama.fi/chart/{coins}?span=30&period=1d) "
        "to estimate impermanent loss from, instead of calling the API. Without it, an offline "
        "run does not estimate impermanent loss.",
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
    prices_file: PricesFileOption = None,
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
        prices_file=prices_file,
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
    prices_file: PricesFileOption = None,
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
        prices_file=prices_file,
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
    prices_file: Path | None,
) -> Scan:
    """Fetch (or read) the pools, run the model on the ones in scope and rank the results.

    Impermanent loss is estimated last, and only for the pools that are going to be shown, so that
    token prices are looked up for a handful of pools and not for thousands.
    """
    try:
        wanted = [costs.canonical_chain(name) for name in chains or costs.DEFAULT_CHAINS]
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint="--chain") from error
    wanted = list(dict.fromkeys(wanted))  # drop repeats, keep the order
    assumptions = model.Assumptions(size_usd=size, days=days, haircut=haircut)
    saved_prices = _read_prices_file(prices_file)

    with ExitStack() as stack:
        client = None if from_file else stack.enter_context(data.make_client())
        parsed = _read_pools(from_file, client)
        tx_costs, notes = costs.resolve_tx_costs(wanted, client)

        def evaluate(pool: data.Pool) -> model.PoolResult:
            return model.evaluate_pool(pool, assumptions, tx_costs[pool.chain].usd)

        selection = model.select_pools(
            parsed.pools, chains=wanted, min_tvl_usd=min_tvl, include_zero_apy=include_zero_apy
        )
        ranked = model.rank([evaluate(pool) for pool in selection.pools])
        cost_negative = [result for result in ranked if result.cost_negative]
        visible = (cost_negative if only_negative else ranked)[:limit]

        lookup, price_source = _price_lookup(saved_prices, prices_file, client)
        volatilities, il_notes = prices.estimate_volatilities([r.pool for r in visible], lookup)

    # A pool with a measured volatility is evaluated again, now with its impermanent loss. That can
    # make it cost-negative, so the pools shown are ranked again.
    shown = model.rank(
        [
            evaluate(replace(result.pool, il_volatility=volatilities[result.pool.pool_id]))
            if result.pool.pool_id in volatilities
            else result
            for result in visible
        ]
    )
    cost_negative_count = (
        len(cost_negative)
        - sum(result.cost_negative for result in visible)
        + sum(result.cost_negative for result in shown)
    )
    return Scan(
        rows=shown,
        assumptions=assumptions,
        min_tvl_usd=min_tvl,
        include_zero_apy=include_zero_apy,
        tx_costs=tx_costs,
        source=f"file {from_file}" if from_file else "DeFiLlama yields API (live)",
        price_source=price_source,
        analysed=len(ranked),
        cost_negative=cost_negative_count,
        below_min_tvl=selection.below_min_tvl,
        zero_apy=selection.zero_apy,
        skipped_entries=parsed.skipped,
        notes=[*notes, *il_notes],
    )


def _read_pools(from_file: Path | None, client: httpx.Client | None) -> data.ParsedPools:
    try:
        return data.fetch_pools(client) if client else data.load_pools_file(from_file)
    except data.DataError as error:
        hint = "" if from_file else " Use --from-file with a saved pools JSON to work offline."
        fail(f"{error}.{hint}")


def _read_prices_file(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        return prices.load_series(path)
    except data.DataError as error:
        fail(str(error))


def _price_lookup(
    saved: dict | None, path: Path | None, client: httpx.Client | None
) -> tuple[prices.Lookup, str]:
    """Where token prices come from: the saved file if there is one, else the API, else nowhere."""
    if saved is not None:
        return (lambda keys: (saved, [])), f"file {path}"
    if client is not None:
        return (lambda keys: prices.fetch_series(client, keys)), "DeFiLlama chart API (live)"
    return (lambda keys: ({}, [NO_PRICES_NOTE])), "none (offline run without --prices-file)"


def fail(message: str) -> NoReturn:
    """Print an error and stop with a non-zero exit code."""
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code=1)
