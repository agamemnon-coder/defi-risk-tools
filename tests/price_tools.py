"""Helpers that build daily price series and chart responses for tests."""

import math

START = 1_700_000_000  # an arbitrary Tuesday morning, in unix time
DAY = 86_400


def prices_with_returns(returns: list[float], start_price: float = 1.0) -> list[float]:
    """Prices whose daily log returns are exactly `returns`."""
    prices = [start_price]
    for change in returns:
        prices.append(prices[-1] * math.exp(change))
    return prices


def daily(prices: list[float], *, offsets: list[int] | None = None) -> list[tuple[int, float]]:
    """(timestamp, price) pairs, one a day. `offsets` moves each timestamp by some seconds."""
    offsets = offsets or [0] * len(prices)
    return [(START + i * DAY + offsets[i], price) for i, price in enumerate(prices)]


def chart(coins: dict[str, list[float]]) -> dict:
    """A chart response with one price a day for each coin."""
    return {
        "coins": {
            key: {
                "symbol": key[-4:],
                "prices": [
                    {"timestamp": START + i * DAY, "price": p} for i, p in enumerate(series)
                ],
            }
            for key, series in coins.items()
        }
    }
