"""Interim/final V2 evidence audit: read-only snapshot semantics, root-cause classification, two-page PDF with the right banner."""
import glob
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_v2_interim as I  # noqa: E402
import smart_move_v2_interim_pdf as P  # noqa: E402


def test_snapshot_is_a_consistent_copy_and_never_touches_the_original(tmp_path):
    src = tmp_path / "run" / "store"
    src.mkdir(parents=True)
    (src / "v2_evidence.jsonl").write_text(json.dumps({"a": 1}) + "\n" + json.dumps({"a": 2}) + "\n" + '{"a": 3, "trunc')            # the collector is mid-write
    (src / "KILL").write_text("x")
    (tmp_path / "run" / "report.json").write_text(json.dumps({"collected_symbols": []}))
    before = (src / "v2_evidence.jsonl").read_bytes()
    dest = tmp_path / "snap"
    I.snapshot(tmp_path / "run", dest)
    assert [json.loads(x) for x in (dest / "v2_evidence.jsonl").read_text().splitlines()] == [{"a": 1}, {"a": 2}] and not (dest / "KILL").exists()
    assert (src / "v2_evidence.jsonl").read_bytes() == before and (src / "KILL").exists()


def test_root_causes_separate_absence_filtering_failure_history_and_semantics():
    f = lambda name, reason=None: {"factor": name, "reason": reason}  # noqa: E731
    ev = {"uw_flow_alerts": {"rows": 100}, "uw_analysts": {"rows": 5}, "uw_estimates": {"rows": 1}}
    assert I.classify_missing(f("NEWS_GUIDANCE"), {}, "A", ev, set())[0] == "UNVERIFIED_SEMANTICS"
    assert I.classify_missing(f("OPTIONS_FLOW", "TOO_FEW"), {}, "A", ev, set())[0] == "FILTERED_BY_RULE"
    assert I.classify_missing(f("OPTIONS_FLOW", "TOO_FEW"), {}, "A", {"uw_flow_alerts": {"rows": 0}}, set())[0] == "GENUINE_ABSENCE"
    assert I.classify_missing(f("OPTIONS_FLOW", "NOT_COLLECTED"), {}, "A", {}, {"uw_flow_alerts"})[0] == "API_FAILURE"
    assert I.classify_missing(f("OPTIONS_FLOW", "NOT_COLLECTED"), {}, "A", {}, set())[0] == "NOT_COLLECTED_YET"
    assert I.classify_missing(f("ANALYST", "NO_PRIOR_CONSENSUS_SNAPSHOT_AND_NO_ACTIONS"), {}, "A", ev, set())[0] == "INSUFFICIENT_HISTORY"
    assert I.classify_missing(f("F3", "V1_FACTOR_MISSING"), {}, "A", ev, set())[0] == "V1_EVIDENCE_GAP"


def test_pdf_renders_in_two_pages_with_interim_or_final_banner_from_the_real_saved_record(tmp_path):
    files = sorted(glob.glob(r"C:\Users\initi\SmartMoveRun\audits_final\interim_v2_audit_*.json"))
    if not files:
        pytest.skip("no saved audit record on this machine")
    rec = json.loads(Path(files[-1]).read_text(encoding="utf-8"))
    from pypdf import PdfReader
    for title in ("INTERIM AUDIT - COLLECTION STILL IN PROGRESS", "FINAL EVIDENCE AUDIT - COLLECTION COMPLETE"):
        out = P.render(dict(rec, title=title), tmp_path / (title[:5] + ".pdf"))
        pg = PdfReader(out["pdf"]).pages
        text = "\n".join(p.extract_text() for p in pg)
        assert len(pg) == 2 and title in text and "No score is normalised" in text.replace("\n", " ")
