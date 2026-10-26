"""Fetching DeFiLlama yield pools and parsing them into `Pool` records.

Everything this tool does on the network is an HTTP GET or a read-only JSON-RPC call.
Nothing here can sign, send or spend anything.
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

YIELDS_URL = "https://yields.llama.fi/pools"
REQUEST_TIMEOUT_SECONDS = 20.0
ATTEMPTS = 2  # the first try plus one retry
USER_AGENT = "defi-risk-tools yield-scan (read-only)"


class DataError(Exception):
    """Data could not be fetched or read. The message is meant to be shown to the user."""


@dataclass(frozen=True)
class Pool:
    """One DeFiLlama yield pool, reduced to the fields the model uses.

    `apy_base` and `apy_reward` are never None: a missing part is settled while parsing
    (see `_resolve_apy`). `apy` is DeFiLlama's total.
    """

    pool_id: str
    chain: str
    project: str
    symbol: str
    tvl_usd: float
    apy: float
    apy_base: float
    apy_reward: float
    apy_mean_30d: float | None
    stablecoin: bool
    il_risk: bool
    exposure: str
    il_7d: float | None
    outlier: bool
    reward_tokens: tuple[str, ...]
    underlying_tokens: tuple[str, ...]
    # Not part of DeFiLlama's response: the annualised volatility of the price ratio of the pool's
    # two tokens, measured from daily prices. Filled in by the impermanent-loss estimate (see
    # `prices.estimate_volatilities`); None when it was not or could not be measured.
    il_volatility: float | None = None


@dataclass(frozen=True)
class ParsedPools:
    pools: list[Pool]
    skipped: int  # entries that could not be read and were left out


def make_client() -> httpx.Client:
    return httpx.Client(
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


def request_json(client: httpx.Client, method: str, url: str, **kwargs: Any) -> Any:
    """Send a request and return its decoded JSON body. Tries once more if the first try fails.

    A request that the server rejected (a 4xx, other than a timeout or a rate limit) is not tried
    again: the same request would be rejected the same way.
    """
    last_error: Exception
    for _ in range(ATTEMPTS):
        try:
            response = client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            last_error = error
            if not _worth_retrying(error):
                break
    raise DataError(f"{method} {url} failed ({describe_error(last_error)})") from last_error


def _worth_retrying(error: Exception) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return status >= 500 or status in (408, 429)
    return True


def describe_error(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}"
    name = type(error).__name__
    return f"{name}: {error}" if str(error) else name


def fetch_pools(client: httpx.Client) -> ParsedPools:
    return parse_pools(request_json(client, "GET", YIELDS_URL))


def load_pools_file(path: Path) -> ParsedPools:
    """Read a saved `/pools` response (or a bare JSON list of pools) from disk."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DataError(f"cannot read {path} ({describe_error(error)})") from error
    return parse_pools(payload)


def parse_pools(payload: Any) -> ParsedPools:
    """Parse a `/pools` response. Entries that cannot be used are skipped and counted."""
    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise DataError("expected a JSON object with a 'data' list of pools")
    pools: list[Pool] = []
    skipped = 0
    for raw in items:
        try:
            pools.append(parse_pool(raw))
        except ValueError:
            skipped += 1
    return ParsedPools(pools=pools, skipped=skipped)


def parse_pool(raw: Any) -> Pool:
    """Build a `Pool` from one API entry. Raises ValueError if the entry is unusable."""
    if not isinstance(raw, dict):
        raise ValueError("pool entry is not an object")
    apy, apy_base, apy_reward = _resolve_apy(raw)
    tvl_usd = _required_number(raw, "tvlUsd")
    if tvl_usd < 0:
        raise ValueError("tvlUsd is negative")
    exposure = raw.get("exposure")
    return Pool(
        pool_id=_text(raw, "pool"),
        chain=_text(raw, "chain"),
        project=_text(raw, "project"),
        symbol=_text(raw, "symbol"),
        tvl_usd=tvl_usd,
        apy=apy,
        apy_base=apy_base,
        apy_reward=apy_reward,
        apy_mean_30d=_optional_number(raw, "apyMean30d"),
        stablecoin=_flag(raw, "stablecoin"),
        il_risk=raw.get("ilRisk") == "yes",
        exposure=exposure if isinstance(exposure, str) else "multi",
        il_7d=_optional_number(raw, "il7d"),
        outlier=_flag(raw, "outlier"),
        reward_tokens=_strings(raw.get("rewardTokens")),
        underlying_tokens=_strings(raw.get("underlyingTokens")),
    )


def _resolve_apy(raw: dict[str, Any]) -> tuple[float, float, float]:
    """Return (apy, base, reward) with no gaps.

    A missing reward is 0. A missing base is whatever part of `apy` is not reward, so when
    both parts are missing `apy` counts as base. If `apy` itself is missing it is base + reward.
    """
    apy = _optional_number(raw, "apy")
    base = _optional_number(raw, "apyBase")
    reward = _optional_number(raw, "apyReward")
    if apy is None and base is None and reward is None:
        raise ValueError("entry has no APY fields")
    if apy is None:
        apy = (base or 0.0) + (reward or 0.0)
    reward = reward or 0.0
    if base is None:
        base = max(apy - reward, 0.0)
    return apy, base, reward


def _text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is missing or not text")
    return value


def _optional_number(raw: dict[str, Any], key: str) -> float | None:
    """A finite number, or None if the field is null. Booleans and strings are not numbers."""
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key} is not a number")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError(f"{key} is out of range") from error
    if not math.isfinite(number):
        raise ValueError(f"{key} is not finite")
    return number


def _required_number(raw: dict[str, Any], key: str) -> float:
    number = _optional_number(raw, key)
    if number is None:
        raise ValueError(f"{key} is missing")
    return number


def _flag(raw: dict[str, Any], key: str) -> bool:
    value = raw.get(key)
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"{key} is not true or false")
    return bool(value)


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))
