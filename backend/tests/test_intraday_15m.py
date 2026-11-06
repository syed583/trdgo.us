"""15-minute candle confirmation, tiered live scanning and the live engine's hard budget limit.

Everything is mocked: no API calls, no production database. The confirmation never changes a score; it
only decides whether a BUY / SELL that passed every other gate may be shown as actionable."""
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import intraday_15m as c15
import intraday_safety as safety
import intraday_tiers as tiers
import swr
import tab_settings as cfg
import uw_budget as bud
import uw_priority as pri

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 22, 11, 7, tzinfo=ET)          # Thursday; the 11:00 candle is still forming
FRESH = 30.0


# ------------------------------------------------------------------ candles ----
def _bar(start_et: datetime, close: float, vol: float = 1_000_000.0, spread: float = 0.2) -> dict:
    o = close - 0.05
    return {"start_time": start_et.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "open": o, "high": max(o, close) + spread, "low": min(o, close) - spread,
            "close": close, "volume": vol}


def _day_starts(day: datetime, until: datetime = None):
    t = day.replace(hour=9, minute=30, second=0, microsecond=0)
    out = []
    while t.hour < 16:
        if until is not None and t >= until:
            break
        out.append(t)
        t += timedelta(minutes=15)
    return out


def candles(direction: float = 1.0, now: datetime = NOW, include_forming: bool = True) -> list:
    """Three sessions of 15-minute candles trending ``direction`` (+1 up, -1 down) up to ``now``."""
    rows, px = [], 100.0
    for back in (2, 1, 0):
        day = (now - timedelta(days=back))
        for t in _day_starts(day, until=now + timedelta(minutes=15 if include_forming else 0)):
            px += 0.15 * direction
            rows.append(_bar(t, px))
    return rows


# ---------------------------------------------------------------- validation ---
def test_valid_candles_are_closed_aligned_and_regular_hours_only():
    rows = candles(+1)
    v = c15.validate_bars(rows, NOW)
    assert v["forming"] is True and not v["rejected"]
    assert v["bars"][-1]["dt"] == datetime(2026, 10, 22, 10, 45, tzinfo=ET)       # 11:00 candle still forming
    assert all(b["dt"].minute % 15 == 0 for b in v["bars"])


@pytest.mark.parametrize("mutate, reason", [
    (lambda r: r.update(high=r["low"] - 1), "inconsistent OHLC"),
    (lambda r: r.update(close=None), "missing or non-positive price"),
    (lambda r: r.update(close=-3.0), "missing or non-positive price"),
    (lambda r: r.update(volume=None), "missing volume"),
    (lambda r: r.update(volume=-5), "missing volume"),
    (lambda r: r.update(start_time="garbage"), "no start time"),
    (lambda r: r.update(close=float("nan")), "missing or non-positive price"),
])
def test_a_corrupt_candle_is_dropped_and_counted_never_repaired(mutate, reason):
    rows = candles(+1)
    n = len(c15.validate_bars(rows, NOW)["bars"])
    mutate(rows[40])
    v = c15.validate_bars(rows, NOW)
    assert len(v["bars"]) == n - 1 and v["rejected"] == {reason: 1}


def test_misaligned_duplicate_and_extended_hours_candles_are_rejected():
    rows = candles(+1)
    rows.append(dict(rows[10]))                                                    # duplicate start
    rows.append(_bar(datetime(2026, 10, 22, 10, 37, tzinfo=ET), 101.0))            # not on a 15-min boundary
    rows.append(_bar(datetime(2026, 10, 22, 8, 0, tzinfo=ET), 101.0))              # pre-market
    rows.append(_bar(datetime(2026, 10, 24, 11, 0, tzinfo=ET), 101.0))             # Saturday
    v = c15.validate_bars(rows, NOW)
    assert v["rejected"] == {"duplicate": 1, "not aligned to 15 minutes": 1, "outside regular hours": 2}


# ------------------------------------------------------------------ analysis ---
def test_a_rising_tape_reads_bullish_and_a_falling_tape_bearish():
    up = c15.analyse(candles(+1), NOW)
    down = c15.analyse(candles(-1), NOW)
    assert up["status"] == "OK" and up["bias"] == +1 and up["votes"] == {"vwap": 1, "ema": 1, "rsi": 1}
    assert down["status"] == "OK" and down["bias"] == -1 and down["votes"] == {"vwap": -1, "ema": -1, "rsi": -1}
    assert up["timeframe"] == down["timeframe"] == "15m"


