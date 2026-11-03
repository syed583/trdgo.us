"""Calendar validation and versioning, duplicate/moved events, deterministic selection and scheduling, request governor (budget, priority, hourly shared cap)."""
import random
from datetime import date, datetime, time, timedelta, timezone

from smvshadow import calendar_validation as cv
from smvshadow import cutoff as co
from smvshadow import governor as gv
from smvshadow import selection as sel

UTC, ET = timezone.utc, co.ET
NOW = datetime(2026, 10, 30, 14, 0, tzinfo=UTC)
TODAY = date(2026, 10, 30)


def ob(source, d, session=None, status=None, age_h=1.0):
    return cv.Observation(source, "ABC", d, session, status, NOW - timedelta(hours=age_h))


# ------------------------------------------------------------------ calendar validation
def test_date_status_rules():
    d = date(2026, 11, 3)
    assert cv.classify_date([ob("UW_ROW", d, status="company"), ob("FINVIZ", d)], NOW, TODAY)[0] == cv.CONFIRMED
    assert cv.classify_date([ob("UW_ROW", d, status="estimation"), ob("FINVIZ", d)], NOW, TODAY)[0] == cv.ESTIMATED          # not company-announced
    assert cv.classify_date([ob("UW_ROW", d, status="company")], NOW, TODAY)[0] == cv.ESTIMATED                              # a single source never confirms
    assert cv.classify_date([ob("UW_ROW", d, status="company", age_h=30), ob("FINVIZ", d, age_h=30)], NOW, TODAY)[0] == cv.STALE     # 4 days out: limit 6 h
    far = date(2026, 11, 20)
    assert cv.classify_date([ob("UW_ROW", far, age_h=30)], NOW, TODAY)[0] == cv.STALE                                         # beyond D-10 the limit is 24 h
    assert cv.classify_date([ob("UW_ROW", far, age_h=10)], NOW, TODAY)[0] == cv.ESTIMATED
    st, dd, fl = cv.classify_date([ob("UW_ROW", d), ob("FINVIZ", d + timedelta(days=3))], NOW, TODAY)
    assert st == cv.UNKNOWN and dd is None and "DATE_CONFLICT_OVER_1_DAY" in fl
    st, dd, fl = cv.classify_date([ob("UW_ROW", d, status="company"), ob("FINVIZ", d + timedelta(days=1))], NOW, TODAY)
    assert st == cv.ESTIMATED and "DATE_DISAGREEMENT_1_DAY" in fl                                                            # never CONFIRMED with a disagreement
    assert cv.classify_date([], NOW, TODAY)[0] == cv.UNKNOWN


def test_timing_status_rules():
    d = date(2026, 11, 3)
    assert cv.classify_timing([ob("UW_ROW", d, "premarket"), ob("FINVIZ", d, "7:00 AM")], d) == (cv.T_CONFIRMED, "BMO")
    assert cv.classify_timing([ob("UW_ROW", d, "postmarket"), ob("FINVIZ", d, "4:30 PM")], d) == (cv.T_CONFIRMED, "AMC")
    assert cv.classify_timing([ob("UW_ROW", d, "postmarket")], d) == (cv.T_SINGLE, "AMC")
    assert cv.classify_timing([ob("UW_ROW", d, "premarket"), ob("FINVIZ", d, "4:30 PM")], d) == (cv.T_CONFLICT, "UNKNOWN")
    assert cv.classify_timing([ob("FINVIZ", d, "11:00 AM")], d) == (cv.T_UNKNOWN, "UNKNOWN")                                  # inside trading hours: not guessed
    assert cv.classify_timing([ob("UW_ROW", d, "unknown")], d) == (cv.T_UNKNOWN, "UNKNOWN")
    assert cv.classify_timing([], d) == (cv.T_UNKNOWN, "UNKNOWN") and cv.classify_timing([ob("UW_ROW", d, "premarket")], None)[0] == cv.T_UNKNOWN


