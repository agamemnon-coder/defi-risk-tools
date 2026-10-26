"""Token prices for the impermanent-loss estimate.

DeFiLlama's own 7-day IL figure (`il7d`) is empty, so IL is estimated from prices instead. For a
pool that holds exactly two tokens, DeFiLlama's chart endpoint gives 30 daily prices of each. The
volatility of their price ratio gives the expected loss (see `model.impermanent_loss_usd`).
"""

import json
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from defi_risk_tools.data import DataError, Pool, describe_error, request_json
from defi_risk_tools.model import PriceSeries, ratio_volatility

CHART_URL = "https://coins.llama.fi/chart/"
WINDOW_DAYS = 30
MAX_COINS_PER_REQUEST = 10  # one request for 26 coins got HTTP 400, one for 9 was accepted
MAX_FAILED_REQUESTS_IN_A_ROW = 5  # then the price service is given up on, whatever is left
_GAVE_UP = f"gave up after {MAX_FAILED_REQUESTS_IN_A_ROW} failed requests in a row"

# DeFiLlama's coin prefix for each chain. Solana addresses are case-sensitive base58. The
# addresses of the other chains are not: they are written in lowercase.
COIN_PREFIXES = {
    "Arbitrum": "arbitrum",
    "OP Mainnet": "optimism",
    "Base": "base",
    "Solana": "solana",
}
CASE_SENSITIVE_PREFIXES = {"solana"}

# Given coin keys, return the prices found and notes about anything that could not be fetched.
Lookup = Callable[[list[str]], tuple[dict[str, PriceSeries], list[str]]]


def normalise_key(key: str) -> str:
    """`prefix:address` in one spelling, so a token written in two cases is one coin."""
    prefix, _, address = key.partition(":")
    prefix = prefix.lower()
    return f"{prefix}:{address if prefix in CASE_SENSITIVE_PREFIXES else address.lower()}"


def coin_keys(pool: Pool) -> tuple[str, str] | None:
    """The two coin keys of a pool whose impermanent loss can be estimated from prices, else None.

    That is a pool with IL risk, no figure from DeFiLlama, exactly two tokens, on a known chain.
    """
    prefix = COIN_PREFIXES.get(pool.chain)
    if prefix is None or not pool.il_risk or pool.il_7d is not None:
        return None
    if len(pool.underlying_tokens) != 2:
        return None
    first, second = (normalise_key(f"{prefix}:{token}") for token in pool.underlying_tokens)
    return None if first == second else (first, second)


def parse_chart(payload: Any) -> dict[str, PriceSeries]:
    """The daily prices of each coin in a chart response, oldest first. Bad points are dropped."""
    coins = payload.get("coins") if isinstance(payload, dict) else None
    if not isinstance(coins, dict):
        raise DataError("price response has no 'coins' object")
    series: dict[str, PriceSeries] = {}
    for key, entry in coins.items():
        points = entry.get("prices") if isinstance(entry, dict) else None
        if not isinstance(points, list):
            continue
        prices = sorted(point for raw in points if (point := _price_point(raw)) is not None)
        if prices:
            series[normalise_key(key)] = prices
    return series


def _price_point(raw: Any) -> tuple[float, float] | None:
    if not isinstance(raw, dict):
        return None
    timestamp, price = raw.get("timestamp"), raw.get("price")
    for value in (timestamp, price):
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            return None
    return (float(timestamp), float(price)) if price > 0 else None