def test_only_closed_candles_are_analysed_the_forming_one_is_ignored():
    a = c15.analyse(candles(+1), NOW)
    b = c15.analyse(candles(+1, include_forming=False), NOW)
    assert a["close"] == b["close"] and a["last_bar_start"] == b["last_bar_start"]


def _stubbed(monkeypatch, vwap_sign, ema_sign, rsi_value):
    """Force the three votes: price vs VWAP via the candles, EMA and RSI via stubs."""
    monkeypatch.setattr(c15, "_ema", lambda vals, span: (10.0 + ema_sign) if span == 9 else 10.0)
    monkeypatch.setattr(c15, "_rsi", lambda closes, period: rsi_value)
    return c15.analyse(candles(vwap_sign), NOW)


@pytest.mark.parametrize("vw, em, rsi, bias", [
    (+1, +1, 50.0, +1),      # two bullish, one neutral
    (-1, -1, 50.0, -1),      # mirror image
    (+1, -1, 50.0, 0),       # split -> no direction
    (+1, +1, 40.0, 0),       # 2 bull vs 1 bear = +1 only -> mixed
    (-1, -1, 60.0, 0),
    (+1, +1, 60.0, +1),
    (-1, -1, 40.0, -1),
])
def test_two_agreeing_votes_with_none_against_decide_a_direction(monkeypatch, vw, em, rsi, bias):
    assert _stubbed(monkeypatch, vw, em, rsi)["bias"] == bias


def test_the_first_candle_of_the_session_is_warmup_not_a_guess():
    now = datetime(2026, 10, 22, 9, 40, tzinfo=ET)                                  # 09:30 candle not closed yet
    a = c15.analyse(candles(+1, now=now), now)
    assert a["status"] == "WARMUP" and "09:45" in a["detail"]


def test_a_hole_in_todays_newest_candles_invalidates_the_reading():
    rows = candles(+1)
    gone = datetime(2026, 10, 22, 10, 15, tzinfo=ET).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    rows = [r for r in rows if r["start_time"] != gone]
    a = c15.analyse(rows, NOW)
    assert a["status"] == "INVALID" and "missing" in a["detail"]


def test_too_little_history_is_invalid_not_neutral():
    rows = [r for r in candles(+1) if r["start_time"].startswith("2026-10-22")]
    a = c15.analyse(rows, NOW)
    assert a["status"] == "INVALID" and "valid candles" in a["detail"]


# ------------------------------------------------------------- confirmation ----
def seed(symbol="AAA", bias=+1, end_ago=300.0, status="OK", fetched_ago=60.0, detail="15m reading"):
    t = time.time()
    swr._values[c15.KEY.format(symbol)] = (t, {
        "symbol": symbol, "status": status, "bias": bias, "votes": {"vwap": bias, "ema": bias, "rsi": bias},
        "bars": 60, "rejected": {}, "detail": detail, "ts": t - fetched_ago,
        "last_bar_end_epoch": t - end_ago})


def test_confirmation_requires_a_fresh_valid_agreeing_reading():
    seed(bias=+1)
    assert c15.confirmation("AAA", "BUY")["status"] == "CONFIRMED"
    assert c15.confirmation("AAA", "BUY")["confirmed"] is True
    assert c15.confirmation("AAA", "SELL")["status"] == "CONFLICT"
    seed(bias=-1)
    assert c15.confirmation("AAA", "SELL")["confirmed"] is True
    assert c15.confirmation("AAA", "BUY")["status"] == "CONFLICT"
    seed(bias=0)
    for side in ("BUY", "SELL"):
        assert c15.confirmation("AAA", side)["status"] == "MIXED"


@pytest.mark.parametrize("kw, status", [
    (dict(end_ago=1300.0), "STALE"),                 # newest closed candle ended > 20 min ago
    (dict(status="WARMUP"), "WARMUP"),
    (dict(status="INVALID"), "INVALID"),
])
def test_stale_or_unusable_readings_never_confirm(kw, status):
    seed(**kw)
    for side in ("BUY", "SELL"):
        out = c15.confirmation("AAA", side)
        assert out["status"] == status and out["confirmed"] is False


def test_no_reading_is_missing_and_outside_the_session_it_does_not_apply():
    assert c15.confirmation("ZZZ", "BUY")["status"] == "MISSING"
    seed()
    out = c15.confirmation("AAA", "BUY", phase="post")
    assert out["applies"] is False and out["confirmed"] is None


