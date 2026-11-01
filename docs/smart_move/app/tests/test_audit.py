"""Audit record + two-page PDF: page limit, content equals the record, board reconciliation, append-only, retrieval by id, honest NOT VERIFIED/NOT AVAILABLE, critical health -> BLOCKED. Synthetic saved data; no provider."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_audit as A  # noqa: E402
import smart_move_audit_pdf as PDF  # noqa: E402
import smart_move_local_server as srv  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)


def eng(sig, bull, bear, codes):
    return {"signal": sig, "bull": bull, "bear": bear, "available": 40, "reason_codes": codes}


def make_run(tmp_path, kill=True, n=12):
    d = tmp_path / "out" / "run_x"
    (d / "store").mkdir(parents=True)
    if kill:
        (d / "store" / "KILL").write_text("x")
    res = []
    for i in range(n):
        sym = "S%02d" % i
        c = eng("BUY", 20, 1, ["RULE"]) if i == 0 else eng("SELL", 1, 19, ["RULE"]) if i == 1 else eng("NO_TRADE", 5, 5, ["BELOW_MARGIN"])
        k = eng("SELL", 1, 21, ["RULE"]) if i == 0 else eng("NO_TRADE", 5, 5, ["BELOW_MARGIN"])
        res.append({"symbol": sym, "company": sym, "earnings_date": "2026-11-02", "kind": "AMC", "calendar_status": "VENDOR_CONFIRMED", "available_points": 40, "missing_factors": [], "blocked_by": [],
                    "continuation": c, "contrarian": k, "capture_ts": "2026-10-31T21:30:00+00:00", "evidence": [{"kind": "ohlc", "symbol": sym, "retrieved_at": "2026-10-31T21:20:00+00:00", "raw_sha256": "ab" * 32, "mode": "LIVE"}]})
    rep = {"status": "OK", "universe": n, "attempts_total": 50, "cap_total": 100, "requests_by_provider": {"UNUSUAL_WHALES": 50}, "stop_events": [], "errors": [], "results": res, "not_evaluated": [],
           "started_utc": "2026-10-31T21:00:00+00:00", "updated_utc": "2026-10-31T21:40:00+00:00"}
    (d / "audit_report.json").write_text(json.dumps(rep))
    return tmp_path / "out"


def pages(path):
    from pypdf import PdfReader
    return PdfReader(str(path)).pages


def test_record_pdf_two_pages_and_content_equals_the_record(tmp_path):
    root = make_run(tmp_path)
    rec = A.build(root, now=NOW, saved_rows=[], plan_fn=None)
    assert rec["events"]["eligible"] == 12 == rec["events"]["evaluated"] and rec["signals"]["continuation"]["BUY"] == 1 and rec["signals"]["continuation"]["SELL"] == 1 and rec["signals"]["contrarian"]["SELL"] == 1
    saved = A.save(rec, tmp_path / "audits")
    out = PDF.render(rec, saved["base"] + ".pdf")
    pg = pages(out["pdf"])
    assert len(pg) == 2 == out["pages"]
    text = "\n".join(p.extract_text() for p in pg)
    for must in (rec["run_id"], rec["audit_hash"][:16], "EARNINGS PREDICTION AUDIT", "V1 (frozen)", "V2 (experimental)", "S00", "S01", "NOT VERIFIED", "INCONCLUSIVE", "ENGAGED", "PARTIAL", "NOT AVAILABLE"):
        assert must in text, must
    assert "page 1 of 2" in text and "page 2 of 2" in text


def test_hash_is_deterministic_and_changes_with_content(tmp_path):
    root = make_run(tmp_path)
    a, b = A.build(root, now=NOW, saved_rows=[]), A.build(root, now=datetime(2026, 11, 1, 13, tzinfo=UTC), saved_rows=[])
    assert a["audit_hash"] == b["audit_hash"]                                         # the generation time is not part of the hash
    rep = json.loads((root / "run_x" / "audit_report.json").read_text())
    rep["results"][0]["continuation"]["signal"] = "SELL"
    (root / "run_x" / "audit_report.json").write_text(json.dumps(rep))
    assert A.build(root, now=NOW, saved_rows=[])["audit_hash"] != a["audit_hash"]


def test_nothing_is_fabricated_when_data_is_missing(tmp_path):
    rec = A.build(make_run(tmp_path), now=NOW, saved_rows=[])
    assert rec["outcomes"]["graded"] == 0 and "NOT AVAILABLE" in rec["outcomes"]["accuracy"] and rec["v1_vs_v2"]["verdict"].startswith("INCONCLUSIVE")
    assert rec["operations"]["backup"].startswith("NOT VERIFIED") and "NOT VERIFIED" in rec["provider"]["subscription"] and rec["tests"] == {"recorded": "NOT VERIFIED"}
    assert all(f["stocks_with_data"] is None for f in rec["v2"]["factors"]) and rec["v2"]["status"].startswith("EXPERIMENTAL")
    assert {f["factor"]: f["data_status"] for f in rec["v2"]["factors"]}["NEWS_GUIDANCE"] == "BLOCKED"
    assert A.build(tmp_path / "empty", now=NOW)["ok"] is False


def test_a_disengaged_kill_switch_is_critical_and_blocks_the_verdict(tmp_path):
    rec = A.build(make_run(tmp_path, kill=False), now=NOW, saved_rows=[])
    assert rec["verdict"] == "BLOCKED" and rec["operations"]["kill_switch"] == "NOT ENGAGED" and any(d["severity"] == "CRITICAL" for d in rec["defects"])
    out = PDF.render(rec, tmp_path / "b.pdf")
    assert out["pages"] == 2 and "BLOCKED" in pages(out["pdf"])[1].extract_text()


def test_audits_and_pdfs_are_append_only_and_listed(tmp_path):
    rec = A.build(make_run(tmp_path), now=NOW, saved_rows=[])
    s = A.save(rec, tmp_path / "audits")
    with pytest.raises(FileExistsError):
        A.save(rec, tmp_path / "audits")
    PDF.render(rec, s["base"] + ".pdf")
    with pytest.raises(FileExistsError):
        PDF.render(rec, s["base"] + ".pdf")
    ls = A.list_audits(tmp_path / "audits")
    assert len(ls) == 1 and ls[0]["pdf"] is True and ls[0]["run_id"] == "run_x"


def test_very_long_defect_lists_still_fit_two_pages(tmp_path):
    rec = A.build(make_run(tmp_path), now=NOW, saved_rows=[])
    rec["defects"] += [{"severity": "LOW", "component": "tests", "defect": "failure number %d " % i + "x" * 90, "action": "see notes " + "y" * 60} for i in range(40)]
    out = PDF.render(rec, tmp_path / "long.pdf")
    assert out["pages"] == 2 and out["list_limit"] < 8 and len(pages(out["pdf"])) == 2


def test_routes_generate_list_retrieve_and_refuse_path_tricks(tmp_path, monkeypatch):
    root = make_run(tmp_path)
    monkeypatch.setenv("SMART_MOVE_RUN_ROOT", str(root))
    monkeypatch.setenv("SMART_MOVE_AUDIT_DIR", str(tmp_path / "audits"))
    monkeypatch.setenv("SMART_MOVE_V2_DIR", str(tmp_path / "v2"))
    monkeypatch.setenv("SMART_MOVE_BACKUP_DIR", str(tmp_path / "bk"))
    monkeypatch.setenv("SMART_MOVE_TRACKING_DIR", str(tmp_path / "trk"))
    c = TestClient(srv.app)
    g = c.post("/api/smart-move/audits/generate").json()
    assert g["ok"] and g["pages"] == 2 and g["verdict"] in ("PARTIAL", "BLOCKED")
    ls = c.get("/api/smart-move/audits").json()["audits"]
    assert len(ls) == 1
    r = c.get("/api/smart-move/audits/%s.pdf" % ls[0]["file"])
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert c.get("/api/smart-move/audits/%s.json" % ls[0]["file"]).json()["audit_hash"] == ls[0]["audit_hash"]
    assert c.get("/api/smart-move/audits/..%2Fsecret.json").status_code == 404 and c.get("/api/smart-move/audits/other.pdf").status_code == 404
    assert c.get("/api/smart-move/v2").json()["available"] is False
