"""End-to-end checks on the REAL saved evidence report (built offline from the saved V1, pilot, V2-run and news stores). Skipped when the report folder is not on this machine, so the suite stays portable.
Everything asserted here is about saved evidence; nothing is synthetic and nothing is fetched."""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

REAL = Path(r"C:\Users\initi\SmartMoveRun\phase13_quality_report_real")
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.skipif(not (REAL / "quality_report.json").exists(), reason="real quality report not present on this machine")


@pytest.fixture(scope="module")
def rep():
    return json.loads((REAL / "quality_report.json").read_text(encoding="utf-8"))


def _dt(x):
    return datetime.fromisoformat(x)


def test_the_real_report_is_labelled_as_saved_evidence_and_never_synthetic(rep):
    assert rep["synthetic"] is False and rep["source"] == "SAVED_EVIDENCE" and rep["label"] == "Saved evidence - not live signals" and "SYNTHETIC" not in json.dumps(rep["notes"]).upper().replace("NOT SYNTHETIC", "")
    assert any("RECONSTRUCTED" in n for n in rep["notes"]) and any("never a positive or negative" in n for n in rep["notes"])


def test_source_stores_were_only_read(rep):
    integ = json.loads((REAL / "source_integrity.json").read_text(encoding="utf-8"))
    assert integ["ok"] is True and integ["missing"] == [] and integ["changed"] == [] and integ["truncated"] == [] and integ["files_checked"] > 1000


def test_event_counts_reconcile_across_every_evidence_dimension(rep):
    n = rep["summary"]["events"]
    assert n == len(rep["events"]) == 1067
    for dim, counts in rep["summary"]["evidence_classes"].items():
        assert sum(counts.values()) == n, dim
        assert set(counts) <= {"VERIFIED", "UNVERIFIED", "MISSING", "INELIGIBLE"}, dim
    assert sum(rep["summary"]["by_pairing_state"].values()) == n and sum(rep["summary"]["by_capture_status"].values()) == n
    assert sum(rep["summary"]["news"]["by_news_state"].values()) == n and sum(rep["summary"]["outcomes"]["by_state"].values()) == n


def test_fiscal_quarters_are_known_only_with_source_and_before_the_cutoff(rep):
    known = [e for e in rep["events"] if e["quarter_state"] == "KNOWN"]
    assert len(known) == 121
    for e in known:
        assert e["quarter_payload_sha256"] and e["quarter_retrieved_at"] and e["quarter_known_before_cutoff"] is True and _dt(e["quarter_retrieved_at"]) <= _dt(e["cutoff_utc"])
    assert all(e["ending_fiscal_quarter"] == "QUARTER_UNKNOWN" for e in rep["events"] if e["quarter_state"] != "KNOWN")


def test_no_event_is_paired_and_no_outcome_is_verified_because_the_evidence_does_not_exist_yet(rep):
    assert all(e["pairing_state"] != "PAIRED" for e in rep["events"]) and rep["summary"]["evidence_classes"]["snapshot_pair"].get("VERIFIED", 0) == 0
    assert rep["summary"]["evidence_classes"]["outcome"].get("VERIFIED", 0) == 0 and rep["accuracy"]["comparable_pairs"] == 0 and rep["accuracy"]["variants"] == {} and rep["accuracy"]["claim_allowed"] is False
    assert all(e["outcome_class"] is None for e in rep["events"])                                                      # no outcome value is ever shown without bars


def test_missing_evidence_is_never_a_signal(rep):
    for e in rep["events"]:
        assert e["signal_use"].startswith("NONE")
        assert not ({"signal", "direction", "recommendation", "score"} & set(e))
        if "MISSING" in e["evidence"].values():
            assert not e["complete"]


def test_prediction_admissibility_is_point_in_time_and_counts_are_consistent(rep):
    p = rep["summary"]["predictions"]
    assert p["total"] == 384 and 0 < p["admissible"] <= p["total"] and set(p["reasons"]) <= {"EVENT_NOT_ELIGIBLE_AT_CAPTURE", "CAPTURED_AFTER_CUTOFF", "CAPTURED_BEFORE_WINDOW", "EVENT_NOT_RECORDED_AT_CAPTURE",
                                                                                              "EVIDENCE_RETRIEVED_AFTER_CAPTURE", "EVENT_DATE_CHANGED_AFTER_CAPTURE", "EVENT_REMOVED_AFTER_CAPTURE", "IDENTITY_AMBIGUOUS_AT_CAPTURE"}
    adm = [e for e in rep["events"] if e["has_admissible_prediction"]]
    assert adm and all(e["prediction_eligible"] and e["capture_status"] == "CAPTURED_ADMISSIBLE" for e in adm)


def test_release_times_are_all_nominal(rep):
    assert all(e["release_time_verified"] is False for e in rep["events"])


def test_the_review_queue_is_built_from_saved_headlines_and_starts_unlabelled(rep):
    q = json.loads((REAL / "news_review" / "queue.json").read_text(encoding="utf-8"))
    assert len(q) > 100 and {x["audit_class"] for x in q} <= {"VALID", "STALE", "DUPLICATE", "IRRELEVANT", "UNVERIFIED", "INVALID"}
    assert all(x["labels"] == [] for x in q) and not (REAL / "news_review" / "labels.jsonl").exists()                       # nothing was labelled in the real review folder
    assert sum(1 for x in q if x["reviewable"]) == sum(1 for x in q if x["audit_class"] in ("VALID", "UNVERIFIED", "IRRELEVANT"))