# ------------------------------------------------------------------ the gate ---
def row(signal="BUY", score=72.0, symbol="AAA"):
    return {"symbol": symbol, "signal": signal, "score": score, "coverage": 100.0, "now": 100.0,
            "entry": 100.0, "tp": 102.0, "sl": 99.0, "params": [{"name": "a", "label": "Flow", "bias": 1.0}]}


@pytest.mark.parametrize("signal, bias, ok", [
    ("BUY", +1, True), ("SELL", -1, True),
    ("BUY", -1, False), ("SELL", +1, False),
    ("BUY", 0, False), ("SELL", 0, False),
])
def test_a_buy_or_sell_stands_only_when_the_15_minute_candles_agree(signal, bias, ok):
    seed(bias=bias)
    r = safety.gate(row(signal, 72 if signal == "BUY" else 28), phase="rth", signals_age_s=FRESH, price_age=FRESH)
    assert (r["signal"] == signal) is ok
    if ok:
        assert r["confirm15"]["confirmed"] is True and r["health"]["confirm15"]["status"] == "CONFIRMED"
        assert safety.tradable(r)
    else:
        assert r["signal"] == "WAIT" and r["wait"] is True and r["score"] is None
        assert any("15-minute candles do not confirm" in w for w in r["wait_reasons"])
        assert r["signal_raw"] == signal and not safety.tradable(r)             # kept for exits only


def test_missing_or_stale_15_minute_data_turns_a_signal_into_wait():
    for side in ("BUY", "SELL"):
        r = safety.gate(row(side), phase="rth", signals_age_s=FRESH, price_age=FRESH)         # nothing seeded
        assert r["signal"] == "WAIT" and r["confirm15"]["status"] == "MISSING"
    seed(bias=+1, end_ago=2000.0)
    assert safety.gate(row("BUY"), phase="rth", signals_age_s=FRESH, price_age=FRESH)["signal"] == "WAIT"


def test_the_confirmation_never_changes_a_score_and_never_rescues_stale_data():
    seed(bias=+1)
    ok = safety.gate(row("BUY", 72.0), phase="rth", signals_age_s=FRESH, price_age=FRESH)
    assert ok["score"] == 72.0                                                   # untouched
    stale = safety.gate(row("BUY", 72.0), phase="rth", signals_age_s=99 * 3600.0, price_age=FRESH)
    assert stale["signal"] == "WAIT"                                             # a confirming 15m candle does not rescue it


def test_neutral_rows_are_left_alone_and_nothing_can_enter_outside_the_regular_session():
    assert safety.gate(row("NEUTRAL", 50), phase="rth", signals_age_s=FRESH, price_age=FRESH)["confirm15"] is None
    seed(bias=+1)                                                                # even a perfect reading...
    for phase in ("pre", "post", "closed"):
        r = safety.gate(row("BUY"), phase=phase, signals_age_s=FRESH, price_age=FRESH)
        assert r["signal"] == "WAIT" and not safety.tradable(r), phase           # ...cannot open an entry after hours
        assert any("regular session" in w for w in r["wait_reasons"])


def test_there_is_no_setting_that_switches_the_confirmation_off(monkeypatch):
    assert "require_confirmation" not in cfg.INTRADAY_15M
    monkeypatch.setitem(cfg.INTRADAY_15M, "require_confirmation", False)        # an attempt to configure around it
    monkeypatch.setenv("ALLOW_UNCONFIRMED_ENTRIES", "I_UNDERSTAND")
    seed(bias=-1)
    r = safety.gate(row("BUY"), phase="rth", signals_age_s=FRESH, price_age=FRESH)
    assert r["signal"] == "WAIT" and r["confirm15"]["status"] == "CONFLICT"      # still blocked
    assert c15.entry_check("AAA", "BUY", "rth")["allowed"] is False


# ------------------------------------------------------------ the live engine --
class _Rec:
    def __init__(self):
        self.sql = []

    def execute(self, stmt, params=None):
        self.sql.append(str(stmt))
        return self


@pytest.mark.parametrize("side, bias, starts", [("BUY", +1, True), ("SELL", -1, True),
                                                ("BUY", -1, False), ("SELL", +1, False), ("BUY", 0, False)])
