# defi-risk-tools

**Cost-Negative Yield Scanner** (`yield-scan`): shows what a DeFi yield pays *after* the costs of
using it, and flags the pools where an ordinary deposit loses money.

> **This is an estimate tool. It is not financial advice.** Every number it prints comes from a
> small model with the assumptions listed below, applied to public data that may be wrong or out
> of date. It cannot know your wallet, your tax situation, or what a protocol will do tomorrow.
> It reads public data only and never touches a wallet, a key or a transaction.

## What it does, and why

A headline APY does not include what it costs to get in and out. For a small position held for a
few weeks, these costs can eat the whole yield:

- **Gas.** Approving, depositing, withdrawing and claiming rewards are all transactions, and
  Ethereum L2s also pay a data fee to Ethereum.
- **Swaps.** If the pool needs a token you do not hold, you pay swap fees and slippage on the way
  in and again on the way out.
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
$ uv run yield-scan scan --from-file tests/fixtures/pools_sample.json --limit 10
```

```text
Cost-Negative Yield Scanner  -  estimates only, not financial advice

chain       project            symbol               TVL  APY (base+reward)  costs  net APY  break-even size  flags
----------  -----------------  ---------------  -------  -----------------  -----  -------  ---------------  -------------------------------------------------
Solana      loopscale-lending  USD.INFRA          $8.0M          2.99+5.25  $6.02    -1.7%            never  COST_NEGATIVE,REWARD_DEPENDENT
Arbitrum    gmx-evm            ETH-USDC          $47.5M          6.77+0.00  $6.09   -0.65%            never  COST_NEGATIVE,IL_RISK,IL_UNKNOWN
Base        morpho-blue        GROVE-STEAKUSDC  $438.5M          4.67+0.00  $6.07    -2.7%            never  COST_NEGATIVE,IL_RISK,IL_UNKNOWN
OP Mainnet  morpho-blue        RE7WETH            $3.2M          0.82+2.92  $6.08    -5.1%            never  COST_NEGATIVE,REWARD_DEPENDENT
Base        aave-v3            WETH              $42.1M          1.39+1.86  $6.09    -5.1%            never  COST_NEGATIVE,REWARD_DEPENDENT
Arbitrum    morpho-blue        KPK-EURE-YIELD     $1.6M          0.13+2.87  $6.13    -5.9%            never  COST_NEGATIVE,REWARD_DEPENDENT,IL_RISK,IL_UNKNOWN
Arbitrum    gmx-evm            WBTC.B-USDC       $56.9M          2.54+0.00  $6.09    -4.9%            never  COST_NEGATIVE,IL_RISK,IL_UNKNOWN
OP Mainnet  uniswap-v3         USDC-ZCHF          $1.5M          1.28+0.00  $6.06    -6.1%            never  COST_NEGATIVE
Base        aerodrome-v1       VVV-DIEM           $9.0M          0.00+0.80  $6.09    -7.0%            never  COST_NEGATIVE,REWARD_DEPENDENT,IL_RISK,IL_UNKNOWN
Solana      kamino-lend        BONKSOL            $5.7M          0.00+0.33  $6.02    -7.2%            never  COST_NEGATIVE,REWARD_DEPENDENT,APY_OUTLIER

