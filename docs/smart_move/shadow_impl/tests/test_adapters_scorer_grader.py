"""Adapters (F3, F5, F7, F8), the frozen 40-point scorer (continuation / contrarian), and the prospective grader. Synthetic data only."""
import statistics
from datetime import date, datetime, timedelta, timezone

import pytest

from fixtures import context_payload, iv_rank_payload, make_history, make_series, rows_from, trading_days
from smvshadow import adapters as ad
from smvshadow import cutoff as co
from smvshadow import freshness as fr
from smvshadow import grader as gr
from smvshadow import scorer as sc

UTC = timezone.utc
EVENT = date(2026, 11, 4)
PLAN = co.plan_event(EVENT, "AMC")                     # window 2026-11-02 21:00Z .. cutoff 2026-11-03 21:00Z
CAP = datetime(2026, 11, 3, 14, 0, tzinfo=UTC)         # capture 09:00 ET on the day before; last completed session = Mon 2026-11-02
LAST = date(2026, 11, 2)
START = date(2024, 1, 2)


def world(sym="AAA", reactions=None, jumps=None, **kw):
    ser = make_series(sym, START, date(2026, 11, 3), reactions, jumps, **kw)
    return ser, rows_from(ser)


# ------------------------------------------------------------------ F5
def test_f5_known_answer_and_nothing_after_the_last_session_is_used():
    ser, rows = world("AAA")
    spy_ser, spy_rows = world("SPY", drift=0.0002)
    ev = ad.f5_price_technical(rows, spy_rows, PLAN, CAP)
    ds = [d for d in sorted(ser) if d <= LAST]
    exp_ret = ser[LAST] / ser[ds[-21]] - 1
    exp_spy = spy_ser[LAST] / spy_ser[ds[-21]] - 1
    exp_ext = ser[LAST] / statistics.mean(ser[d] for d in ds[-50:]) - 1
    exp_adv = statistics.mean(ser[d] * 2_000_000 for d in ds[-20:])
    x = ev.extracted
    assert ev.status == "OK" and ev.session_reflected == LAST and ev.freshness_class == fr.FRESH
    assert x["ret20"] == pytest.approx(exp_ret) and x["rel20"] == pytest.approx(exp_ret - exp_spy) and x["ext50"] == pytest.approx(exp_ext) and x["adv20"] == pytest.approx(exp_adv)
    # the partial row of the capture day (2026-11-03) is in the payload but must not change anything
    shocked = [dict(r, close=r["close"] * 50, open=r["open"] * 50) if r["date"] == "2026-11-03" else r for r in rows]
    assert ad.f5_price_technical(shocked, spy_rows, PLAN, CAP).extracted == x
    with pytest.raises(fr.LeakageError):
        ad.f5_price_technical(rows, spy_rows, PLAN, PLAN["cutoff_utc"] + timedelta(seconds=1))      # a capture after the cutoff can never feed a prediction


def test_f5_suspect_window_missing_not_repaired_and_reaction_sessions_exempt():
    ser, rows = world("BBB", jumps={date(2026, 10, 20): 40.0})
    spy_ser, spy_rows = world("SPY")
    ev = ad.f5_price_technical(rows, spy_rows, PLAN, CAP)
    assert ev.extracted["ret20"] is None and ev.extracted["missing_why"]["ret20"] == "SUSPECT_WINDOW" and ev.extracted["adv20"] is None
    assert ev.extracted["ext50"] is not None or ev.extracted["missing_why"].get("ext50")                  # the 50-session window also contains it
    ev2 = ad.f5_price_technical(rows, spy_rows, PLAN, CAP, reaction_sessions={date(2026, 10, 20)})
    assert ev2.extracted["ret20"] is not None                                                           # the move was an earnings reaction session: not suspect
    far_ser, far_rows = world("CCC", jumps={date(2026, 3, 2): 40.0})
    assert ad.f5_price_technical(far_rows, spy_rows, PLAN, CAP).extracted["ret20"] is not None           # outside the window: unaffected