def test_the_live_engine_starts_confirming_only_with_a_15_minute_agreement(monkeypatch, side, bias, starts):
    import intraday_signal_v2 as eng
    seed(bias=bias)
    monkeypatch.setattr(eng, "_pending", lambda *a, **k: None)
    sc = {"call": side, "side": side, "score": 70.0 if side == "BUY" else 30.0, "spot": 100.0, "atr": 2.0,
          "status": "OK"}
    rec = _Rec()
    eng._maybe_open(rec, "AAA", "2026-10-22", sc, datetime(2026, 10, 22, 11, 0, tzinfo=ET))
    assert any("INSERT INTO intraday_sig_v2_pending" in q for q in rec.sql) is starts


# -------------------------------------------------------------------- refresh --
def test_due_only_after_a_newer_candle_has_closed():
    now = time.time()
    assert c15.due(None, now)
    fresh = {"ts": now - 120, "last_bar_end_epoch": now - 300}                       # next close in ~10 min
    assert not c15.due(fresh, now)
    rolled = {"ts": now - 120, "last_bar_end_epoch": now - 15 * 60 - 40}             # a candle closed 40 s ago
    assert c15.due(rolled, now)
    assert not c15.due(dict(rolled, ts=now - 20), now)                               # never twice within a minute
    assert c15.due({"ts": now - 120, "status": "INVALID"}, now)                      # unusable last time: retry


def test_cycle_refreshes_due_names_strongest_first_inside_the_budget(monkeypatch):
    bud.reset_for_tests(bud.Controller(persist=False))
    swr._values["intrafeed:interest"] = (time.time(), {"AAA": 3.0, "BBB": 30.0, "CCC": 12.0})
    got = []

    def fetch(s):
        got.append(s)
        return {"ts": time.time(), "status": "OK"}

    out = c15.cycle(["AAA", "BBB", "CCC"], "rth", max_symbols=2, fetch=fetch)
    assert got == ["BBB", "CCC"] and out["due"] == 3 and out["refreshed"] == ["BBB", "CCC"]
    assert c15.cycle(["AAA"], "post", fetch=fetch)["refreshed"] == []                 # regular session only


def test_cycle_stops_at_its_hourly_cap_and_when_the_budget_says_no(monkeypatch):
    ctl = bud.reset_for_tests(bud.Controller(persist=False))
    monkeypatch.setitem(cfg.UW_BUDGET, "candles15_hourly_cap", 2)
    for _ in range(2):
        ctl.record_call(pri.ESSENTIAL, c15.MODULE, "ok")
    out = c15.cycle(["AAA", "BBB"], "rth", fetch=lambda s: {"ts": time.time()})
    assert out["refreshed"] == [] and out["skipped"][0][1] == "CANDLES15_HOURLY_CAP"
    monkeypatch.setitem(cfg.UW_BUDGET, "candles15_hourly_cap", 99)
    bud.reset_for_tests(bud.Controller(settings=dict(cfg.UW_BUDGET, essential_budget=0), persist=False))
    out = c15.cycle(["AAA"], "rth", fetch=lambda s: {"ts": time.time()})
    assert out["refreshed"] == [] and out["skipped"][0][1] == "ESSENTIAL_BUDGET"


def test_a_failed_fetch_keeps_the_old_reading_and_is_reported(monkeypatch):
    bud.reset_for_tests(bud.Controller(persist=False))
    seed(bias=+1, fetched_ago=3000.0, end_ago=3000.0)
    out = c15.cycle(["AAA"], "rth", fetch=lambda s: None)
    assert out["failed"] == ["AAA"] and c15.read("AAA")["bias"] == +1
    assert c15.confirmation("AAA", "BUY")["status"] == "STALE"                          # old and too old: never confirms


# ------------------------------------------------- the live engine's hard limit --
def _ctl(**over):
    clock = [1_760_000_000.0]
    c = bud.Controller(settings=dict(cfg.UW_BUDGET, **over), now=lambda: clock[0], persist=False)
    return c, clock


def test_the_live_engine_has_a_hard_daily_limit():
    c, _ = _ctl(live_hard_cap=5, live_hourly_cap=999)
    for _i in range(5):
        assert c.admit(pri.LIVE, "intraday_engine").ok
        c.record_call(pri.LIVE, "intraday_engine", "ok")
    d = c.admit(pri.LIVE, "intraday_engine")
    assert not d.ok and d.code == "LIVE_HARD_CAP"
    assert c.capacity(pri.LIVE) == {"ok": False, "code": "LIVE_HARD_CAP", "reason": d.reason}
    assert c.capacity(pri.ESSENTIAL)["ok"]                                  # others are separate pools


