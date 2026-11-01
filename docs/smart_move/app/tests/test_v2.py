"""V2 rules: explicit, deterministic, point-in-time, missing data never normalised, magnitude-only evidence never directional, V1 gates inherited, V1 untouched."""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_v2 as V  # noqa: E402
import smart_move_v2_compare as C  # noqa: E402

UTC = timezone.utc
CAP = datetime(2026, 11, 1, 12, tzinfo=UTC)
ED = "2026-11-03"


def mk(w, b, e, a=True):
    return {"weight": w, "available": a, "bull": b, "bear": e}


def v1_sub(bull=True):
    b, e = (1, 0) if bull else (0, 1)
    return {"F3_mean": mk(10, 10 * b, 10 * e), "F3_consistency": mk(5, 5 * b, 5 * e), "F5_trend": mk(4, 4 * b, 4 * e), "F5_relative_strength": mk(3, 3 * b, 3 * e), "F5_extension": mk(3, 3 * b, 3 * e),
            "F7_skew": mk(10, 10 * b, 10 * e), "F8_market": mk(2, 2 * b, 2 * e), "F8_sector": mk(3, 3 * b, 3 * e)}


def iso(d):
    return d.isoformat()


# ------------------------------------------------------------------ config / V1 factors
def test_weights_sum_to_100_config_hashed_and_magnitude_only_factor_is_not_directional():
    assert sum(V.WEIGHTS.values()) == 100 and len(V.CONFIG_HASH) == 64 and V.STATUS == "EXPERIMENTAL"
    assert "IMPLIED_VS_HIST" not in V.DIRECTIONAL and "NEWS_GUIDANCE" in V.DIRECTIONAL


def test_v1_factors_are_taken_from_v1_not_recomputed():
    f = {x["factor"]: x for x in V.from_v1(v1_sub())}
    assert f["F3"]["bull"] == 15 and f["F5"]["bull"] == 10 and f["F7"]["bull"] == 10 and f["F8"]["bull"] == 5
    assert not any(x["available"] for x in V.from_v1({k: dict(v, available=False) for k, v in v1_sub().items()}))


# ------------------------------------------------------------------ analyst: consensus revisions + actions
def snaps(eps0, eps1, rev0=100.0, rev1=100.0, gap=14):
    return [{"captured_at": iso(CAP - timedelta(days=gap + 1)), "eps": eps0, "revenue": rev0}, {"captured_at": iso(CAP - timedelta(days=1)), "eps": eps1, "revenue": rev1}]


def test_analyst_measures_eps_and_revenue_revisions_against_the_prior_consensus():
    up = V.analyst(snaps(1.00, 1.05, 100, 103), None, CAP, "continuation")
    assert up["available"] and up["bull"] == 10 and up["bear"] == 0 and up["eps_revision_pct"] == 5.0 and up["revenue_revision_pct"] == 3.0
    half = V.analyst(snaps(1.00, 1.05, 100, 100), None, CAP, "continuation")
    assert half["bull"] == 5                                                     # only EPS moved: half of the estimate points
    down = V.analyst(snaps(1.00, 0.90, 100, 97), None, CAP, "continuation")
    assert down["bear"] == 10 and V.analyst(snaps(1.00, 0.90, 100, 97), None, CAP, "contrarian")["bull"] == 10
    assert V.analyst(snaps(1.00, 1.05, 100, 97), None, CAP, "continuation")["bull"] == 0     # EPS up, revenue down: conflicting, no points


def test_analyst_needs_a_prior_snapshot_and_never_uses_a_future_one():
    one = V.analyst(snaps(1, 1.1)[1:], None, CAP, "continuation")
    assert not one["available"] and one["reason"].startswith("NO_PRIOR_CONSENSUS")
    too_close = V.analyst(snaps(1, 1.1, gap=3), None, CAP, "continuation")
    assert not too_close["available"]                                            # fewer than 7 days apart is not a revision history
    future = snaps(1.00, 1.10) + [{"captured_at": iso(CAP + timedelta(days=2)), "eps": 5.0, "revenue": 500}]
    assert V.analyst(future, None, CAP, "continuation")["eps_revision_pct"] == 10.0
    assert not V.analyst(None, None, CAP, "continuation")["available"]


