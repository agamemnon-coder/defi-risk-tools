"""Fetching and parsing pools: null fields, bad entries, retries."""

import dataclasses
import json

import httpx
import pytest

from defi_risk_tools import data
from defi_risk_tools.data import DataError, Pool, parse_pool, parse_pools


def raw_pool(**overrides) -> dict:
    """A DeFiLlama pool entry as the API sends it."""
    entry = {
        "pool": "abc-123",
        "chain": "Base",
        "project": "demo",
        "symbol": "USDC",
        "tvlUsd": 2_000_000,
        "apy": 5.0,
        "apyBase": 5.0,
        "apyReward": None,
        "apyMean30d": 4.8,
        "stablecoin": True,
        "ilRisk": "no",
        "exposure": "single",
        "il7d": None,
        "outlier": False,
        "rewardTokens": None,
        "underlyingTokens": ["0xabc"],
    }
    entry.update(overrides)
    return entry


# The saved response


def test_the_sample_response_parses_completely(sample_payload):
    parsed = parse_pools(sample_payload)

    assert len(parsed.pools) == 76
    assert parsed.skipped == 0
    chains = {pool.chain for pool in parsed.pools}
    assert chains == {"Ethereum", "Arbitrum", "OP Mainnet", "Base", "Solana"}


def test_load_pools_file_reads_the_sample(fixture_path):
    assert len(data.load_pools_file(fixture_path).pools) == 76


def test_a_sample_pool_is_read_field_by_field(sample_payload):
    pool = parse_pools(sample_payload).pools[0]

    assert pool == Pool(
        pool_id="712ce948-bd9e-4f4a-8916-b72c447f7578",
        chain="Arbitrum",
        project="usd-ai",
        symbol="SUSDAI",
        tvl_usd=554287067.0,
        apy=6.25,
        apy_base=6.25,
        apy_reward=0.0,
        apy_mean_30d=6.77825,
        stablecoin=True,
        il_risk=False,
        exposure="single",
        il_7d=None,
        outlier=False,
        reward_tokens=(),
        underlying_tokens=("0x0a1a1a107e45b7ced86833863f482bc5f4ed82ef",),
    )


# Null fields


def test_a_null_reward_is_zero():
    pool = parse_pool(raw_pool(apyBase=5.0, apyReward=None))

    assert (pool.apy_base, pool.apy_reward) == (5.0, 0.0)


def test_when_both_parts_are_null_the_apy_counts_as_base():
    pool = parse_pool(raw_pool(apy=7.5, apyBase=None, apyReward=None))

    assert (pool.apy, pool.apy_base, pool.apy_reward) == (7.5, 7.5, 0.0)


def test_a_null_base_next_to_a_reward_does_not_count_the_reward_twice():
    # Seen on real pools: apy 28.3 is all reward, apyBase is null.
    pool = parse_pool(raw_pool(apy=28.3, apyBase=None, apyReward=28.3))

    assert (pool.apy_base, pool.apy_reward) == (0.0, 28.3)


def test_a_null_base_takes_the_part_of_the_apy_that_is_not_reward():
    pool = parse_pool(raw_pool(apy=10.0, apyBase=None, apyReward=4.0))

    assert (pool.apy_base, pool.apy_reward) == (6.0, 4.0)


def test_a_null_apy_is_base_plus_reward():
    pool = parse_pool(raw_pool(apy=None, apyBase=2.0, apyReward=3.0))

    assert pool.apy == 5.0


def test_integers_become_floats():
    pool = parse_pool(raw_pool(tvlUsd=1_000_000, apy=0, apyBase=0, apyReward=0))

    assert isinstance(pool.tvl_usd, float)
    assert (pool.apy, pool.apy_base, pool.apy_reward) == (0.0, 0.0, 0.0)


def test_optional_fields_may_be_missing_or_null():
    entry = raw_pool(apyMean30d=None, exposure=None, rewardTokens=None, underlyingTokens=None)
    for key in ("stablecoin", "outlier", "ilRisk", "il7d"):
        del entry[key]

    pool = parse_pool(entry)

    assert pool.apy_mean_30d is None
    assert pool.stablecoin is False
    assert pool.outlier is False
    assert pool.il_risk is False
    assert pool.il_7d is None
    assert pool.exposure == "multi"  # unknown exposure is treated as the costlier case
    assert pool.reward_tokens == ()
    assert pool.underlying_tokens == ()


def test_a_parsed_pool_has_no_il_volatility_until_one_is_measured():
    pool = parse_pool(raw_pool(ilRisk="yes"))

    assert pool.il_volatility is None
    assert dataclasses.replace(pool, il_volatility=0.5).il_volatility == 0.5


def test_il_risk_is_true_only_for_yes():
    assert parse_pool(raw_pool(ilRisk="yes")).il_risk is True
    assert parse_pool(raw_pool(ilRisk="no")).il_risk is False
    assert parse_pool(raw_pool(ilRisk="maybe")).il_risk is False


def test_il7d_is_read_when_present():
    assert parse_pool(raw_pool(ilRisk="yes", il7d=-1.25)).il_7d == -1.25


def test_reward_tokens_are_kept_as_text():
    pool = parse_pool(raw_pool(rewardTokens=["0xr1", "0xr2", 7, None]))

    assert pool.reward_tokens == ("0xr1", "0xr2")


# Bad entries