def test_the_live_engine_has_an_hourly_limit_that_resets_with_the_hour():
    c, clock = _ctl(live_hard_cap=999, live_hourly_cap=3)
    for _i in range(3):
        c.record_call(pri.LIVE, "intraday_engine", "ok")
    assert c.admit(pri.LIVE, "intraday_engine").code == "LIVE_HOURLY_CAP"
    clock[0] += 3700
    assert c.admit(pri.LIVE, "intraday_engine").ok


def test_the_default_limits_fit_inside_the_plan_allowance():
    b = cfg.UW_BUDGET
    # live (its share + the reserve) + essential + dashboards + warmer can never add up to more than the day
    assert (b["live_hard_cap"] + b["essential_budget"] + b["other_budget"] + b["warmer_budget"]) <= b["daily_limit"]
    assert b["live_hard_cap"] == (b["intraday_budget"] - b["essential_budget"]) + b["emergency_reserve"]


def test_an_exhausted_live_engine_makes_the_boards_say_wait_api_unavailable():
    c, _ = _ctl(live_hard_cap=1, live_hourly_cap=999)
    bud.reset_for_tests(c)
    c.record_call(pri.LIVE, "intraday_engine", "ok")
    seed(bias=+1)
    for side in ("BUY", "SELL"):
        r = safety.gate(row(side), phase="rth", signals_age_s=FRESH, price_age=FRESH, model_status="OK",
                        inputs_label="live 5-minute engine score")
        assert r["signal"] == "WAIT" and not safety.tradable(r)
        assert any(w.startswith("API UNAVAILABLE") for w in r["wait_reasons"])
        assert r["health"]["api_unavailable"] is True and r["health"]["api_capacity"] == "LIVE_HARD_CAP"
        assert r["health"]["status"] == "STALE"


def test_the_live_engine_refuses_new_entries_while_its_capacity_is_exhausted(monkeypatch):
    import intraday_signal_v2 as eng
    c, _ = _ctl(live_hard_cap=1, live_hourly_cap=999)
    bud.reset_for_tests(c)
    c.record_call(pri.LIVE, "intraday_engine", "ok")
    reason = safety.api_capacity(live_path=True)
    assert reason["ok"] is False
    # the live client itself refuses too, so the engine's scores go WITHHELD / stale rather than invented
    import unusualwhales_service as uw
    monkeypatch.setattr(uw, "configured", lambda: True)
    with pri.use(pri.LIVE, "intraday_engine"):
        out = uw.get("/api/stock/AAA/ohlc/5m")
    assert out["status"] == "BUDGET_EXHAUSTED" and out["code"] == "LIVE_HARD_CAP"


def test_an_exhausted_essential_pool_only_explains_why_the_inputs_cannot_refresh():
    c, _ = _ctl(essential_budget=0)
    bud.reset_for_tests(c)
    assert safety.api_capacity(live_path=False)["ok"] is False
    e = {"signals": {}, "meta": {}, "dropped": [], "missing_critical": ["options flow"], "phase": "rth"}
    r = safety.gate(row("BUY"), phase="rth", signals_age_s=FRESH, price_age=FRESH, meta=e)
    assert r["signal"] == "WAIT"
    assert any("shared feed cannot refresh" in w for w in r["wait_reasons"])
    # fresh data is not blocked just because the pool is empty: cached data is still judged by its age
    fresh = safety.gate(row("BUY"), phase="rth", signals_age_s=FRESH, price_age=FRESH,
                        meta={"signals": {}, "meta": {}, "dropped": [], "missing_critical": [], "phase": "rth"})
    seed(bias=+1)
    fresh = safety.gate(row("BUY"), phase="rth", signals_age_s=FRESH, price_age=FRESH,
                        meta={"signals": {}, "meta": {}, "dropped": [], "missing_critical": [], "phase": "rth"})
    assert fresh["signal"] == "BUY"


def test_the_stale_signal_age_setting_that_nothing_used_is_gone():
    assert "max_signal_age_s" not in cfg.INTRADAY_SAFETY and not hasattr(safety, "max_signal_age")


# ----------------------------------------------------------------- tiers -------
NAMES = [f"S{i:02d}" for i in range(38)]


