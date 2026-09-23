"""
Tests for the external-provider layer.

No network. What is checked here is the behaviour that protects the operator:
an unconfigured provider is a reportable state rather than an error, keys are
never echoed back, provider payloads are parsed defensively, and seed rows can
never become a statistic.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

import alpha_vantage_estimates_service as av
import benzinga_earnings_service as bz
import earnings_intelligence_service as ei
import provider_config as cfg


# ------------------------------------------------------------ configuration


def test_unconfigured_provider_reports_not_configured(monkeypatch):
    monkeypatch.delenv("BENZINGA_API_KEY", raising=False)
    status = cfg.BENZINGA.status()
    assert status["status"] == cfg.PROVIDER_NOT_CONFIGURED
    assert status["configured"] is False
    assert status["env_var"] == "BENZINGA_API_KEY"
    assert status["signup"]


def test_configured_provider_never_echoes_the_key(monkeypatch):
    monkeypatch.setenv("BENZINGA_API_KEY", "super-secret-value")
    status = cfg.BENZINGA.status()
    assert status["status"] == cfg.OK
    assert status["configured"] is True
    # The key must not appear anywhere in the payload.
    assert "super-secret-value" not in repr(status)


def test_blank_key_counts_as_unconfigured(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "   ")
    assert cfg.ALPHA_VANTAGE.configured is False


def test_provider_matrix_covers_every_provider(monkeypatch):
    matrix = cfg.provider_matrix()
    assert "BENZINGA_API_KEY" in matrix
    assert "ALPHA_VANTAGE_API_KEY" in matrix


def test_status_constants_are_distinct():
    values = [cfg.OK, cfg.PROVIDER_NOT_CONFIGURED, cfg.PROVIDER_OFFLINE,
              cfg.ENTITLEMENT_REQUIRED, cfg.DATA_UNAVAILABLE,
              cfg.PARTIAL_DATA, cfg.RATE_LIMITED]
    assert len(set(values)) == len(values)


def test_unconfigured_sync_makes_no_network_call(monkeypatch):
    """A missing key must short-circuit before any HTTP is attempted."""
    monkeypatch.delenv("BENZINGA_API_KEY", raising=False)

    def explode(*args, **kwargs):
        raise AssertionError("provider was called without a key")

    monkeypatch.setattr(cfg, "fetch_json", explode)
    out = bz.sync_calendar()
    assert out["status"] == cfg.PROVIDER_NOT_CONFIGURED
    assert out["synced"] == 0


# ------------------------------------------------------- benzinga parsing


@pytest.mark.parametrize("raw,expected", [
    ("BMO", "BMO"), ("amc", "AMC"), ("08:30", "BMO"),
    ("16:05", "AMC"), ("", "UNKNOWN"), (None, "UNKNOWN"),
])
def test_reporting_time_parsing(raw, expected):
    assert bz._timing(raw) == expected


@pytest.mark.parametrize("raw", ["2024-11-20", "11/20/2024", "2024-11-20T16:05:00"])
def test_date_parsing_accepts_provider_formats(raw):
    assert bz._date(raw) == date(2024, 11, 20)


def test_date_parsing_rejects_nonsense():
    assert bz._date("not-a-date") is None
    assert bz._date(None) is None


def test_number_parsing_handles_thousands_and_blanks():
    assert bz._num("1,234.5") == Decimal("1234.5")
    assert bz._num("") is None
    assert bz._num("-") is None
    assert bz._num(None) is None


def test_pick_takes_the_first_present_alias():
    row = {"eps_est": None, "eps_estimate": "1.25"}
    assert bz._pick(row, "eps_est", "eps_estimate") == "1.25"


def test_surprise_percentage_and_zero_guard():
    assert bz._surprise(Decimal("1.10"), Decimal("1.00")) == Decimal("10")
    assert bz._surprise(Decimal("1.10"), Decimal("0")) is None
    assert bz._surprise(None, Decimal("1.00")) is None


# ------------------------------------------------------------- lifecycle


def test_lifecycle_partial_when_revenue_is_missing():
    """EPS without revenue is not a finished report."""
    stage = bz._lifecycle(Decimal("1.2"), None, date.today())
    assert stage == "PARTIAL_RESULT"


def test_lifecycle_verified_only_when_both_actuals_present():
    assert bz._lifecycle(Decimal("1.2"), Decimal("5e9"), date.today()) == "OFFICIAL_VERIFIED"


def test_lifecycle_future_event_is_scheduled():
    future = date.today() + timedelta(days=30)
    assert bz._lifecycle(None, None, future) == "SCHEDULED"


def test_lifecycle_flags_a_passed_date_with_no_numbers():
    past = date.today() - timedelta(days=2)
    assert bz._lifecycle(None, None, past) == "RESULT_DETECTED"


def test_lifecycle_stage_list_is_ordered_and_labelled():
    assert ei.LIFECYCLE_STAGES[0] == "SCHEDULED"
    assert "TRADE_READY" in ei.LIFECYCLE_STAGES
    for stage in ei.LIFECYCLE_STAGES:
        assert stage in ei.LIFECYCLE_LABELS


# --------------------------------------------------- earnings statistics


def _row(eps_est, eps_act, verified, move=None, rev_est=None, rev_act=None):
    return {
        "eps_estimate": eps_est, "eps_actual": eps_act,
        "eps_surprise_percent": (
            round((eps_act - eps_est) / abs(eps_est) * 100, 2)
            if eps_act is not None and eps_est else None
        ),
        "revenue_estimate": rev_est, "revenue_actual": rev_act,
        "post_earnings_move_percent": move,
        "verified": verified,
    }


def test_statistics_refuse_to_score_seed_rows():
    """
    The important guarantee: a seed fixture must not look like a track record.
    """
    seed = [_row(1.0, 1.2, verified=False), _row(1.0, 1.1, verified=False)]
    stats = ei._statistics(seed)
    assert stats["basis"] == "TEST_DATA"
    assert stats["beat_rate"] is None
    assert stats["average_surprise"] is None
    assert stats["sample_size"] == 0


def test_statistics_use_verified_rows_only():
    rows = [
        _row(1.0, 1.2, verified=True, move=5.0),
        _row(1.0, 0.9, verified=True, move=-3.0),
        _row(1.0, 5.0, verified=False, move=99.0),   # must be ignored
    ]
    stats = ei._statistics(rows)
    assert stats["basis"] == "VERIFIED"
    assert stats["sample_size"] == 2
    assert stats["beat_rate"] == 50
    assert stats["largest_move"] == 5.0          # not 99 from the seed row


def test_statistics_report_no_data_on_an_empty_history():
    stats = ei._statistics([])
    assert stats["basis"] == "NO_DATA"
    assert stats["beat_rate"] is None


def test_consecutive_beats_counts_from_the_latest_report():
    rows = [                                  # newest first
        _row(1.0, 1.3, verified=True),
        _row(1.0, 1.2, verified=True),
        _row(1.0, 0.8, verified=True),        # streak stops here
        _row(1.0, 1.4, verified=True),
    ]
    assert ei._statistics(rows)["consecutive_beats"] == 2


def test_move_statistics_use_absolute_moves():
    rows = [
        _row(1.0, 1.1, verified=True, move=-8.0),
        _row(1.0, 1.1, verified=True, move=2.0),
        _row(1.0, 1.1, verified=True, move=4.0),
    ]
    stats = ei._statistics(rows)
    assert stats["largest_move"] == 8.0
    assert stats["median_move"] == 4.0
    assert stats["average_move"] == pytest.approx(4.67, abs=0.01)


def test_revenue_beat_rate_ignores_rows_without_both_values():
    rows = [
        _row(1.0, 1.1, verified=True, rev_est=100.0, rev_act=120.0),
        _row(1.0, 1.1, verified=True, rev_est=100.0, rev_act=90.0),
        _row(1.0, 1.1, verified=True),        # no revenue at all
    ]
    assert ei._statistics(rows)["revenue_beat_rate"] == 50


# --------------------------------------------------- alpha vantage windows


def test_revision_horizons_are_the_documented_windows():
    assert av.HORIZONS == [0, 7, 30, 60, 90]
    for horizon in av.HORIZONS:
        assert horizon in av.HORIZON_TOLERANCE


def test_unconfigured_revisions_report_not_configured(monkeypatch):
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    out = av.get_revisions("NVDA")
    assert out["status"] == cfg.PROVIDER_NOT_CONFIGURED
    assert out["rows"] == []


def test_unconfigured_snapshot_makes_no_network_call(monkeypatch):
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)

    def explode(*args, **kwargs):
        raise AssertionError("provider was called without a key")

    monkeypatch.setattr(cfg, "fetch_json", explode)
    out = av.snapshot_symbol("NVDA")
    assert out["status"] == cfg.PROVIDER_NOT_CONFIGURED


# ------------------------------------------------------------- key redaction


def test_redact_removes_a_configured_key(monkeypatch):
    """
    Alpha Vantage echoes the API key inside its rate-limit message, so any
    provider text that gets logged or returned must be scrubbed first.
    """
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "SECRETKEY123")
    message = "We have detected your API key as SECRETKEY123 and our limit is 25/day"
    out = cfg.redact(message)
    assert "SECRETKEY123" not in out
    assert "***REDACTED***" in out


def test_redact_leaves_ordinary_text_alone(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "SECRETKEY123")
    assert cfg.redact("nothing sensitive here") == "nothing sensitive here"


def test_redact_handles_no_key_configured(monkeypatch):
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    monkeypatch.delenv("BENZINGA_API_KEY", raising=False)
    assert cfg.redact("plain message") == "plain message"


def test_provider_error_redacts_on_construction(monkeypatch):
    monkeypatch.setenv("BENZINGA_API_KEY", "BZKEY999")
    err = cfg.ProviderError(cfg.RATE_LIMITED, "token BZKEY999 was rate limited")
    assert "BZKEY999" not in err.detail
    assert "BZKEY999" not in str(err)


# ------------------------------------------------- benzinga surprise units


def test_surprise_is_computed_not_taken_from_the_provider_field():
    """
    Benzinga sends eps_surprise_percent as a fraction (0.0688 = +6.88%).
    Trusting it verbatim understates every surprise by 100x, so the value is
    recomputed from the estimate and the actual.
    """
    from decimal import Decimal
    assert bz._surprise(Decimal("2.02"), Decimal("1.89")) == pytest.approx(
        Decimal("6.878"), abs=Decimal("0.01"))


def test_as_percent_scales_a_fraction_but_not_a_percentage():
    from decimal import Decimal
    assert bz._as_percent("0.0688") == pytest.approx(Decimal("6.88"), abs=Decimal("0.01"))
    assert bz._as_percent("6.88") == pytest.approx(Decimal("6.88"), abs=Decimal("0.01"))
    assert bz._as_percent("") is None


def test_scope_key_stays_within_the_column_width():
    """A long ticker list must not overflow provider_fetch_log.scope."""
    long_scope = "2024-01-01:2027-01-01:" + ",".join(f"SYM{i}" for i in range(40))
    key = bz._scope_key(long_scope)
    assert len(key) <= bz.SCOPE_MAX
    # Deterministic, so the cache still hits.
    assert key == bz._scope_key(long_scope)


def test_scope_key_is_left_readable_when_short():
    assert bz._scope_key("2026-01-01:2026-02-01:") == "2026-01-01:2026-02-01:"


# ------------------------------------------- outage must not poison the cache


def test_move_measurement_reports_why_it_found_nothing(monkeypatch):
    """
    An IBKR outage and "the reports predate the bar window" both yield zero
    moves, but they are different facts: only the first should expire fast.
    """
    monkeypatch.setattr(ei.market, "get_chart",
                        lambda *a, **k: {"status": "IBKR_UNAVAILABLE", "bars": []})
    rows = [{"date": "2026-01-29", "reporting_time": "AMC"}]
    filled, status = ei._attach_post_earnings_moves("AAPL", rows)
    assert filled == 0
    assert status == "IBKR_UNAVAILABLE"


def test_move_measurement_is_ok_when_there_is_nothing_to_measure():
    filled, status = ei._attach_post_earnings_moves("AAPL", [])
    assert (filled, status) == (0, cfg.OK)


def test_moves_are_measured_from_daily_bars(monkeypatch):
    """AMC reports move the next session; BMO reports move the same session."""
    bars = [
        {"t": "2026-01-28T00:00:00", "close": 100.0},
        {"t": "2026-01-29T00:00:00", "close": 110.0},
        {"t": "2026-01-30T00:00:00", "close": 99.0},
    ]
    monkeypatch.setattr(ei.market, "get_chart",
                        lambda *a, **k: {"status": "OK", "bars": bars})

    amc = [{"date": "2026-01-29", "reporting_time": "AMC"}]
    filled, status = ei._attach_post_earnings_moves("AAPL", amc)
    assert (filled, status) == (1, cfg.OK)
    assert amc[0]["post_earnings_move_percent"] == -10.0     # 110 -> 99
    assert amc[0]["post_earnings_date"] == "2026-01-30"

    bmo = [{"date": "2026-01-29", "reporting_time": "BMO"}]
    ei._attach_post_earnings_moves("AAPL", bmo)
    assert bmo[0]["post_earnings_move_percent"] == 10.0      # 100 -> 110
    assert bmo[0]["post_earnings_date"] == "2026-01-29"


def test_reports_older_than_the_bar_window_are_left_unmeasured(monkeypatch):
    """Out of range stays None rather than being approximated."""
    bars = [{"t": "2026-01-28T00:00:00", "close": 100.0},
            {"t": "2026-01-29T00:00:00", "close": 110.0}]
    monkeypatch.setattr(ei.market, "get_chart",
                        lambda *a, **k: {"status": "OK", "bars": bars})
    rows = [{"date": "2020-05-01", "reporting_time": "AMC"}]
    filled, status = ei._attach_post_earnings_moves("AAPL", rows)
    assert filled == 0
    assert status == cfg.OK                      # the source worked; the row is old
    assert rows[0].get("post_earnings_move_percent") is None


def test_degraded_history_is_not_served_from_a_stale_cache(monkeypatch):
    """
    The real bug this guards: the Benzinga half succeeded while TWS was down,
    and the merged result was cached for six hours reporting zero moves.
    """
    import time as _time
    key = "earnhist:ZZZ:8"
    degraded = {"symbol": "ZZZ", "moves_measured": 0,
                "moves_status": "IBKR_UNAVAILABLE"}
    ei.market.cache.put(key, degraded)

    # Fresh enough for the long TTL, far too old for the degraded one.
    with ei.market.cache._lock:
        ei.market.cache._data[key] = (_time.time() - (ei.DEGRADED_TTL + 60), degraded)

    assert ei.market.cache.get(key, ei.HISTORY_TTL) is not None      # long TTL alone
    assert ei.market.cache.get(key, ei.DEGRADED_TTL) is None         # short TTL expires it

    healthy = {"symbol": "ZZZ", "moves_measured": 4, "moves_status": cfg.OK}
    ei.market.cache.put(key, healthy)
    assert ei.market.cache.get(key, ei.HISTORY_TTL)["moves_measured"] == 4
    ei.market.cache.clear()


# ------------------------------------- verified history must reach the scorer


def test_score_history_adapter_carries_the_verified_beat_rate():
    """
    The gate approves Benzinga data, so the scorer must be handed Benzinga
    data. Previously it was handed the legacy table instead, which is empty for
    most symbols, so a verified 100% beat rate scored 0/15 and reported OK.
    """
    payload = {
        "symbol": "AAPL",
        "quarters": [
            {"date": "2026-07-30", "verified": True,
             "eps_estimate": 1.89, "eps_actual": 2.02,
             "eps_surprise_percent": 6.88,
             "revenue_estimate": 1.0, "revenue_actual": 1.1,
             "revenue_surprise_percent": 10.0},
            {"date": "2026-04-30", "verified": True,
             "eps_estimate": 1.94, "eps_actual": 2.01,
             "eps_surprise_percent": 3.61,
             "revenue_estimate": 1.0, "revenue_actual": 1.05,
             "revenue_surprise_percent": 5.0},
        ],
        "stats": {"beat_rate": 100, "revenue_beat_rate": 100},
    }
    out = ei.as_score_history(payload)
    assert out["summary"]["eps_beat_rate"] == 100
    assert out["summary"]["revenue_beat_rate"] == 100
    assert out["summary"]["eps_comparable_events"] == 2
    assert out["summary"]["revenue_comparable_events"] == 2
    assert len(out["events"]) == 2

    from earnings_history_score_service import score_earnings_history
    scored = score_earnings_history(out)
    assert scored["score"] > 0, "a verified beat history must not score zero"
    assert scored["eps_beat_rate"] == 100


def test_score_history_adapter_excludes_seed_rows():
    """A seed row must not reach the scorer even through the adapter."""
    payload = {
        "symbol": "NVDA",
        "quarters": [
            {"date": "2026-07-30", "verified": False,
             "eps_estimate": 1.0, "eps_actual": 5.0,
             "eps_surprise_percent": 400.0,
             "revenue_estimate": 1.0, "revenue_actual": 9.0,
             "revenue_surprise_percent": 800.0},
        ],
        "stats": {"beat_rate": None, "revenue_beat_rate": None},
    }
    out = ei.as_score_history(payload)
    assert out["events"] == []
    assert out["summary"]["eps_comparable_events"] == 0

    from earnings_history_score_service import score_earnings_history
    scored = score_earnings_history(out)
    assert scored["score"] == 0
    assert scored["confidence"] == 0


def test_score_history_adapter_counts_only_comparable_quarters():
    """A quarter with an actual but no estimate cannot be scored as a beat."""
    payload = {
        "symbol": "AAPL",
        "quarters": [
            {"date": "2026-07-30", "verified": True,
             "eps_estimate": 1.89, "eps_actual": 2.02,
             "eps_surprise_percent": 6.88,
             "revenue_estimate": None, "revenue_actual": 1.1},
            {"date": "2026-04-30", "verified": True,
             "eps_estimate": None, "eps_actual": 2.01,
             "eps_surprise_percent": None,
             "revenue_estimate": None, "revenue_actual": None},
        ],
        "stats": {"beat_rate": 100, "revenue_beat_rate": None},
    }
    out = ei.as_score_history(payload)
    assert out["summary"]["eps_comparable_events"] == 1
    assert out["summary"]["revenue_comparable_events"] == 0
