"""The yield-cost model: pure functions with no I/O, no clock and no randomness.

All money is in USD. APYs are percentages per year, as DeFiLlama reports them. Yield is
simple (not compounded) and the APY is assumed to stay constant over the holding period.
"""

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from defi_risk_tools.data import Pool

DAYS_PER_YEAR = 365
SECONDS_PER_DAY = 86_400
IL_WINDOW_DAYS = 7  # DeFiLlama's `il7d` is the impermanent loss over 7 days
IL_VARIANCE_DIVISOR = 8  # expected IL = variance of the log price ratio / 8, for small moves
MIN_RETURNS = 14  # fewer daily returns than this is too little history to measure a volatility

PriceSeries = Sequence[tuple[float, float]]  # (unix timestamp, USD price), daily

# The flags, in the order they are shown.
COST_NEGATIVE = "COST_NEGATIVE"
REWARD_DEPENDENT = "REWARD_DEPENDENT"
APY_OUTLIER = "APY_OUTLIER"
LOW_TVL = "LOW_TVL"
IL_RISK = "IL_RISK"
IL_ESTIMATED = "IL_ESTIMATED"
IL_UNKNOWN = "IL_UNKNOWN"

LOW_TVL_USD = 100_000.0
REWARD_DEPENDENT_SHARE = 0.5  # rewards are more than this share of the APY
APY_OUTLIER_MULTIPLE = 3.0  # the APY is more than this multiple of its 30-day mean
BREAK_EVEN_MAX_DAYS = 365  # how far the break-even days search looks

FLAG_DESCRIPTIONS = {
    COST_NEGATIVE: "Net yield is below zero: the costs are larger than what the pool pays.",
    REWARD_DEPENDENT: "More than half of the APY is paid in reward tokens.",
    APY_OUTLIER: "DeFiLlama marks the APY as an outlier, or it is over 3x its 30-day mean.",
    LOW_TVL: "Less than $100,000 is deposited in the pool.",
    IL_RISK: "DeFiLlama says the pool carries impermanent loss risk.",
    IL_ESTIMATED: "IL is estimated from 30 days of token prices (a lower bound if concentrated).",
    IL_UNKNOWN: "Impermanent loss risk, but no 7-day figure exists, so no IL cost is counted.",
}


@dataclass(frozen=True)
class Assumptions:
    """The knobs of the model. The defaults are the ones the README documents."""

    size_usd: float = 1_000.0  # position size P
    days: int = 30  # holding period T
    haircut: float = 0.5  # share of the reward APY assumed lost as reward tokens fall
    swap_rate: float = 0.003  # swap fee plus slippage, as a share of the amount swapped
    multi_asset_swap_share: float = 0.5  # share of the position swapped to enter a multi-asset pool
    enter_txs: int = 2  # approve and deposit
    exit_txs: int = 1
    claim_every_days: int = 30  # one claim per started period, while the pool pays rewards
    il_cap: float = 0.5  # impermanent loss never costs more than this share of the position

    def __post_init__(self) -> None:
        if self.size_usd <= 0:
            raise ValueError("size_usd must be positive")
        if self.days < 1:
            raise ValueError("days must be at least 1")
        if not 0 <= self.haircut <= 1:
            raise ValueError("haircut must be between 0 and 1")
        if not 0 <= self.multi_asset_swap_share <= 1:
            raise ValueError("multi_asset_swap_share must be between 0 and 1")


@dataclass(frozen=True)
class PoolResult:
    pool: Pool
    gross_usd: float
    gas_usd: float
    swap_usd: float
    il_usd: float
    net_apy: float  # percent per year
    break_even_size_usd: float | None  # None: no position size is ever worth it
    break_even_days: int | None  # None: not within the search window
    flags: tuple[str, ...]

    @property
    def costs_usd(self) -> float:
        return self.gas_usd + self.swap_usd + self.il_usd

    @property
    def net_usd(self) -> float:
        return self.gross_usd - self.costs_usd

    @property
    def cost_negative(self) -> bool:
        return COST_NEGATIVE in self.flags


def swapped_share(pool: Pool, a: Assumptions) -> float:
    """The share of the position that is swapped to enter the pool, and again to leave it.

    A single-asset pool takes the asset itself, which the user is assumed to hold: no swap, whatever
    the asset. Any other pool is multi-asset, and so is one whose exposure is not known. It takes a
    mix of tokens, so part of the position is swapped into the others.
    """
    return 0.0 if pool.exposure == "single" else a.multi_asset_swap_share


def claim_transactions(pool: Pool, days: int, a: Assumptions) -> int:
    """One claim per started `claim_every_days`, and none if the pool pays no rewards."""
    if pool.apy_reward <= 0:
        return 0
    return math.ceil(days / a.claim_every_days)


