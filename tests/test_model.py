"""The model, checked against numbers worked out by hand.

One transaction costs $0.10 in these tests, so a stablecoin deposit that is approved, made and
withdrawn costs $0.30 in gas. A 36.5% APY pays exactly $1.00 a day on $1,000.
"""

import dataclasses

import pytest

from defi_risk_tools import model
from defi_risk_tools.data import parse_pools
from defi_risk_tools.model import Assumptions, evaluate_pool

TX = 0.10


def test_single_stablecoin_pool_pays_no_swap_and_no_il(make_pool):
    pool = make_pool(apy_base=5.0)

    result = evaluate_pool(pool, Assumptions(), TX)

    # gross: $1,000 * 5% * 30/365. gas: approve + deposit + withdraw = 3 * $0.10.
    assert result.gross_usd == pytest.approx(4.109589, abs=1e-6)
    assert result.gas_usd == pytest.approx(0.30)
    assert result.swap_usd == 0
    assert result.il_usd == 0
    assert result.net_usd == pytest.approx(3.809589, abs=1e-6)
    # 5% a year, minus $0.30 a month: 0.30 / 1000 * 365/30 * 100 = 0.365 points.
    assert result.net_apy == pytest.approx(5.0 - 0.365)
    # gas / (per-dollar yield over 30 days) = 0.30 / (0.05 * 30/365)
    assert result.break_even_size_usd == pytest.approx(73.0)
    # $1,000 * 5% * T/365 >= $0.30 first holds at T = 3 (T = 2.19 is not a whole day)
    assert result.break_even_days == 3
    assert result.flags == ()


def test_volatile_lp_with_a_known_il_figure(make_pool):
    pool = make_pool(
        stablecoin=False,
        exposure="multi",
        apy_base=36.5,
        il_risk=True,
        il_7d=-0.5,
        apy_mean_30d=36.5,
    )

    result = evaluate_pool(pool, Assumptions(days=28), TX)

    assert result.gross_usd == pytest.approx(28.0)  # $1,000 * 36.5% * 28/365
    assert result.gas_usd == pytest.approx(0.30)
    assert result.swap_usd == pytest.approx(6.0)  # 2 * 0.3% * $1,000
    assert result.il_usd == pytest.approx(20.0)  # $1,000 * 0.5% * 28/7
    assert result.costs_usd == pytest.approx(26.30)
    assert result.net_usd == pytest.approx(1.70)
    assert result.net_apy == pytest.approx(1.70 / 1000 * 365 / 28 * 100)
    # per-dollar margin: 0.028 - 0.006 - 0.020 = 0.002, so gas 0.30 / 0.002
    assert result.break_even_size_usd == pytest.approx(150.0)
    # net(T) = T - 5T/7 - 6.30 turns positive between T = 22 (-0.014) and T = 23 (+0.271)
    assert result.break_even_days == 23
    assert result.flags == (model.IL_RISK,)


def test_rewards_are_cut_by_the_haircut_and_add_a_claim_transaction(make_pool):
    pool = make_pool(apy_base=2.0, apy_reward=10.0)

    result = evaluate_pool(pool, Assumptions(), TX)

    # $1,000 * 30/365 * (2% + 10% * (1 - 0.5)) = 82.19178 * 0.07
    assert result.gross_usd == pytest.approx(5.753425, abs=1e-6)
    assert result.gas_usd == pytest.approx(0.40)  # 4 transactions: one of them is the claim
    assert model.REWARD_DEPENDENT in result.flags


def test_haircut_of_one_makes_rewards_worthless_and_zero_keeps_them_whole(make_pool):
    pool = make_pool(apy_base=0.0, apy_reward=36.5)

    worthless = evaluate_pool(pool, Assumptions(days=28, haircut=1.0), TX)
    whole = evaluate_pool(pool, Assumptions(days=28, haircut=0.0), TX)

    assert worthless.gross_usd == pytest.approx(0.0)
    assert whole.gross_usd == pytest.approx(28.0)


@pytest.mark.parametrize(
    ("days", "claims"),
    [(1, 1), (30, 1), (31, 2), (60, 2), (61, 3), (90, 3)],
)
def test_one_claim_per_started_30_days_while_the_pool_pays_rewards(make_pool, days, claims):
    pool = make_pool(apy_reward=3.0)

    assert model.claim_transactions(pool, days, Assumptions()) == claims


def test_no_claims_when_the_pool_pays_no_rewards(make_pool):
    assert model.claim_transactions(make_pool(apy_reward=0.0), 90, Assumptions()) == 0


