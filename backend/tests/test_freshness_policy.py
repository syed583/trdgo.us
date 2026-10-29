"""Per-kind freshness policy: limits by days-to-earnings band, parameter mapping, stale / unknown verdicts, config overrides."""

from __future__ import annotations

import json

import pytest

import freshness_policy as fp

ALL = ["eps_estimates", "revenue_estimates", "guidance", "historical_reaction", "price_action", "sector_market",
       "options_positioning", "unusual_options", "disparity", "dark_pool", "peer_readthrough", "short_insider"]
W = dict(zip(ALL, [16, 12, 8, 6, 14, 8, 10, 5, 4, 3, 8, 6]))


def params(skip=(), zero=()):
    out = []
    for n in ALL:
        out.append({"name": n, "weight": W[n], "available": n not in skip, "bias": 0.0 if n in zero else 0.3})
    return out


def test_every_equity_parameter_has_a_data_kind_and_a_source():
    assert set(fp.PARAM_DATA_TYPE) == set(ALL)
    assert set(fp.PARAM_SOURCE) == set(ALL)
    assert sum(W.values()) == 100


@pytest.mark.parametrize("days,band", [(0, "imminent"), (2, "imminent"), (3, "near"), (10, "near"), (11, "far"), (84, "far"), (None, "unknown"), (-3, "imminent")])
def test_bands(days, band):
    assert fp.band(days) == band


def test_limits_differ_by_kind_and_tighten_as_the_report_approaches():
    assert fp.limit_hours("PRICE", 1) == 12.0 and fp.limit_hours("PRICE", 5) == 24.0 and fp.limit_hours("PRICE", 15) == 36.0
    assert fp.limit_hours("HISTORICAL", 1) == 168.0                  # history does not need a daily refresh
    assert fp.limit_hours("OPTIONS_FLOW", 1) < fp.limit_hours("ESTIMATES", 1) < fp.limit_hours("HISTORICAL", 1)
    assert fp.limit_hours("NEWS", 1) == 1.0                          # news is measured in minutes, not days
    assert fp.limit_hours("UNKNOWN_KIND", 1) == 12.0                 # an unmapped kind is judged strictly
    assert fp.limit_hours("PRICE", None) == 12.0                     # no usable date: the strictest limit


def test_warmer_ages_by_proximity():
    assert fp.warm_max_age_seconds(1) == 12 * 3600
    assert fp.warm_max_age_seconds(5) == 20 * 3600
    assert fp.warm_max_age_seconds(None) == 20 * 3600                # unknown date: default, not extra requests


def test_a_young_cache_is_fresh_and_an_old_one_names_exactly_what_is_stale():
    ok = fp.assess(3.3, params(), 0)
    assert ok["status"] == fp.FRESH and ok["stale"] == []
    old = fp.assess(20.0, params(), 0)                               # the audit case: 20-24 h old on the report day
    assert old["status"] == fp.STALE
    assert set(old["stale"]) == {"price_action", "sector_market", "options_positioning", "unusual_options", "disparity", "dark_pool"}
    assert "eps_estimates" not in old["stale"]                       # estimates may be up to 36 h old that close to the report
    assert fp.stale_weight_share(params(), old["stale"]) == 0.44


def test_the_same_age_can_be_fine_for_a_name_that_reports_later():
    assert fp.assess(20.0, params(), 5)["status"] == fp.FRESH         # 24 h limit for price / options in the 3-10 day band
    assert fp.assess(30.0, params(), 5)["status"] == fp.STALE
    assert fp.assess(300.0, params(), 19)["status"] == fp.STALE      # DIS / HD / WMT in the audit: 315 h old, 19-21 days out
    assert "historical_reaction" in fp.assess(300.0, params(), 19)["stale"]


def test_unavailable_parameters_do_not_make_a_cache_stale():
    only_estimates = params(skip=[n for n in ALL if fp.PARAM_DATA_TYPE[n] != "ESTIMATES"])
    assert fp.assess(30.0, only_estimates, 0)["status"] == fp.FRESH   # 30 h < 36 h estimate limit; the stale-prone kinds carry no weight


def test_no_timestamp_is_unknown_not_fresh():
    out = fp.assess(None, params(), 0)
    assert out["status"] == fp.UNKNOWN and "NO_CACHE_TIMESTAMP" in out["notes"]


def test_the_age_is_always_labelled_an_upper_bound():
    assert "AGE_IS_CACHE_BUILD_TIME_UPPER_BOUND" in fp.assess(1.0, params(), 0)["notes"]


def test_environment_override_and_malformed_override(monkeypatch):
    monkeypatch.setenv("EARN_FRESHNESS_JSON", json.dumps({"warm_imminent_h": 6, "limits_h": {"PRICE": [1, 2, 3]}}))
    assert fp.warm_max_age_seconds(0) == 6 * 3600
    assert fp.limit_hours("PRICE", 0) == 1.0
    assert fp.limit_hours("MARKET", 0) == 12.0                       # untouched kinds keep their defaults
    monkeypatch.setenv("EARN_FRESHNESS_JSON", "{not json")
    assert fp.warm_max_age_seconds(0) == 12 * 3600                   # a broken override is ignored, never fatal