def test_registry_versions_moves_and_idempotency():
    reg = cv.EventRegistry()
    d1, d2 = date(2026, 11, 3), date(2026, 11, 4)
    v1 = reg.observe("ABC", [ob("UW_ROW", d1, "postmarket", "estimation")], NOW, TODAY)
    assert v1.version == 1 and v1.reason == "NEW" and v1.kind == "AMC"
    assert reg.observe("ABC", [ob("UW_ROW", d1, "postmarket", "estimation")], NOW, TODAY) is v1 and len(reg.versions) == 1          # same facts: no new version
    v2 = reg.observe("ABC", [ob("UW_ROW", d2, "postmarket", "company"), ob("FINVIZ", d2, "4:30 PM")], NOW + timedelta(hours=6), TODAY)
    assert (v2.version, v2.reason, v2.supersedes, v2.date_status) == (2, "MOVED", 1, cv.CONFIRMED) and reg.versions[0].event_date == d1     # the old version is untouched
    v3 = reg.observe("ABC", [ob("UW_ROW", date(2027, 2, 3), "postmarket", "estimation")], NOW + timedelta(hours=12), TODAY)
    assert v3.reason == "NEW_EVENT_SAME_SYMBOL" and v3.version == 3                                                                    # 91 days later: a different event
    v4 = reg.observe("ABC", [ob("UW_ROW", date(2027, 2, 3), "postmarket", "company"), ob("FINVIZ", date(2027, 2, 3), "4:15 PM")], NOW + timedelta(hours=13), TODAY)
    assert v4.reason == "STATUS_CHANGE" and v4.event_date == v3.event_date


def test_duplicate_dates_are_an_uncertain_pair_not_merged_not_scheduled():
    reg = cv.EventRegistry()
    pairs = reg.register_concurrent_dates("TRV", {date(2026, 10, 15): [], date(2026, 10, 16): []}, NOW, TODAY)
    assert len(pairs) == 1 and pairs[0]["decision"] == "KEPT_SEPARATE" and pairs[0]["code"] == "UNCERTAIN_CONCURRENT_OBSERVATIONS"
    assert [v.date_status for v in reg.versions] == [cv.UNKNOWN, cv.UNKNOWN] and all("UNCERTAIN_PAIR" in v.flags for v in reg.versions)
    c = [sel.Candidate("TRV", v.event_date, v.version, "UNKNOWN", v.date_status, v.timing_status, NOW, NOW + timedelta(days=1)) for v in reg.versions]
    plan = sel.schedule(c, seed="s")
    assert plan["assignments"] == [] and [x["type"] for x in plan["ledger"]] == ["SKIPPED_UNCERTAIN_OR_UNKNOWN_DATE"] * 2


# ------------------------------------------------------------------ selection and scheduling
def cand(sym, d, kind="AMC", ds=cv.CONFIRMED, ts=cv.T_CONFIRMED, liquid=None):
    p = co.plan_event(d, kind)
    return sel.Candidate(sym, d, 1, kind, ds, ts, p["window_start_utc"], p["deadline_utc"], liquid, p["evidence_only"])


def test_plan_is_deterministic_and_order_independent():
    cs = [cand("S%03d" % i, date(2026, 11, 4), "BMO" if i % 2 else "AMC") for i in range(60)]
    a = sel.schedule(cs, seed="seed-1", core_cap=25)
    shuffled = cs[:]
    random.Random(7).shuffle(shuffled)
    b = sel.schedule(shuffled, seed="seed-1", core_cap=25)
    assert a["plan_hash"] == b["plan_hash"] and a["assignments"] == b["assignments"]
    assert sel.schedule(cs, seed="seed-2", core_cap=25)["plan_hash"] != a["plan_hash"]                       # the seed decides the tie-break and is part of the record


def test_two_day_windows_use_spare_capacity_and_overflow_is_logged_not_dropped():
    cs = [cand("S%03d" % i, date(2026, 11, 4), "AMC") for i in range(10)]                                    # window Nov 2 16:00 .. Nov 3 16:00 ET: touches two ET days
    ok = sel.schedule(cs[:6], seed="s", core_cap=4)
    assert len(ok["assignments"]) == 6 and not [x for x in ok["ledger"] if x["type"] == "MISSED_CAPACITY"] and set(a["capture_day"] for a in ok["assignments"]) == {"2026-11-02", "2026-11-03"}
    over = sel.schedule(cs, seed="s", core_cap=4)
    missed = [x for x in over["ledger"] if x["type"] == "MISSED_CAPACITY"]
    assert len(over["assignments"]) == 8 and len(missed) == 2 and all(x["core_cap"] == 4 and x["priority_hash"] for x in missed)
    assert {m["symbol"] for m in missed}.isdisjoint({a["symbol"] for a in over["assignments"]})
    worst = sorted(cs, key=lambda c: sel.tie_break("s", c.symbol, c.event_date), reverse=True)[:2]
    assert {m["symbol"] for m in missed} == {c.symbol for c in worst}                                          # the lowest-priority (hash order) events are the ones missed


