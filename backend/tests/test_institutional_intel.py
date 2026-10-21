"""Institutional Intelligence (SEC 13F) dashboard service -- dev-only.

These tests exercise the real TRDGO-US-DEV dataset when it is reachable and
SKIP otherwise, so they never connect to production and never fail in an
environment that has no dev database configured. They lock in the Phase 6
invariants: point-in-time quarter selection, the honest amendment/identity
limitation flags, and -- most importantly -- that institutional
BULLISH/BEARISH/NEUTRAL stays separate from trading BUY/SELL.
"""
import pytest

import institutional_dashboard_service as ins

pytestmark = pytest.mark.skipif(not ins.available(),
                                reason="TRDGO-US-DEV institutional dataset not configured")

# The institutional classification vocabulary must never overlap the trading
# signal vocabulary -- they are different axes and must never be conflated.
INST_LABELS = {"bullish", "bearish", "neutral", "n/a"}
TRADING_LABELS = {"BUY", "SELL", "NEUTRAL"}


def test_overview_reports_bounded_universe():
    ov = ins.overview()
    assert ov["securities_tracked"] == 75
    assert ov["total_managers"] > 0 and ov["total_reported_holdings"] > 0
    # every change bucket present and non-negative
    for k in ("new_positions", "increased_positions", "reduced_positions",
              "closed_positions", "unchanged_positions"):
        assert ov[k] >= 0


def test_stock_classification_is_not_a_trading_signal():
    s = ins.stock("AAPL")
    assert s["status"] == "OK"
    assert s["institutional_classification"] in INST_LABELS
    assert s["institutional_classification"] not in TRADING_LABELS
    # fund-count classification and net-share direction are independent fields
    assert "net_share_direction" in s
    assert s["net_share_direction"] in ("up", "down", "flat")


def test_stock_metadata_backfilled():
    s = ins.stock("AAPL")
    assert s["security_type"]  # Task 1: no longer null
    assert s["exchange"]
    assert s["mapping_confidence"] in ("HIGH", "MEDIUM")


def test_goog_googl_are_separate_share_classes():
    g = ins.stock("GOOG")
    gl = ins.stock("GOOGL")
    assert g["status"] == "OK" and gl["status"] == "OK"
    assert g["cusip"] != gl["cusip"]


def test_point_in_time_before_any_filing_is_unverifiable():
    s = ins.stock("AAPL", as_of="2026-03-01")
    assert s["effective_quarter"] is None
    assert s["verifiable"] is False
    assert s["total_reported_shares"] == 0  # no look-ahead leak


def test_point_in_time_selects_latest_visible_quarter_not_always_q2():
    # Between the Q1 and Q2 filing windows, the latest publicly-available
    # quarter is Q1 -- never the not-yet-filed Q2.
    s = ins.stock("AAPL", as_of="2026-06-30")
    assert s["effective_quarter"] == "2026-Q1"
    assert s["comparison_quarters"] is None


def test_point_in_time_full_comparison_after_q2():
    s = ins.stock("AAPL", as_of="2026-09-30")
    assert s["comparison_quarters"] == "2026-Q1->2026-Q2"
    assert s["verifiable"] is True


def test_invalid_as_of_is_rejected():
    # A malformed as_of must never reach a SQL date comparison.
    r = ins.stock("AAPL", as_of="not-a-date")
    assert r["status"] == "INVALID_AS_OF"
    r2 = ins.stock("AAPL", as_of="2026-13-99")
    assert r2["status"] == "INVALID_AS_OF"


def test_limitations_are_flagged_not_hidden():
    s = ins.stock("AAPL")
    assert "13F-A" in s["amendment_handling"]          # amendments not faked
    assert "current" in s["historical_identity_note"].lower()


def test_rankings_bounded_and_sorted():
    r = ins.rankings("accumulation", limit=10)
    assert len(r["rows"]) <= 10
    nets = [row["net_share_change"] for row in r["rows"]]
    assert nets == sorted(nets, reverse=True)  # top accumulation first


def test_mapping_confidence_counts():
    m = ins.mapping()
    assert m["confidence_counts"].get("HIGH", 0) == 70
    assert m["confidence_counts"].get("MEDIUM", 0) == 5


def test_history_has_no_fabricated_quarters():
    h = ins.history("NVDA")
    assert h["status"] == "OK"
    quarters = {p["quarter"] for p in h["series"]}
    assert quarters <= {"2026-Q1", "2026-Q2"}
    assert all(p["reported_shares"] > 0 for p in h["series"])
