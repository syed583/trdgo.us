"""News review API: queue, human labels (append-only), disputes, progress, validation gate (never enabled). Synthetic headlines only."""
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).parent))
import smart_move_news_audit as NA  # noqa: E402
import smart_move_news_review as NR  # noqa: E402
import smart_move_news_review_routes as RT  # noqa: E402
from test_phase13_audits import EV, head, rec  # noqa: E402

app = FastAPI()
app.include_router(RT.router)
client = TestClient(app)
U = "/api/smart-move/news-review"


@pytest.fixture()
def review_dir(tmp_path, monkeypatch):
    items = [head("Acme Robotics ships units"), head("Acme Robotics ships units"), head("Chip makers rally on demand"), head("Acme Robotics old note", hours_before_cut=24 * 9), head("Acme Robotics blog", src="Some Blog"), head("")]
    audit = NA.audit([EV], [rec(items)], {"ACME": "ACME ROBOTICS INC"})
    q = NR.build_queue(audit, reviewable_only=False)
    (tmp_path / "queue.json").write_text(json.dumps(q), encoding="utf-8")
    monkeypatch.setenv("SMART_MOVE_NEWS_REVIEW_DIR", str(tmp_path))
    return tmp_path, q


def test_the_queue_shows_every_automated_class_with_provenance_and_marks_which_can_be_reviewed(review_dir):
    _, q = review_dir
    r = client.get(U + "/queue").json()
    assert r["status"] == "OK" and r["news_guidance"]["enabled"] is False and r["gate"]["enabled"] is False
    assert r["by_audit_class"] == {"VALID": 1, "DUPLICATE": 1, "IRRELEVANT": 1, "STALE": 1, "UNVERIFIED": 1, "INVALID": 1}
    by = {x["audit_class"]: x for x in r["rows"]}
    assert by["VALID"]["reviewable"] and by["VALID"]["human_state"] == "UNLABELLED" and by["VALID"]["source_credibility"] == "TRUSTED_WIRE" and by["VALID"]["company"] == "ACME ROBOTICS INC"
    assert by["UNVERIFIED"]["source_credibility"] == "UNKNOWN_SOURCE" and by["VALID"]["published"] and by["VALID"]["retrieved_at"] and by["VALID"]["age_hours_at_cutoff"] == 30.0
    assert all(by[c]["reviewable"] is False and by[c]["human_state"] == "NOT_REVIEWABLE" for c in ("STALE", "DUPLICATE", "INVALID"))
    assert r["progress"]["reviewable"] == 3 and r["progress"]["unlabelled"] == 3 and r["progress"]["percent_reviewed"] == 0.0


def test_labels_are_appended_validated_and_disputes_are_detected(review_dir):
    d, q = review_dir
    valid = next(x for x in q if x["audit_class"] == "VALID")["item_id"]
    post = lambda **b: client.post(U + "/label", json=b)  # noqa: E731
    assert post(item_id=valid, label="BULLISH", reviewer="ann").status_code == 422 and post(item_id=valid, label="POSITIVE", reviewer=" ").status_code == 422
    assert post(item_id="nope", label="POSITIVE", reviewer="ann").status_code == 404
    stale = next(x for x in q if x["audit_class"] == "STALE")["item_id"]
    assert post(item_id=stale, label="NEUTRAL", reviewer="ann").status_code == 404                                       # a stale headline is not in the reviewable queue
    ok = post(item_id=valid, label="POSITIVE", reviewer="ann").json()
    assert ok["status"] == "RECORDED" and ok["human_state"] == "LABELLED" and ok["news_guidance"]["enabled"] is False
    assert post(item_id=valid, label="NEGATIVE", reviewer="bob").json()["human_state"] == "DISPUTED"
    r = client.get(U + "/queue", params={"state": "DISPUTED"}).json()
    assert r["total"] == 1 and r["rows"][0]["human_labels"] == {"ann": "POSITIVE", "bob": "NEGATIVE"} and r["rows"][0]["human_consensus"] is None and r["progress"]["disputed"] == 1
    assert post(item_id=valid, label="NEGATIVE", reviewer="ann").json()["human_state"] == "LABELLED"                     # ann revises; the old line is kept
    assert len((d / "labels.jsonl").read_text(encoding="utf-8").splitlines()) == 3
    g = client.get(U + "/queue").json()
    assert g["gate"]["labelled"] == 1 and g["gate"]["may_be_considered_for_scoring"] is False and g["gate"]["status"] == "DISABLED_UNTIL_VALIDATED_AND_APPROVED" and g["progress"]["reviewers"] == ["ann", "bob"]


def test_filters_and_validation(review_dir):
    g = lambda **q: client.get(U + "/queue", params=q)  # noqa: E731
    assert g(audit_class="STALE").json()["total"] == 1 and g(q="robotics").json()["total"] >= 3 and g(state="LABELLED").json()["total"] == 0
    assert g(audit_class="BAD").status_code == 422 and g(limit=1000).status_code == 422 and g(limit=2).json()["rows"].__len__() == 2


def test_missing_or_unreadable_queue_is_an_explicit_state_and_labels_cannot_be_written(tmp_path, monkeypatch):
    monkeypatch.delenv("SMART_MOVE_NEWS_REVIEW_DIR", raising=False)
    assert client.get(U + "/queue").json()["status"] == "NO_QUEUE" and client.post(U + "/label", json={"item_id": "x", "label": "POSITIVE", "reviewer": "a"}).status_code == 409
    monkeypatch.setenv("SMART_MOVE_NEWS_REVIEW_DIR", str(tmp_path))
    assert client.get(U + "/queue").json()["status"] == "NO_QUEUE"
    (tmp_path / "queue.json").write_text("{bad", encoding="utf-8")
    assert client.get(U + "/queue").json()["status"] == "UNREADABLE"
    assert not (tmp_path / "labels.jsonl").exists()


def test_the_review_routes_cannot_reach_scoring_or_a_provider():
    src = (ROOT / "backend" / "smart_move_news_review_routes.py").read_text(encoding="utf-8")
    imports = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
    assert not [ln for ln in imports if any(w in ln for w in ("requests", "httpx", "urllib", "socket", "smart_move_live", "smart_move_v2", "scorer", "smart_move_outcomes"))]
    assert '"enabled": True' not in src