@pytest.mark.parametrize(
    ("stablecoin", "exposure", "swap_usd"),
    [
        (True, "single", 0.0),  # the only case that needs no swap
        (True, "multi", 6.0),
        (False, "single", 6.0),
        (False, "multi", 6.0),
    ],
)
def test_only_single_asset_stablecoin_pools_skip_the_swap(
    make_pool, stablecoin, exposure, swap_usd
):
    pool = make_pool(stablecoin=stablecoin, exposure=exposure)

    assert model.swap_cost_usd(pool, 1000.0, Assumptions()) == swap_usd


def test_swap_cost_scales_with_the_position_and_the_rate(make_pool):
    pool = make_pool(stablecoin=False)

    assert model.swap_cost_usd(pool, 50_000.0, Assumptions(swap_rate=0.001)) == pytest.approx(100.0)


def test_impermanent_loss_is_capped_at_half_the_position(make_pool):
    pool = make_pool(stablecoin=False, exposure="multi", il_risk=True, il_7d=-20.0)

    loss = model.impermanent_loss_usd(pool, 1000.0, 28, Assumptions())

    assert loss == pytest.approx(500.0)  # uncapped it would be 1000 * 20% * 28/7 = 800


def test_the_sign_of_il7d_does_not_matter(make_pool):
    kwargs = {"il_risk": True, "stablecoin": False, "exposure": "multi"}

    negative = model.impermanent_loss_usd(
        make_pool(il_7d=-0.5, **kwargs), 1000.0, 14, Assumptions()
    )
    positive = model.impermanent_loss_usd(make_pool(il_7d=0.5, **kwargs), 1000.0, 14, Assumptions())

    assert negative == positive == pytest.approx(10.0)


def test_unknown_il_adds_no_cost_but_is_flagged(make_pool):
    pool = make_pool(stablecoin=False, exposure="multi", il_risk=True, il_7d=None)

    result = evaluate_pool(pool, Assumptions(), TX)

    assert result.il_usd == 0
    assert model.IL_RISK in result.flags
    assert model.IL_UNKNOWN in result.flags


def test_il7d_is_ignored_when_the_pool_has_no_il_risk(make_pool):
    pool = make_pool(il_risk=False, il_7d=-3.0)

    assert model.impermanent_loss_usd(pool, 1000.0, 30, Assumptions()) == 0
    assert model.IL_RISK not in model.flags_for(pool, net_usd=1.0)


def test_position_size_and_holding_period_flow_through(make_pool):
    pool = make_pool(apy_base=10.0)

    result = evaluate_pool(pool, Assumptions(size_usd=10_000.0, days=365), TX)

    assert result.gross_usd == pytest.approx(1000.0)  # 10% of $10,000 for a year
    assert result.gas_usd == pytest.approx(0.30)
    assert result.net_apy == pytest.approx(10.0 - 0.30 / 10_000 * 100)


# Break-even edge cases


def test_no_margin_means_no_break_even_size_but_a_longer_hold_still_pays(make_pool):
    # 3% for 30 days is 0.25% of the position, less than the 0.6% the two swaps cost.
    pool = make_pool(stablecoin=False, exposure="single", apy_base=3.0)

    result = evaluate_pool(pool, Assumptions(), TX)

    assert result.break_even_size_usd is None
    assert model.COST_NEGATIVE in result.flags
    # $1,000 * 3% * T/365 >= $6.00 + $0.30: T = 76 pays 6.2466, T = 77 pays 6.3288
    assert result.break_even_days == 77


def test_zero_gas_means_every_size_breaks_even(make_pool):
    pool = make_pool(apy_base=5.0)

    result = evaluate_pool(pool, Assumptions(), 0.0)

    assert result.break_even_size_usd == 0.0
    assert result.break_even_days == 1
    assert model.COST_NEGATIVE not in result.flags


def test_zero_gas_and_zero_margin_has_no_break_even_size(make_pool):
    pool = make_pool(apy_base=0.0)

    result = evaluate_pool(pool, Assumptions(), 0.0)

    assert result.break_even_size_usd is None  # margin is not above zero
    assert result.net_usd == 0
    assert model.COST_NEGATIVE not in result.flags  # nothing was lost either


def test_a_pool_that_pays_nothing_never_breaks_even(make_pool):
    pool = make_pool(apy_base=0.0)

    result = evaluate_pool(pool, Assumptions(), TX)

    assert result.break_even_size_usd is None
    assert result.break_even_days is None
    assert model.COST_NEGATIVE in result.flags
    assert result.net_usd == pytest.approx(-0.30)


