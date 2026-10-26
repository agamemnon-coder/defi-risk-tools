"""The gas and fee table, and the chain cost lookups that turn it into USD per transaction.

Every number in `COST_TABLE` and `DEFAULT_NATIVE_PRICES_USD` is an estimate, read from the
public endpoints on 2026-10-26 and rounded. They are used when a live lookup fails and for
offline runs (`--from-file`). Edit them here to tune the model; add a `COST_TABLE` entry to
support another chain.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass

import httpx

from defi_risk_tools.data import DataError, request_json

NATIVE_PRICES_URL = "https://coins.llama.fi/prices/current/coingecko:ethereum,coingecko:solana"
COINGECKO_IDS = {"ETH": "coingecko:ethereum", "SOL": "coingecko:solana"}
DEFAULT_NATIVE_PRICES_USD = {"ETH": 2_300.0, "SOL": 100.0}

GAS_UNITS_PER_TX = 250_000  # one approve, deposit, withdraw or claim, on an L2
# One transaction on Ethereum mainnet, in gas units. An estimate: the average of an approve, a
# deposit, a withdrawal and a claim. It varies a lot with the protocol, so it is a round figure.
MAINNET_GAS_UNITS_PER_TX = 200_000
L1_DATA_FEE_USD = 0.02  # estimate of what an L2 pays to post one transaction's data to Ethereum
ETH_PER_GWEI = 1e-9


@dataclass(frozen=True)
class ChainCosts:
    """What it costs to send one transaction on a chain, before any live lookup."""

    native_symbol: str  # the token that pays fees: a key of COINGECKO_IDS
    rpc_url: str | None = None  # public RPC that is asked for `eth_gasPrice` (EVM chains)
    default_gas_price_gwei: float = 0.0  # used when the RPC call fails or the run is offline
    gas_units: int = GAS_UNITS_PER_TX
    l1_fee_usd: float = 0.0  # fixed L1 data fee per transaction (L2s)
    fixed_fee_native: float | None = None  # flat fee per transaction, in the native token


COST_TABLE: dict[str, ChainCosts] = {
    # Mainnet is the L1: it has no L1 data fee. The default gas price is the reading of
    # `eth_gasPrice` on 2026-10-26 (0.087 gwei), rounded. Mainnet gas moves by large factors in a
    # day, so unlike the L2s' this default is only good for the day it was read.
    "Ethereum": ChainCosts(
        native_symbol="ETH",
        rpc_url="https://ethereum-rpc.publicnode.com",
        default_gas_price_gwei=0.09,
        gas_units=MAINNET_GAS_UNITS_PER_TX,
        l1_fee_usd=0.0,
    ),
    "Arbitrum": ChainCosts(
        native_symbol="ETH",
        rpc_url="https://arb1.arbitrum.io/rpc",
        default_gas_price_gwei=0.02,
        l1_fee_usd=L1_DATA_FEE_USD,
    ),
    "OP Mainnet": ChainCosts(
        native_symbol="ETH",
        rpc_url="https://mainnet.optimism.io",
        default_gas_price_gwei=0.001,
        l1_fee_usd=L1_DATA_FEE_USD,
    ),
    "Base": ChainCosts(
        native_symbol="ETH",
        rpc_url="https://mainnet.base.org",
        default_gas_price_gwei=0.006,
        l1_fee_usd=L1_DATA_FEE_USD,
    ),
    # 5,000 lamports (0.000005 SOL) is the base fee for one signature. Ten times that leaves
    # room for a priority fee.
    "Solana": ChainCosts(native_symbol="SOL", fixed_fee_native=0.00005),
}


# The chains that are scanned unless `--chain` says otherwise, in the order of the table: Ethereum,
# Arbitrum, OP Mainnet, Base and Solana.
DEFAULT_CHAINS = tuple(COST_TABLE)


@dataclass(frozen=True)
class TxCost:
    """The assumed cost of one transaction on a chain, and where each input came from."""

    chain: str
    spec: ChainCosts
    native_price_usd: float
    native_price_live: bool
    gas_price_gwei: float | None  # None on chains with a fixed fee
    gas_price_live: bool

    @property
    def usd(self) -> float:
        if self.spec.fixed_fee_native is not None:
            return self.spec.fixed_fee_native * self.native_price_usd
        gas_in_eth = self.spec.gas_units * (self.gas_price_gwei or 0.0) * ETH_PER_GWEI
        return gas_in_eth * self.native_price_usd + self.spec.l1_fee_usd

    def describe(self) -> str:
        symbol = self.spec.native_symbol
        price = f"{symbol} ${self.native_price_usd:,.2f} ({_source(self.native_price_live)})"
        if self.spec.fixed_fee_native is not None:
            fee = f"{self.spec.fixed_fee_native:.5f} {symbol} fixed fee"
            return f"${self.usd:.4f} per tx = {fee} x {price}"
        gas_price = f"{self.gas_price_gwei:.4f} gwei ({_source(self.gas_price_live)})"
        text = f"${self.usd:.4f} per tx = {self.spec.gas_units:,} gas x {gas_price} x {price}"
        if self.spec.l1_fee_usd:  # an L2 also pays for its data on Ethereum; mainnet does not
            text += f" + ${self.spec.l1_fee_usd:.2f} L1 data fee (estimate)"
        return text


def _source(live: bool) -> str:
    return "live" if live else "default"


def canonical_chain(name: str) -> str:
    """Match a chain name typed by the user to the cost table, ignoring case."""
    for known in COST_TABLE:
        if known.casefold() == name.strip().casefold():
            return known
    raise ValueError(f"no cost data for chain {name!r}; supported chains: {', '.join(COST_TABLE)}")


def fetch_native_prices(client: httpx.Client) -> dict[str, float]:
    """USD prices of the native tokens for which the endpoint returned a usable price."""
    payload = request_json(client, "GET", NATIVE_PRICES_URL)
    coins = payload.get("coins") if isinstance(payload, dict) else None
    if not isinstance(coins, dict):
        raise DataError("price response has no 'coins' object")
    prices: dict[str, float] = {}
    for symbol, coin_id in COINGECKO_IDS.items():
        coin = coins.get(coin_id)
        price = _usable_price(coin.get("price")) if isinstance(coin, dict) else None
        if price is not None:
            prices[symbol] = price
    if not prices:
        raise DataError("price response has no usable prices")
    return prices


def _usable_price(value: object) -> float | None:
    """A positive, finite number; anything else is not a price."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def fetch_gas_price_gwei(client: httpx.Client, rpc_url: str) -> float:
    """One `eth_gasPrice` call: a read, never a transaction."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "eth_gasPrice", "params": []}
    payload = request_json(client, "POST", rpc_url, json=body)
    try:
        wei = int(payload["result"], 16)
        if wei < 0:
            raise ValueError("negative gas price")
    except (KeyError, TypeError, ValueError) as error:
        raise DataError(f"{rpc_url} gave no usable eth_gasPrice result") from error
    return wei / 1e9


def resolve_tx_costs(
    chains: Iterable[str], client: httpx.Client | None
) -> tuple[dict[str, TxCost], list[str]]:
    """The cost of one transaction on each chain, plus notes about anything that fell back.

    With `client=None` nothing is fetched: every input is the default from the cost table.
    """
    notes: list[str] = []
    live_prices: dict[str, float] = {}
    if client is None:
        notes.append(
            "Offline run: no network calls were made. Gas prices and native token prices "
            "are the defaults from the cost table."
        )
    else:
        try:
            live_prices = fetch_native_prices(client)
        except DataError as error:
            notes.append(f"Native token prices unavailable ({error}); using the defaults.")

    costs = {chain: _chain_tx_cost(chain, client, live_prices, notes) for chain in chains}
    if live_prices:
        missing = {c.spec.native_symbol for c in costs.values()} - set(live_prices)
        if missing:
            notes.append(f"No live price for {', '.join(sorted(missing))}; using the default.")
    return costs, notes


def _chain_tx_cost(
    chain: str, client: httpx.Client | None, live_prices: dict[str, float], notes: list[str]
) -> TxCost:
    spec = COST_TABLE[chain]
    symbol = spec.native_symbol
    gas_price: float | None = None
    gas_price_live = False
    if spec.fixed_fee_native is None:
        gas_price = spec.default_gas_price_gwei
        if client is not None and spec.rpc_url is not None:
            try:
                gas_price = fetch_gas_price_gwei(client, spec.rpc_url)
                gas_price_live = True
            except DataError as error:
                notes.append(
                    f"{chain}: gas price unavailable ({error}); using the default of "
                    f"{gas_price} gwei."
                )
    return TxCost(
        chain=chain,
        spec=spec,
        native_price_usd=live_prices.get(symbol, DEFAULT_NATIVE_PRICES_USD[symbol]),
        native_price_live=symbol in live_prices,
        gas_price_gwei=gas_price,
        gas_price_live=gas_price_live,
    )