def test_analyst_rating_actions_count_only_before_capture_and_within_30_days():
    acts = [{"action": "upgrade", "timestamp": iso(CAP - timedelta(days=2))}] * 2 + [{"action": "upgrade", "timestamp": iso(CAP + timedelta(hours=1))}] * 4 + [{"action": "upgrade", "timestamp": iso(CAP - timedelta(days=60))}] * 4
    r = V.analyst(None, acts, CAP, "continuation")
    assert r["available"] and r["upgrades"] == 2 and r["bull"] == 5 and r["available_points"] == 5


# ------------------------------------------------------------------ options flow with context
def alert(typ="call", ask=400_000, bid=0, sweep=False, multi=False, expiry="2026-11-20", voi=2.0, when=None):
    return {"type": typ, "total_ask_side_prem": ask, "total_bid_side_prem": bid, "has_sweep": sweep, "has_multileg": multi, "expiry": expiry, "volume_oi_ratio": voi, "created_at": iso(when or CAP - timedelta(days=1))}


def test_flow_direction_comes_from_the_aggressor_side_not_from_call_or_put_alone():
    calls_bought = V.options_flow([alert("call", ask=300_000)] * 3, ED, CAP, "continuation")
    assert calls_bought["bull"] == 15 and calls_bought["net_ratio"] == 1.0
    calls_sold = V.options_flow([alert("call", ask=0, bid=300_000)] * 3, ED, CAP, "continuation")
    assert calls_sold["bear"] == 15 and calls_sold["bull"] == 0                  # call premium sold at the bid is NOT bullish
    puts_sold = V.options_flow([alert("put", ask=0, bid=300_000)] * 3, ED, CAP, "continuation")
    assert puts_sold["bull"] == 15
    puts_bought = V.options_flow([alert("put", ask=300_000)] * 3, ED, CAP, "continuation")
    assert puts_bought["bear"] == 15 and V.options_flow([alert("put", ask=300_000)] * 3, ED, CAP, "contrarian")["bull"] == 15


def test_flow_context_filters_and_sweep_weighting_and_thresholds():
    base = [alert("call", ask=200_000)] * 3
    assert V.options_flow(base + [alert("call", ask=9e6, multi=True)], ED, CAP, "continuation")["alerts_used"] == 3         # multi-leg ignored
    assert V.options_flow(base + [alert("call", ask=9e6, expiry="2026-11-02")], ED, CAP, "continuation")["alerts_used"] == 3  # expires before earnings
    assert V.options_flow(base + [alert("call", ask=9e6, voi=0.2)], ED, CAP, "continuation")["alerts_used"] == 3             # not new positioning
    fut = V.options_flow(base + [alert("put", ask=9e6, when=CAP + timedelta(hours=2))], ED, CAP, "continuation")
    assert fut["alerts_used"] == 3 and fut["skipped_future"] == 1 and fut["bull"] == 15
    few = V.options_flow([alert("call", ask=300_000)] * 2, ED, CAP, "continuation")
    assert not few["available"] and not V.options_flow([alert("call", ask=1000)] * 5, ED, CAP, "continuation")["available"]
    mixed = V.options_flow([alert("call", ask=300_000)] * 3 + [alert("put", ask=200_000)] * 2, ED, CAP, "continuation")
    assert mixed["bull"] == 7.5 and mixed["net_ratio"] == 0.385                  # moderate lean = half points
    sw = V.options_flow([alert("call", ask=300_000, sweep=True)] * 3 + [alert("put", ask=400_000)] * 3, ED, CAP, "continuation")
    assert sw["sweeps"] == 3 and sw["net_ratio"] > 0                              # sweeps weigh 1.5x


# ------------------------------------------------------------------ implied move: risk only
def test_implied_move_is_magnitude_only_and_does_not_count_toward_directional_coverage():
    erows = [{"report_date": ED, "expected_move_perc": "0.06"}] + [{"report_date": "2026-0%d-15" % m, "post_earnings_move_1d": "-0.03"} for m in range(1, 7)]
    f = V.implied_vs_hist(erows, ED)
    assert f["available"] and f["ratio"] == 2.0 and "LARGER" in f["risk_label"] and f["bull"] == f["bear"] == 0 and f["directional"] is False
    assert not V.implied_vs_hist(erows[:3], ED)["available"]
    facs = V.from_v1(v1_sub()) + [f]
    r = V.score(facs, v1_gates=[], variant="continuation")
    assert r["available_points"] == 50 and r["directional_available_points"] == 40