Pools analysed: 53 on Arbitrum, OP Mainnet, Base, Solana. Cost-negative: 19. Shown: 10.
Left out: 0 under the TVL floor, 8 advertising an APY of 0 or less, 0 unreadable entries in the source.
Source: file tests/fixtures/pools_sample.json
Sorted with cost-negative pools first, the highest advertised APY leading, then the other pools by net APY, highest first.
```

The real output goes on to print the assumptions, the cost of one transaction on each chain, any
notes (for example that a gas price fell back to its default) and the disclaimer. That is the
rule of the tool: every report states the assumptions it was computed with.

The fixture is a real DeFiLlama response saved on 2026-10-26 (61 pools), and `--from-file` makes no
network calls, so you get the same output as above.

The first rows are the most misleading pools: they advertise a real APY and lose money anyway.
USD.INFRA on Solana shows 8.24% a year, but 5.25 points of that are reward tokens (half of which
are assumed lost) and getting in and out of it costs $6.00 in swaps, so $1,000 held for 30 days
ends $1.41 down. "Net APY" annualises the result: -1.7% a year. Pools that advertise no APY at
all are left out (8 of the 61 here), because they mislead nobody; `--include-zero-apy` brings them
back.

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

### `yield-scan scan` and `yield-scan report`

`report` takes the same options as `scan` (except `--json`) plus `--out`.

| Option | Default | Meaning |
| --- | --- | --- |
| `--chain NAME` | Arbitrum, OP Mainnet, Base, Solana | A DeFiLlama chain name. Repeat it for several. Case does not matter. v1 has cost data for the four default chains only; any other name is rejected with a message that lists them. |
| `--size USD` | 1000 | Position size, in dollars. |
| `--days N` | 30 | Holding period, in whole days. |
| `--haircut H` | 0.5 | Share of the reward APY assumed lost, from 0 to 1. |
| `--min-tvl USD` | 100000 | Leave out pools with less TVL than this. |
| `--limit N` | 50 | Show at most this many pools. |
| `--only-negative` | off | Show only `COST_NEGATIVE` pools. |
| `--include-zero-apy` | off | Also show pools that advertise an APY of 0 or less. They are left out by default: they mislead nobody. |
| `--json` | off | Print JSON instead of the table (`scan` only). |
| `--from-file PATH` | none | Read a saved pools JSON instead of calling the API. |
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

### What goes over the network

Without `--from-file`, the tool makes HTTP requests to public endpoints, with a 20 second timeout
and one retry. It uses no API key and has no wallet.

- `GET https://yields.llama.fi/pools`: the pools.
- `GET https://coins.llama.fi/prices/current/coingecko:ethereum,coingecko:solana`: the prices of ETH
  and SOL.
- One read-only `eth_gasPrice` JSON-RPC call to each public RPC of the chains scanned:
  `https://arb1.arbitrum.io/rpc`, `https://mainnet.optimism.io`, `https://mainnet.base.org`.

If a price or gas call fails, the default from the cost table is used and the report says so.
If the pools call fails, the tool stops with an error.

### JSON output

`scan --json` prints one JSON document: `assumptions`, `chain_costs` (the cost of one transaction
per chain and where each input came from), `counts`, `notes` and `pools`. Each pool has `pool` (the
DeFiLlama id), `chain`, `project`, `symbol`, `tvl_usd`, `apy`, `apy_base`, `apy_reward`, `gross_usd`,
`gas_usd`, `swap_usd`, `il_usd`, `costs_usd`, `net_usd`, `net_apy`, `break_even_size_usd`,
`break_even_days`, `flags`, `reward_tokens` and `underlying_tokens`. `break_even_size_usd` and
`break_even_days` are `null` when there is none.

## How the numbers are made

For every pool, with position size P, holding period T (in days) and reward haircut h:

1. **Gross yield** = `P × apyBase/100 × T/365 + P × apyReward/100 × T/365 × (1 − h)`
2. **Gas** = `(enter + exit + claims) × cost of one transaction`
3. **Swaps** = `2 × 0.3% × P`, unless the pool is a single-asset stablecoin pool
4. **Impermanent loss** = `P × |il7d|/100 × T/7`, capped at 50% of P, if DeFiLlama gives `il7d`
5. **Net** = gross − gas − swaps − impermanent loss. The pool is `COST_NEGATIVE` when net < 0.
6. **Net APY** = `net / P × 365/T × 100`

### Worked example

Aave v3 WETH on Base, from the sample data. DeFiLlama shows 3.25% APY: 1.39% base plus 1.86% in
reward tokens. For $1,000 held 30 days:

