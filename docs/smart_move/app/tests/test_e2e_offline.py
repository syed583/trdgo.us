"""Phase 6: offline end-to-end checks across the whole chain, the frontend/backend contract, and the frozen-file / no-network / no-write boundaries. Synthetic data only; nothing here touches a provider."""
import ast
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).parent))
import options_flow_analysis as OF  # noqa: E402
import smart_move_quality_synthetic as SYN  # noqa: E402

UTC = timezone.utc
H = lambda p: hashlib.sha256((ROOT / p).read_bytes().replace(b"\r\n", b"\n")).hexdigest()  # noqa: E731
NEW_MODULES = ["smart_move_event_records", "smart_move_snapshot_pairs", "smart_move_pipeline", "smart_move_evidence_integrity", "smart_move_news_audit", "smart_move_news_review", "smart_move_outcome_audit",
               "smart_move_price_bars", "smart_move_accuracy", "smart_move_quality_report", "smart_move_quality_routes", "smart_move_quality_synthetic", "smart_move_phase13_audits", "options_flow_analysis"]
FORBIDDEN_IMPORTS = ("requests", "httpx", "urllib", "socket", "aiohttp", "websocket", "smart_move_live", "uw_client", "unusualwhales_service", "smart_move_v2_collect", "smart_move_collection_runner")


READERS = {"smart_move_outcome_audit": ("predictions.jsonl",)}


def imports_of(name):
    tree = ast.parse((ROOT / "backend" / (name + ".py")).read_text(encoding="utf-8"))
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            out.append(n.module or "")
    return out


def test_no_new_module_can_reach_a_network_provider_or_the_collection_runner():
    for m in NEW_MODULES:
        bad = [i for i in imports_of(m) if any(i == f or i.startswith(f + ".") for f in FORBIDDEN_IMPORTS)]
        assert not bad, (m, bad)


def test_frozen_scoring_grader_outcome_news_and_live_flow_files_are_unchanged():
    pins = {"docs/smart_move/shadow_impl/smvshadow/scorer.py": "62e09aa0ed785e2a", "backend/smart_move_v2.py": "de2d8f9612934338", "docs/smart_move/shadow_impl/smvshadow/grader.py": "70078fb2996fc7a6",
            "backend/smart_move_outcomes.py": "0572074c4d0cf81f", "backend/smart_move_news.py": "1121048777e55273"}
    for f, prefix in pins.items():
        assert H(f).startswith(prefix), f
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if head:
        for f in ("backend/uw_flow_service.py", "backend/market_flow_service.py", "backend/unusual_activity_service.py"):
            diff = subprocess.run(["git", "diff", "--name-only", "f3b55a7a~1", "HEAD", "--", f], cwd=ROOT, capture_output=True, text=True).stdout.strip()
            assert diff == "", "live Options Flow file changed: " + f                                                       # the live service files are untouched since before Phase 13


def test_no_new_module_references_prediction_outcome_kill_switch_attestation_or_credential_files():
    for m in NEW_MODULES:
        src = (ROOT / "backend" / (m + ".py")).read_text(encoding="utf-8")
        for token in ("outcome_details.jsonl", "outcome_quarantine", "predictions.jsonl", "attestation", "KILL", "load_dotenv", "API_KEY", "os.environ[", "getenv(\"UW"):
            if m in READERS and token in READERS[m]:
                assert "write_text" not in src and ".open(" not in src.replace("Path(d) / ", ""), m                      # a documented read-only loader
                continue
            assert token not in src, (m, token)


def test_whole_chain_event_to_prediction_to_pairing_to_news_to_outcome_to_accuracy():
    b = SYN.bundle()
    ev = {e["symbol"]: e for e in b["events"]}
    s1 = ev["SYN1"]
    assert (s1["prediction_eligible"], s1["quarter_state"], s1["pairing_state"], s1["news_state"], s1["has_admissible_prediction"], s1["outcome_state"], s1["outcome_class"]) == \
        (True, "KNOWN", "PAIRED", "HAS_VALID_HEADLINES", True, "OUTCOME_COMPUTABLE", "UP")
    assert s1["quarter_known_before_cutoff"] is True and s1["quarter_payload_sha256"] and s1["quarter_retrieved_at"] < s1["cutoff_utc"]
    acc = b["accuracy"]["variants"]["continuation"]
    assert (acc["compared_events"], acc["strict_hits"], acc["minimum_sample_met"]) == (1, 1, False) and b["accuracy"]["claim_allowed"] is False                    # one hit is not evidence of anything
    assert b["summary"]["predictions"] == {"total": 3, "admissible": 2, "reasons": {"CAPTURED_AFTER_CUTOFF": 1}}
    assert ev["SYN7"]["outcome_class"] == "DOWN" and b["accuracy"]["exclusions"]["PREDICTION_NOT_ADMISSIBLE:CAPTURED_AFTER_CUTOFF"] == 1                          # an outcome without an admissible prediction is not accuracy


