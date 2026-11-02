"""V2 candidate (v2c-1): identical scoring, honest status taxonomy, independent Continuation/Contrarian; and the point-in-time flow-lookback backtest (cutoff, leakage, windows, inconclusive verdict)."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_flow_backtest as FB  # noqa: E402
import smart_move_v2 as V2  # noqa: E402
import smart_move_v2c as C  # noqa: E402

from test_v2 import CAP, full_factors, v1_sub  # noqa: E402

UTC = timezone.utc


def stock(factor_states=None, gates=(), stale=False, f5="available"):
    names = list(V2.WEIGHTS)
    fs = {n: {"state": "available", "cause": None} for n in names}
    fs["F5"]["state"] = f5
    for n, (st, cause) in (factor_states or {}).items():
        fs[n] = {"state": st, "cause": cause}
    return {"factors": fs, "eligibility": {"v1_gates": list(gates)}, "stale_calendar_at_capture": stale}


def v2res(direction=1, variant="continuation", gates=()):
    return V2.score(full_factors(variant, direction), v1_gates=list(gates), variant=variant)


def test_the_candidate_changes_no_weight_threshold_or_factor_rule():
    assert C.CONFIG["weights"] == V2.WEIGHTS and sum(C.CONFIG["weights"].values()) == 100
    assert C.CONFIG["decision"] == V2.CONFIG["decision"] and C.CONFIG["scoring_changes"] == [] and C.CONFIG["factor_redistribution"] == [] and C.CONFIG["fallbacks"] == []
    assert C.CONFIG["flow_lookback_days"] == 5 and C.CONFIG_HASH != V2.CONFIG_HASH and C.VERSION != V2.VERSION
    r = v2res()
    out = C.classify(r, stock())
    assert (out["bull"], out["bear"], out["available_points"], out["directional_available_points"]) == (r["bull"], r["bear"], r["available_points"], r["directional_available_points"])


def test_buy_and_sell_are_produced_only_by_the_unchanged_rule_and_per_variant_independently():
    st = stock()
    buy_cont = C.classify(v2res(1, "continuation"), st)
    sell_cont = C.classify(v2res(-1, "continuation"), st)
    contr = C.classify(v2res(1, "contrarian"), st)
    assert buy_cont["display_status"] == "BUY" and sell_cont["display_status"] == "SELL" and buy_cont["research_only"] is True and buy_cont["accuracy"] == "NOT VERIFIED"
    assert contr["variant"] == "contrarian" and contr["frozen_v2_signal"] == v2res(1, "contrarian")["signal"] and contr["display_status"] == contr["frozen_v2_signal"]
    summ = C.summarize([{"continuation": buy_cont, "contrarian": contr}, {"continuation": sell_cont, "contrarian": contr}])
    assert summ["counts"]["continuation"] == {"BUY": 1, "SELL": 1}                           # the two variants are counted separately


def test_every_stock_gets_exactly_one_named_category_and_valid_absence_earns_no_directional_credit():
    short = v2res()
    short = dict(short, directional_available_points=55.0)                                  # below the unchanged 60-point minimum
    miss = stock({"ANALYST": ("partial", "INSUFFICIENT_HISTORY"), "OPTIONS_FLOW": ("missing", "FILTERED_BY_RULE"), "NEWS_GUIDANCE": ("missing", "UNVERIFIED_SEMANTICS")})
    o = C.classify(short, miss)
    assert o["display_status"] == "DATA_INCOMPLETE" and o["primary_cause"] == "MISSING_REQUIRED" and set(o["secondary_causes"]) >= {"VALID_ABSENCE", "UNVERIFIED_SEMANTICS"}
    assert o["directional_available_points"] == 55.0                                        # a valid absence of flow is information, never points
    failed = stock({"OPTIONS_FLOW": ("missing", "API_FAILURE"), "ANALYST": ("partial", "INSUFFICIENT_HISTORY")})
    assert C.classify(short, failed)["primary_cause"] == "API_FAILED"                        # a failed request outranks a history gap
    stale = C.classify(short, stock({"F3": ("missing", "STALE_CALENDAR_AT_CAPTURE")}, gates=["UNKNOWN_TIMING"], stale=True))
    assert stale["primary_cause"] == "STALE_CALENDAR" and stale["display_status"] == "DATA_INCOMPLETE"


def test_known_illiquidity_is_a_genuine_no_trade_but_unknown_liquidity_or_an_unconfirmed_event_is_incomplete():
    short = dict(v2res(), directional_available_points=55.0)
    known = C.classify(short, stock(gates=["ILLIQUID_OR_UNKNOWN"], f5="available"))
    assert known["display_status"] == "NO_TRADE" and known["category"] == "GENUINE_NO_TRADE" and known["primary_cause"] == "ILLIQUID_KNOWN"
    unknown = C.classify(short, stock({"F5": ("missing", "V1_EVIDENCE_GAP")}, gates=["ILLIQUID_OR_UNKNOWN"], f5="missing"))
    assert unknown["display_status"] == "DATA_INCOMPLETE"                                   # liquidity was never measured: that is missing data
    nt = C.classify(short, stock(gates=["NOT_TRADABLE"]))
    assert nt["display_status"] == "DATA_INCOMPLETE" and nt["primary_cause"] == "EVENT_NOT_CONFIRMED"
    enough = dict(v2res(), directional_available_points=70.0, signal="NO_TRADE")
    g = C.classify(enough, stock())
    assert g["display_status"] == "NO_TRADE" and g["primary_cause"] == "BELOW_MARGIN" and g["category"] == "GENUINE_NO_TRADE"


# ------------------------------------------------------------------ point-in-time flow backtest
def test_cutoff_is_release_minus_24h_in_new_york_time_across_the_dst_change():
    k, c = FB.cutoff_for("2026-10-29", "postmarket")                                         # EDT: 16:00 ET = 20:00 UTC, cutoff 20:00 UTC the day before
    assert k == "AMC" and c == datetime(2026, 10, 28, 20, tzinfo=UTC)
    k, c = FB.cutoff_for("2026-11-03", "premarket")                                          # EST after 1 Nov: 06:00 ET = 11:00 UTC
    assert k == "BMO" and c == datetime(2026, 11, 2, 11, tzinfo=UTC) and FB.cutoff_for("2026-11-03", "unknown") == (None, None)


def alert(when, typ="call", ask=400_000, bid=0, expiry="2026-11-20", voi=2.0):
    return {"created_at": when.isoformat().replace("+00:00", "Z"), "type": typ, "total_ask_side_prem": str(ask), "total_bid_side_prem": str(bid), "expiry": expiry, "volume_oi_ratio": str(voi), "has_multileg": False, "has_sweep": False}


def test_alerts_after_the_cutoff_are_leakage_and_never_used_and_windows_widen_coverage_only_by_older_alerts():
    cut = datetime(2026, 11, 2, 20, tzinfo=UTC)
    near = [alert(cut - timedelta(days=1, hours=h)) for h in range(3)]
    old = [alert(cut - timedelta(days=12, hours=h)) for h in range(3)]
    late = [alert(cut + timedelta(hours=h + 1), typ="put", ask=9_000_000) for h in range(5)]                        # created after the cutoff
    s5, sig5, d5 = FB.flow_signal(near + late, "2026-11-04", cut, 5)
    assert s5 == "QUALIFIES" and sig5 == "BUY" and d5["leaked"] == 5 and d5["used"] == 3                          # the huge late puts changed nothing
    assert FB.flow_signal(old, "2026-11-04", cut, 5)[0] == "NONE_IN_WINDOW" and FB.flow_signal(old, "2026-11-04", cut, 15)[0] == "QUALIFIES"
    assert FB.flow_signal([], "2026-11-04", cut, 30)[0] == "NO_ALERTS" and FB.flow_signal(near[:2], "2026-11-04", cut, 5)[0] == "NOT_ENOUGH"
    assert FB.flow_signal(near, "2026-11-25", cut, 5)[0] == "NOT_ENOUGH"                                         # expiry before the report date carries no earnings exposure


def test_the_backtest_run_is_descriptive_and_inconclusive_without_a_decided_sample():
    store = Path(r"C:\Users\initi\SmartMoveRun\v2_runs\v2_20261101\store")
    if not store.exists():
        import pytest
        pytest.skip("saved collection store not on this machine")
    r = FB.run(store)
    assert set(r["windows"]) == {"5", "10", "15", "30"} and r["verdict"].startswith("INCONCLUSIVE") and not any(w["minimum_sample_met"] for w in r["windows"].values())
    assert all("ci95" in w and "walk_forward_halves" in w for w in r["windows"].values())
    cov = [r["windows"][k]["qualifying_events"] for k in ("5", "10", "15", "30")]
    assert cov == sorted(cov)                                                                                    # a wider window can only add qualifying events


# ------------------------------------------------------------------ route + audit integration (synthetic saved files)
def test_the_candidate_route_labels_research_only_and_the_audit_reports_candidate_and_findings(tmp_path, monkeypatch):
    import json
    from fastapi.testclient import TestClient
    import smart_move_audit as A
    import smart_move_audit_pdf as PDF
    import smart_move_local_server as srv
    from test_audit import make_run, pages
    root = make_run(tmp_path)
    res = {"model": "V2C", "variant": "continuation", "signal": "NO_TRADE", "display_status": "DATA_INCOMPLETE", "category": "DATA_INCOMPLETE", "primary_cause": "MISSING_REQUIRED", "secondary_causes": ["VALID_ABSENCE"], "explanation": "55 of the required 60",
           "bull": 5, "bear": 0, "available_points": 55, "directional_available_points": 55, "missing_points": 45, "factor_gaps": {}, "frozen_v2_signal": "NO_TRADE"}
    ev = [{"symbol": "S00", "earnings_date": "2026-11-02", "scored_as_of": "t", "eligibility": {}, "v2c": {"continuation": res, "contrarian": dict(res, variant="contrarian")}}]
    summ = {"counts": {"continuation": {"DATA_INCOMPLETE": 1}, "contrarian": {"DATA_INCOMPLETE": 1}}, "primary_causes": {"continuation": {"MISSING_REQUIRED": 1}, "contrarian": {"MISSING_REQUIRED": 1}}}
    v2c = {"version": "v2c-1", "status": "RESEARCH_CANDIDATE", "config_hash": "h", "base_version": "v2-exp-2", "summary": summ, "frozen_v2_counts": {"continuation": {"DATA_INCOMPLETE": 1}, "contrarian": {"DATA_INCOMPLETE": 1}},
           "events": ev, "accuracy": "NOT VERIFIED", "changes": ["x"], "scoring_changes": [], "factor_redistribution": [], "audit_hash": "a"}
    (tmp_path / "v2").mkdir()
    (tmp_path / "v2" / "v2c_run_x.json").write_text(json.dumps(v2c))
    monkeypatch.setenv("SMART_MOVE_RUN_ROOT", str(root))
    monkeypatch.setenv("SMART_MOVE_V2_DIR", str(tmp_path / "v2"))
    j = TestClient(srv.app).get("/api/smart-move/v2c").json()
    assert j["usable_for_decisions"] is False and j["accuracy"] == "NOT VERIFIED" and "RESEARCH CANDIDATE" in j["banner"] and j["events"][0]["primary_cause"] == "MISSING_REQUIRED" and j["scoring_changes"] == []
    findings = {"analyst_history": {"stocks_with_two_or_more": 116, "max_span_hours": 13.3, "stocks_with_a_changed_value": 0, "usable_revisions": 0, "conclusion": "none"}, "news_schema": {"headlines": 56, "sentiment": {"neutral": 56}, "conclusion": "unverified"},
                "flow_lookback_backtest": {"events_total": 4625, "verdict": "INCONCLUSIVE: x", "windows": {k: {"qualifying_events": n, "decided_buy_sell": 3, "hits": 1, "ci95": [0.1, 0.8]} for k, n in (("5", 3), ("10", 3), ("15", 6), ("30", 12))}}, "accuracy": "NOT VERIFIED"}
    rec = A.build(root, now=datetime(2026, 11, 2, tzinfo=UTC), saved_rows=[], v2c=v2c, findings=findings)
    assert rec["candidate"]["scoring_changes"] == [] and rec["findings"]["flow_lookback"]["verdict"].startswith("INCONCLUSIVE") and rec["candidate"]["accuracy"] == "NOT VERIFIED"
    out = PDF.render(rec, tmp_path / "c.pdf")
    text = " ".join(p.extract_text() for p in pages(out["pdf"]))
    assert out["pages"] == 2 and "V2 candidate Continuation (research)" in text.replace("\n", " ") and "Flow lookback" in text and "NOT VERIFIED" in text