def test_deadline_first_then_status_then_hash():
    early = cand("EARLY", date(2026, 11, 3), "BMO")          # deadline Nov 2 06:00 ET
    late = cand("LATE", date(2026, 11, 3), "AMC")            # deadline Nov 2 16:00 ET
    est = cand("EST", date(2026, 11, 3), "AMC", ds=cv.ESTIMATED, ts=cv.T_SINGLE)
    order = [c.symbol for c in sorted([late, est, early], key=lambda c: sel.priority_key(c, "s"))]
    assert order == ["EARLY", "LATE", "EST"]


def test_skips_are_logged_with_reasons():
    cs = [cand("OK", date(2026, 11, 4)), cand("ILL", date(2026, 11, 4), liquid=False), cand("UNK", date(2026, 11, 4), ds=cv.UNKNOWN),
          cand("STALE", date(2026, 11, 4), ds=cv.STALE)]
    plan = sel.schedule(cs, seed="s")
    assert [a["symbol"] for a in plan["assignments"]] == ["OK"]
    assert {x["symbol"]: x["type"] for x in plan["ledger"]} == {"ILL": "SKIPPED_ILLIQUID", "UNK": "SKIPPED_UNCERTAIN_OR_UNKNOWN_DATE", "STALE": "SKIPPED_STALE_DATE_REFRESH_FIRST"}


def test_full_capture_is_stratified_confirmed_and_capped():
    cs = [cand("B%02d" % i, date(2026, 11, 4), "BMO") for i in range(40)] + [cand("A%02d" % i, date(2026, 11, 4), "AMC") for i in range(40)]
    cs += [cand("E%02d" % i, date(2026, 11, 4), "AMC", ds=cv.ESTIMATED, ts=cv.T_SINGLE) for i in range(5)] + [cand("U%02d" % i, date(2026, 11, 4), "UNKNOWN", ts=cv.T_UNKNOWN) for i in range(5)]
    plan = sel.schedule(cs, seed="s", core_cap=220, full_cap=30)
    full = [a for a in plan["assignments"] if a["tier"] == "FULL"]
    assert len(full) == 30 and sum(a["kind"] == "BMO" for a in full) == 15 and sum(a["kind"] == "AMC" for a in full) == 15
    assert not any(a["symbol"][0] in "EU" for a in full)                                                      # estimated or unknown-timing events are never full-capture
    few = sel.schedule([cand("B%02d" % i, date(2026, 11, 4), "BMO") for i in range(40)] + [cand("A%02d" % i, date(2026, 11, 4), "AMC") for i in range(5)], seed="s")
    f2 = [a for a in few["assignments"] if a["tier"] == "FULL"]
    assert len(f2) == 30 and sum(a["kind"] == "AMC" for a in f2) == 5 and sum(a["kind"] == "BMO" for a in f2) == 25   # short stratum is filled from the other


def test_peak_day_of_216_events_fits_220_and_smoothing_is_real():
    cs = [cand("P%03d" % i, date(2026, 10, 29), "BMO" if i % 2 else "AMC") for i in range(216)]
    ok = sel.schedule(cs, seed="s", core_cap=220)
    assert len(ok["assignments"]) == 216 and not [x for x in ok["ledger"] if x["type"] == "MISSED_CAPACITY"]
    sm = sel.schedule(cs, seed="s", core_cap=110)                                                             # two ET days of 110 each can hold 216 events with 24-hour windows
    assert len(sm["assignments"]) + len([x for x in sm["ledger"] if x["type"] == "MISSED_CAPACITY"]) == 216 and len(sm["assignments"]) > 110


# ------------------------------------------------------------------ governor
def clock_at(h, d=date(2026, 11, 3)):
    return gv.FakeClock(datetime.combine(d, time(h, 0), ET).astimezone(UTC))


def test_default_is_unauthorized_and_kill_switch_works():
    g = gv.Governor(clock_at(10))
    assert g.request("x") == (False, "NOT_AUTHORIZED") and g.ledger[-1]["type"] == "DENIED_NOT_AUTHORIZED"
    g2 = gv.Governor(clock_at(10), authorized_daily=10, enabled=False)
    assert g2.request("x") == (False, "KILL_SWITCH")