def test_the_plan_never_exceeds_the_hourly_budget_and_keeps_every_name():
    for budget in (0, 100, 400, 800, 1500, 5000):
        p = tiers.plan(NAMES, set(), budget, 6.0)
        assert sorted(p["hot"] + p["warm"] + p["cold"]) == sorted(NAMES)            # all 38 stay on the board
        if p["fits"]:
            assert p["cost_per_hour"] <= budget * cfg.INTRADAY_TIERS["budget_safety"] + 1e-6


def test_more_budget_means_more_reliably_fresh_names_and_the_strongest_come_first():
    small = tiers.plan(NAMES, set(), 300, 3.5)
    big = tiers.plan(NAMES, set(), 2000, 3.5)
    assert big["reliable"] > small["reliable"]
    assert small["warm"] == NAMES[:len(small["warm"])]                              # ranked order is respected
    assert big["reliable"] == 38 and big["cold"] == []                              # roomy: every name is reliable


def test_open_positions_and_pending_confirmations_are_always_hot():
    p = tiers.plan(NAMES, {"S30", "S35"}, 200, 6.0)
    assert {"S30", "S35"} <= set(p["hot"])
    broke = tiers.plan(NAMES, {"S30", "S35"}, 0, 6.0)
    assert {"S30", "S35"} <= set(broke["hot"]) and broke["fits"] is False           # still watched; the hard cap protects


def test_ranking_puts_the_strongest_leaners_first_and_unseen_names_ahead_of_them():
    ranked = tiers.rank(["A", "B", "C", "D"], {"A": 52.0, "B": 80.0, "C": 20.0})
    assert ranked == ["D", "B", "C", "A"]                                           # D unscored; B,C lean 30; A leans 2


def test_hourly_budget_spreads_what_is_left_of_the_hard_cap_over_the_session():
    c, _ = _ctl(live_hard_cap=1000, live_hourly_cap=400)
    snap = c.snapshot()
    assert tiers.hourly_budget(snap, 5.0) == 200.0                                  # 1000 over 5 hours
    assert tiers.hourly_budget(snap, 1.0) == 400.0                                  # never above the hourly cap
    for _i in range(900):
        c.record_call(pri.LIVE, "intraday_engine", "ok")
    assert tiers.hourly_budget(c.snapshot(), 2.0) <= 50.0                           # 100 left over 2 hours


def test_scan_once_rescores_only_the_due_names_and_still_lists_every_name(monkeypatch):
    import intraday_signal_v2 as eng
    scored = []
    monkeypatch.setattr(eng, "universe", lambda: ["AAA", "BBB", "CCC"])
    monkeypatch.setattr(eng, "_active_symbols", lambda sess: set())
    monkeypatch.setattr(eng.im, "bars_meta", lambda s, critical=False: ([], False))
    monkeypatch.setattr(eng.im, "score", lambda s: scored.append(s) or {"score": 55.0, "status": "OK"})
    monkeypatch.setattr(eng, "step", lambda s, sc, now: None)
    monkeypatch.setattr(eng, "_compose", lambda rows, now, spy: {"rows": rows})
    bud.reset_for_tests(bud.Controller(persist=False))
    eng._last_scored.clear()
    out = eng.scan_once(datetime(2026, 10, 22, 11, 0, tzinfo=ET))
    assert sorted(scored) == ["AAA", "BBB", "CCC"] and len(out["rows"]) == 3          # first look at everything
    scored.clear()
    out = eng.scan_once(datetime(2026, 10, 22, 11, 0, tzinfo=ET))
    assert scored == [] and len(out["rows"]) == 3                                     # nothing due; all still listed
    assert all(r["rescored"] is False for r in out["rows"])
    assert all(r["status"] == "STALE_SCORE" and r["call"] is None for r in out["rows"])   # nothing held: no candidate
    eng._last_scored["BBB"] -= 10_000                                                 # one name has waited long enough
    eng.scan_once(datetime(2026, 10, 22, 11, 0, tzinfo=ET))
    assert scored == ["BBB"]


def test_a_tight_budget_slows_the_cold_names_down_before_it_drops_any_warm_ones():
    p = tiers.plan(NAMES, set(), 400, 3.5)
    roomy = tiers.plan(NAMES, set(), 4000, 3.5)
    assert roomy["periods_s"]["cold"] == cfg.INTRADAY_TIERS["cold_period_s"]
    assert p["periods_s"]["cold"] in (cfg.INTRADAY_TIERS["cold_period_s"], cfg.INTRADAY_TIERS["cold_max_period_s"])
    assert p["reliable"] >= tiers.plan(NAMES, set(), 400, 3.5)["reliable"] > 0