def test_break_even_days_search_covers_a_year_or_the_holding_period_if_longer(make_pool):
    # 0.5% APY on a volatile pool: $1,000 * 0.5% * T/365 >= $6.30 first holds at T = 460
    pool = make_pool(stablecoin=False, exposure="multi", apy_base=0.5)

    assert evaluate_pool(pool, Assumptions(days=30), TX).break_even_days is None
    assert evaluate_pool(pool, Assumptions(days=500), TX).break_even_days == 460


def test_break_even_size_agrees_with_the_sign_of_net_on_every_sample_pool(sample_payload):
    a = Assumptions()
    for pool in parse_pools(sample_payload).pools:
        result = evaluate_pool(pool, a, TX)
        size = result.break_even_size_usd
        if size is None:
            assert result.cost_negative, pool.symbol
        elif a.size_usd > size * (1 + 1e-9):
            assert not result.cost_negative, pool.symbol
        elif a.size_usd < size * (1 - 1e-9):
            assert result.cost_negative, pool.symbol


# Flags


def test_cost_negative_is_net_below_zero(make_pool):
    pool = make_pool()

    assert model.COST_NEGATIVE in model.flags_for(pool, net_usd=-0.01)
    assert model.COST_NEGATIVE not in model.flags_for(pool, net_usd=0.0)
    assert model.COST_NEGATIVE not in model.flags_for(pool, net_usd=0.01)


@pytest.mark.parametrize(
    ("base", "reward", "flagged"),
    [(1.0, 1.5, True), (1.0, 1.0, False), (3.0, 1.0, False), (0.0, 0.5, True), (0.0, 0.0, False)],
)
def test_reward_dependent_means_more_than_half_of_the_apy_is_rewards(
    make_pool, base, reward, flagged
):
    pool = make_pool(apy_base=base, apy_reward=reward)

    assert (model.REWARD_DEPENDENT in model.flags_for(pool, 1.0)) is flagged


@pytest.mark.parametrize(
    ("apy", "mean", "outlier", "flagged"),
    [
        (31.0, 10.0, False, True),  # more than 3x the 30-day mean
        (30.0, 10.0, False, False),  # exactly 3x is not more than 3x
        (500.0, None, False, False),  # no mean to compare with
        (5.0, 5.0, True, True),  # DeFiLlama's own outlier flag
        (0.0, 0.0, False, False),
    ],
)
def test_apy_outlier(make_pool, apy, mean, outlier, flagged):
    pool = make_pool(apy_base=apy, apy_mean_30d=mean, outlier=outlier)

    assert (model.APY_OUTLIER in model.flags_for(pool, 1.0)) is flagged


@pytest.mark.parametrize(("tvl", "flagged"), [(99_999.0, True), (100_000.0, False)])
def test_low_tvl_is_under_100k(make_pool, tvl, flagged):
    pool = make_pool(tvl_usd=tvl)

    assert (model.LOW_TVL in model.flags_for(pool, 1.0)) is flagged


def test_flags_come_in_a_fixed_order(make_pool):
    pool = make_pool(
        apy_base=1.0,
        apy_reward=5.0,
        apy_mean_30d=1.0,
        tvl_usd=10.0,
        il_risk=True,
        il_7d=None,
    )

    assert model.flags_for(pool, net_usd=-1.0) == (
        model.COST_NEGATIVE,
        model.REWARD_DEPENDENT,
        model.APY_OUTLIER,
        model.LOW_TVL,
        model.IL_RISK,
        model.IL_UNKNOWN,
    )


# Ranking and assumptions


def test_cost_negative_pools_lead_and_the_highest_advertised_apy_comes_first(make_pool):
    # Each of these loses money: a stablecoin pool pays $0.30 in gas and a volatile one adds $6.
    bad = {"stablecoin": False}
    advertises_most = evaluate_pool(make_pool(pool_id="a", apy_base=6.0, **bad), Assumptions(), TX)
    advertises_less = evaluate_pool(make_pool(pool_id="b", apy_base=2.0, **bad), Assumptions(), TX)
    advertises_none = evaluate_pool(make_pool(pool_id="c", apy_base=0.0), Assumptions(), TX)

    ranked = model.rank([advertises_none, advertises_less, advertises_most])

    assert all(r.cost_negative for r in ranked)
    assert [r.pool.pool_id for r in ranked] == ["a", "b", "c"]  # advertised APY: 6%, 2%, 0%
    # It is not the order of net APY: "c" loses the least (only gas, no swap) and still comes last.
    assert ranked[2].net_apy > ranked[1].net_apy