# ------------------------------------------------------------------ short interest / institutions
def ss(si0, si1, f1=0.20, gap=14, dtc=5.0):
    d1 = CAP - timedelta(days=1)
    return [{"market_date": (d1 - timedelta(days=gap)).date().isoformat(), "si_float": f1, "short_interest": si0, "days_to_cover": dtc},
            {"market_date": d1.date().isoformat(), "si_float": f1, "short_interest": si1, "days_to_cover": dtc}]


def test_high_short_interest_alone_is_not_a_signal_only_high_and_rising_is_bearish():
    assert not V.short_inst(ss(100, 100)[1:], None, CAP, "continuation")["available"]                 # one snapshot: no positioning change
    rising = V.short_inst(ss(100, 115), None, CAP, "continuation")
    assert rising["available"] and rising["bear"] == 2.5 and rising["short_interest_change_pct"] == 15.0
    falling = V.short_inst(ss(100, 80), None, CAP, "continuation")
    assert falling["available"] and falling["bull"] == falling["bear"] == 0                            # covering/squeeze is ambiguous: no direction
    low = V.short_inst(ss(100, 120, f1=0.03), None, CAP, "continuation")
    assert low["bull"] == low["bear"] == 0
    assert V.short_inst(ss(100, 115), None, CAP, "contrarian")["bull"] == 2.5


def test_institutional_net_change_uses_only_filings_before_capture():
    own = [{"units_change": 600_000, "shares_outstanding": 100_000_000, "filing_date": iso(CAP - timedelta(days=5))}, {"units_change": -9_000_000, "shares_outstanding": 100_000_000, "filing_date": iso(CAP + timedelta(days=1))}]
    r = V.short_inst(None, own, CAP, "continuation")
    assert r["available"] and r["bull"] == 2.5 and r["institutional_net_pct_of_shares"] == 0.6 and r["available_points"] == 2.5


# ------------------------------------------------------------------ relative strength
def test_relative_strength_is_synchronised_to_the_last_common_session():
    d0 = date(2026, 10, 1)
    stock = {d0 + timedelta(days=i): 100 + 2 * i for i in range(30)}
    spy = {d0 + timedelta(days=i): 100 for i in range(25)}                          # SPY data ends 5 days earlier
    r = V.rel_strength(stock, spy, None, d0 + timedelta(days=29), "continuation")
    assert r["available"] and r["sync_session"] == (d0 + timedelta(days=24)).isoformat() and r["bull"] == 2.5 and r["available_points"] == 2.5
    assert not V.rel_strength({}, spy, None, d0, "continuation")["available"]


# ------------------------------------------------------------------ news / guidance
def test_news_guidance_is_blocked_until_semantics_are_verified_then_ignores_post_release_items(monkeypatch):
    rel = CAP + timedelta(days=1)
    assert V.news_guidance([{"created_at": iso(CAP - timedelta(days=1)), "is_major": True, "sentiment": "positive"}] * 3, rel, CAP, "continuation")["reason"] == "BLOCKED_SENTIMENT_SEMANTICS_UNVERIFIED"
    monkeypatch.setitem(V.CONFIG["news_guidance"], "sentiment_semantics_verified", True)
    items = [{"created_at": iso(CAP - timedelta(days=1)), "is_major": True, "sentiment": "positive"}] * 2 + [{"created_at": iso(rel + timedelta(hours=1)), "is_major": True, "sentiment": "negative"}] * 9
    r = V.news_guidance(items, rel, CAP, "continuation")
    assert r["available"] and r["bull"] == 10 and r["net_major_sentiment"] == 2                  # the post-release items never count
    assert not V.news_guidance(None, rel, CAP, "continuation")["available"]


# ------------------------------------------------------------------ engine
def full_factors(variant="continuation", direction=1):
    erows = [{"report_date": ED, "expected_move_perc": "0.04"}] + [{"report_date": "2026-0%d-15" % m, "post_earnings_move_1d": "0.05"} for m in range(1, 7)]
    d0 = date(2026, 10, 1)
    trend = {d0 + timedelta(days=i): 100 + i * 2 * direction for i in range(30)}
    flat = {d0 + timedelta(days=i): 100 for i in range(30)}
    s = snaps(1.0, 1.1 if direction > 0 else 0.9, 100, 105 if direction > 0 else 95)
    act = "upgrade" if direction > 0 else "downgrade"
    acts = [{"action": act, "timestamp": iso(CAP - timedelta(days=3))}] * 2
    flow = [alert("call" if direction > 0 else "put", ask=300_000)] * 3
    own = [{"units_change": 1_000_000 * direction, "shares_outstanding": 100_000_000, "filing_date": iso(CAP - timedelta(days=5))}]
    return V.from_v1(v1_sub(direction > 0)) + [V.implied_vs_hist(erows, ED), V.rel_strength(trend, flat, flat, d0 + timedelta(days=29), variant), V.analyst(s, acts, CAP, variant),
                                              V.options_flow(flow, ED, CAP, variant), V.short_inst(ss(100, 115 if direction < 0 else 80), own, CAP, variant), V.news_guidance(None, None, CAP, variant)]


