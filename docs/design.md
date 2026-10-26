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
  2026-10-26, and 76 since the Ethereum pools of v1.4).
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
3. **Impermanent loss from prices.** DeFiLlama's `il7d` is null for every pool, so IL is estimated
   for a pool with `ilRisk == "yes"`, no `il7d` and exactly two `underlyingTokens`.
   - **Data:** `GET https://coins.llama.fi/chart/{coins}?span=30&period=1d`, with the coins as
     `<prefix>:<address>` and the prefixes `arbitrum`, `optimism`, `base` and `solana`. The
     response is `{"coins": {key: {"symbol", "prices": [{"timestamp", "price"}]}}}`. At most ten
     coins go in a request (one request for 26 coins, several of them obscure, was rejected with
     HTTP 400 and one for 9 major tokens was accepted; the cause is not known); a rejected request
     is split in two; a failed request is skipped after one retry.
   - **Volatility:** each price of one token is paired with the nearest price of the other (they
     are within half a day, but their timestamps differ by minutes). `sigma` is the sample
     standard deviation of the daily log returns of the price ratio, times `sqrt(365)`. At least 14
     daily returns are needed.
   - **Loss:** `P × sigma² × (T/365) / 8`, the small-move result for a 50/50 constant-product
     pool, capped at 50% of `P` like the `il7d` figure. The pool is flagged `IL_ESTIMATED`. For
     concentrated-liquidity pools (Uniswap v3 and v4, Velodrome v3) it is a lower bound. Pools
     with another number of tokens, or without prices, keep `IL_UNKNOWN`.
   - **When:** only for the pools that survive the filters and `--limit` (the ranking is decided
     without IL; the pools shown are then evaluated and ranked again). Changed in v1.3: every pool
     that survives the filters is estimated, and the limit is applied last.
   - **Offline:** `--from-file` makes no network calls. `--prices-file` is a saved chart response
     for offline runs; without it IL stays unknown. `tests/fixtures/prices_sample.json` is one,
     for the major tokens of the fixture's pools.

## v1.2 changes

1. **A fairer swap model.** v1 charged a swap on every pool except a single-asset stablecoin pool.
   A live run found 1,135 of 1,947 pools cost-negative, mostly because of those swaps, but a
   WETH holder does not swap to lend WETH on Aave. The rule is now:
   - `exposure == "single"` (any asset): no swap. The user is assumed to hold the asset.
   - Multi-asset pools: half of the position is swapped each way, so `2 × rate × P × 0.5`
     (`Assumptions.multi_asset_swap_share`, default 0.5).
   - An exposure that is missing or unknown counts as multi-asset.

   The `stablecoin` field no longer affects the model. On the saved 2026-10-26 snapshot (an
   offline run, without impermanent loss estimates) the cost-negative pools go from 1,137 to 793 of
   1,949, and on the fixture from 19 to 12 of 53.
2. **A daily live report on GitHub Pages.** `.github/workflows/pages.yml` runs every day at 06:17
   UTC, on every push to `main` and on demand. It runs `yield-scan report --out site/index.html
   --limit 100` with live data and deploys the `site` folder with `actions/upload-pages-artifact`
   and `actions/deploy-pages` (permissions `contents: read`, `pages: write` and `id-token: write`;
   concurrency group `pages`; environment `github-pages`). A fork turns it on under
   Settings > Pages > Source: GitHub Actions.

## v1.3 changes

1. **Exact counts.** The headline "Cost-negative: N" depended on `--limit`: impermanent loss was
   only estimated for the pools shown, so a pool that loses money only once its IL is counted was
   not counted unless it was shown. Now:
   - IL is estimated for every pool that passes the TVL and zero-APY filters, whatever the limit.
     A live run on 2026-10-26 needed prices for 828 tokens: 83 requests of at most ten coins, each
     coin asked for once. That took 10 seconds on a fast connection, and a few minutes on a slow
     one.
   - The pools are ranked with their IL, and then `--only-negative` and `--limit` cut the list.
     The counts, the summary by chain and the order of the pools do not depend on the limit, and
     `--only-negative` finds a pool that only loses money with its IL.
   - The coins are asked for in the order of a first ranking without IL (the cost-negative pools
     with the highest advertised APY first), so that if the price service gives out partway, the
     pools at the top of the table are the ones that were estimated.
   - **Failing partway:** a failed chart request is skipped as before. If five requests in a row
     get no answer at all, the rest are not asked for. A request that the server rejects (HTTP 400)
     is split in two, and it counts as failed only if none of its parts got an answer, so a few bad
     coins do not stop the lookup, but a server that rejects everything does. A chart without the
     coins asked for is an answer. The pools that were not estimated stay `IL_UNKNOWN`, and the
     notes say "estimated for X of the Y pools with IL risk and two tokens", so the counts are
     then lower bounds for those pools.
   - `counts.il_risk`, `counts.il_estimated` and `counts.il_unknown` now count all the pools
     analysed, not only the pools shown. The text line says "of the N analysed pools with IL risk".