def test_the_other_pools_follow_by_net_apy_highest_first(make_pool):
    low = evaluate_pool(make_pool(pool_id="low", apy_base=4.0), Assumptions(), TX)
    mid = evaluate_pool(make_pool(pool_id="mid", apy_base=8.0), Assumptions(), TX)
    high = evaluate_pool(make_pool(pool_id="high", apy_base=12.0), Assumptions(), TX)

    ranked = model.rank([low, high, mid])

    assert not any(r.cost_negative for r in ranked)
    assert [r.pool.pool_id for r in ranked] == ["high", "mid", "low"]


def test_cost_negative_pools_come_before_better_looking_ones_that_do_not_lose(make_pool):
    loses = evaluate_pool(
        make_pool(pool_id="loses", apy_base=1.0, stablecoin=False), Assumptions(), TX
    )
    wins = evaluate_pool(make_pool(pool_id="wins", apy_base=50.0), Assumptions(), TX)

    assert [r.pool.pool_id for r in model.rank([wins, loses])] == ["loses", "wins"]


def test_equal_pools_are_ordered_by_tvl_then_by_name(make_pool):
    pools = [
        make_pool(pool_id="small", tvl_usd=1e6, symbol="AAA"),
        make_pool(pool_id="big", tvl_usd=9e6, symbol="ZZZ"),
        make_pool(pool_id="mid-b", tvl_usd=5e6, symbol="BBB"),
        make_pool(pool_id="mid-a", tvl_usd=5e6, symbol="AAA"),
    ]

    ranked = model.rank([evaluate_pool(pool, Assumptions(), TX) for pool in pools])

    assert [r.pool.pool_id for r in ranked] == ["big", "mid-a", "mid-b", "small"]


def test_ranking_does_not_depend_on_the_input_order(make_pool):
    pools = [make_pool(pool_id=f"pool-{i}", symbol=f"S{i % 2}") for i in range(6)]
    results = [evaluate_pool(pool, Assumptions(), TX) for pool in pools]

    forwards = [r.pool.pool_id for r in model.rank(results)]
    backwards = [r.pool.pool_id for r in model.rank(results[::-1])]

    assert forwards == backwards


# The cheap filters


def test_select_pools_filters_by_chain_and_tvl_floor_and_counts_what_it_drops(make_pool):
    pools = [
        make_pool(pool_id="keep", chain="Base", tvl_usd=100_000.0),  # exactly at the floor
        make_pool(pool_id="small", chain="Base", tvl_usd=99_999.0),
        make_pool(pool_id="elsewhere", chain="Solana", tvl_usd=5e6),
        make_pool(pool_id="also-keep", chain="Arbitrum", tvl_usd=5e6),
    ]

    selection = model.select_pools(
        pools, chains=["Base", "Arbitrum"], min_tvl_usd=100_000.0, include_zero_apy=False
    )

    assert [pool.pool_id for pool in selection.pools] == ["keep", "also-keep"]
    assert selection.below_min_tvl == 1  # chains that were not asked for are not counted
    assert selection.zero_apy == 0


def test_pools_advertising_no_apy_are_left_out_unless_asked_for(make_pool):
    pools = [
        make_pool(pool_id="pays", apy_base=3.0),
        make_pool(pool_id="zero", apy_base=0.0),
        make_pool(pool_id="negative", apy_base=-1.0),
        make_pool(pool_id="only-rewards", apy_base=0.0, apy_reward=2.0),
    ]

    left_out = model.select_pools(
        pools, chains=["Arbitrum"], min_tvl_usd=0.0, include_zero_apy=False
    )
    kept = model.select_pools(pools, chains=["Arbitrum"], min_tvl_usd=0.0, include_zero_apy=True)

    assert [pool.pool_id for pool in left_out.pools] == ["pays", "only-rewards"]
    assert left_out.zero_apy == 2
    assert [pool.pool_id for pool in kept.pools] == ["pays", "zero", "negative", "only-rewards"]
    assert kept.zero_apy == 0


def test_a_pool_under_the_tvl_floor_is_counted_once_not_as_zero_apy(make_pool):
    pools = [make_pool(pool_id="tiny", tvl_usd=10.0, apy_base=0.0)]

    selection = model.select_pools(
        pools, chains=["Arbitrum"], min_tvl_usd=100_000.0, include_zero_apy=False
    )

    assert (selection.below_min_tvl, selection.zero_apy) == (1, 0)


@pytest.mark.parametrize(
    "bad",
    [{"size_usd": 0}, {"days": 0}, {"haircut": -0.1}, {"haircut": 1.1}],
)
def test_assumptions_reject_nonsense(bad):
    with pytest.raises(ValueError):
        Assumptions(**bad)


def test_assumptions_are_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Assumptions().days = 7