| Step | Result |
| --- | --- |
| Gross yield | $1,000 × 30/365 × (1.39% + 1.86% × (1 − 0.5)) = **$1.91** |
| Gas | 4 transactions (approve, deposit, withdraw, one claim) × $0.0234 = **$0.09** |
| Swaps | WETH is not a stablecoin: 2 × 0.3% × $1,000 = **$6.00** |
| Impermanent loss | no IL risk, so **$0.00** |
| Net | $1.91 − $0.09 − $6.00 = **−$4.19**, which is **−5.1%** net APY |
| Break-even size | **never**: 30 days of yield per dollar is smaller than the swap cost per dollar, so no size fixes it |
| Break-even days | **97**: holding for 97 days at this size pays for the swaps and the extra claims |

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
| Swaps | You are assumed to hold stablecoins and nothing else. Every pool except a single-asset stablecoin pool (`stablecoin` true and `exposure` single) needs one swap in and one swap out, each costing 0.3% of the position (swap fee plus slippage). The rate is flat: it does not depend on the token, the pool's depth or your size. |
| Impermanent loss | Only for pools with `ilRisk` = yes **and** a 7-day figure `il7d`: the 7-day IL is scaled linearly to the holding period (`P × |il7d|/100 × T/7`) and capped at 50% of P. With no figure, no IL cost is added and the pool is flagged `IL_UNKNOWN`. |
| What is not counted | Deposit or withdrawal fees that are not already in the APY, price impact, bridging, Solana account rent, taxes, lockups, depegs and smart contract risk. |
| Net APY | One-off costs annualised: a loss of $6 on $1,000 over 30 days is shown as −7.3% a year. Short holds therefore look more extreme than long ones. |
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
| `IL_UNKNOWN` | `IL_RISK`, but there is no `il7d` figure, so no IL cost is counted. |

### Sorting

The most misleading pools come first. Cost-negative pools lead, and among them the pools that
advertise the **highest APY**: they look the best and lose money. The other pools follow, by
net APY, highest first. Ties are broken by TVL (largest first) and then by name, so the same data
always gives the same order. `--only-negative` is applied before `--limit`.

## Limitations

Please read these before relying on any number.

- **Estimates, not advice.** The model is deliberately simple. It is meant to show where costs
  matter, not to predict what you will earn or lose.
- **Impermanent loss is mostly not priced.** DeFiLlama's `il7d` field was null for every one of the
  6,836 pools on these four chains when this tool was written (2026-10-26), and 5,091 of them have
  IL risk. So every pool with IL risk is also `IL_UNKNOWN`, and its net APY is **too optimistic**:
  the real result for a volatile liquidity pool can be much worse than shown. The code does price
  IL whenever the figure is present.
- **The data is DeFiLlama's.** If DeFiLlama's APY for a pool is wrong, stale or spiking, so is the
  result. Check the `APY_OUTLIER` flag, and look at the pool on DeFiLlama before you act.
- **Costs are rough.** Gas units are the same for every transaction, the L1 data fee is a flat
  $0.02, and the swap rate is flat. Real costs depend on the transaction, the congestion at the
  moment, the route and your size. Offline runs use stale default prices.
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
| `src/defi_risk_tools/model.py` | The model: pure functions, no I/O. |
| `src/defi_risk_tools/report.py` | The text table, the JSON document and the static HTML page. |
| `src/defi_risk_tools/cli.py` | The `yield-scan` command line. |

The design is in [`docs/design.md`](docs/design.md).

## Later (not in v1)

- A Zero-Airdrop Calculator: the cost of farming a hoped-for airdrop, and what is lost if it is zero.
- History, using DeFiLlama's `chart/{pool}`.

## License

MIT. Copyright (c) 2026 the defi-risk-tools contributors. See [`LICENSE`](LICENSE).