def gross_yield_usd(pool: Pool, size_usd: float, days: int, haircut: float) -> float:
    """Base yield plus reward yield after the haircut."""
    years = days / DAYS_PER_YEAR
    base = size_usd * pool.apy_base / 100 * years
    reward = size_usd * pool.apy_reward / 100 * years * (1 - haircut)
    return base + reward


def gas_cost_usd(pool: Pool, days: int, tx_cost_usd: float, a: Assumptions) -> float:
    transactions = a.enter_txs + a.exit_txs + claim_transactions(pool, days, a)
    return transactions * tx_cost_usd


def swap_cost_usd(pool: Pool, size_usd: float, a: Assumptions) -> float:
    """A swap on the way in and another on the way out, of the share that has to change token."""
    return 2 * a.swap_rate * size_usd * swapped_share(pool, a)


def impermanent_loss_usd(pool: Pool, size_usd: float, days: int, a: Assumptions) -> float:
    """Impermanent loss over the holding period, capped. Zero when it is not known.

    DeFiLlama's 7-day figure is used if the pool has one. Otherwise, if the volatility of the
    pool's price ratio was measured, the expected loss is `volatility² × T/365 / 8` of the position.
    """
    if not pool.il_risk:
        return 0.0
    if pool.il_7d is not None:
        loss = size_usd * abs(pool.il_7d) / 100 * days / IL_WINDOW_DAYS
    elif pool.il_volatility is not None:
        years = days / DAYS_PER_YEAR
        loss = size_usd * pool.il_volatility**2 * years / IL_VARIANCE_DIVISOR
    else:
        return 0.0
    return min(loss, a.il_cap * size_usd)


def ratio_volatility(prices_a: PriceSeries, prices_b: PriceSeries) -> float | None:
    """Annualised volatility of the daily log returns of the price ratio A / B.

    Each price of A is paired with the nearest price of B, if that is within half a day: the two
    series come from separate lookups, so their timestamps differ by minutes or hours. A return
    is the change between two pairs one day apart, so a gap in the data is never counted as a
    one-day return. Returns None when there are fewer than `MIN_RETURNS` returns.
    """
    pairs = _paired_log_ratios(prices_a, prices_b)
    returns = [
        later - earlier
        for (time_before, earlier), (time_after, later) in zip(pairs, pairs[1:], strict=False)
        if SECONDS_PER_DAY / 2 <= time_after - time_before <= SECONDS_PER_DAY * 3 / 2
    ]
    if len(returns) < MIN_RETURNS:
        return None
    return statistics.stdev(returns) * math.sqrt(DAYS_PER_YEAR)


def _paired_log_ratios(prices_a: PriceSeries, prices_b: PriceSeries) -> list[tuple[float, float]]:
    """(time, ln(A / B)) for each price of A that has a price of B within half a day."""
    prices_a, prices_b = sorted(prices_a), sorted(prices_b)
    if not prices_b:
        return []
    pairs = []
    nearest = 0  # index in B; it only moves forward, because A is in time order too
    for time_a, price_a in prices_a:
        while nearest + 1 < len(prices_b):
            if abs(prices_b[nearest + 1][0] - time_a) > abs(prices_b[nearest][0] - time_a):
                break
            nearest += 1
        time_b, price_b = prices_b[nearest]
        if abs(time_b - time_a) <= SECONDS_PER_DAY / 2 and price_a > 0 and price_b > 0:
            pairs.append((time_a, math.log(price_a / price_b)))
    return pairs


def net_yield_usd(
    pool: Pool, a: Assumptions, tx_cost_usd: float, size_usd: float, days: int
) -> float:
    """Net result of a position of `size_usd` held for `days`: gross minus every cost."""
    return (
        gross_yield_usd(pool, size_usd, days, a.haircut)
        - gas_cost_usd(pool, days, tx_cost_usd, a)
        - swap_cost_usd(pool, size_usd, a)
        - impermanent_loss_usd(pool, size_usd, days, a)
    )


def break_even_size_usd(pool: Pool, a: Assumptions, tx_cost_usd: float) -> float | None:
    """The smallest position that is not cost-negative over the holding period.

    Gas is a fixed cost; yield, swaps and IL all scale with the position. So
    net(P) = P * margin - gas, and net(P) >= 0 exactly when P >= gas / margin, where margin
    is the net yield per dollar. Returns None when the margin is zero or less: then no
    size is ever worth it.
    """
    margin = net_yield_usd(pool, a, 0.0, 1.0, a.days)  # per dollar, with gas left out
    if margin <= 0:
        return None
    return gas_cost_usd(pool, a.days, tx_cost_usd, a) / margin


def break_even_days(pool: Pool, a: Assumptions, tx_cost_usd: float) -> int | None:
    """The shortest holding period (in whole days) with a net yield of zero or more.

    The search is by day because the number of claims steps up every `claim_every_days`.
    It looks up to a year ahead, or up to the holding period if that is longer.
    """
    for days in range(1, max(BREAK_EVEN_MAX_DAYS, a.days) + 1):
        if net_yield_usd(pool, a, tx_cost_usd, a.size_usd, days) >= 0:
            return days
    return None