def test_the_frontend_page_reads_only_fields_the_bundle_provides():
    src = (ROOT / "frontend" / "src" / "pages" / "SmartMoveQualityPage.tsx").read_text(encoding="utf-8")
    b = SYN.bundle()
    for key in set(re.findall(r"\bs\.summary\.([a-z_]+)", src)):
        assert key in b["summary"], "summary." + key
    for key in set(re.findall(r"\bs\.accuracy\.([a-z_]+)", src)):
        assert key in b["accuracy"], "accuracy." + key
    for key in set(re.findall(r"\bs\.summary\.news\.([a-z_]+)", src)):
        assert key in b["summary"]["news"], "summary.news." + key
    for key in set(re.findall(r"\b(?:e|open)\.([a-z_]+)\b", src)):
        if key in ("symbol", "length", "map", "key", "preventDefault", "target", "value"):
            continue
        assert key in b["events"][0] or key in ("event_id",), "event." + key
    client = (ROOT / "frontend" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    assert "/api/smart-move/quality/summary" in client and "/api/smart-move/quality/events?" in client
    assert "/smart-move/quality" in (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")


def test_the_page_always_shows_the_synthetic_label_and_has_loading_error_and_empty_states():
    src = (ROOT / "frontend" / "src" / "pages" / "SmartMoveQualityPage.tsx").read_text(encoding="utf-8")
    for needle in ("SYNTHETIC EXAMPLE DATA, not market data", "synthetic ?", "Loading the evidence-quality report", "could not be loaded", "No evidence-quality report yet", "Report unreadable",
                   "No events match these filters", "Saved evidence, not live signals", "aria-label", "role=\"alert\"", "role=\"status\""):
        assert needle in src, needle
    assert "market data" in src and "not live signals" in src.lower()


def test_options_flow_pipeline_from_rows_to_ui_state_matches_the_live_row_shape():
    now = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
    epoch = int(now.timestamp() * 1000)
    rows = [{"symbol": "SYNTH", "right": "C", "side": "ask", "premium": 80_000, "volume": 400, "open_interest": 100, "expiry": "2026-11-06", "kind": "SWEEP", "sweep": True, "epoch": epoch - 30_000, "strike": 100},
            {"symbol": "SYNTH", "right": "P", "side": None, "premium": 20_000, "volume": 50, "open_interest": 400, "expiry": "2026-11-20", "kind": "PRINT", "sweep": False, "epoch": epoch - 30_000, "strike": 95}]
    s = OF.summarize(rows, now)
    assert s["classification"] == {"BULLISH": 1, "BEARISH": 0, "NEUTRAL": 0, "UNKNOWN_SIDE": 1} and s["missing_data"]["unclassifiable_share"] == 0.5 and s["freshness"]["state"] == "LIVE"
    assert OF.ui_state({"status": "OK", "trades": rows, "delay_minutes": 0, "note": "Live option flow."}, now)["state"] == "LIVE"
    assert OF.ui_state({"status": "NO_TRADES", "trades": []}, now)["state"] == "EMPTY" and OF.ui_state(None, now)["state"] == "ERROR"
    assert OF.audit_file_pair(ROOT)["unaccepted_backend_values"] == {}


def test_preserved_evidence_manifest_detects_a_changed_pilot_store_file_if_present(tmp_path):
    import smart_move_evidence_integrity as EI
    f = tmp_path / "evidence.jsonl"
    f.write_text("a\nb\n", encoding="utf-8")
    m = EI.build_manifest([f])
    f.write_text("a\nb\nc\n", encoding="utf-8")
    assert EI.compare(m, EI.build_manifest([f]))["ok"] is True
    f.write_text("A\nb\nc\n", encoding="utf-8")
    assert EI.compare(m, EI.build_manifest([f]))["ok"] is False
