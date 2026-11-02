"""Data-quality audit (read only) and the V2 inheritance of V1 calendar blocks. Fixtures and saved data only; no provider, no store write."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_dq_audit as DQ  # noqa: E402
import smart_move_dq_audit_run as RUN  # noqa: E402
import smart_move_v2 as V2  # noqa: E402
import smart_move_v2_run as V2R  # noqa: E402

UTC = timezone.utc
CAP = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
ED = "2026-11-03"


def alert(sym="AAA", typ="call", ask=400000, bid=0, hours_ago=24, expiry="2026-11-20", voi=2.0, multileg=False, sweep=False, chain="C1", prem=None):
    t = (CAP - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")
    return {"type": typ, "ticker": sym, "created_at": t, "expiry": expiry, "option_chain": chain + t, "total_premium": str(prem if prem is not None else ask + bid), "volume_oi_ratio": str(voi),
            "total_ask_side_prem": str(ask), "total_bid_side_prem": str(bid), "has_multileg": multileg, "has_sweep": sweep}


# ------------------------------------------------------------------ V2 inherits V1 calendar blocks
def bullish_factors():
    f = [V2._factor(n, V2.WEIGHTS[n], 0, True) for n in ("F3", "F5", "F7", "F8", "ANALYST", "OPTIONS_FLOW")]
    return f + [V2._factor(n, 0, 0, False, reason="X") for n in ("IMPLIED_VS_HIST", "SHORT_INST", "REL_STRENGTH", "NEWS_GUIDANCE")]


def test_a_stale_calendar_block_in_v1_stops_a_v2_signal_but_an_unblocked_event_still_scores():
    free = V2.score(bullish_factors(), v1_gates=V2R.inherit_blocks([], []), variant="continuation")
    assert free["signal"] == "BUY"                                              # the scoring rule itself is unchanged
    blocked = V2.score(bullish_factors(), v1_gates=V2R.inherit_blocks([], ["STALE_CALENDAR", "CALENDAR_STALE"]), variant="continuation")
    assert blocked["signal"] == "NO_TRADE" and blocked["reason_codes"] == ["BLOCKED_BY_V1_GATE"] and "BLOCKED_BY_GATE" in blocked["inherited_gates"]


def test_inherit_blocks_keeps_known_codes_maps_others_and_never_removes_a_gate():
    assert V2R.inherit_blocks(["UNKNOWN_TIMING"], ["STALE_EVIDENCE", "NO_EVENT_DATE"]) == ["UNKNOWN_TIMING", "STALE_EVIDENCE", "BLOCKED_BY_GATE"]
    assert V2R.inherit_blocks(["OUT_OF_WINDOW"], None) == ["OUT_OF_WINDOW"] and V2R.inherit_blocks([], []) == []


def test_no_gate_ever_turns_data_incomplete_into_a_signal():
    thin = [V2._factor(n, V2.WEIGHTS[n], 0, True) for n in ("F3", "F5")] + [V2._factor(n, 0, 0, False, reason="X") for n in ("F7", "F8", "ANALYST", "OPTIONS_FLOW", "IMPLIED_VS_HIST", "SHORT_INST", "REL_STRENGTH", "NEWS_GUIDANCE")]
    for gates in ([], ["BLOCKED_BY_GATE"]):
        r = V2.score(thin, v1_gates=gates, variant="continuation")
        assert r["signal"] not in ("BUY", "SELL")


# ------------------------------------------------------------------ options-flow audit
def evid(sym, kind="uw_flow_alerts", status="OK"):
    return {"symbol": sym, "kind": kind, "status": status, "raw_sha256": "sha-" + sym, "retrieved_at": CAP.isoformat()}


def test_flow_audit_counts_accepted_and_rejected_by_the_existing_rules_and_changes_nothing():
    good = [alert(chain="C%d" % i, ask=300000, sweep=i == 0) for i in range(4)]                          # 4 call-bought alerts, $1.2M ask-side
    bad = [alert(chain="M", multileg=True), alert(chain="E", expiry="2026-11-02"), alert(chain="V", voi=0.5), alert(chain="O", hours_ago=24 * 9), alert(chain="Z", ask=0, bid=0, prem=100000)]
    payload = {"AAA": {"data": good + bad}, "BBB": {"data": []}}
    ev = [{"symbol": s, "earnings_date": ED, "scored_as_of": CAP.isoformat()} for s in ("AAA", "BBB", "CCC", "DDD")]
    evidence = [evid("AAA"), evid("BBB"), evid("CCC", status="DENIED")]
    out = DQ.flow_audit(ev, evidence, lambda sha: payload.get(sha[4:]))
    t = out["table"]
    assert t["accepted_alerts"] == 4 and t["rejected_alerts"] == 5 and t["unavailable_stocks"] == 1 and t["failed_stocks"] == 1
    assert out["rejected_by_reason"] == {DQ.GATE_REASON[k]: 1 for k in ("multi_leg", "expiry_before_earnings", "volume_below_open_interest", "outside_lookback", "side_not_attributable")}
    states = {p["symbol"]: p["state"] for p in out["per_stock"]}
    assert states == {"AAA": "QUALIFIES", "BBB": "NO_ALERTS", "CCC": "API_FAILURE", "DDD": "NOT_COLLECTED"}
    assert out["rule"]["min_attributed_premium_usd"] == 500_000.0                                         # rule echoed, never altered


def test_flow_audit_direction_uses_the_aggressor_side_not_call_or_put_alone():
    sold_calls = [alert(chain="S%d" % i, ask=0, bid=300000) for i in range(4)]                            # calls SOLD = bearish
    out = DQ.flow_audit([{"symbol": "AAA", "earnings_date": ED, "scored_as_of": CAP.isoformat()}], [evid("AAA")], lambda sha: {"data": sold_calls})
    p = out["per_stock"][0]
    assert p["state"] == "QUALIFIES" and p["lean"] == "BEARISH" and p["net_ratio"] < 0


def test_integrity_checks_flag_duplicates_mismatched_symbols_and_future_stamps():
    a = alert()
    dup = dict(a)
    wrong = alert(sym="ZZZ", chain="W")
    future = alert(chain="F", hours_ago=-2)
    c = DQ.integrity([a, dup, wrong, future], "AAA", CAP)
    assert c["duplicate"] == 1 and c["symbol_mismatch"] == 1 and c["stamped_after_capture"] == 1 and c["call"] == 4


# ------------------------------------------------------------------ news audit
def test_news_audit_reports_only_saved_news_and_never_scores():
    hl = [{"headline": "Acme Corp lowers full-year guidance", "source": "Business Wire", "created_at": (CAP - timedelta(days=1)).isoformat().replace("+00:00", "Z"), "tickers": ["AAA"], "sentiment": "neutral"},
          {"headline": "AAA Price Prediction: momentum", "source": "Blockchain News", "created_at": (CAP - timedelta(days=1)).isoformat().replace("+00:00", "Z"), "tickers": ["AAA"], "sentiment": "neutral"},
          {"headline": "Acme Corp posts update", "source": "Business Wire", "created_at": (CAP + timedelta(days=1)).isoformat().replace("+00:00", "Z"), "tickers": ["AAA"], "sentiment": "neutral"}]
    ev = [{"symbol": s, "earnings_date": ED, "scored_as_of": CAP.isoformat()} for s in ("AAA", "BBB")]
    out = DQ.news_audit(ev, [evid("AAA", "uw_news")], lambda sha: {"data": hl}, {"AAA": "Acme Corp"})
    assert out["stocks_with_saved_news"] == 1 and out["stocks_without_saved_news"] == 1 and out["headlines"] == 3
    assert out["labels"].get("NEGATIVE") == 1 and out["labels"].get("UNVERIFIED") == 2 and out["after_capture"] == 1
    assert out["provider_sentiment_values"] == {"neutral": 3} and "blocked" in out["approval"].lower()


# ------------------------------------------------------------------ root causes and analyst readiness
def test_root_cause_table_never_marks_a_closed_window_recoverable_and_groups_by_kind():
    rec = {"per_stock": [{"symbol": "A", "unresolved": [{"factor": "F3", "points_missing": 15, "category": 6, "reason": "r", "recoverable_today": False},
                                                      {"factor": "ANALYST", "points_missing": 10, "category": 4, "reason": "r", "recoverable_today": False}]}]}
    out = DQ.root_cause_table(rec)
    assert out["fixable_from_stored_data"] == 0 and out["by_problem_kind"]["collection failure (stale calendar at capture)"] == {"F3": 1}
    assert all(r["future_collection_required"] for r in out["rows"])


def test_analyst_readiness_needs_a_second_snapshot_seven_days_later():
    now = datetime(2026, 11, 2, tzinfo=UTC)
    one = DQ.analyst_readiness([{"symbol": "A", "retrieved_at": "2026-11-01T10:00:00+00:00"}], ["A", "B"], now)
    assert one["with_a_snapshot"] == 1 and one["revision_measurable_now"] == 0 and one["earliest_second_snapshot_useful"].startswith("2026-11-08")
    two = DQ.analyst_readiness([{"symbol": "A", "retrieved_at": "2026-11-01T10:00:00+00:00"}, {"symbol": "A", "retrieved_at": "2026-11-09T10:00:00+00:00"}], ["A"], datetime(2026, 11, 10, tzinfo=UTC))
    assert two["revision_measurable_now"] == 1


# ------------------------------------------------------------------ saved data
@pytest.mark.skipif(not (RUN.STORE.exists() and RUN.V2R.exists() and list((RUN.RUN / "audits_recovery").glob("recovery_audit_*.json"))), reason="saved collection not on this machine")
def test_audit_on_the_saved_collection_is_read_only_and_matches_the_earlier_findings():
    res = RUN.run()
    assert res["store_unchanged"] is True and res["provider_requests"] == 0 and res["events"] == 95
    f = res["flow"]
    assert f["table"]["returned_alerts"] == 5863 and f["table"]["accepted_alerts"] + f["table"]["rejected_alerts"] == 5863
    assert f["stock_states"].get("QUALIFIES", 0) == 3 and f["integrity"].get("symbol_mismatch", 0) == 0
    assert res["news"]["stocks_with_saved_news"] == 4 and res["news"]["after_capture"] == 0
    assert res["root_causes"]["fixable_from_stored_data"] == 0 and res["analyst_readiness"]["revision_measurable_now"] == 0
