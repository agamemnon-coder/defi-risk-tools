# Cost-Negative Yield Scanner: design (v1)

A free, open-source (MIT) tool that shows what a DeFi yield pays after the costs of using it. It flags
the pools where an ordinary deposit loses money: where gas, swap fees and the expected fall of reward
tokens eat the advertised APY. It is a public good for users and ecosystems. It gives no financial advice
and never touches a wallet.

## Scope of v1

- **Chains:** Arbitrum, OP Mainnet, Base and Solana. The DeFiLlama chain names are exactly `Arbitrum`,
  `OP Mainnet`, `Base` and `Solana`. A `--chain` option takes any DeFiLlama chain name.
- **Data:** public endpoints only, no keys:
  - `GET https://yields.llama.fi/pools` gives `{"status","data":[pool]}`. The pool fields used are
    `pool, chain, project, symbol, tvlUsd, apy, apyBase, apyReward, apyMean30d, stablecoin, ilRisk,
    exposure, il7d, outlier, rewardTokens, underlyingTokens`.
  - `GET https://coins.llama.fi/prices/current/coingecko:ethereum,coingecko:solana` gives the native
    prices.
  - For each EVM chain, the gas price comes from one `eth_gasPrice` call to a public RPC:
    - Arbitrum: `https://arb1.arbitrum.io/rpc`
    - OP Mainnet: `https://mainnet.optimism.io`
    - Base: `https://mainnet.base.org`

    If a call fails, the chain's default from the cost table is used, and the report says so.
  - Solana uses a fixed fee per transaction, set in the cost table.
- **Never:** no wallet, no private keys, no transactions, no scraping. HTTP GETs and RPC reads only.

## The model

Assumptions, all of which can be overridden from the command line and are printed in every report:

- position size `P`: $1,000
- holding period `T`: 30 days

For each pool:

- **Gross yield:** `P × apyBase/100 × T/365`, plus `P × apyReward/100 × T/365 × (1 − h)`.
  - `h` is the reward-token haircut, default 0.5. Reward tokens tend to fall, so half of the reward APY
    is assumed lost.
  - When `apyBase`/`apyReward` are null, `apy` counts as base.
- **Gas:** `(enter + exit + claims) × gas cost of one transaction in USD`.
  - enter is 2 transactions (approve and deposit), exit is 1, and claims are 1 per 30 days when
    `apyReward > 0`.
  - One EVM transaction costs `gas_units × gas_price × native_price`. Default gas units per
    transaction: 250,000 on L2s. Arbitrum, OP Mainnet and Base also pay an L1 data fee, set as a fixed
    `l1_fee_usd` per transaction in the cost table: $0.02 by default, and documented as an estimate.
- **Swaps:** entering and exiting a pool whose assets the user does not hold is assumed to need a swap
  each way.
  - The rate is `swap_fee + slippage`: default 0.3% each way, so `2 × 0.003 × P`.
  - A single-asset stablecoin pool (`stablecoin` true and `exposure == "single"`) needs no swap.
- **Impermanent loss:**
  - `ilRisk == "yes"` with an `il7d` value: annualise `il7d` (a 7-day IL in percent) to the holding
    period, `P × |il7d|/100 × T/7`, capped at 50% of `P`.
  - `ilRisk == "yes"` without `il7d`: the pool is flagged `IL_UNKNOWN`, and no IL cost is added.
- **Net:**
  - `net = gross − gas − swaps − IL`.
  - `net_apy = net / P × 365/T × 100`.
  - `COST_NEGATIVE` when `net < 0`.
- **Break-even:**
  - Break-even size is the smallest `P` with `net >= 0` for the holding period: the fixed costs (gas)
    divided by the per-dollar margin, solved in closed form. It is `None` when the margin is ≤ 0 at any
    size.
  - Break-even days is the smallest `T` with `net >= 0` at size `P`, found by search or in closed form.
- **Flags:**
  - `COST_NEGATIVE`
  - `REWARD_DEPENDENT`: more than half of the APY is rewards.
  - `APY_OUTLIER`: DeFiLlama's `outlier` is true, or `apy > 3 × apyMean30d`.
  - `LOW_TVL`: under $100k.
  - `IL_RISK`
  - `IL_UNKNOWN`

## The tool

- A Python 3.12 package `defi_risk_tools` built with uv, a CLI `yield-scan` (typer), and httpx with a
  timeout and one retry.
- **Commands:**
  - `yield-scan scan`
    - Options: `--chain` (repeatable; default the four chains), `--size` (default 1000), `--days`
      (default 30), `--haircut` (default 0.5), `--min-tvl` (default 100000), `--limit` (default 50),
      `--only-negative`, `--json`, and `--from-file` (read a saved pools JSON instead of the API; used
      for tests and offline runs).
    - Output: a table of `chain | project | symbol | TVL | APY (base+reward) | costs | net APY | break-even size | flags`,
      sorted with `COST_NEGATIVE` first and then by net APY.
  - `yield-scan report --out report.html`: one self-contained static HTML page (no CDN) with the same
    data and the assumptions, for GitHub Pages.
- **Code layout:**
  - `data.py`: fetching and parsing into a `Pool` dataclass; bad entries are skipped and counted.
  - `costs.py`: the gas and fee table and the chain cost lookups.
  - `model.py`: pure functions, with no I/O.
  - `report.py`
  - `cli.py`
- **Tests:** pytest, offline only, using `tests/fixtures/pools_sample.json` (61 real pools from
  2026-10-26).
  - The model math is checked with hand-computed numbers, including a stablecoin single pool and a
    volatile LP.
  - Break-even edge cases: no margin, and zero gas.
  - Flags.
  - Parsing of null fields.
  - CLI runs on the fixture, in text and in JSON.
  - The HTML report is written and holds no external URLs, apart from links to DeFiLlama and the
    repository.
- **Docs:** `README.md` covers:
  - what the tool does and why (user protection);
  - the methodology with every assumption;
  - limitations: estimates, not advice, data from DeFiLlama;
  - usage;
  - an example output (from the fixture).
- `LICENSE` (MIT, copyright "the defi-risk-tools contributors"), `.gitignore`, `pyproject.toml` with
  ruff, and a GitHub Actions workflow that runs ruff and pytest on ubuntu.

## Later (not v1)

- Zero-Airdrop Calculator: the cost of farming a hoped-for airdrop, and what is lost if it is zero.
- History, using DeFiLlama's `chart/{pool}`.

## v1.1 changes

1. **Ranking.** Cost-negative pools come first, sorted by their advertised `apy`, highest first, so
   that the most misleading pools lead: they look like X% and lose money. The other pools follow,
   sorted by net APY, highest first. Ties are broken as before (TVL, then chain, project, symbol
   and pool id).
2. **Zero-APY pools.** Pools whose advertised `apy` is 0 or less are left out by default, since
   they mislead nobody. `--include-zero-apy` brings them back. The report says how many were left
   out.
