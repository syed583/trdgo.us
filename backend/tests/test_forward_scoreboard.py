"""Audit tests for the Genuine Forward Scoreboard (Track A).

Pure-logic tests (no DB, no network): bars are synthetic via monkeypatch, so
these exercise the simulation/dedup/portfolio/timestamp logic directly. Each
test maps to a required audit scenario and asserts PASS/FAIL.
"""
from datetime import datetime, timezone

import pytest

import forward_scoreboard_service as fsb

# A small synthetic daily series (weekday gap between 10-02 and 10-05).
BARS = [
    {"date": "2026-10-01", "open": 100.0, "high": 100, "low": 100, "close": 100.0},
    {"date": "2026-10-02", "open": 101.0, "high": 101, "low": 101, "close": 101.0},
    {"date": "2026-10-05", "open": 102.0, "high": 102, "low": 102, "close": 102.0},
    {"date": "2026-10-06", "open": 103.0, "high": 103, "low": 103, "close": 103.0},
    {"date": "2026-10-07", "open": 104.0, "high": 104, "low": 104, "close": 104.0},
    {"date": "2026-10-08", "open": 105.0, "high": 105, "low": 105, "close": 105.0},
]


@pytest.fixture(autouse=True)
def _patch_bars(monkeypatch):
    monkeypatch.setattr(fsb, "_bars", lambda symbol: BARS)


def _row(**kw):
    base = {"model": "m1", "symbol": "TEST", "earnings_date": "2026-10-06",
            "report_timing": "premarket", "snapshot_date": "2026-10-02",
            "predicted_at": None, "signal": "BUY", "score": 60.0}
    base.update(kw)
    return base


# --- timezone conversion (req 6: incorrect timezone conversion) ----------------
def test_pub_dt_timezone_amc_summer():
    # 2026-07-01 AMC (postmarket) = 16:00 ET (EDT, UTC-4) -> 20:00 UTC
    dt = fsb._pub_dt("2026-07-01", "postmarket")
    assert dt == datetime(2026, 7, 1, 20, 0, tzinfo=timezone.utc)


def test_pub_dt_timezone_bmo_winter():
    # 2026-12-01 BMO (premarket) = 08:00 ET (EST, UTC-5) -> 13:00 UTC
    dt = fsb._pub_dt("2026-12-01", "premarket")
    assert dt == datetime(2026, 12, 1, 13, 0, tzinfo=timezone.utc)


# --- prediction before / after publication (req 6) -----------------------------
def test_prediction_before_publication_included():
    # predicted a day before an AMC release -> included
    r = _row(report_timing="postmarket",
             predicted_at=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc))
    trades, skips = fsb._simulate("TEST", [r])
    assert skips["pub_violation"] == 0 and len(trades) == 1


def test_prediction_after_publication_excluded():
    # AMC release 2026-10-06 16:00 ET (=20:00 UTC); predicted 21:00 UTC same day
    r = _row(snapshot_date="2026-10-05", report_timing="postmarket",
             predicted_at=datetime(2026, 10, 6, 21, 0, tzinfo=timezone.utc))
    trades, skips = fsb._simulate("TEST", [r])
    assert skips["pub_violation"] == 1 and len(trades) == 0


def test_missing_publication_timestamp_not_falsely_excluded():
    # predicted_at None -> cannot disprove; kept, but flagged UNVERIFIED upstream
    r = _row(predicted_at=None)
    trades, skips = fsb._simulate("TEST", [r])
    assert skips["pub_violation"] == 0 and len(trades) == 1


# --- lookahead (snapshot on/after earnings date) -------------------------------
def test_lookahead_excluded():
    r = _row(snapshot_date="2026-10-06", earnings_date="2026-10-06")
    trades, skips = fsb._simulate("TEST", [r])
    assert skips["lookahead"] == 1 and len(trades) == 0


# --- BUY/SELL sign consistency (req 6) -----------------------------------------
def test_buy_sign_consistency():
    # BUY, entry close 10-02 = 101, BMO exit = open 10-06 = 103 -> +gross
    r = _row(signal="BUY", snapshot_date="2026-10-02", earnings_date="2026-10-06",
             report_timing="premarket")
    trades, _ = fsb._simulate("TEST", [r])
    assert trades[0]["gross_pct"] > 0