def test_daily_authorization_and_planning_ceiling():
    g = gv.Governor(clock_at(22), authorized_daily=5)
    assert [g.request("x")[0] for _ in range(6)] == [True] * 5 + [False] and g.ledger[-1]["type"] == "DENIED_DAILY_CAP"
    g2 = gv.Governor(clock_at(22), authorized_daily=999999)
    assert g2.daily_limit() == 1700                                                                          # the ceiling bounds any authorization


def test_hourly_shared_cap_keeps_the_existing_reserve():
    g = gv.Governor(clock_at(10), authorized_daily=1700, spacing_s=0)                                         # live hours, reserve 300 of 400 for the existing warmer
    assert sum(g.request("x")[0] for _ in range(150)) == 100 and g.ledger[-1]["type"] == "DENIED_HOURLY_SHARED_CAP"
    g2 = gv.Governor(clock_at(10), authorized_daily=1700, spacing_s=0, existing_usage_fn=lambda ts: 380)    # the warmer is using 380 this hour
    assert sum(g2.request("x")[0] for _ in range(50)) == 20
    g3 = gv.Governor(clock_at(10), authorized_daily=1700, spacing_s=0, existing_usage_fn=lambda ts: 100)
    assert sum(g3.request("x")[0] for _ in range(150)) == 100                                                  # below the reserve the reserve still holds
    g4 = gv.Governor(clock_at(22), authorized_daily=1700, spacing_s=0)                                         # off hours: 6,000 - 1,000 reserve
    assert sum(g4.request("x")[0] for _ in range(1800)) == 1700 and g4.ledger[-1]["type"] == "DENIED_DAILY_CAP"


def test_hourly_window_rolls_over():
    c = clock_at(10)
    g = gv.Governor(c, authorized_daily=1700, spacing_s=0)
    assert sum(g.request("x")[0] for _ in range(120)) == 100
    c.sleep(3600)
    assert g.request("x")[0] is True


def test_spacing_between_requests():
    c = clock_at(22)
    g = gv.Governor(c, authorized_daily=10, spacing_s=2.0)
    t0 = c.now()
    g.request("a"); g.request("b"); g.request("c")
    assert (c.now() - t0).total_seconds() >= 4.0


def test_core_is_never_starved_by_lower_priorities():
    g = gv.Governor(clock_at(22), authorized_daily=10, spacing_s=0)
    g.reserve_core(8)
    assert [g.request("x", "full_extra")[0] for _ in range(3)] == [True, True, False] and g.ledger[-1]["type"] == "DENIED_CORE_RESERVATION"
    assert [g.request("c", "core")[0] for _ in range(9)] == [True] * 8 + [False]
    assert g.request("cal", "calendar")[0] is False


def test_retries_failures_and_429_stop():
    c = clock_at(22)
    g = gv.Governor(c, authorized_daily=20, spacing_s=0, max_retries=1)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise gv.ProviderError("transient", 503)
        return "ok"
    r = g.execute(flaky, purpose="p", symbol="AAA")
    assert r["status"] == "OK" and r["attempts"] == 2 and g.used_day["2026-11-03"] == 2 and [x["type"] for x in g.ledger] == ["PROVIDER_FAILURE"]

    def always():
        raise gv.ProviderError("transient", 500)
    r2 = g.execute(always, purpose="p")
    assert r2["status"] == "FAILED" and r2["reason"] == "TRANSIENT_RETRIES_EXHAUSTED" and r2["attempts"] == 2 and g.used_day["2026-11-03"] == 4

    def limited():
        raise gv.ProviderError("rate_limited", 429)
    r3 = g.execute(limited, purpose="p")
    assert r3["reason"] == "RATE_LIMITED" and g.request("x") == (False, "STOPPED") and any(x["type"] == "STOP_429" for x in g.ledger)
    c.sleep(24 * 3600)
    assert g.request("x")[0] is True                                                                          # stopped for the rest of the day only
    g.missed_deadline("AAA", date(2026, 11, 4), c.now(), "CAPACITY")
    assert g.ledger[-1]["type"] == "MISSED_DEADLINE"


def test_auth_failure_stops_and_is_logged():
    g = gv.Governor(clock_at(22), authorized_daily=20, spacing_s=0)

    def bad():
        raise gv.ProviderError("auth", 401)
    assert g.execute(bad, purpose="p")["reason"] == "AUTH" and g.request("x") == (False, "STOPPED")


