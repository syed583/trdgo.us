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


def test_audit_with_v2_results_and_graded_outcomes_stays_two_pages_and_never_claims_accuracy(tmp_path):
    root = make_run(tmp_path)
    fac = [{"factor": f, "available": True, "bull": 0, "bear": 0, "max": 10} for f in ("F3", "F5")]
    v2 = lambda sig, disp: {"signal": sig, "display_status": disp, "available_points": 55, "directional_available_points": 45, "missing": [{"factor": "ANALYST"}], "factors": fac + [{"factor": "ANALYST", "available": False, "bull": 0, "bear": 0, "max": 15}]}  # noqa: E731
    events = [{"symbol": "S%02d" % i, "earnings_date": "2026-11-02", "v2": {"continuation": v2("NO_TRADE", "DATA_INCOMPLETE"), "contrarian": v2("NO_TRADE", "DATA_INCOMPLETE")}} for i in range(12)]
    f = tmp_path / "v2.json"
    f.write_text(json.dumps({"events": events, "audit_hash": "h", "source_run": "run_x"}))
    outs = [{"symbol": "S00", "earnings_date": "2026-11-02", "move_pct": 6.0, "mode": "LIVE"}, {"symbol": "S01", "earnings_date": "2026-11-02", "move_pct": -4.0, "mode": "LIVE"},
            {"symbol": "S05", "earnings_date": "2026-11-02", "move_pct": 1.0, "mode": "SIMULATION"}]
    rec = A.build(root, now=NOW, saved_rows=[], v2_file=f, outcomes=outs)
    assert rec["signals_v2"]["continuation"] == {"DATA_INCOMPLETE": 12} and rec["outcomes"]["graded"] == 2                      # the simulated outcome is not counted as real
    d = rec["outcomes"]["v1_descriptive"]["continuation"]
    assert d["decided"] == 2 and d["hit_rate"] == 1.0 and d["buy"] == {"n": 1, "hits": 1} and rec["outcomes"]["claim_allowed"] is False
    assert rec["v1_vs_v2"]["verdict"].startswith("INCONCLUSIVE") and rec["v2"]["evidence_complete_stocks"] == 0 and any("V2 data" == x["component"] for x in rec["defects"])
    out = PDF.render(rec, tmp_path / "v2.pdf")
    text = "\n".join(p.extract_text() for p in pages(out["pdf"]))
    assert out["pages"] == 2 and "V2 Continuation (exp.)" in text and "V1 descriptive" in text and "no accuracy claim" in text.replace("\n", " ")


def test_v2_and_compare_routes_expose_explanations_eligibility_and_inconclusive_comparison(tmp_path, monkeypatch):
    root = make_run(tmp_path)
    fac = [{"factor": "F3", "available": True, "bull": 5, "bear": 0, "max": 15}]
    x = lambda: {"signal": "NO_TRADE", "display_status": "DATA_INCOMPLETE", "bull": 5, "bear": 0, "available_points": 40, "directional_available_points": 40, "missing_points": 60, "reason_codes": ["INSUFFICIENT_COVERAGE"],  # noqa: E731
                 "explanation": "only 40 of the required 60 DIRECTIONAL points have data.", "missing": [{"factor": "ANALYST", "reason": "NOT_COLLECTED", "data_status": "PARTIAL"}], "factors": fac, "validated_real_data_signal": False}
    ev = [{"symbol": "S00", "earnings_date": "2026-11-02", "capture_ts": "t", "eligibility": {"liquidity_ok": True}, "evidence": [], "v2": {"continuation": x(), "contrarian": x()}}]
    (tmp_path / "v2").mkdir()
    (tmp_path / "v2" / "v2_run_x.json").write_text(json.dumps({"events": ev, "status": "EXPERIMENTAL", "version": "v2-exp-2", "config_hash": "c", "source_run": "run_x", "as_of": "t"}))
    for k, v in (("SMART_MOVE_RUN_ROOT", root), ("SMART_MOVE_V2_DIR", tmp_path / "v2"), ("SMART_MOVE_TRACKING_DIR", tmp_path / "trk")):
        monkeypatch.setenv(k, str(v))
    c = TestClient(srv.app)
    j = c.get("/api/smart-move/v2").json()
    e = j["events"][0]
    assert j["usable_for_decisions"] is False and e["display_status"] == "DATA_INCOMPLETE" and "required 60" in e["explanation"] and e["missing_points"] == 60 and e["validated_real_data_signal"] is False
    cmp = c.get("/api/smart-move/v2/compare").json()
    assert cmp["verdict"].startswith("INCONCLUSIVE") and cmp["graded_outcomes"] == 0