def test_current_saved_evidence_can_never_decide_and_reports_data_incomplete():
    facs = V.from_v1(v1_sub()) + [V.implied_vs_hist(None, ED), V.rel_strength({}, {}, None, date(2026, 11, 1), "continuation"), V.analyst(None, None, CAP, "continuation"),
                                  V.options_flow(None, ED, CAP, "continuation"), V.short_inst(None, None, CAP, "continuation"), V.news_guidance(None, None, CAP, "continuation")]
    r = V.score(facs, v1_gates=[], variant="continuation")
    assert r["bull"] == 40 and r["available_points"] == 40 and r["missing_points"] == 60 and r["signal"] == "NO_TRADE" and r["display_status"] == "DATA_INCOMPLETE"
    assert "INSUFFICIENT_COVERAGE" in r["reason_codes"] and r["validated_real_data_signal"] is False and r["usable_for_decisions"] is False and "Missing:" in r["explanation"]
    assert [m for m in r["missing"] if m["factor"] == "NEWS_GUIDANCE"][0]["data_status"] == "BLOCKED"


def test_with_enough_directional_evidence_the_rule_fires_and_v1_gates_still_block():
    buy = V.score(full_factors(), v1_gates=[], variant="continuation")
    assert buy["directional_available_points"] >= 60 and buy["signal"] == "BUY" and buy["reason_codes"] == ["RULE"] and buy["bull"] >= 35 and buy["display_status"] == "BUY"
    assert buy["validated_real_data_signal"] is False                                  # NEWS_GUIDANCE is missing, so it is never labelled validated
    blocked = V.score(full_factors(), v1_gates=["NOT_TRADABLE", "ILLIQUID_OR_UNKNOWN"], variant="continuation")
    assert blocked["signal"] == "NO_TRADE" and blocked["reason_codes"] == ["BLOCKED_BY_V1_GATE"] and blocked["inherited_gates"] == ["NOT_TRADABLE", "ILLIQUID_OR_UNKNOWN"]
    assert V.score(full_factors(direction=-1), v1_gates=[], variant="continuation")["signal"] == "SELL"


def test_contrarian_is_independent_and_scoring_is_deterministic():
    c = V.score(full_factors("contrarian"), v1_gates=[], variant="contrarian")
    k = V.score(full_factors("continuation"), v1_gates=[], variant="continuation")
    assert c["variant"] == "contrarian" and c != k and c["bear"] > 0
    assert V.score(full_factors(), v1_gates=[], variant="continuation") == V.score(full_factors(), v1_gates=[], variant="continuation")


# ------------------------------------------------------------------ comparison
def test_comparison_is_inconclusive_without_sample_and_never_declares_a_winner():
    rows = [{"symbol": "A", "earnings_date": "2026-11-02", "variant": "continuation", "move_pct": 3.0, "v1_signal": "BUY", "v2_signal": "NO_TRADE", "kind": "AMC", "sector": "Tech"}]
    out = C.compare(rows)
    assert out["verdict"].startswith("INCONCLUSIVE") and not out["variants"]["continuation"]["sample_sufficient"]
    many = [{"symbol": "S%d" % i, "earnings_date": (date(2026, 11, 2) + timedelta(days=7 * (i % 9))).isoformat(), "variant": v, "move_pct": 2.0, "v1_signal": "BUY", "v2_signal": "BUY"}
            for i in range(120) for v in ("continuation", "contrarian")]
    big = C.compare(many, simulated=True)
    assert big["variants"]["continuation"]["sample_sufficient"] and big["verdict"].startswith("SIMULATED") and "better" not in big["verdict"].lower()
    assert len(big["variants"]["continuation"]["walk_forward"]["v1"]) == 3


def test_v2_never_imports_or_edits_the_v1_engine():
    src = (ROOT / "backend" / "smart_move_v2.py").read_text()
    assert "smvshadow" not in src and "import smart_move_live" not in src