@pytest.mark.parametrize(
    "entry",
    [
        raw_pool(pool=None),
        raw_pool(pool=""),
        raw_pool(chain=None),
        raw_pool(project=" "),
        raw_pool(symbol=7),
        raw_pool(tvlUsd=None),
        raw_pool(tvlUsd="1000"),
        raw_pool(tvlUsd=-5),
        raw_pool(tvlUsd=True),
        raw_pool(apy=None, apyBase=None, apyReward=None),
        raw_pool(apy="5.0"),
        raw_pool(apyBase=float("inf")),
        raw_pool(apyReward=float("nan")),
        raw_pool(il7d="n/a"),
        raw_pool(apyMean30d=[1]),
        raw_pool(stablecoin="true"),
        raw_pool(outlier=1),
        raw_pool(apy=10**400),
        "not an object",
        None,
        [],
    ],
)
def test_unusable_entries_are_rejected(entry):
    with pytest.raises(ValueError):
        parse_pool(entry)


def test_bad_entries_are_skipped_and_counted():
    payload = {
        "status": "success",
        "data": [
            raw_pool(pool="ok-1"),
            raw_pool(pool=None),
            "junk",
            raw_pool(pool="ok-2"),
            raw_pool(tvlUsd=None),
        ],
    }

    parsed = parse_pools(payload)

    assert [pool.pool_id for pool in parsed.pools] == ["ok-1", "ok-2"]
    assert parsed.skipped == 3


def test_nan_in_the_json_text_is_skipped_not_crashing():
    text = json.dumps({"data": [raw_pool(apyMean30d=float("nan"))]})  # Python writes it as NaN

    parsed = parse_pools(json.loads(text))

    assert parsed.pools == []
    assert parsed.skipped == 1


# Payload shape and files


def test_a_bare_list_of_pools_is_accepted():
    assert len(parse_pools([raw_pool(pool="a"), raw_pool(pool="b")]).pools) == 2


@pytest.mark.parametrize("payload", [{"status": "success"}, {"data": {"a": 1}}, "text", 7, None])
def test_a_payload_without_a_list_of_pools_is_an_error(payload):
    with pytest.raises(DataError, match="'data' list"):
        parse_pools(payload)


def test_a_missing_file_is_a_data_error(tmp_path):
    with pytest.raises(DataError, match="cannot read"):
        data.load_pools_file(tmp_path / "nope.json")


def test_a_file_that_is_not_json_is_a_data_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(DataError, match="cannot read"):
        data.load_pools_file(path)


# HTTP: timeout, one retry


def test_the_client_has_a_timeout():
    with data.make_client() as client:
        assert client.timeout.read == data.REQUEST_TIMEOUT_SECONDS


def test_fetch_pools_reads_the_api_response(http_mock, sample_payload):
    http_mock.get(data.YIELDS_URL).respond(json=sample_payload)

    with data.make_client() as client:
        parsed = data.fetch_pools(client)

    assert len(parsed.pools) == 76


def test_a_failed_request_is_tried_once_more(http_mock):
    route = http_mock.get("https://example.test/x")
    route.side_effect = [httpx.ConnectError("boom"), httpx.Response(200, json={"ok": True})]

    with data.make_client() as client:
        assert data.request_json(client, "GET", "https://example.test/x") == {"ok": True}

    assert route.call_count == 2


def test_a_server_error_is_retried_too(http_mock):
    route = http_mock.get("https://example.test/x")
    route.side_effect = [httpx.Response(503), httpx.Response(200, json=[1])]

    with data.make_client() as client:
        assert data.request_json(client, "GET", "https://example.test/x") == [1]


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503])
def test_timeouts_rate_limits_and_server_errors_are_retried(http_mock, status):
    route = http_mock.get("https://example.test/x")
    route.side_effect = [httpx.Response(status), httpx.Response(200, json={"ok": True})]

    with data.make_client() as client:
        assert data.request_json(client, "GET", "https://example.test/x") == {"ok": True}

    assert route.call_count == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_a_request_the_server_rejected_is_not_sent_again(http_mock, status):
    route = http_mock.get("https://example.test/x").respond(status)

    with data.make_client() as client, pytest.raises(DataError, match=f"HTTP {status}"):
        data.request_json(client, "GET", "https://example.test/x")

    assert route.call_count == 1  # asking again would only get the same answer


def test_two_failures_in_a_row_give_up_with_a_clear_message(http_mock):
    route = http_mock.get("https://example.test/x")
    route.side_effect = [httpx.ConnectError("boom"), httpx.ConnectError("boom again")]

    with data.make_client() as client, pytest.raises(DataError) as caught:
        data.request_json(client, "GET", "https://example.test/x")

    assert route.call_count == 2  # one try and one retry, no more
    assert str(caught.value) == "GET https://example.test/x failed (ConnectError: boom again)"


def test_an_http_error_status_is_reported_without_the_url_noise(http_mock):
    http_mock.get("https://example.test/x").respond(404)

    with data.make_client() as client, pytest.raises(DataError, match=r"failed \(HTTP 404\)"):
        data.request_json(client, "GET", "https://example.test/x")


def test_a_body_that_is_not_json_is_an_error(http_mock):
    http_mock.get("https://example.test/x").respond(200, text="<html>nope</html>")

    with data.make_client() as client, pytest.raises(DataError, match="JSONDecodeError"):
        data.request_json(client, "GET", "https://example.test/x")


def test_requests_identify_the_tool_and_only_read(http_mock):
    route = http_mock.get("https://example.test/x").respond(json={})

    with data.make_client() as client:
        data.request_json(client, "GET", "https://example.test/x")

    request = route.calls.last.request
    assert request.method == "GET"
    assert "yield-scan" in request.headers["user-agent"]
