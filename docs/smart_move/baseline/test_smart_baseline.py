"""Automated leakage / guard tests for the Smart Move offline baseline. Run: SMV_CANDLES=<candles_raw dir> python -m pytest test_smart_baseline.py -q
(tests that need the real local candle files are skipped when SMV_CANDLES is not set). No provider request, no database, no outcome statistics."""
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import smart_baseline as sb  # noqa: E402

PRE, PRE_HASH = sb.load_prereg()
MC, EO = sb._cal()
ROOT = HERE.parent
CANDLES = os.environ.get("SMV_CANDLES")
UTC = timezone.utc


def make_series(start=date(2024, 1, 2), end=date(2024, 12, 31), base=100.0, vol=1_000_000.0, drift=0.001):
    ser, d, i = {}, start, 0
    while d <= end:
        if MC.is_trading_day(d):
            c = base * (1 + drift) ** i
            ser[d] = (c, c * 1.01, c * 0.99, c, vol)
            i += 1
        d += timedelta(days=1)
    return ser


# ---------------------------------------------------------------- pre-registration and sealing
def test_prereg_hash_verified_and_tamper_detected(tmp_path):
    assert sb.sha256_file(HERE / "prereg_baseline_v1.json") == PRE_HASH
    bad = tmp_path / "p.json"
    shutil.copy(HERE / "prereg_baseline_v1.json", bad)
    shutil.copy(HERE / "prereg_baseline_v1.json.sha256", str(bad) + ".sha256")
    bad.write_bytes(bad.read_bytes().replace(b'"plus": 0.05', b'"plus": 0.04'))
    with pytest.raises(sb.PreregError):
        sb.load_prereg(bad)


def test_code_constants_match_preregistration():
    c = PRE["costs"]["per_side_half_spread_plus_slippage_bps"]
    assert (sb.side_bps(250e6), sb.side_bps(50e6), sb.side_bps(5e6)) == tuple(c[k] for k in ("adv20>=200e6", "20e6<=adv20<200e6", "adv20<20e6"))
    assert PRE["costs"]["commission_bps_per_side"] == 1 and PRE["costs"]["borrow_bps_for_SELL"] == 1
    assert sb.decide(2, 0, 2, 19_999_999, True, PRE)[1] == "ILLIQUID_OR_UNKNOWN" and sb.decide(2, 0, 2, 20_000_000, True, PRE)[0] == "BUY"
    assert PRE["label"]["flat_band_pct"] == 0.5 and PRE["cutoff"]["availability_buffer_minutes"] == 45
    assert PRE["entry_exit"]["order_deadline_minutes_before_close"] == 10