def flags_for(pool: Pool, net_usd: float) -> tuple[str, ...]:
    flags = []
    if net_usd < 0:
        flags.append(COST_NEGATIVE)
    total_apy = pool.apy_base + pool.apy_reward
    if total_apy > 0 and pool.apy_reward / total_apy > REWARD_DEPENDENT_SHARE:
        flags.append(REWARD_DEPENDENT)
    mean = pool.apy_mean_30d
    if pool.outlier or (mean is not None and pool.apy > APY_OUTLIER_MULTIPLE * mean):
        flags.append(APY_OUTLIER)
    if pool.tvl_usd < LOW_TVL_USD:
        flags.append(LOW_TVL)
    if pool.il_risk:
        flags.append(IL_RISK)
        if pool.il_7d is None:
            flags.append(IL_UNKNOWN if pool.il_volatility is None else IL_ESTIMATED)
    return tuple(flags)


@dataclass(frozen=True)
class Selection:
    pools: list[Pool]
    below_min_tvl: int  # on the chosen chains, but under the TVL floor
    zero_apy: int  # above the floor, but advertising an APY of 0 or less


def select_pools(
    pools: list[Pool], *, chains: list[str], min_tvl_usd: float, include_zero_apy: bool
) -> Selection:
    """The cheap filters: chain, TVL floor, and pools that advertise no yield.

    A pool whose advertised APY is 0 or less misleads nobody, so it is left out unless asked for.
    """
    in_scope = [pool for pool in pools if pool.chain in chains]
    above_floor = [pool for pool in in_scope if pool.tvl_usd >= min_tvl_usd]
    kept = above_floor if include_zero_apy else [pool for pool in above_floor if pool.apy > 0]
    return Selection(
        pools=kept,
        below_min_tvl=len(in_scope) - len(above_floor),
        zero_apy=len(above_floor) - len(kept),
    )


def evaluate_pool(pool: Pool, a: Assumptions, tx_cost_usd: float) -> PoolResult:
    """Run the whole model on one pool."""
    gross = gross_yield_usd(pool, a.size_usd, a.days, a.haircut)
    gas = gas_cost_usd(pool, a.days, tx_cost_usd, a)
    swaps = swap_cost_usd(pool, a.size_usd, a)
    loss = impermanent_loss_usd(pool, a.size_usd, a.days, a)
    net = gross - gas - swaps - loss
    return PoolResult(
        pool=pool,
        gross_usd=gross,
        gas_usd=gas,
        swap_usd=swaps,
        il_usd=loss,
        net_apy=net / a.size_usd * DAYS_PER_YEAR / a.days * 100,
        break_even_size_usd=break_even_size_usd(pool, a, tx_cost_usd),
        break_even_days=break_even_days(pool, a, tx_cost_usd),
        flags=flags_for(pool, net),
    )


def rank(results: list[PoolResult]) -> list[PoolResult]:
    """Put the most misleading pools first.

    Cost-negative pools lead, and among them the ones that advertise the highest APY: they look
    the best and lose money. The other pools follow, by net APY, highest first. Ties are broken the
    same way every time, so the same data always gives the same order.
    """

    def key(result: PoolResult) -> tuple:
        pool = result.pool
        order = (0, -pool.apy) if result.cost_negative else (1, -result.net_apy)
        return (*order, -pool.tvl_usd, pool.chain, pool.project, pool.symbol, pool.pool_id)

    return sorted(results, key=key)


@dataclass(frozen=True)
class ChainSummary:
    """How the pools of one chain came out: the numbers behind the headline counts."""

    chain: str
    analysed: int
    cost_negative: int
    il_risk: int  # pools that carry impermanent loss risk
    il_estimated: int  # of those, the ones whose IL was estimated from token prices
    il_unknown: int  # and the ones that have no IL figure at all

    @property
    def cost_negative_share(self) -> float | None:
        """The share of the analysed pools that are cost-negative, from 0 to 1. None if none."""
        return self.cost_negative / self.analysed if self.analysed else None


def summarise_chains(results: Iterable[PoolResult], chains: Sequence[str]) -> list[ChainSummary]:
    """One summary for each of `chains`, in that order, over every result on that chain."""
    by_chain: dict[str, list[PoolResult]] = {chain: [] for chain in chains}
    for result in results:
        if result.pool.chain in by_chain:
            by_chain[result.pool.chain].append(result)
    return [
        ChainSummary(
            chain=chain,
            analysed=len(group),
            cost_negative=sum(result.cost_negative for result in group),
            il_risk=sum(IL_RISK in result.flags for result in group),
            il_estimated=sum(IL_ESTIMATED in result.flags for result in group),
            il_unknown=sum(IL_UNKNOWN in result.flags for result in group),
        )
        for chain, group in by_chain.items()
    ]