def test_f5_missing_or_stale_data_is_missing_with_reason():
    ser, rows = world("DDD")
    spy_ser, spy_rows = world("SPY")
    short = [r for r in rows if r["date"] <= "2026-10-30"]                                               # the last completed session (Mon) is absent
    ev = ad.f5_price_technical(short, spy_rows, PLAN, CAP)
    assert ev.status == "MISSING" and ev.missing_reason == "NOT_AVAILABLE_AS_OF" and not ev.usable
    assert ad.f5_price_technical([], spy_rows, PLAN, CAP).missing_reason == "PROVIDER_EMPTY"
    partial = ad.f5_price_technical([r for r in rows if "2026-09-01" <= r["date"] <= "2026-11-03"], spy_rows, PLAN, CAP)      # about 42 sessions: 50-session mean unavailable only
    assert partial.status == "OK" and partial.extracted["ext50"] is None and partial.extracted["missing_why"]["ext50"] == "INSUFFICIENT_HISTORY" and partial.extracted["ret20"] is not None
    young = [r for r in rows if "2026-10-15" <= r["date"] <= "2026-11-03"]
    assert ad.f5_price_technical(young, spy_rows, PLAN, CAP).missing_reason == "INSUFFICIENT_HISTORY"


# ------------------------------------------------------------------ F3
def test_f3_recomputes_reactions_from_candles_and_ignores_provider_fields():
    hist, react = make_history(EVENT, "AMC", [4.0, 5.0, -1.0, 3.5, 6.0, 4.5])
    ser, rows = world("EEE", reactions=react)
    ev = ad.f3_reaction_history(hist, rows, EVENT, PLAN, CAP)
    x = ev.extracted
    assert ev.status == "OK" and x["n"] == 6 and x["mean_pct"] == pytest.approx(statistics.mean([4.0, 5.0, -1.0, 3.5, 6.0, 4.5]), abs=0.05)
    assert x["up_share"] == pytest.approx(5 / 6) and x["down_share"] == pytest.approx(1 / 6) and ev.as_of_quality == "DERIVED"       # provider "9.99" moves never used
    # an event whose reaction session closes after the capture time is not available yet
    recent = hist + [{"report_date": "2026-11-02", "report_time": "postmarket"}]
    ev2 = ad.f3_reaction_history(recent, rows, EVENT, PLAN, CAP)
    assert ev2.extracted["n"] == 6 and ev2.extracted["dropped"]["not_closed_before_capture"] == 1
    # the target event itself and later events are never prior reactions
    ev3 = ad.f3_reaction_history(hist + [{"report_date": EVENT.isoformat(), "report_time": "postmarket"}, {"report_date": "2027-02-03", "report_time": "postmarket"}], rows, EVENT, PLAN, CAP)
    assert ev3.extracted["n"] == 6


def test_f3_needs_four_reactions_and_closes():
    hist, react = make_history(EVENT, "AMC", [4.0, 5.0, -1.0])
    ser, rows = world("FFF", reactions=react)
    ev = ad.f3_reaction_history(hist, rows, EVENT, PLAN, CAP)
    assert ev.status == "MISSING" and ev.missing_reason == "INSUFFICIENT_HISTORY" and ev.extracted["n"] == 3
    hist5, react5 = make_history(EVENT, "AMC", [1.0, 2.0, 3.0, 4.0, 5.0])
    ser5, rows5 = world("GGG", reactions=react5)
    first_react = co.reaction_session(date.fromisoformat(hist5[0]["report_date"]), "AMC")
    holed = [r for r in rows5 if not (r["date"] == first_react.isoformat() and r["market_time"] == "r")]
    ev2 = ad.f3_reaction_history(hist5, holed, EVENT, PLAN, CAP)
    assert ev2.extracted["n"] == 4 and ev2.extracted["dropped"]["missing_close"] == 1


# ------------------------------------------------------------------ F7
def test_f7_conditions():
    stamp = datetime(2026, 11, 2, 22, 35, tzinfo=UTC)
    ok = ad.f7_volatility_context(context_payload(LAST, "call_rich"), iv_rank_payload(LAST, stamp)["data"], PLAN, CAP)
    assert ok.status == "OK" and ok.extracted["skew_label"] == "call_rich" and ok.as_of_quality == "PER_ROW_STAMPED" and ok.provider_as_of == stamp
    assert ad.f7_volatility_context(context_payload(LAST, "flat"), None, PLAN, CAP).as_of_quality == "DATED_NOT_STAMPED"
    old = ad.f7_volatility_context(context_payload(date(2026, 10, 30), "flat"), None, PLAN, CAP)
    assert old.status == "MISSING" and old.missing_reason == "STALE_AGE" and not old.usable
    assert ad.f7_volatility_context(context_payload(date(2026, 11, 3), "flat"), None, PLAN, CAP).missing_reason == "NOT_AVAILABLE_AS_OF"        # a session that has not completed
    late_stamp = PLAN["cutoff_utc"] + timedelta(minutes=1)
    assert ad.f7_volatility_context(context_payload(LAST, "flat"), [{"updated_at": late_stamp.isoformat()}], PLAN, CAP).status == "MISSING"
    early = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)                                                     # before the window opens (21:00Z)
    assert ad.f7_volatility_context(context_payload(co.last_completed_session_at(early), "flat"), None, PLAN, early).missing_reason == "NOT_AVAILABLE_AS_OF"
    assert ad.f7_volatility_context(context_payload(LAST, "weird"), None, PLAN, CAP).missing_reason == "UNINFORMATIVE_FIELD"
    assert ad.f7_volatility_context({"data": {}}, None, PLAN, CAP).missing_reason == "PROVIDER_EMPTY"


