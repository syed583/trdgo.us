"""Phase 1 offline pipeline: prediction admissibility, capture planning, completeness reason codes, event-level snapshot scheduling, evidence preservation. Synthetic fixtures only."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).parent))
import smart_move_evidence_integrity as EI  # noqa: E402
import smart_move_event_records as ER  # noqa: E402
import smart_move_pipeline as PL  # noqa: E402
from test_snapshot_pairs import B_AT, cal, est, records  # noqa: E402

UTC = timezone.utc
EVENT = "2026-11-12"          # AMC: release 21:00Z, window 11-10 21:00Z .. cutoff 11-11 21:00Z; records() observes it at 11-10 21:40Z
IN_WINDOW = B_AT + timedelta(hours=2)        # 2026-11-11 00:00Z


def pred(at, edate=EVENT, evid=None, digest="d" * 64, sym="AAA", version=1):
    return {"symbol": sym, "earnings_date": edate, "captured_at": at.isoformat() if at else None, "digest": digest, "version": version, "engine": {"continuation": {"signal": "BUY"}},
            "evidence": evid if evid is not None else [{"kind": "ohlc", "symbol": sym, "retrieved_at": (IN_WINDOW - timedelta(minutes=5)).isoformat(), "raw_sha256": "a" * 64}]}


def test_an_in_window_prediction_with_prior_evidence_is_admissible_and_bound_to_the_event_version():
    r = PL.validate_prediction(pred(IN_WINDOW), records())
    assert r["valid"] is True and r["reasons"] == [] and r["event_id"].startswith("AAA|") and r["event_version"] == 1 and r["signals"] == {"continuation": "BUY"}


def test_cutoff_window_and_lookahead_violations_are_reported_not_repaired():
    st = records()
    assert PL.validate_prediction(pred(datetime(2026, 11, 11, 22, 0, tzinfo=UTC)), st)["reasons"] == ["CAPTURED_AFTER_CUTOFF"]
    assert "EVENT_NOT_RECORDED_AT_CAPTURE" in PL.validate_prediction(pred(datetime(2026, 11, 10, 21, 10, tzinfo=UTC), evid=[]), st)["reasons"]
    early = records(observed=datetime(2026, 11, 9, 12, 0, tzinfo=UTC), payload_at=datetime(2026, 11, 9, 11, 0, tzinfo=UTC))
    assert "CAPTURED_BEFORE_WINDOW" in PL.validate_prediction(pred(datetime(2026, 11, 9, 13, 0, tzinfo=UTC), evid=[{"kind": "ohlc", "retrieved_at": "2026-11-09T12:30:00+00:00"}]), early)["reasons"]
    look = pred(IN_WINDOW, evid=[{"kind": "uw_news", "symbol": "AAA", "retrieved_at": (IN_WINDOW + timedelta(minutes=1)).isoformat(), "raw_sha256": "b" * 64}])
    assert PL.validate_prediction(look, st)["reasons"] == ["EVIDENCE_RETRIEVED_AFTER_CAPTURE"]
    assert set(PL.validate_prediction(pred(IN_WINDOW, digest=None, evid=[]), st)["reasons"]) == {"NO_DIGEST", "NO_EVIDENCE_LISTED"}
    assert PL.validate_prediction(pred(None), st)["reasons"] == ["NO_CAPTURED_AT"]


def test_a_reschedule_or_removal_after_capture_invalidates_the_prediction_for_that_date():
    st = records()
    p = pred(IN_WINDOW)
    assert PL.validate_prediction(p, st)["valid"] is True
    moved = ER.build_observation("AAA", ER.calobs_from_rows(cal("AAA", "2026-11-17", B_AT + timedelta(hours=5)))["AAA"], B_AT + timedelta(hours=6))
    assert st.observe(moved)["action"] == "RESCHEDULED"
    r = PL.validate_prediction(p, st)
    assert r["valid"] is False and r["reasons"] == ["EVENT_DATE_CHANGED_AFTER_CAPTURE"] and r["event_version"] == 1                   # still bound to the version it was made on
    st2 = records()
    st2.observe_snapshot([], observed=B_AT + timedelta(hours=3), window=(datetime(2026, 11, 1).date(), datetime(2026, 11, 30).date()), complete=True)
    assert "EVENT_REMOVED_AFTER_CAPTURE" in PL.validate_prediction(p, st2)["reasons"]


def test_capture_plan_lists_what_to_capture_now_what_was_missed_and_what_waits():
    st = records()
    now = PL.capture_plan(st, IN_WINDOW)
    assert [r["symbol"] for r in now["capture_now"]] == ["AAA"] and now["capture_now"][0]["minutes_to_cutoff"] == 21 * 60 and now["missed"] == []
    assert PL.capture_plan(st, IN_WINDOW, [pred(IN_WINDOW)])["capture_now"] == []                                                      # already has an admissible prediction
    assert [r["symbol"] for r in PL.capture_plan(st, datetime(2026, 11, 12, 6, 0, tzinfo=UTC))["missed"]] == ["AAA"]
    early = records(observed=datetime(2026, 11, 9, 12, 0, tzinfo=UTC), payload_at=datetime(2026, 11, 9, 11, 0, tzinfo=UTC))
    assert PL.capture_plan(early, datetime(2026, 11, 9, 13, 0, tzinfo=UTC))["waiting"][0]["window_opens_in_hours"] > 0


def test_completeness_emits_only_known_reason_codes_and_counts_them():
    st = records()
    head = st.as_of(st.event_ids()[0], IN_WINDOW)
    r = PL.event_completeness(head)
    assert not r["complete"] and {"SNAPSHOT_PAIR_MISSING", "NEWS_NOT_COLLECTED", "PRICE_BARS_NOT_SAVED", "FLOW_NOT_COLLECTED"} <= set(r["missing"]) and r["warnings"] == ["RELEASE_TIME_NOT_VERIFIED"]
    assert r["prediction_ready"] and r["pairing_ready"] and set(r["missing"]) <= PL.REASONS
    full = PL.event_completeness(head, pair={"state": "PAIRED"}, news={"news_state": "HAS_VALID_HEADLINES"}, bars=[1], flow_records=[1])
    assert full["complete"] is True and full["missing"] == []
    unk = records(quarter=None)
    assert "QUARTER_UNKNOWN" in PL.event_completeness(unk.as_of(unk.event_ids()[0], IN_WINDOW))["missing"]
    for state, code in (("FAILED_RESPONSES_ONLY", "NEWS_FAILED"), ("EMPTY_PROVIDER_RESPONSE", "NEWS_EMPTY_RESPONSE"), ("NO_VALID_HEADLINES", "NEWS_NO_VALID_HEADLINE")):
        assert code in PL.event_completeness(head, news={"news_state": state})["missing"]
    s = PL.completeness_summary([r, full])
    assert s["events"] == 2 and s["complete"] == 1 and s["missing_by_code"]["NEWS_NOT_COLLECTED"] == 1


def test_snapshot_scheduling_is_event_level_deduplicated_and_flags_a_missing_quarter():
    st = records()
    at_b = B_AT + timedelta(hours=1)
    t = PL.schedule_snapshots(st, [], at_b)
    assert [(x["stage"], x["event_id"], x["ending_fiscal_quarter"], x["pairing_possible"]) for x in t["tasks"]] == [("B", st.event_ids()[0], "2026-09-30", True)]
    assert PL.schedule_snapshots(st, [est("AAA", at_b - timedelta(minutes=5), 1, 1)], at_b)["tasks"] == []                         # satisfied: not requested again
    a_time = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)                                                                               # release-10 d: the A stage
    ta = PL.schedule_snapshots(records(observed=a_time - timedelta(hours=1), payload_at=a_time - timedelta(hours=2)), [], a_time)
    assert [x["stage"] for x in ta["tasks"]] == ["A"]
    unk = PL.schedule_snapshots(records(quarter=None, observed=a_time - timedelta(hours=1)), [], a_time)
    assert unk["tasks"][0]["ending_fiscal_quarter"] == "QUARTER_NEEDED_BEFORE_PAIRING" and unk["tasks"][0]["pairing_possible"] is False
    assert PL.schedule_snapshots(st, [], datetime(2026, 11, 1, 0, 0, tzinfo=UTC))["tasks"] == []                                  # not an A/B moment


def test_evidence_preservation_allows_appends_and_detects_edits_truncation_and_deletion(tmp_path):
    f = tmp_path / "e.jsonl"
    f.write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
    pay = tmp_path / "p"
    pay.mkdir()
    (pay / "x.json.gz").write_bytes(b"abc")
    old = EI.build_manifest([f, pay])
    with f.open("a", encoding="utf-8") as fh:
        fh.write('{"a":3}\n')
    c = EI.compare(old, EI.build_manifest([f, pay]))
    assert c["ok"] and c["appended"] == [str(f)]
    f.write_text('{"a":1}\n{"a":9}\n{"a":3}\n', encoding="utf-8")                                                                   # an edited old line
    assert EI.compare(old, EI.build_manifest([f, pay]))["changed"] == [str(f)]
    f.write_text('{"a":1}\n', encoding="utf-8")
    assert EI.compare(old, EI.build_manifest([f, pay]))["truncated"] == [str(f)]
    (pay / "x.json.gz").unlink()
    assert EI.compare(old, EI.build_manifest([f]))["missing"] == [str(pay / "x.json.gz")]
    m = EI.save(old, tmp_path / "m.json")
    with pytest.raises(FileExistsError):
        EI.save(old, m)
