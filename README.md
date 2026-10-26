# defi-risk-tools

**Cost-Negative Yield Scanner** (`yield-scan`): shows what a DeFi yield pays *after* the costs of
using it, and flags the pools where an ordinary deposit loses money.

Source: https://github.com/agamemnon-coder/defi-risk-tools (MIT)

**Live report:** <https://agamemnon-coder.github.io/defi-risk-tools/>, rebuilt every day from live
DeFiLlama data. If you fork this repository, turn it on under Settings → Pages → Source: GitHub
Actions.

> **This is an estimate tool. It is not financial advice.** Every number it prints comes from a
> small model with the assumptions listed below, applied to public data that may be wrong or out
> of date. It cannot know your wallet, your tax situation, or what a protocol will do tomorrow.
> It reads public data only and never touches a wallet, a key or a transaction.

## What it does, and why

A headline APY does not include what it costs to get in and out. For a small position held for a
few weeks, these costs can eat the whole yield:

- **Gas.** Approving, depositing, withdrawing and claiming rewards are all transactions, and
  Ethereum L2s also pay a data fee to Ethereum.
- **Swaps.** A pool that holds several assets needs a mix of them, so part of your deposit is
  swapped on the way in and swapped back on the way out, with swap fees and slippage each time.
  Lending a single asset that you already hold needs no swap.
- **Reward tokens.** Many APYs are mostly paid in a reward token, and reward tokens tend to fall.
- **Impermanent loss.** Providing liquidity to a volatile pair can lose more than the fees earn.