def test_sell_sign_consistency():
    # SELL on the same rising series -> gross must be NEGATIVE (price rose)
    r = _row(signal="SELL", snapshot_date="2026-10-02", earnings_date="2026-10-06",
             report_timing="premarket")
    trades, _ = fsb._simulate("TEST", [r])
    assert trades[0]["gross_pct"] < 0


# --- BMO vs AMC exit index (req: realistic execution) --------------------------
def test_bmo_exit_is_earnings_session_open():
    r = _row(report_timing="premarket", snapshot_date="2026-10-02", earnings_date="2026-10-06")
    trades, _ = fsb._simulate("TEST", [r])
    assert trades[0]["exit_date"] == "2026-10-06"       # gap-open of the report session


def test_amc_exit_is_next_session_open():
    r = _row(report_timing="postmarket", snapshot_date="2026-10-02", earnings_date="2026-10-06")
    trades, _ = fsb._simulate("TEST", [r])
    assert trades[0]["exit_date"] == "2026-10-07"       # next session after the close release


# --- missing entry/exit prices -------------------------------------------------
def test_missing_entry_price_skipped(monkeypatch):
    bad = [dict(b) for b in BARS]
    bad[1]["close"] = None                               # entry day has no close
    monkeypatch.setattr(fsb, "_bars", lambda s: bad)
    r = _row(snapshot_date="2026-10-02", earnings_date="2026-10-06", report_timing="premarket")
    trades, skips = fsb._simulate("TEST", [r])
    assert len(trades) == 0 and skips["no_entry"] == 1


def test_exit_not_yet_reported_skipped():
    # earnings on the last bar, AMC -> exit would be the NEXT (nonexistent) session
    r = _row(snapshot_date="2026-10-02", earnings_date="2026-10-08", report_timing="postmarket")
    trades, skips = fsb._simulate("TEST", [r])
    assert len(trades) == 0 and skips["no_exit_reported"] == 1


# --- event dedup / duplicates / conflicts (req 2) ------------------------------
def _trade(model, side, sym="TEST", ed="2026-10-06", tminus=1):
    return {"model": model, "symbol": sym, "side": side, "signal": side,
            "earnings_date": ed, "snapshot_date": "2026-10-02", "t_minus": tminus,
            "entry_idx": 1, "exit_idx": 3, "cost_pct": 0.1, "net_pct": 0.0,
            "entry_price": 101.0, "exit_price": 103.0}


def test_duplicate_models_collapse_to_one_event():
    ts = [_trade("m1", "BUY"), _trade("m2", "BUY"), _trade("m3", "BUY")]
    dedup = fsb._dedup_events(ts)
    assert len(dedup) == 1 and dedup[0]["side"] == "BUY"


def test_conflicting_votes_resolved_by_majority():
    ts = [_trade("m1", "BUY"), _trade("m2", "BUY"), _trade("m3", "SELL")]
    dedup = fsb._dedup_events(ts)
    assert len(dedup) == 1 and dedup[0]["side"] == "BUY"


def test_tie_votes_produce_no_trade():
    ts = [_trade("m1", "BUY"), _trade("m2", "SELL")]
    dedup = fsb._dedup_events(ts)
    assert len(dedup) == 0                                # tie -> WAIT, no position


# --- portfolio drawdown: no summed-overlap inflation (req 7) -------------------
def test_portfolio_does_not_sum_overlapping_trades():
    # Two IDENTICAL overlapping positions must not double the loss/drawdown.
    one = [_trade("m1", "SELL")]                          # SELL on rising price = losing
    two = [_trade("m1", "SELL"), _trade("m2", "SELL")]    # same event twice
    p1 = fsb._portfolio(one, net=False)
    p2 = fsb._portfolio(two, net=False)
    assert abs(p1["max_drawdown_pct"] - p2["max_drawdown_pct"]) < 1e-6
    assert abs(p1["total_return_pct"] - p2["total_return_pct"]) < 1e-6


def test_portfolio_drawdown_is_bounded():
    # A single losing SELL from 101 -> 103 is about -2% -- drawdown must be small,
    # never a summed -450%-style number.
    p = fsb._portfolio([_trade("m1", "SELL")], net=False)
    assert -5.0 < p["max_drawdown_pct"] <= 0.0