def _csv(path, rows):
    cols = ["symbol", "earnings_date", "provider_kind", "category", "cutoff_utc", "last_session", "entry_session", "cutoff_flags", "status", "move_pct", "direction"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def test_sealed_splits_refused_and_sealed_rows_never_returned(tmp_path):
    cat = PRE["event_set"]["categories"][0]
    mk = lambda sym, ed: {"symbol": sym, "earnings_date": ed, "provider_kind": "AMC", "category": cat, "cutoff_utc": "2025-01-01 00:00:00", "last_session": "2024-12-31", "entry_session": "2025-01-02",
                          "cutoff_flags": "", "status": "GRADED", "move_pct": "SECRET", "direction": "UP"}
    p = tmp_path / "c.csv"
    _csv(p, [mk("A", "2025-03-03"), mk("B", "2025-11-03"), mk("C", "2026-05-05"), mk("D", "2025-09-30")])
    got = sb.load_events(p, PRE, "dev")
    assert {r["symbol"] for r in got} == {"A", "D"}
    assert all(r["move_pct"] != "SECRET" or r["symbol"] in ("A", "D") for r in got)
    for s in ("validation", "test", "all", None):
        with pytest.raises(sb.SealedDataError):
            sb.load_events(p, PRE, s)


def test_candles_truncated_so_later_prices_cannot_leak(tmp_path):
    rows = [{"date": "2025-09-30", "market_time": "r", "open": "1", "high": "1", "low": "1", "close": "1", "volume": 5},
            {"date": "2025-10-01", "market_time": "r", "open": "1", "high": "1", "low": "1", "close": "2", "volume": 5},
            {"date": "2025-10-02", "market_time": "r", "open": "1", "high": "1", "low": "1", "close": "999", "volume": 5},
            {"date": "2025-10-01", "market_time": "po", "open": "1", "high": "1", "low": "1", "close": "777", "volume": 5}]
    (tmp_path / "X.json").write_text(json.dumps(rows))
    ser = sb.load_series(tmp_path, ["X"], date(2025, 10, 1))
    assert max(ser["X"]) == date(2025, 10, 1) and 999 not in [v[3] for v in ser["X"].values()] and ser["X"][date(2025, 10, 1)][3] == 2


# ---------------------------------------------------------------- features: only data up to the last usable session
def test_features_invariant_to_anything_after_last_session():
    ser = make_series()
    last = date(2024, 9, 13)
    f1, w1, m1 = sb.features(ser, last, set(), ser)
    trunc = {d: v for d, v in ser.items() if d <= last}
    f2, w2, m2 = sb.features(trunc, last, set(), trunc)
    shocked = {d: ((v[0] * 7, v[1] * 7, v[2] * 7, v[3] * 7, v[4] * 7) if d > last else v) for d, v in ser.items()}
    f3, w3, m3 = sb.features(shocked, last, set(), shocked)
    assert f1 == f2 == f3 and m1 == m2 == m3 == last
    assert f1["ret20"] is not None and f1["rel20"] == pytest.approx(0.0)


def test_ret20_known_answer():
    ser = make_series(drift=0.002)
    ds = sorted(ser)
    last = ds[100]
    f, _, _ = sb.features(ser, last, set(), ser)
    assert f["ret20"] == pytest.approx(ser[last][3] / ser[ds[80]][3] - 1)


def test_suspect_jump_in_window_makes_feature_missing_not_repaired():
    ser = make_series()
    ds = sorted(ser)
    last = ds[100]
    inside = {ds[90]}
    f, why, _ = sb.features(ser, last, inside, ser)
    assert f["ret20"] is None and why["ret20"] == "SUSPECT_PRICE_JUMP_IN_WINDOW" and f["rel20"] is None and f["adv20"] is None
    outside = {ds[10]}
    f2, why2, _ = sb.features(ser, last, outside, ser)
    assert f2["ret20"] is not None and not why2


def test_find_suspects_exempts_the_events_own_reaction_session():
    ser = make_series()
    ds = sorted(ser)
    for d in ds[50:]:                                               # a permanent +50 % step on one session
        o, h, l, c, v = ser[d]
        ser[d] = (o * 1.5, h * 1.5, l * 1.5, c * 1.5, v)
    assert find_suspect(ser, set()) == {ds[50]}
    assert find_suspect(ser, {ds[50]}) == set()


def find_suspect(ser, react):
    return sb.find_suspects(ser, react, 25)


def test_prior_mean4_uses_only_reactions_closed_before_cutoff_and_needs_three():
    sym = "Z"
    ev = [(date(2024, 1, 31), date(2024, 1, 30), date(2024, 1, 31), 2.0), (date(2024, 4, 30), date(2024, 4, 29), date(2024, 4, 30), 4.0),
          (date(2024, 7, 31), date(2024, 7, 30), date(2024, 7, 31), 6.0), (date(2024, 10, 30), date(2024, 10, 29), date(2024, 10, 30), 100.0)]
    cutoff = datetime(2024, 10, 29, 10, 0, tzinfo=UTC)               # the Oct-30 reaction has not happened yet
    m, n = sb.prior_mean4(sym, cutoff, {sym: ev}, set(), 45)
    assert n == 3 and m == pytest.approx(0.04)
    m2, n2 = sb.prior_mean4(sym, cutoff, {sym: ev[:2]}, set(), 45)
    assert m2 is None and n2 == 2
    m3, _ = sb.prior_mean4(sym, datetime(2024, 11, 5, tzinfo=UTC), {sym: ev}, set(), 45)
    assert m3 == pytest.approx((4 + 6 + 100 + 2) / 4 / 100)           # the late event counts only once it has closed
    m4, n4 = sb.prior_mean4(sym, cutoff, {sym: ev}, {date(2024, 4, 30)}, 45)  # a suspect jump between base and reaction removes that prior
    assert n4 == 2 and m4 is None
    m5, n5 = sb.prior_mean4(sym, cutoff, {sym: ev}, {date(2024, 4, 15)}, 45)   # a jump outside every base..reaction span removes nothing
    assert n5 == 3


# ---------------------------------------------------------------- rules keep bullish and bearish evidence separate
def test_votes_and_decision_rules():
    f = {"ret20": 0.08, "rel20": 0.05, "mean4": None}
    v, bull, bear = sb.votes(f, PRE)
    assert v == {"trend": 1, "relative_strength": 1, "earnings_history": None} and (bull, bear) == (2, 0)
    assert sb.decide(bull, bear, 2, 50e6, True, PRE) == ("BUY", "RULE")
    assert sb.decide(bull, bear, 2, 50e6, True, PRE, "contrarian")[0] == "SELL"
    f2 = {"ret20": 0.08, "rel20": -0.05, "mean4": 0.02}
    v2, b2, e2 = sb.votes(f2, PRE)
    assert (b2, e2) == (2, 1) and sb.decide(b2, e2, 3, 50e6, True, PRE)[0] == "NO_TRADE"        # conflict never nets out
    assert sb.decide(1, 0, 1, 50e6, True, PRE) == ("NO_TRADE", "INSUFFICIENT_COVERAGE")
    assert sb.decide(0, 3, 3, 50e6, True, PRE)[0] == "SELL" and sb.decide(2, 0, 2, 50e6, False, PRE) == ("NO_TRADE", "NOT_TRADABLE")
    assert sb.votes({"ret20": 0.04, "rel20": 0.02, "mean4": 0.005}, PRE)[0] == {"trend": 0, "relative_strength": 0, "earnings_history": 0}
    assert sb.votes({"ret20": 0.04}, PRE, 0.6)[0]["trend"] == 1                                    # sensitivity scaling only


# ---------------------------------------------------------------- cutoff, calendar, entry
def test_last_usable_session_weekend_holiday_and_dst():
    cut = datetime(2025, 1, 19, 11, 0, tzinfo=UTC)                  # Sunday before MLK day
    assert sb.last_usable_session(cut, 45) == date(2025, 1, 17)
    cut2 = datetime(2025, 1, 21, 11, 0, tzinfo=UTC)                 # Tuesday 06:00 ET after the Monday holiday
    assert sb.last_usable_session(cut2, 45) == date(2025, 1, 17)
    cut3 = datetime(2025, 3, 11, 20, 30, tzinfo=UTC)                # after DST began: close 16:00 EDT = 20:00Z, +45 min > 20:30Z? no, 20:45 > 20:30
    assert sb.last_usable_session(cut3, 45) == date(2025, 3, 10)
    cut4 = datetime(2025, 3, 11, 20, 50, tzinfo=UTC)
    assert sb.last_usable_session(cut4, 45) == date(2025, 3, 11)
    cut5 = datetime(2025, 3, 5, 21, 50, tzinfo=UTC)                 # before DST, EST close 21:00Z
    assert sb.last_usable_session(cut5, 45) == date(2025, 3, 5)


def test_entry_must_be_executable_after_the_cutoff():
    base = date(2025, 1, 24)                                         # Friday close = 21:00Z
    assert not sb.order_deadline_ok(datetime(2025, 1, 26, 11, 0, tzinfo=UTC), base, 10)      # Sunday cutoff: Friday close already passed
    assert not sb.order_deadline_ok(datetime(2025, 1, 24, 20, 55, tzinfo=UTC), base, 10)     # inside the 10-minute MOC window
    assert sb.order_deadline_ok(datetime(2025, 1, 24, 11, 0, tzinfo=UTC), base, 10)
    assert not sb.order_deadline_ok(datetime(2025, 1, 1, 0, 0, tzinfo=UTC), date(2025, 1, 25), 10)   # a Saturday is not a session


# ---------------------------------------------------------------- costs and statistics
def test_costs_and_flat_outcomes_stay_in_denominators():
    assert sb.net_return(1, 1.0, 250e6) == pytest.approx(1.0 - 0.08)
    assert sb.net_return(-1, -1.0, 250e6) == pytest.approx(1.0 - 0.09)
    assert sb.net_return(1, 1.0, 5e6, mult=2) == pytest.approx(1.0 - 2 * 0.42)
    calls = [{"sig": "BUY", "move": 1.0, "spy_move": 0.0, "adv": 50e6, "week": "w1", "outcome": "UP"}, {"sig": "BUY", "move": 0.1, "spy_move": 0.0, "adv": 50e6, "week": "w1", "outcome": "FLAT"},
             {"sig": "SELL", "move": -2.0, "spy_move": 0.0, "adv": 50e6, "week": "w2", "outcome": "DOWN"}, {"sig": "NO_TRADE", "move": 3.0, "spy_move": 0.0, "adv": 50e6, "week": "w2", "outcome": "UP"}]
    s = sb.summarise(calls, PRE, "t")
    assert s["decided"] == 3 and s["strict_hit_rate"] == pytest.approx(2 / 3, abs=1e-3) and s["direction_only_n"] == 2 and s["direction_only_hit_rate"] == 1.0
    assert s["confusion"]["BUY"] == {"UP": 1, "FLAT": 1, "DOWN": 0} and s["confusion"]["NO_TRADE"]["UP"] == 1 and s["coverage"] == 0.75 and s["abstention_rate"] == 0.25


def test_second_run_is_refused(tmp_path):
    (tmp_path / "dev_run_record.json").write_text("{}")
    r = subprocess.run([sys.executable, str(HERE / "run_dev.py"), "--candles-dir", str(tmp_path), "--out-dir", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode != 0 and "already been made" in (r.stdout + r.stderr)


# ---------------------------------------------------------------- real local data (development period only)
needs_data = pytest.mark.skipif(not CANDLES or not Path(CANDLES).exists(), reason="SMV_CANDLES not set")


@needs_data
def test_real_dev_events_features_cutoffs_and_entries():
    ev = sb.load_events(ROOT / "h1b/baseline/cutoffs.csv", PRE, "dev")
    assert len(ev) >= 150 and all(e["earnings_date"] <= PRE["splits"]["dev"][1] for e in ev)
    syms = sorted({e["symbol"] for e in ev} | {"SPY"})
    assert sb.verify_candle_index(CANDLES, ROOT / "h1b/candles_raw_index.json", syms) == []
    until = date.fromisoformat(PRE["sealing"]["candle_truncation_date"][:10])
    series = sb.load_series(CANDLES, syms, until)
    assert all(max(s) <= until for s in series.values())
    sus_spy = sb.find_suspects(series["SPY"], set(), 25)
    bad_cut = bad_src = bad_entry = bad_inv = 0
    for e in ev:
        sym, cutoff = e["symbol"], sb.utc_dt(e["cutoff_utc"])
        last = sb.last_usable_session(cutoff, 45)
        assert last.isoformat() == e["last_session"]
        if not MC.session_close_utc(last) + timedelta(minutes=45) <= cutoff:
            bad_cut += 1
        mv, base, react = sb.reaction_return(series[sym], date.fromisoformat(e["earnings_date"]), e["provider_kind"])
        assert mv is not None and abs(mv - float(e["move_pct"])) <= 0.005
        if not (cutoff < MC.session_close_utc(base) and base > last):
            bad_entry += 1
        f, why, maxsrc = sb.features(series[sym], last, set(), series["SPY"], sus_spy)
        if maxsrc is not None and maxsrc > last:
            bad_src += 1
        trunc = {d: v for d, v in series[sym].items() if d <= last}
        spy_t = {d: v for d, v in series["SPY"].items() if d <= last}
        f2, _, _ = sb.features(trunc, last, set(), spy_t, sus_spy)
        if f != f2:
            bad_inv += 1
    assert (bad_cut, bad_src, bad_entry, bad_inv) == (0, 0, 0, 0)