2. **A summary by chain.** For each scanned chain (in the order given), the pools analysed, the
   cost-negative count and its share, and how many of the pools with IL risk have an IL estimate,
   with a total row. It is in the text output (under the counts), in the JSON (`chain_summary`,
   keyed by chain, with `cost_negative_share` from 0 to 1 and `null` when nothing was analysed)
   and in the HTML page, above the table of pools. The pages workflow keeps its command,
   `report --out site/index.html --limit 100`.
3. **Outliers last.** With impermanent loss estimated for every pool, the top of the live page
   filled with pools that advertise a huge APY and lose it to IL: on 2026-10-26, 58 of the first
   100 rows were flagged `APY_OUTLIER`. Their APY is not to be trusted, and their volatility is
   where the small-move IL formula is least reliable. So in each group (cost-negative, then the
   rest) the pools flagged `APY_OUTLIER` now come after the others, in the same order among
   themselves. They are still counted and still shown.

## v1.4 changes

Ethereum mainnet is the fifth chain, and the first in the list. The DeFiLlama chain name is
`Ethereum`.

1. **Cost table.** `Ethereum` is an EVM chain that pays fees in ETH (priced with
   `coingecko:ethereum`, which is already fetched) and reads one `eth_gasPrice` from a public RPC.
   - **RPC:** `https://ethereum-rpc.publicnode.com`, which needs no key. Three candidates were
     called three times each on 2026-10-26: publicnode answered every time (0.087 gwei, in 50 to
     350 ms); `https://eth.llamarpc.com` answered HTTP 525 (an SSL handshake error from the CDN in
     front of it) every time; `https://cloudflare-eth.com` answered every time with the JSON-RPC
     error -32046, "Cannot fulfill request". If the call fails, the default applies, as on every
     chain, and the report says so.
   - **Gas units:** 200,000 per transaction. It is an estimate of the average of an approve, a
     deposit, a withdrawal and a claim on mainnet, and a round figure: it varies a lot with the
     protocol. The L2s keep 250,000.
   - **L1 data fee:** none. Mainnet is the L1. `TxCost.describe()` leaves the L1 part out when the
     fee is 0.
   - **Default gas price:** 0.09 gwei, the reading of 2026-10-26 (0.087 gwei, and 0.18 gwei later
     that day) rounded. At ETH $2,300 one transaction costs 200,000 gas × 0.09 gwei × $2,300 =
     $0.0414. The default is used offline and when the RPC call fails. Unlike the L2s' it is only
     good for the day it was read: mainnet gas moves by large factors.
2. **Impermanent loss.** `COIN_PREFIXES["Ethereum"] = "ethereum"`, so the tokens of an Ethereum pool
   are looked up as `ethereum:<address>` (lowercase, like the other EVM chains), and Ethereum pools
   with IL risk and two tokens are estimated like the others.
3. **Default chains.** `DEFAULT_CHAINS` is the order of the cost table, with Ethereum first, so it
   leads the list of chains, the by-chain table and the costs of one transaction. The pages
   workflow keeps its command, `report --out site/index.html --limit 100`.
4. **Fixture.** 15 real Ethereum pools of 2026-10-26 are appended to the 61 of the other chains
   (76 in all), unchanged, picked in the same way: large single-asset lending and staking pools
   (Lido, Aave, Sky, Ethena, Morpho with rewards, a pool that pays only rewards), Uniswap v3
   pairs with prices, low-APY multi-asset pools that lose money (Curve, Fluid, Balancer), an
   outlier APY (Uniswap v2 PEPE-WETH) and a pool of three tokens. The prices file gets 30 daily
   prices of WETH, USDC, WBTC and USDT on Ethereum, from a second chart request, merged into the
   first; the nine coins that were there are unchanged.
5. **What a fifth chain costs a live run.** Ethereum has more pools than the four other chains
   together: 4,102 pools were analysed on 2026-10-26, 2,159 of them on Ethereum. The workflow
   command made 146 chart requests for 1,456 tokens (83 requests and 828 tokens with four chains),
   all answered, and took 42 seconds (10 seconds with four chains); the model itself takes 2
   seconds of that. Ethereum alone added 63 requests. The give-up rule of v1.3 bounds a failing
   price service at five requests in a row.
6. **Mainnet gas moves the daily page.** The counts of the live run of 2026-10-26, replayed with
   only the answer of the Ethereum RPC changed (everything else as it was received):

   | Mainnet gas | One transaction | Ethereum pools cost-negative | All five chains |
   | --- | --- | --- | --- |
   | 0.087 gwei | $0.040 | 1,067 of 2,159 (49.4%) | 2,052 of 4,102 |
   | 0.18 gwei (the run) | $0.083 | 1,084 (50.2%) | 2,069 |
   | 0.5 gwei | $0.231 | 1,140 (52.8%) | 2,125 |
   | 2 gwei | $0.925 | 1,468 (68.0%) | 2,453 |
   | 5 gwei | $2.313 | 1,877 (86.9%) | 2,862 |
   | 10 gwei | $4.627 | 2,049 (94.9%) | 3,034 |
   | 30 gwei | $13.88 | 2,127 (98.5%) | 3,112 |

   So the headline of the page depends on the gas price of the hour it was built. At today's
   levels, mainnet costs about what an L2 does, and the Ethereum pools that lose money do so to
   the swaps of multi-asset pools.
