"""Phase 5/6 offline: the evidence-quality bundle, its read-only API and the frontend/backend contract. Synthetic data only, always labelled synthetic."""
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_quality_report as QR  # noqa: E402
import smart_move_quality_routes as QRT  # noqa: E402
import smart_move_quality_synthetic as SYN  # noqa: E402

app = FastAPI()
app.include_router(QRT.router)
client = TestClient(app)
BUNDLE = SYN.bundle()


@pytest.fixture()
def report(tmp_path, monkeypatch):
    p = tmp_path / "q.json"
    p.write_text(json.dumps(BUNDLE), encoding="utf-8")
    monkeypatch.setenv("SMART_MOVE_QUALITY_REPORT", str(p))
    return p


def test_the_synthetic_bundle_is_labelled_and_exercises_every_state_the_page_renders():
    assert BUNDLE["synthetic"] is True and BUNDLE["source"] == "SYNTHETIC_FIXTURE" and BUNDLE["label"] == "SYNTHETIC EXAMPLE DATA - not market data"
    ev = {e["symbol"]: e for e in BUNDLE["events"]}
    assert len(ev) == 7 and ev["SYN1"]["pairing_state"] == "PAIRED" and ev["SYN1"]["outcome_class"] == "UP" and ev["SYN1"]["has_admissible_prediction"] is True
    assert ev["SYN2"]["quarter_state"] == "QUARTER_UNKNOWN" and "QUARTER_UNKNOWN" in ev["SYN2"]["missing"] and ev["SYN3"]["prediction_eligible"] is False
    assert ev["SYN4"]["outcome_state"] == "QUARANTINED_CORPORATE_ACTION" and ev["SYN5"]["news_state"] == "EMPTY_PROVIDER_RESPONSE" and ev["SYN6"]["outcome_state"] == "PENDING_REACTION_SESSION_NOT_CLOSED"
    assert ev["SYN7"]["has_admissible_prediction"] is False and ev["SYN7"]["outcome_state"] == "OUTCOME_COMPUTABLE"                                  # an outcome exists but its prediction was captured too late
    assert all(e["release_time_verified"] is False for e in BUNDLE["events"]) and all("RELEASE_TIME_NOT_VERIFIED" in e["warnings"] for e in BUNDLE["events"])


def test_the_accuracy_section_separates_verified_from_unverified_and_makes_no_claim():
    acc = BUNDLE["accuracy"]
    assert acc["comparable_pairs"] == 1 and acc["variants"]["continuation"]["decided_n"] == 1 and acc["claim_allowed"] is False and acc["claim_text"].startswith("NO ACCURACY CLAIM")
    assert acc["exclusions"] == {"PREDICTION_NOT_ADMISSIBLE:CAPTURED_AFTER_CUTOFF": 1, "OUTCOME_NOT_VERIFIED:QUARANTINED_CORPORATE_ACTION": 1}
    assert acc["outcomes_by_verification"]["VERIFIED"] == 2 and any(k.startswith("UNVERIFIED:") for k in acc["outcomes_by_verification"])


def test_summary_endpoint_passes_provenance_through_and_is_read_only(report):
    r = client.get("/api/smart-move/quality/summary").json()
    assert r["status"] == "OK" and r["synthetic"] is True and r["label"].startswith("SYNTHETIC") and r["read_only"] is True and r["summary"]["events"] == 7 and r["accuracy"]["claim_allowed"] is False
    assert client.post("/api/smart-move/quality/summary").status_code == 405 and client.delete("/api/smart-move/quality/events").status_code == 405


def test_events_endpoint_filters_paginates_and_validates(report):
    g = lambda **q: client.get("/api/smart-move/quality/events", params=q).json()  # noqa: E731
    assert g()["total"] == 7 and g(quarter_state="QUARTER_UNKNOWN")["rows"][0]["symbol"] == "SYN2" and g(eligible="false")["total"] == 1 and g(pairing="PAIRED")["total"] == 1
    assert g(news_state="EMPTY_PROVIDER_RESPONSE")["rows"][0]["symbol"] == "SYN5" and g(outcome_state="QUARANTINED_CORPORATE_ACTION")["total"] == 1 and g(missing="QUARTER_UNKNOWN")["total"] == 1
    assert g(q="delta")["rows"][0]["symbol"] == "SYN4" and g(limit=2)["rows"].__len__() == 2 and g(limit=2, offset=6)["rows"].__len__() == 1 and g()["synthetic"] is True
    assert client.get("/api/smart-move/quality/events", params={"limit": 9999}).status_code == 422 and client.get("/api/smart-move/quality/events", params={"quarter_state": "BAD"}).status_code == 422


def test_missing_and_unreadable_reports_are_explicit_states_not_errors(tmp_path, monkeypatch):
    monkeypatch.delenv("SMART_MOVE_QUALITY_REPORT", raising=False)
    r = client.get("/api/smart-move/quality/summary")
    assert r.status_code == 200 and r.json()["status"] == "NO_REPORT"
    monkeypatch.setenv("SMART_MOVE_QUALITY_REPORT", str(tmp_path / "absent.json"))
    assert client.get("/api/smart-move/quality/events").json() == {"status": "NO_REPORT", "detail": "The configured evidence-quality report does not exist yet.", "rows": [], "total": 0}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("SMART_MOVE_QUALITY_REPORT", str(bad))
    assert client.get("/api/smart-move/quality/summary").json()["status"] == "UNREADABLE"
    bad.write_text(json.dumps({"schema": "other", "events": []}), encoding="utf-8")
    assert client.get("/api/smart-move/quality/events").json()["status"] == "UNREADABLE"


def test_the_report_writer_never_overwrites(tmp_path):
    p = QR.write(BUNDLE, tmp_path / "out" / "q.json")
    assert json.loads(p.read_text(encoding="utf-8"))["schema"] == QR.SCHEMA
    with pytest.raises(FileExistsError):
        QR.write(BUNDLE, p)


def test_every_bundle_event_field_the_page_reads_exists():
    page = (ROOT / "frontend" / "src" / "pages" / "SmartMoveQualityPage.tsx")
    if not page.exists():
        pytest.skip("page not present")
    src = page.read_text(encoding="utf-8")
    import re
    used = set(re.findall(r"\be\.([a-z_]+)\b", src))
    have = set(BUNDLE["events"][0])
    assert used - have == set(), sorted(used - have)