# ------------------------------------------------------------------ F8
def test_f8_market_and_sector():
    spy_ser, spy_rows = world("SPY", drift=0.0006)
    sec_ser, sec_rows = world("XLK", drift=0.0012)
    ev = ad.f8_sector_market(spy_rows, sec_rows, PLAN, CAP)
    ds = [d for d in sorted(spy_ser) if d <= LAST]
    s = spy_ser[LAST] / spy_ser[ds[-21]] - 1
    t = sec_ser[LAST] / sec_ser[ds[-21]] - 1
    assert ev.extracted["spy_ret20"] == pytest.approx(s) and ev.extracted["sector_rel20"] == pytest.approx(t - s)
    assert ad.f8_sector_market(spy_rows, None, PLAN, CAP).extracted["sector_rel20"] is None
    assert ad.f8_sector_market([r for r in spy_rows if r["date"] >= "2026-10-20"], None, PLAN, CAP).status == "MISSING"


# ------------------------------------------------------------------ scorer
PRE, PRE_SHA = sc.load_prereg()


def E(f, extracted, status="OK", reason=None, fc=fr.FRESH):
    return fr.FactorEvidence(f, "t", status, reason, extracted, None, CAP, None, "DERIVED", fc, "ab" * 32 if status == "OK" else None)


def full_evidence(**over):
    ev = {3: E(3, {"mean_pct": 4.0, "up_share": 0.75, "down_share": 0.0}),
          5: E(5, {"ret20": 0.06, "rel20": 0.04, "ext50": 0.09, "adv20": 50e6, "missing_why": {}}),
          7: E(7, {"skew_label": "call_rich"}),
          8: E(8, {"spy_ret20": 0.04, "sector_rel20": 0.03})}
    ev.update(over)
    return ev


def run(ev, variant="continuation", kind="AMC", tstatus="CONFIRMED_TIMING", plan=PLAN, cap=CAP):
    return sc.score(PRE, PRE_SHA, ev, symbol="AAA", event_date=EVENT, event_version=1, kind=kind, timing_status=tstatus, plan=plan, capture_ts=cap, variant=variant)


def test_prereg_is_hash_checked_and_the_hardcoded_rule_text_matches_it(tmp_path):
    assert PRE["decision"]["BUY"] == "bull >= 14 and bear <= 4 and bull - bear >= 10" and PRE["decision"]["SELL"] == "bear >= 14 and bull <= 4 and bear - bull >= 10"
    assert PRE["decision"]["min_coverage"] == 0.60 and PRE["factors"]["total_max"] == 40 and PRE["event_eligibility"]["liquidity_gate"].startswith("adv20")
    bad = tmp_path / "p.json"
    bad.write_bytes(sc.PREREG.read_bytes().replace(b'"min_coverage": 0.6', b'"min_coverage": 0.5'))
    (tmp_path / "p.json.sha256").write_text(sc.PREREG.with_name(sc.PREREG.name + ".sha256").read_text())
    with pytest.raises(sc.PreregError):
        sc.load_prereg(bad)