# ------------------------------------------------------------------ opt-in 429 policy: Retry-After, bounded jittered backoff, circuit breaker (the default above is unchanged)
def _limited(retry_after=None):
    def f():
        raise gv.ProviderError("rate_limited", 429, retry_after=retry_after)
    return f


def test_backoff_policy_pauses_for_retry_after_then_recovers_and_counts_the_refused_request():
    c = clock_at(22)
    g = gv.Governor(c, authorized_daily=20, spacing_s=0, max_retries=0, rate_limit_policy="backoff", backoff_base_s=30, backoff_max_s=900, rng=lambda: 1.0)
    r = g.execute(_limited(retry_after=120), purpose="p", symbol="AAA")
    assert r["reason"] == "RATE_LIMITED" and g.used_day["2026-11-03"] == 1                                    # the refused request still counts against the budget
    assert g.request("x") == (False, "STOPPED") and (g.stopped_until - c.now()).total_seconds() == 120      # Retry-After 120 s beats the 30 s backoff
    c.sleep(121)
    assert g.request("x")[0] is True                                                                          # recovered by itself the same day
    assert g.execute(lambda: "ok", purpose="p")["status"] == "OK" and g.consecutive_429 == 0


def test_backoff_policy_is_bounded_and_a_hostile_retry_after_cannot_park_collection_for_a_day():
    c = clock_at(22)
    g = gv.Governor(c, authorized_daily=50, spacing_s=0, max_retries=0, rate_limit_policy="backoff", backoff_base_s=30, backoff_max_s=900, breaker_threshold=99, rng=lambda: 1.0)
    waits = []
    for _ in range(8):
        g.execute(_limited(retry_after=86400), purpose="p")
        waits.append(round((g.stopped_until - c.now()).total_seconds()))
        c.sleep(waits[-1] + 1)
    assert max(waits) <= 900 and waits[0] == 900                                                               # Retry-After is capped at the maximum, never a day
    g2 = gv.Governor(clock_at(22), authorized_daily=50, spacing_s=0, max_retries=0, rate_limit_policy="backoff", backoff_base_s=30, backoff_max_s=900, breaker_threshold=99, rng=lambda: 1.0)
    seq = []
    for _ in range(7):
        g2.execute(_limited(), purpose="p")
        seq.append(round((g2.stopped_until - g2.clock.now()).total_seconds()))
        g2.clock.sleep(seq[-1] + 1)
    assert seq == [30, 60, 120, 240, 480, 900, 900]                                                           # exponential, then flat at the bound


def test_backoff_policy_opens_a_circuit_breaker_after_repeated_429s_and_stops_for_the_day_after_repeated_trips():
    c = clock_at(22)
    g = gv.Governor(c, authorized_daily=100, spacing_s=0, max_retries=0, rate_limit_policy="backoff", backoff_base_s=10, backoff_max_s=60, breaker_threshold=3, breaker_cooldown_s=1800, breaker_trips_per_day=2, rng=lambda: 1.0)
    for _ in range(3):
        g.execute(_limited(), purpose="p")
        c.sleep(61) if g.stopped_until and (g.stopped_until - c.now()).total_seconds() <= 61 else None
    assert any(x["type"] == "BREAKER_OPEN_429" for x in g.ledger) and (g.stopped_until - c.now()).total_seconds() >= 1700
    c.sleep(1801)
    assert g.request("x")[0] is True                                                                          # the breaker closes after its cool-down
    for _ in range(3):
        g.execute(_limited(), purpose="p")
        c.sleep(61) if g.stopped_until and (g.stopped_until - c.now()).total_seconds() <= 61 else None
    assert any(x["type"] == "STOP_429_BREAKER_DAY" for x in g.ledger) and g.request("x") == (False, "STOPPED")      # a second trip the same day: stop until midnight (New York), like the default


def test_backoff_policy_keeps_the_hard_daily_limit_and_the_kill_switch():
    c = clock_at(22)
    g = gv.Governor(c, authorized_daily=3, spacing_s=0, max_retries=0, rate_limit_policy="backoff", rng=lambda: 1.0)
    for i in range(3):
        assert g.execute(lambda: "ok", purpose="p")["status"] == "OK"
    assert g.request("x") == (False, "DAILY_CAP")
    g.enabled = False
    assert g.request("x") == (False, "KILL_SWITCH")
