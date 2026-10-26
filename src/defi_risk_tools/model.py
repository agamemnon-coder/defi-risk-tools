"""The yield-cost model: pure functions with no I/O, no clock and no randomness.

All money is in USD. APYs are percentages per year, as DeFiLlama reports them. Yield is
simple (not compounded) and the APY is assumed to stay constant over the holding period.
"""

import math
from dataclasses import dataclass

from defi_risk_tools.data import Pool

DAYS_PER_YEAR = 365
IL_WINDOW_DAYS = 7  # DeFiLlama's `il7d` is the impermanent loss over 7 days

# The flags, in the order they are shown.
COST_NEGATIVE = "COST_NEGATIVE"
REWARD_DEPENDENT = "REWARD_DEPENDENT"
APY_OUTLIER = "APY_OUTLIER"
LOW_TVL = "LOW_TVL"
IL_RISK = "IL_RISK"
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
    IL_UNKNOWN: "Impermanent loss risk, but no 7-day figure exists, so no IL cost is counted.",
}


@dataclass(frozen=True)
class Assumptions:
    """The knobs of the model. The defaults are the ones the README documents."""

    size_usd: float = 1_000.0  # position size P
    days: int = 30  # holding period T
    haircut: float = 0.5  # share of the reward APY assumed lost as reward tokens fall
    swap_rate: float = 0.003  # swap fee plus slippage, each way, as a share of the position
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


def needs_swap(pool: Pool) -> bool:
    """Only a single-asset stablecoin pool is assumed to take the user's deposit as it is."""
    return not (pool.stablecoin and pool.exposure == "single")


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
    """A swap on the way in and another on the way out."""
    return 2 * a.swap_rate * size_usd if needs_swap(pool) else 0.0


def impermanent_loss_usd(pool: Pool, size_usd: float, days: int, a: Assumptions) -> float:
    """The 7-day IL scaled to the holding period and capped. Zero when it is not known."""
    if not pool.il_risk or pool.il_7d is None:
        return 0.0
    loss = size_usd * abs(pool.il_7d) / 100 * days / IL_WINDOW_DAYS
    return min(loss, a.il_cap * size_usd)


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
            flags.append(IL_UNKNOWN)
    return tuple(flags)


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
    """Cost-negative pools first, then by net APY, lowest first. Ties are broken the same way
    every time, so the same data always gives the same order."""

    def key(result: PoolResult) -> tuple:
        pool = result.pool
        return (
            not result.cost_negative,
            result.net_apy,
            -pool.tvl_usd,
            pool.chain,
            pool.project,
            pool.symbol,
            pool.pool_id,
        )

    return sorted(results, key=key)
