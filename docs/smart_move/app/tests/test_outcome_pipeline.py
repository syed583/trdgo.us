"""Phase 3 offline: price-bar ingestion, outcome processing, prediction-versus-outcome comparison and accuracy reporting (verified vs unverified kept apart). Synthetic fixtures only."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).parent))
import smart_move_accuracy as AC  # noqa: E402
import smart_move_outcome_audit as OA  # noqa: E402
import smart_move_pipeline as PL  # noqa: E402
import smart_move_price_bars as PB  # noqa: E402
from test_pipeline import EVENT, IN_WINDOW, pred  # noqa: E402
from test_snapshot_pairs import records  # noqa: E402

UTC = timezone.utc
RET = datetime(2026, 11, 13, 22, 0, tzinfo=UTC)          # after the 11-13 close


def bar(d, o, h, l, c, mt="r", v=1000):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v, "market_time": mt}


# ---------------------------------------------------------------- ingestion
def test_valid_bars_are_accepted_and_every_bad_row_is_rejected_with_a_reason(tmp_path):
    rows = [bar("2026-11-12", 100, 101, 99, 100.5), bar("2026-11-13", 100.5, 103, 100, 102), bar("2026-11-12", 100, 101, 99, 100.5), bar("2026-11-12", 1, 2, 0.5, 1.5),
            bar("2026-11-14", 100, 101, 99, 100), bar("2026-11-11", 100, 99, 101, 100), bar("2026-11-10", -1, 1, 1, 1), bar("2026-11-09", 100, 101, 99, 100, mt="pre"),
            bar("2026-11-26", 100, 101, 99, 100), bar("2026-12-01", 100, 101, 99, 100), {"date": "garbage"}, bar("2026-11-08", float("nan"), 1, 1, 1), bar("2026-11-06", 100, 101, 99, 100, v=-5)]
    v = PB.validate_rows(rows, RET)
    assert [r["date"] for r in v["accepted"]] == ["2026-11-12", "2026-11-13"]
    reasons = [x["reason"] for x in v["rejected"]]
    assert reasons == ["DUPLICATE_DATE", "CONFLICTING_DUPLICATE_DATE", "NOT_A_TRADING_DAY", "OHLC_INCONSISTENT", "BAD_OR_NON_POSITIVE_PRICE", "NOT_REGULAR_SESSION", "NOT_A_TRADING_DAY", "DATED_AFTER_RETRIEVAL",
                       "BAD_DATE", "BAD_OR_NON_POSITIVE_PRICE", "BAD_VOLUME"]


def test_a_bar_retrieved_before_its_session_closed_is_partial_and_gaps_are_reported_not_filled(tmp_path):
    same_day = datetime(2026, 11, 13, 19, 0, tzinfo=UTC)                                    # 14:00 ET: session still open
    assert PB.validate_rows([bar("2026-11-13", 1, 2, 1, 1.5)], same_day)["rejected"][0]["reason"] == "PARTIAL_OR_UNCLOSED_SESSION"
    st = PB.BarStore(tmp_path / "bars.jsonl")
    b = st.ingest("acme", [bar("2026-11-10", 100, 101, 99, 100), bar("2026-11-13", 100, 101, 99, 100)], source="fixture file", retrieved_at=RET)
    assert b["stored"] and b["missing_sessions"] == ["2026-11-11", "2026-11-12"] and len(b["accepted"]) == 2          # the two missing sessions are NOT invented


def test_the_bar_store_is_append_only_deduplicated_and_requires_a_source(tmp_path):
    st = PB.BarStore(tmp_path / "bars.jsonl")
    rows = [bar("2026-11-12", 100, 101, 99, 100.5), bar("2026-11-13", 100.5, 103, 100, 102)]
    with pytest.raises(ValueError):
        st.ingest("ACME", rows, source=" ", retrieved_at=RET)
    first = st.ingest("ACME", rows, source="fixture", retrieved_at=RET)
    again = st.ingest("ACME", rows, source="fixture", retrieved_at=RET + timedelta(hours=1))
    assert first["stored"] and not again["stored"] and len(PB.BarStore(tmp_path / "bars.jsonl").batches) == 1
    assert st.coverage("ACME") == {"symbol": "ACME", "bars": 2, "first": "2026-11-12", "last": "2026-11-13"} and set(st.bars_by_symbol()) == {"ACME"}


# ---------------------------------------------------------------- end to end: event -> prediction -> bars -> outcome -> accuracy
def run_one(close_after, signal="BUY", late_prediction=False, bars_batches=None):
    st = records()
    p = pred(IN_WINDOW if not late_prediction else datetime(2026, 11, 12, 6, 0, tzinfo=UTC))
    p["engine"] = {"continuation": {"signal": signal}}
    val = PL.validate_all([p], st)
    store = PB.BarStore()
    store.ingest("AAA", bars_batches or [bar("2026-11-12", 100, 101, 99, 100.0), bar("2026-11-13", 100, max(100, close_after) + 1, min(100, close_after) - 1, close_after)], source="fixture", retrieved_at=RET)
    ev = {"event_id": val["rows"][0]["event_id"] or "x", "symbol": "AAA", "event_date": EVENT, "session": "AMC", "cutoff_utc": "2026-11-11T21:00:00+00:00"}
    out = OA.audit([ev], store.bars_by_symbol(), {}, datetime(2026, 11, 14, 12, 0, tzinfo=UTC))
    return val, out


def test_a_buy_followed_by_a_rise_is_one_verified_hit_and_the_report_states_the_sample_size():
    val, out = run_one(103.0)
    assert out["events"][0]["state"] == "OUTCOME_COMPUTABLE" and out["events"][0]["outcome_class"] == "UP"           # AMC 11-12: base 11-12 close, reaction 11-13 close
    rep = AC.report(val["rows"], out["events"])
    v = rep["variants"]["continuation"]
    assert (v["decided_n"], v["strict_hits"], v["strict_hit_rate"], v["compared_events"]) == (1, 1, 1.0, 1) and rep["comparable_pairs"] == 1
    assert v["minimum_sample_met"] is False and rep["claim_allowed"] is False and rep["claim_text"].startswith("NO ACCURACY CLAIM") and rep["outcomes_by_verification"] == {"VERIFIED": 1}


def test_flat_outcomes_are_misses_for_strict_hits_and_excluded_from_direction_only():
    val, out = run_one(100.3)
    v = AC.report(val["rows"], out["events"])["variants"]["continuation"]
    assert out["events"][0]["outcome_class"] == "FLAT" and v["strict_hits"] == 0 and v["direction_only_n"] == 0 and v["direction_only_hit_rate"] is None
    sell_val, sell_out = run_one(97.0, signal="SELL")
    assert AC.report(sell_val["rows"], sell_out["events"])["variants"]["continuation"]["strict_hits"] == 1


def test_no_trade_is_an_abstention_not_a_miss():
    val, out = run_one(103.0, signal="NO_TRADE")
    v = AC.report(val["rows"], out["events"])["variants"]["continuation"]
    assert v["decided_n"] == 0 and v["abstained_no_trade_n"] == 1 and v["strict_hit_rate"] is None


def test_unadmissible_predictions_and_unverified_outcomes_never_enter_a_rate():
    val, out = run_one(103.0, late_prediction=True)                                                                  # captured after the cutoff
    rep = AC.report(val["rows"], out["events"])
    assert rep["comparable_pairs"] == 0 and rep["exclusions"] == {"PREDICTION_NOT_ADMISSIBLE:CAPTURED_AFTER_CUTOFF": 1} and rep["variants"] == {} and rep["claim_allowed"] is False
    val2, out2 = run_one(50.0)                                                                                       # a 2-for-1 shaped move is quarantined
    rep2 = AC.report(val2["rows"], out2["events"])
    assert out2["events"][0]["state"] == "QUARANTINED_CORPORATE_ACTION" and rep2["comparable_pairs"] == 0 and rep2["outcomes_by_verification"] == {"UNVERIFIED:QUARANTINED_CORPORATE_ACTION": 1}
    val3, out3 = run_one(103.0, bars_batches=[bar("2026-11-12", 100, 101, 99, 100.0)])                               # reaction bar absent: nothing is invented
    assert out3["events"][0]["state"] == "MISSING_BARS" and AC.report(val3["rows"], out3["events"])["exclusions"] == {"OUTCOME_NOT_VERIFIED:MISSING_BARS": 1}


def test_vendor_label_timing_outcomes_are_unverified_for_accuracy():
    val, _ = run_one(103.0)
    fake = [{"symbol": "AAA", "event_date": EVENT, "state": "OUTCOME_COMPUTABLE", "timing_basis": "VENDOR_LABEL_ONLY", "outcome_class": "UP", "move_pct": 3.0}]
    rep = AC.report(val["rows"], fake)
    assert rep["comparable_pairs"] == 0 and list(rep["exclusions"]) == ["OUTCOME_NOT_VERIFIED:OUTCOME_COMPUTABLE:VENDOR_LABEL_ONLY"]


def test_the_minimum_sample_rule_is_the_frozen_graders_and_a_claim_needs_it():
    rows = []
    for i in range(120):
        d = (datetime(2026, 1, 5, tzinfo=UTC) + timedelta(days=7 * (i % 10))).date().isoformat()
        rows.append({"symbol": "S%d" % i, "event_date": d, "state": "OUTCOME_COMPUTABLE", "timing_basis": "EVENT_RECORD", "outcome_class": "UP", "move_pct": 2.0})
    vals = [{"symbol": r["symbol"], "earnings_date": r["event_date"], "valid": True, "reasons": [], "event_id": "e", "event_version": 1, "signals": {"continuation": "BUY"}} for r in rows]
    rep = AC.report(vals, rows)
    v = rep["variants"]["continuation"]
    assert v["decided_n"] == 120 and v["earnings_weeks"] == 10 and v["minimum_sample_met"] is True and rep["claim_allowed"] is True and "no performance guarantee" in rep["claim_text"]
    assert v["strict_hit_ci95"][0] > 0.9 and v["strict_hit_rate"] == 1.0


def test_the_new_modules_do_not_import_network_or_write_outcome_files():
    for name in ("smart_move_price_bars.py", "smart_move_accuracy.py", "smart_move_pipeline.py"):
        src = (ROOT / "backend" / name).read_text(encoding="utf-8")
        imports = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
        assert not [ln for ln in imports if any(w in ln for w in ("requests", "httpx", "urllib", "socket", "uw_client", "smart_move_live", "smart_move_outcomes"))]
        assert "outcome_details" not in src