def test_full_bullish_evidence_buys_in_continuation_and_conflicts_in_contrarian():
    c, k = run(full_evidence()), run(full_evidence(), "contrarian")
    assert (c["signal"], c["bull_points"], c["bear_points"], c["weight_available"]) == ("BUY", 40, 0, 40) and "RULE" in c["reason_codes"]
    # contrarian: F5 and F8 flip to bear (15), F3 and F7 stay bull (25): both sides above 14 -> CONFLICT, no trade
    assert (k["signal"], k["bull_points"], k["bear_points"]) == ("NO_TRADE", 25, 15) and "CONFLICT" in k["reason_codes"]
    assert c["explanation"]["weight_max"] == 40 and c["explanation"]["never_scaled_to_100"] and c["bull_points"] <= 40                  # never normalised to 100
    assert c["variant"] == "continuation" and k["variant"] == "contrarian" and c["prereg_sha256"] == PRE_SHA == k["prereg_sha256"]


def test_bull_and_bear_are_kept_separate():
    ev = full_evidence(**{"3": None}) if False else full_evidence()
    ev[3] = E(3, {"mean_pct": -4.0, "up_share": 0.0, "down_share": 0.75})
    ev[5] = E(5, {"ret20": -0.06, "rel20": -0.04, "ext50": -0.09, "adv20": 50e6, "missing_why": {}})
    ev[7] = E(7, {"skew_label": "put_rich"})
    ev[8] = E(8, {"spy_ret20": -0.04, "sector_rel20": -0.03})
    c = run(ev)
    assert (c["signal"], c["bull_points"], c["bear_points"]) == ("SELL", 0, 40)
    k = run(ev, "contrarian")
    assert (k["bull_points"], k["bear_points"]) == (15, 25) and k["signal"] == "NO_TRADE"                                              # F5/F8 (15) flip to bull; F3/F7 stay bear
    mixed = full_evidence()
    mixed[3] = E(3, {"mean_pct": -4.0, "up_share": 0.0, "down_share": 0.75})
    m = run(mixed)
    assert m["bull_points"] == 25 and m["bear_points"] == 15 and m["signal"] == "NO_TRADE" and "CONFLICT" in m["reason_codes"]       # evidence never nets out


def test_missing_and_stale_evidence_earns_nothing_and_is_not_counted():
    no7 = full_evidence()
    del no7[7]
    r = run(no7)
    assert r["weight_available"] == 30 and r["bull_points"] == 30 and r["explanation"]["subscores"]["F7_skew"]["available"] is False
    gone = full_evidence()
    del gone[3], gone[7]
    r2 = run(gone)
    assert r2["weight_available"] == 15 and r2["signal"] == "NO_TRADE" and "INSUFFICIENT_COVERAGE" in r2["reason_codes"] and r2["explanation"]["coverage"] == 0.375
    stale = full_evidence(**{})
    stale[5] = fr.missing(5, "uw.ohlc.1d", "STALE_AGE", CAP, freshness_class=fr.STALE)
    r3 = run(stale)
    assert "STALE_EVIDENCE" in r3["reason_codes"] and "ILLIQUID_OR_UNKNOWN" in r3["reason_codes"] and r3["signal"] == "NO_TRADE"
    usable_stale = full_evidence()
    usable_stale[7] = E(7, {"skew_label": "call_rich"}, fc=fr.STALE)
    assert run(usable_stale)["explanation"]["subscores"]["F7_skew"]["available"] is False                                           # a STALE_AGE snapshot is never valid evidence


def test_gates_unknown_timing_not_tradable_illiquid_out_of_window():
    assert "UNKNOWN_TIMING" in run(full_evidence(), kind="UNKNOWN", tstatus="UNKNOWN")["reason_codes"]
    assert "TIMING_CONTRADICTED" in run(full_evidence(), tstatus="CONFLICT")["reason_codes"]
    mon = co.plan_event(date(2026, 11, 9), "BMO")
    assert "NOT_TRADABLE" in run(full_evidence(), kind="BMO", plan=mon, cap=mon["window_start_utc"] + timedelta(hours=2))["reason_codes"]
    thin = full_evidence()
    thin[5] = E(5, {"ret20": 0.06, "rel20": 0.04, "ext50": 0.09, "adv20": 19_999_999, "missing_why": {}})
    assert run(thin)["signal"] == "NO_TRADE" and "ILLIQUID_OR_UNKNOWN" in run(thin)["reason_codes"]
    late = run(full_evidence(), cap=PLAN["cutoff_utc"] + timedelta(minutes=1))
    assert late["in_window"] is False and "OUT_OF_WINDOW" in late["reason_codes"] and late["signal"] == "NO_TRADE"