def load_series(path: Path) -> dict[str, PriceSeries]:
    """Read a saved chart response (`GET /chart/{coins}?span=30&period=1d`) from disk."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DataError(f"cannot read {path} ({describe_error(error)})") from error
    return parse_chart(payload)


@dataclass
class _Fetch:
    """What a `fetch_series` call has collected so far."""

    series: dict[str, PriceSeries] = field(default_factory=dict)
    failures: list[tuple[int, str]] = field(default_factory=list)  # (coins in the request, reason)
    answers: int = 0  # how many requests got a chart back, even an empty one


def fetch_series(
    client: httpx.Client, keys: Iterable[str]
) -> tuple[dict[str, PriceSeries], list[str]]:
    """Daily prices for the coins, a few per request.

    A request that fails is not fatal: its coins are left out and a note says why. A request that
    the server rejects (HTTP 400) is split in two and tried again, so one bad coin does not cost
    the others their prices. A request fails for good when none of the requests it was split into
    got an answer. If `MAX_FAILED_REQUESTS_IN_A_ROW` requests in a row fail like that, the price
    service is given up on and the coins not asked for yet are left out as well, so that a service
    which is down does not hold up a run for as long as it has coins to ask for.
    """
    wanted = list(dict.fromkeys(keys))
    fetch = _Fetch()
    failed_in_a_row = 0
    for start in range(0, len(wanted), MAX_COINS_PER_REQUEST):
        if failed_in_a_row >= MAX_FAILED_REQUESTS_IN_A_ROW:
            fetch.failures.append((len(wanted) - start, _GAVE_UP))
            break
        answers_before = fetch.answers
        _fetch_batch(client, wanted[start : start + MAX_COINS_PER_REQUEST], fetch)
        failed_in_a_row = 0 if fetch.answers > answers_before else failed_in_a_row + 1
    return fetch.series, _failure_notes(fetch.failures, len(wanted))


def _fetch_batch(client: httpx.Client, batch: list[str], fetch: _Fetch) -> None:
    url = CHART_URL + ",".join(batch)
    try:
        payload = request_json(client, "GET", url, params={"span": WINDOW_DAYS, "period": "1d"})
        fetch.series.update(parse_chart(payload))
        fetch.answers += 1
    except DataError as error:
        if len(batch) > 1 and _was_rejected(error):
            middle = len(batch) // 2
            _fetch_batch(client, batch[:middle], fetch)
            _fetch_batch(client, batch[middle:], fetch)
        else:
            fetch.failures.append((len(batch), describe_error(error.__cause__ or error)))


def _was_rejected(error: DataError) -> bool:
    cause = error.__cause__
    return isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code == 400


def _failure_notes(failures: list[tuple[int, str]], total: int) -> list[str]:
    if not failures:
        return []
    failed = sum(count for count, _ in failures)
    reasons = ", ".join(dict.fromkeys(reason for _, reason in failures))
    return [f"Token prices: {failed} of {total} coins could not be fetched ({reasons})."]


def estimate_volatilities(
    pools: Iterable[Pool], lookup: Lookup
) -> tuple[dict[str, float], list[str]]:
    """The volatility of the price ratio of each pool whose impermanent loss can be estimated.

    Returns it by pool id, for the pools whose two tokens both have enough daily prices, and
    notes about what could not be estimated. The coins are asked for in the order of the pools, so
    that if the lookup gives out, the pools that came first are the ones that were estimated.
    """
    pairs = {pool.pool_id: keys for pool in pools if (keys := coin_keys(pool)) is not None}
    if not pairs:
        return {}, []
    wanted = list(dict.fromkeys(key for pair in pairs.values() for key in pair))
    series, notes = lookup(wanted)
    volatilities: dict[str, float] = {}
    for pool_id, (first, second) in pairs.items():
        if first in series and second in series:
            volatility = ratio_volatility(series[first], series[second])
            if volatility is not None:
                volatilities[pool_id] = volatility
    unestimated = len(pairs) - len(volatilities)
    if unestimated:
        notes.append(_unestimated_note(len(volatilities), unestimated))
    return volatilities, notes


def _unestimated_note(estimated: int, unestimated: int) -> str:
    """How many of the pools that could be estimated were, and why the others were not."""
    total = estimated + unestimated
    pools = "pool" if total == 1 else "pools"
    stays, its = ("stays", "its") if unestimated == 1 else ("stay", "their")
    return (
        f"Impermanent loss is estimated for {estimated} of the {total} {pools} with IL risk and "
        f"two tokens. The other {unestimated} {stays} IL_UNKNOWN: no usable daily prices for one "
        f"or both of {its} two tokens."
    )