`yield-scan` reads the public [DeFiLlama](https://defillama.com/yields) yield data, applies the
cost model below to a position of a size and holding period you choose (default: $1,000 for 30
days), and puts the most misleading pools first: the ones that advertise the highest APY and
still lose money. A pool is `COST_NEGATIVE` when the costs are larger than what it pays. The
point is user protection: to make these costs visible before someone deposits, not to rank the
best yields.

It is free and open source (MIT). Version 1 covers Arbitrum, OP Mainnet, Base and Solana.

## Example

```console
$ uv run yield-scan scan --from-file tests/fixtures/pools_sample.json \
>   --prices-file tests/fixtures/prices_sample.json --limit 10
```

```text
Cost-Negative Yield Scanner  -  estimates only, not financial advice

chain       project       symbol              TVL  APY (base+reward)  costs  net APY  break-even size  flags
----------  ------------  --------------  -------  -----------------  -----  -------  ---------------  -------------------------------------------------
Arbitrum    morpho-blue   KPK-EURE-YIELD    $1.6M          0.13+2.87  $3.13    -2.2%            never  COST_NEGATIVE,REWARD_DEPENDENT,IL_RISK,IL_UNKNOWN
Arbitrum    gmx-evm       WBTC.B-USDC      $56.9M          2.54+0.00  $3.79    -2.1%            never  COST_NEGATIVE,IL_RISK,IL_ESTIMATED
OP Mainnet  uniswap-v3    USDC-ZCHF         $1.5M          1.28+0.00  $3.06    -2.4%            never  COST_NEGATIVE
Base        aerodrome-v1  VVV-DIEM          $9.0M          0.00+0.80  $3.09    -3.4%            never  COST_NEGATIVE,REWARD_DEPENDENT,IL_RISK,IL_UNKNOWN
Base        uniswap-v3    JITOSOL-CBBTC    $14.0M          0.14+0.00  $3.07    -3.6%            never  COST_NEGATIVE,IL_RISK,IL_UNKNOWN
Solana      raydium-amm   XDC-USDC         $34.9M          0.04+0.00  $3.02    -3.6%            never  COST_NEGATIVE,IL_RISK,IL_UNKNOWN
OP Mainnet  uniswap-v4    USDC-FNX        $645.3k          0.04+0.00  $3.06    -3.7%            never  COST_NEGATIVE,IL_RISK,IL_UNKNOWN
Solana      raydium-amm   USDC-AKE         $37.9M          0.03+0.00  $3.02    -3.6%            never  COST_NEGATIVE,IL_RISK,IL_UNKNOWN
Solana      raydium-amm   USDC-PHA         $71.0M          0.02+0.00  $3.02    -3.6%            never  COST_NEGATIVE,IL_RISK,IL_UNKNOWN
Solana      raydium-amm   PEARL-USDC       $38.5M          0.02+0.00  $3.02    -3.6%            never  COST_NEGATIVE,IL_RISK,IL_UNKNOWN

Pools analysed: 53 on Arbitrum, OP Mainnet, Base, Solana. Cost-negative: 12. Shown: 10.
Left out: 0 under the TVL floor, 8 advertising an APY of 0 or less, 0 unreadable entries in the source.
Impermanent loss: estimated from token prices for 9 of the 24 analysed pools with IL risk.

By chain
chain       analysed  cost-negative  share  IL estimated
----------  --------  -------------  -----  ------------
Arbitrum          12              2  16.7%        4 of 6
OP Mainnet        11              2  18.2%        1 of 4
Base              15              2  13.3%        4 of 8
Solana            15              6  40.0%        0 of 6
all chains        53             12  22.6%       9 of 24

Source: file tests/fixtures/pools_sample.json
Prices: file tests/fixtures/prices_sample.json
Sorted with cost-negative pools first, the highest advertised APY leading, then the other pools by net APY, highest first. In each group, pools with an APY_OUTLIER come last.
```

The real output goes on to print the assumptions, the cost of one transaction on each chain, any
notes (for example that a gas price fell back to its default) and the disclaimer. That is the
rule of the tool: every report states the assumptions it was computed with.

The fixture is a real DeFiLlama response saved on 2026-10-26 (61 pools). `--prices-file` is a real
chart response from the same day with 30 daily prices of WETH, USDC, WBTC and cbBTC. Both
options make the run offline: no network calls, so you get the same output as above.

The first rows are the most misleading pools: they advertise a real APY and lose money anyway.
KPK-EURE-YIELD on Arbitrum shows 3.00% a year, but 2.87 points of that are reward tokens (half of
which are assumed lost), and as a multi-asset pool it swaps half of a $1,000 deposit on the way in
and back on the way out, which costs $3.00. Held for 30 days, $1,000 ends $1.84 down. "Net APY"
annualises the result: -2.2% a year. Pools that advertise no APY at all are left out (8 of the 61
here), because they mislead nobody; `--include-zero-apy` brings them back. A single-asset pool,
such as lending WETH on Aave, pays no swap, because you are assumed to hold the asset: only 12 of
the 53 pools lose money.

One row shows the impermanent loss estimate. GMX WBTC.B-USDC is flagged `IL_ESTIMATED`: over the
last 30 days the WBTC/USDC price ratio had a volatility of 26% a year, so $1,000 held for 30 days
is expected to lose 1000 × 0.26² × 30/365 / 8 = $0.69 to impermanent loss, and the costs go from
$3.09 to $3.79. The eight other pools shown with IL risk stay `IL_UNKNOWN`: one holds a single
token, and seven have a token with no prices in the saved file.

The lines under the table count all 53 pools, not only the 10 shown: `--limit` cuts the table and
nothing else. The impermanent loss of every pool whose tokens are in the saved prices is estimated
first (9 of the 24 with IL risk), and only then is the list ranked and cut, so the counts and the
order do not depend on the limit. The table by chain splits the totals: Solana has the largest
share of cost-negative pools, 6 of its 15, and no estimate at all, because the saved prices are
of tokens on the other chains.

A sample report built from the same data is in [`docs/report.html`](docs/report.html).

## Install and use

You need [uv](https://docs.astral.sh/uv/) and Python 3.12. From a checkout of this repository:

```console
$ uv sync
$ uv run yield-scan scan                       # live data, the four chains, $1,000 for 30 days
$ uv run yield-scan scan --size 250 --days 14  # a smaller, shorter position
$ uv run yield-scan scan --only-negative --chain Base --limit 20
$ uv run yield-scan scan --json                # the same data as JSON, for scripts
$ uv run yield-scan report --out report.html   # one self-contained HTML page
```

A live run looks up prices for every pool it analyses, so that the counts are exact: 828 tokens in
83 requests when this was written. That took 10 seconds on a fast connection, and the tool says
what it is doing on stderr. A run with `--from-file` makes no requests at all.

### `yield-scan scan` and `yield-scan report`

`report` takes the same options as `scan` (except `--json`) plus `--out`.

| Option | Default | Meaning |
| --- | --- | --- |
| `--chain NAME` | Arbitrum, OP Mainnet, Base, Solana | A DeFiLlama chain name. Repeat it for several. Case does not matter. v1 has cost data for the four default chains only; any other name is rejected with a message that lists them. |
| `--size USD` | 1000 | Position size, in dollars. |
| `--days N` | 30 | Holding period, in whole days. |
| `--haircut H` | 0.5 | Share of the reward APY assumed lost, from 0 to 1. |
| `--min-tvl USD` | 100000 | Leave out pools with less TVL than this. |
| `--limit N` | 50 | Show at most this many pools. The counts and the summary by chain always cover every pool analysed. |
| `--only-negative` | off | Show only `COST_NEGATIVE` pools. |
| `--include-zero-apy` | off | Also show pools that advertise an APY of 0 or less. They are left out by default: they mislead nobody. |
| `--json` | off | Print JSON instead of the table (`scan` only). |
| `--from-file PATH` | none | Read a saved pools JSON instead of calling the API. |
| `--prices-file PATH` | none | A saved chart response to estimate impermanent loss from, instead of calling the API. |
| `--out PATH` | `report.html` | Where `report` writes the page (`report` only). |

Exit codes: 0 on success, 1 if data could not be fetched or read or a file could not be written,
2 for a bad option.

For the GitHub Pages page use more rows than the default, for example
`yield-scan report --limit 500 --out docs/report.html`.

### Saving data and working offline

`--from-file` reads a saved response from `https://yields.llama.fi/pools` (the whole response, or
just its list of pools). **A run with `--from-file` makes no network calls at all**: the gas prices
and token prices are the defaults from the cost table below, and the output says so. That keeps
saved runs reproducible, and it is how the tests work.

```console
$ curl -o pools.json https://yields.llama.fi/pools      # about 11 MB
$ uv run yield-scan scan --from-file pools.json
```

`--prices-file` does the same for token prices: it is a saved response from
`https://coins.llama.fi/chart/{coins}?span=30&period=1d`, where `{coins}` is a comma-separated list
of `<prefix>:<address>` with the prefix `arbitrum`, `optimism`, `base` or `solana` (the addresses
of a pool are in the `underlying_tokens` of `scan --json`). With it, impermanent loss is estimated
offline, for every pool whose tokens are in the file. Without it, an offline run does not estimate
impermanent loss, and says so. Ask for ten coins or so at a time: when this was written, DeFiLlama
rejected one request for 26 coins (several of them obscure tokens, some on Solana) with HTTP 400
and accepted one for 9 major tokens. The cause is not known, so the tool asks for at most ten at a
time and splits a rejected request.

```console
$ curl -o prices.json "https://coins.llama.fi/chart/arbitrum:0x82af49447d8a07e3bd95bd0d56f35241523fbab1,arbitrum:0xaf88d065e77c8cc2239327c5edb3a432268e5831?span=30&period=1d"
$ uv run yield-scan scan --from-file pools.json --prices-file prices.json
```

### What goes over the network

Without `--from-file`, the tool makes HTTP requests to public endpoints, with a 20 second timeout
and one retry. It uses no API key and has no wallet.

- `GET https://yields.llama.fi/pools`: the pools.
- `GET https://coins.llama.fi/prices/current/coingecko:ethereum,coingecko:solana`: the prices of ETH
  and SOL.
- One read-only `eth_gasPrice` JSON-RPC call to each public RPC of the chains scanned:
  `https://arb1.arbitrum.io/rpc`, `https://mainnet.optimism.io`, `https://mainnet.base.org`.
- `GET https://coins.llama.fi/chart/{coins}?span=30&period=1d`: 30 daily prices of the tokens of
  every pool analysed that has IL risk and holds exactly two tokens, at most ten coins per
  request, each coin once. The coins of the pools that rank highest are asked for first.
  Nothing is asked for the other pools, and `--prices-file` replaces these calls.

If a price or gas call fails, the default from the cost table is used and the report says so.
If a chart request fails, its coins are skipped and the pools that need them stay `IL_UNKNOWN`. If
DeFiLlama rejects a chart request (HTTP 400), it is split in two and tried again, so one bad coin
costs only itself. A request that the server rejected (a 4xx other than a timeout or a rate limit)
is not sent twice. If five requests in a row get no answer at all (a rejected request is split,
and counts as failed only if none of its parts is answered), the tool stops asking, so that a
price service that is down does not hold up the run: the pools that were not estimated stay
`IL_UNKNOWN`, the notes say how many were estimated and how many were not, and the counts are then
lower bounds for those pools. If the pools call fails, the tool stops with an error.

### JSON output

`scan --json` prints one JSON document: `source` and `price_source`, `assumptions`, `chain_costs`
(the cost of one transaction per chain and where each input came from), `counts` (the pools
analysed, how many are cost-negative and how many are shown, and of all the pools analysed how
many have IL risk, an estimated IL or none), `chain_summary` (the same counts for each chain, and
`cost_negative_share`, a fraction from 0 to 1 that is `null` when no pool was analysed), `notes`
and `pools`. Each pool has `pool`
(the DeFiLlama id), `chain`, `project`, `symbol`, `tvl_usd`, `apy`, `apy_base`, `apy_reward`,
`gross_usd`, `gas_usd`, `swap_usd`, `il_usd`, `costs_usd`, `net_usd`, `net_apy`,
`break_even_size_usd`, `break_even_days`, `il_volatility`, `flags`, `reward_tokens` and
`underlying_tokens`. `break_even_size_usd`, `break_even_days` and `il_volatility` are `null` when
there is none.

### Live report on GitHub Pages

[`.github/workflows/pages.yml`](.github/workflows/pages.yml) builds the report from live data and
publishes it at <https://agamemnon-coder.github.io/defi-risk-tools/>. It runs every day at 06:17
UTC, on every push to `main`, and on demand (Actions → Pages → Run workflow). It runs
`yield-scan report --out site/index.html --limit 100`, so the page shows the 100 most misleading
pools, with the default assumptions, but its summary and its table by chain count every pool
analysed. The build looks up prices for all of them (83 requests when this was written). If the
pools cannot be fetched the build fails and the previous page stays up; if only the prices cannot
be fetched, the page is built without those estimates and says so in its notes.

To publish your own copy from a fork, turn it on once under Settings → Pages → Source: GitHub
Actions.

## How the numbers are made

For every pool, with position size P, holding period T (in days) and reward haircut h:

1. **Gross yield** = `P × apyBase/100 × T/365 + P × apyReward/100 × T/365 × (1 − h)`
2. **Gas** = `(enter + exit + claims) × cost of one transaction`
3. **Swaps** = `0` for a single-asset pool, `2 × 0.3% × P × 0.5` for a multi-asset pool
4. **Impermanent loss** = `P × |il7d|/100 × T/7` if DeFiLlama gives `il7d`; otherwise, for a pool
   with two priced tokens, `P × σ² × T/365 / 8`, where σ is the annualised volatility of the price
   ratio of the two tokens. Capped at 50% of P.
5. **Net** = gross − gas − swaps − impermanent loss. The pool is `COST_NEGATIVE` when net < 0.
6. **Net APY** = `net / P × 365/T × 100`

### Worked example

Two pools from the sample data, for $1,000 held 30 days. They show both sides of the swap rule.

| Step | Aave v3 WETH, Base (single asset) | Uniswap v3 USDC-ZCHF, OP Mainnet (multi-asset) |
| --- | --- | --- |
| Advertised APY | 3.25%: 1.39% base plus 1.86% in reward tokens | 1.28% |
| Gross yield | $1,000 × 30/365 × (1.39% + 1.86% × (1 − 0.5)) = **$1.9075** | $1,000 × 30/365 × 1.28% = **$1.0547** |
| Gas | 4 transactions (approve, deposit, withdraw, one claim) × $0.02345 = **$0.0938** | 3 transactions × $0.020575 = **$0.0617** |
| Swaps | **none**: you hold WETH, and lending it needs no swap | half of $1,000 is swapped each way: 2 × 0.3% × $500 = **$3.00** |
| Impermanent loss | no IL risk: **$0.00** | no IL risk: **$0.00** |
| Net | **+$1.8137**, which is **+2.2%** net APY | **−$2.0071**, which is **−2.4%** net APY |
| Break-even size | **$49**: gas / (yield per dollar over 30 days) | **never**: 0.1% of yield per dollar against 0.3% of swaps |
| Break-even days | **2** | **88**: holding for 88 days at this size pays for the swaps |

### Assumptions

The first three can be changed with `--size`, `--days` and `--haircut`. The others are fixed in v1;
they live in `Assumptions` in `src/defi_risk_tools/model.py` and the cost table in
`src/defi_risk_tools/costs.py`. Every report prints the ones that matter for its numbers.

| Assumption | v1 value |
| --- | --- |
| Position size P | $1,000 |
| Holding period T | 30 days |
| Reward haircut h | 0.5: half of the reward APY is assumed lost as reward tokens fall. This is a rule of thumb, not a forecast. |
| Yield | Simple, not compounded. The APY DeFiLlama shows is assumed to stay the same for the whole holding period. |
| Base and reward split | `apyBase` is base yield and `apyReward` is reward yield. A null reward counts as 0. A null base counts as the part of `apy` that is not reward, so when both are null, `apy` counts as base. |
| Transactions | 2 to enter (approve and deposit), 1 to exit, and 1 claim for every started 30 days while the reward APY is above 0 (so 3 or 4 transactions at 30 days, 5 at 31 to 60 days with rewards, and so on). |
| Cost of one EVM transaction | `gas units × gas price × price of ETH + L1 data fee`. 250,000 gas units on every L2. The gas price is one `eth_gasPrice` call at run time. The L1 data fee is a flat $0.02 per transaction: a rough estimate, not a measurement. |
| Cost of one Solana transaction | A fixed 0.00005 SOL (ten times the 5,000 lamport base fee, to leave room for a priority fee) times the price of SOL. |
| Swaps | A single-asset pool (`exposure` = `single`) needs no swap, whatever the asset: you are assumed to hold it. A multi-asset pool takes a mix of tokens, so half of the position is swapped into the others on the way in and swapped back on the way out: `2 × 0.3% × P × 0.5`. An exposure that is missing or unknown counts as multi-asset. The 0.3% (swap fee plus slippage) and the one half are flat: they do not depend on the token, the pool's depth or your size, and one half is exact only for a pool of two tokens at equal weights. |
| Impermanent loss | Only for pools with `ilRisk` = yes. If DeFiLlama has a 7-day figure `il7d` (it had none for any pool when this was written), it is scaled linearly to the holding period: `P × \|il7d\|/100 × T/7`. Otherwise, if the pool holds exactly two tokens with prices, it is **estimated**: take 30 days of daily prices of both tokens, the daily log returns of their price ratio, and their sample standard deviation times √365 (σ). The expected loss is then `P × σ² × T/365 / 8`, the small-move result for a 50/50 constant-product pool. Both are capped at 50% of P. The estimate assumes that the volatility of the last 30 days continues, needs at least 14 daily returns, and is a **lower bound for concentrated-liquidity pools** (Uniswap v3 and v4, Velodrome v3 and similar), whose positions can leave their range. With no figure and no estimate, no IL cost is added and the pool is flagged `IL_UNKNOWN`. |
| Prices are looked up for every pool analysed | Impermanent loss is estimated for every pool that passes the filters, not only for the ones shown, and the pools are ranked after that. `--only-negative` and `--limit` then cut the ranked list. So the counts, the summary by chain and the order do not depend on the limit, and a pool that only loses money once IL is counted is found by `--only-negative`. The price of this is the lookups of a live run: 83 requests when this was written. |
| What is not counted | Deposit or withdrawal fees that are not already in the APY, price impact, bridging, Solana account rent, taxes, lockups, depegs and smart contract risk. |
| Net APY | One-off costs annualised: a loss of $3 on $1,000 over 30 days is shown as −3.7% a year. Short holds therefore look more extreme than long ones. |
| TVL floor | Pools with less than $100,000 TVL are left out (`--min-tvl`). |
| Zero-APY pools | Pools whose advertised `apy` is 0 or less are left out (`--include-zero-apy` keeps them). The count is reported. |
| Data | DeFiLlama's values are taken as given. An entry that cannot be read (a missing id or TVL, a text where a number should be, no APY at all) is skipped and counted in the report. |

### The cost table

These are the values used when a live lookup fails and for every `--from-file` run. They are
estimates read from the public endpoints on 2026-10-26 and rounded.

| Chain | Pays fees in | Default gas price | Gas units | L1 data fee | One transaction at the defaults |
| --- | --- | --- | --- | --- | --- |
| Arbitrum | ETH at $2,300 | 0.02 gwei | 250,000 | $0.02 | $0.0315 |
| OP Mainnet | ETH at $2,300 | 0.001 gwei | 250,000 | $0.02 | $0.0206 |
| Base | ETH at $2,300 | 0.006 gwei | 250,000 | $0.02 | $0.0234 |
| Solana | SOL at $100 | fixed fee of 0.00005 SOL | | | $0.0050 |

To support another chain, add an entry to `COST_TABLE` in `src/defi_risk_tools/costs.py`.

### Break-even

- **Break-even size** is the smallest position with net ≥ 0 over the holding period. Gas is a fixed
  cost, while yield, swaps and impermanent loss all scale with the position, so
  `net(P) = P × margin − gas` and the answer is `gas / margin`, where margin is the net yield per
  dollar. It is `never` (`null` in JSON) when the margin is zero or less: no size is ever worth it.
- **Break-even days** is the smallest whole number of days with net ≥ 0 at the position size you
  chose. It is searched day by day, because the number of claims steps up every 30 days, up to one
  year or to your holding period if that is longer. It is `null` if there is none in that window.
  It is in the JSON output and the HTML report, not in the text table.

### Flags

| Flag | Meaning |
| --- | --- |
| `COST_NEGATIVE` | Net is below zero. |
| `REWARD_DEPENDENT` | More than half of the APY is paid in reward tokens. |
| `APY_OUTLIER` | DeFiLlama marks the pool as an outlier, or its APY is more than 3 times its 30-day mean. |
| `LOW_TVL` | Under $100,000 TVL. It only shows up if you lower `--min-tvl` below that. |
| `IL_RISK` | DeFiLlama says the pool carries impermanent loss risk. |
| `IL_ESTIMATED` | `IL_RISK`, and the impermanent loss was estimated from 30 days of token prices (a lower bound for concentrated liquidity). |
| `IL_UNKNOWN` | `IL_RISK`, but there is neither an `il7d` figure nor an estimate (another number of tokens than two, no prices, or an offline run without `--prices-file`), so no IL cost is counted. |

### Sorting

The most misleading pools come first. Cost-negative pools lead, and among them the pools that
advertise the **highest APY**: they look the best and lose money. The other pools follow, by
net APY, highest first. In each group, pools flagged `APY_OUTLIER` come last: their APY is not to
be trusted, so it should not decide what leads. Ties are broken by TVL (largest first) and then by
name, so the same data always gives the same order. Impermanent loss is estimated for every pool first, so the order
counts it; then `--only-negative` is applied, and then `--limit`.

## Limitations

Please read these before relying on any number.

- **Estimates, not advice.** The model is deliberately simple. It is meant to show where costs
  matter, not to predict what you will earn or lose.
- **Impermanent loss is estimated, not measured.** DeFiLlama's `il7d` field was null for every one
  of the 6,836 pools on these four chains when this tool was written (2026-10-26), and 5,091 of
  them have IL risk. So the loss is estimated from 30 days of prices, for pools that hold exactly
  two tokens: it is the loss of a 50/50 constant-product pool and assumes that last month's
  volatility continues. For concentrated-liquidity pools (Uniswap v3 and v4, Velodrome v3 and
  similar) it is a **lower bound**. Pools with another number of tokens, pools whose tokens have no
  prices, and offline runs without `--prices-file` stay `IL_UNKNOWN`, and their net APY is **too
  optimistic**.
- **A pool without prices has no IL cost.** Prices are looked up for every pool analysed, so the
  counts do not depend on `--limit`. A pool whose tokens have no usable prices, or whose prices
  could not be fetched (the notes say how many), stays `IL_UNKNOWN` and counts as if it had no
  impermanent loss. The cost-negative counts are lower bounds for those pools.
- **The data is DeFiLlama's.** If DeFiLlama's APY for a pool is wrong, stale or spiking, so is the
  result. Check the `APY_OUTLIER` flag, and look at the pool on DeFiLlama before you act.
- **Costs are rough.** Gas units are the same for every transaction, the L1 data fee is a flat
  $0.02, and the swap rate is flat. Real costs depend on the transaction, the congestion at the
  moment, the route and your size. Offline runs use stale default prices.
- **A single-asset pool is assumed to need no swap**, because you are assumed to hold its asset. If
  you have to buy the asset first, that swap is not counted, and the pool costs more than shown.
- **The reward haircut is a guess.** Some reward tokens hold their price and many do not.
- **Everything else that can go wrong is outside the model:** smart contract bugs, depegs, hacks,
  withdrawal limits, taxes and your own mistakes.

## Development

```console
$ uv sync
$ uv run pytest -q                   # offline: HTTP is mocked with respx, the CLI tests use --from-file
$ uv run ruff check .
$ uv run ruff format --check .
```

All tests run offline. A fixture in `tests/conftest.py` makes any HTTP request that a test did not
mock fail, so a test cannot reach the network by accident.

| File | What it holds |
| --- | --- |
| `src/defi_risk_tools/data.py` | Fetching with a timeout and one retry, and parsing into `Pool`. Bad entries are skipped and counted. |
| `src/defi_risk_tools/costs.py` | The gas and fee table, and the lookups of gas and token prices. |
| `src/defi_risk_tools/prices.py` | Token prices for the impermanent loss estimate: which pools qualify, fetching in batches, reading a saved response. |
| `src/defi_risk_tools/model.py` | The model: pure functions, no I/O. |
| `src/defi_risk_tools/report.py` | The text table, the JSON document and the static HTML page. |
| `src/defi_risk_tools/cli.py` | The `yield-scan` command line. |

The design is in [`docs/design.md`](docs/design.md).

## Later (not in v1)

- A Zero-Airdrop Calculator: the cost of farming a hoped-for airdrop, and what is lost if it is zero.
- History, using DeFiLlama's `chart/{pool}`.

## License

MIT. Copyright (c) 2026 the defi-risk-tools contributors. See [`LICENSE`](LICENSE).