def test_variants_agree_when_direction_free_factors_decide():
    ev = {3: E(3, {"mean_pct": 4.0, "up_share": 0.75, "down_share": 0.0}), 5: E(5, {"ret20": 0.0, "rel20": 0.0, "ext50": 0.0, "adv20": 50e6, "missing_why": {}}),
          7: E(7, {"skew_label": "call_rich"}), 8: E(8, {"spy_ret20": 0.0, "sector_rel20": 0.0})}
    a, b = run(ev), run(ev, "contrarian")
    assert (a["signal"], a["bull_points"], a["bear_points"]) == (b["signal"], b["bull_points"], b["bear_points"]) == ("BUY", 25, 0)


# ------------------------------------------------------------------ grader
def pred(sym, d, var, sig, ver=1, event_version=1, in_window=True, adv=50e6):
    return {"symbol": sym, "earnings_date": d, "variant": var, "signal": sig, "prediction_version": ver, "event_version": event_version, "in_window": in_window, "adv20": adv}


def test_grader_matrices_flat_denominators_and_exclusions():
    d = [date(2026, 11, 3) + timedelta(days=i) for i in range(10)]
    preds = [pred("A", d[0], "continuation", "BUY"), pred("B", d[1], "continuation", "BUY"), pred("C", d[2], "continuation", "SELL"), pred("D", d[3], "continuation", "NO_TRADE"),
             pred("E", d[4], "continuation", "BUY"),                                     # outcome pending
             pred("F", d[5], "continuation", "BUY", in_window=False),                    # out of window: never graded
             pred("G", d[6], "continuation", "BUY", ver=1), pred("G", d[6], "continuation", "SELL", ver=2),      # superseded version
             pred("H", d[7], "continuation", "BUY", event_version=1),                     # event moved: final version is 2
             pred("A", d[0], "contrarian", "SELL"), pred("B", d[1], "contrarian", "SELL")]
    outs = {("A", d[0]): {"status": "GRADED", "move_pct": 2.0}, ("B", d[1]): {"status": "GRADED", "move_pct": 0.2}, ("C", d[2]): {"status": "GRADED", "move_pct": -3.0},
            ("D", d[3]): {"status": "GRADED", "move_pct": 4.0}, ("E", d[4]): {"status": "PENDING_REACTION", "move_pct": None}, ("G", d[6]): {"status": "GRADED", "move_pct": -1.0},
            ("H", d[7]): {"status": "GRADED", "move_pct": 1.0}, ("F", d[5]): {"status": "GRADED", "move_pct": 1.0}}
    rep = gr.grade(preds, outs, {("H", d[7]): 2})
    c = rep["variants"]["continuation"]
    assert c["graded_events"] == 5 and c["decided"] == 4 and c["coverage"] == 0.8                                  # A, B, C, G(SELL v2) decided; D abstained
    assert c["confusion"]["BUY"] == {"UP": 1, "FLAT": 1, "DOWN": 0} and c["confusion"]["SELL"] == {"UP": 0, "FLAT": 0, "DOWN": 2} and c["confusion"]["NO_TRADE"]["UP"] == 1
    assert c["strict_hit_rate"] == 0.75 and c["flat_among_decided"] == 1 and c["direction_only_n"] == 3 and c["direction_only_hit_rate"] == 1.0   # FLAT stays in the denominator
    a = rep["audit"]
    assert a["superseded_prediction_versions"] == 1 and a["out_of_window_excluded"] == 1 and a["not_final_event_version"] == 1 and a["ungraded_PENDING_REACTION"] == 1
    assert rep["variants"]["contrarian"]["decided"] == 2 and rep["claim_allowed"] is False and rep["claim_text"].startswith("NO ACCURACY CLAIM")
    assert c["minimum_sample_met"] is False
    assert c["net_mean_pct_cost_multiplier"]["0"] > c["net_mean_pct_cost_multiplier"]["3"]


def test_grader_never_allows_a_claim_without_sample_and_audit():
    d0 = date(2026, 1, 5)
    preds = [pred("S%03d" % i, d0 + timedelta(days=7 * (i % 10)), "continuation", "BUY") for i in range(120)]
    outs = {(p["symbol"], p["earnings_date"]): {"status": "GRADED", "move_pct": 1.0} for p in preds}
    rep = gr.grade(preds, outs, {})
    assert rep["variants"]["continuation"]["minimum_sample_met"] is True and rep["claim_allowed"] is False                  # the evidence audit has not passed
    assert gr.grade(preds, outs, {}, audit_passed=True)["claim_allowed"] is True
