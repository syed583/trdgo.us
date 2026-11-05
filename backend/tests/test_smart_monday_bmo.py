"""Smart Money Move MONDAY_BMO_FRIDAY reader: the separate variant ledger, status and accuracy under <live>/monday_bmo_friday. Files only (no provider, no network); the frozen live view is never affected."""
import hashlib
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from earnings_monitor import smart_live as SL  # noqa: E402

UTC = timezone.utc
T = datetime(2026, 11, 13, 19, 30, tzinfo=UTC)                       # Friday 14:30 ET, variant window open
LIVE_FIX = Path(__file__).resolve().parent / "fixtures" / "smart_live"


def _canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), default=str).encode()


def write_ledger(path: Path, recs):
    prev, out = "0" * 64, []
    for i, r in enumerate(recs, 1):
        r = dict(r, seq=i, prev_sha256=prev)
        r["record_sha256"] = hashlib.sha256(_canon({k: v for k, v in r.items() if k != "record_sha256"})).hexdigest()
        prev = r["record_sha256"]
        out.append(json.dumps(r, sort_keys=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def pred(sym, res, **kw):
    base = {"schema": "smart_move.monday_bmo_friday_prediction.v1", "record_type": "PREDICTION", "variant": "MONDAY_BMO_FRIDAY", "symbol": sym, "company": sym + " Inc", "event_date": "2026-11-16", "monday_earnings_date": "2026-11-16", "session": "BMO",
            "result": res, "result_text": res, "reason": "frozen rule met", "reason_codes": [], "bull": 16, "bear": 2, "coverage_points": 30, "coverage_max": 40, "liquidity": "PASS", "adv20_usd": 5e7, "missing_factors": [["F7", "NOT_CAPTURED_LIVE_IN_WINDOW"]],
            "window": "OPEN", "window_cutoff_utc": "2026-11-13T20:30:00+00:00", "friday_entry_deadline_utc": "2026-11-13T20:30:00+00:00", "friday_entry_deadline_et": "2026-11-13 15:30 EST", "base_session": "2026-11-13", "reaction_session": "2026-11-16",
            "frozen_cutoff_utc": "2026-11-15T11:00:00+00:00", "evidence": [{"kind": "ohlc", "symbol": sym, "retrieved_at": "2026-11-13T19:00:00+00:00", "sha256": "a" * 64}], "evidence_latest": "2026-11-13T19:00:00+00:00", "evidence_digest": "d" * 64,
            "generated_at": "2026-11-13T19:05:00+00:00", "generated_at_et": "2026-11-13 14:05:00 EST", "scan_id": "run_x", "model_version": "score40-v1", "mode": "LIVE"}
    base.update(kw)
    return base


def make(tmp_path, recs, status=True, accuracy=None):
    live = tmp_path / "live"
    sub = live / SL.MONDAY_BMO_SUBDIR
    write_ledger(sub / SL.LEDGER, recs)
    if status:
        (sub / SL.STATUS).write_text(json.dumps({"schema": "smart_move.monday_bmo_friday_status.v1", "variant": "MONDAY_BMO_FRIDAY", "written_at": "2026-11-13T19:05:00+00:00", "mode": "LIVE", "scan_ok": True,
                                                  "last_successful_scan_at": "2026-11-13T19:05:00+00:00", "events": {"open_scanned": len(recs), "published_new": len(recs)}}), encoding="utf-8")
    if accuracy is not None:
        (sub / SL.MONDAY_BMO_ACCURACY).write_text(json.dumps(accuracy), encoding="utf-8")
    return live


ACC = {"schema": "smart_move.monday_bmo_friday_accuracy.v1", "summary": {"variant": "MONDAY_BMO_FRIDAY", "total_predictions": 2, "by_signal": {"BUY": 1, "SELL": 1}, "graded_events": 2, "decided_calls": 2, "buy_accuracy": {"n": 1, "hits": 1, "rate": 1.0, "wilson95": [0.207, 1.0]},
                                                                           "sell_accuracy": {"n": 1, "hits": 0, "rate": 0.0, "wilson95": [0.0, 0.793]}, "overall_directional_accuracy": {"strict_hit_rate": 0.5, "wilson95": [0.095, 0.905], "direction_only_hit_rate": 0.5, "direction_only_n": 2},
                                                                           "average_post_earnings_move_pct": 2.5, "average_abs_move_pct": 2.5, "weekend_gap_risk": {"n": 2, "mean_gap_pct": 1.0, "mean_abs_gap_pct": 1.25, "max_abs_gap_pct": 1.5},
                                                                           "hypothetical_trading_performance": {"n": 2, "mean_net_pct_per_trade_cost_x1": 0.3}, "earnings_weeks": 1, "minimum_sample_met": False, "claim_allowed": False, "claim_text": "NO ACCURACY CLAIM: shadow descriptive statistics only", "separate_from_frozen_model": True, "secret_field": "never shown"},
       "rows": [{"symbol": "AAA", "monday_earnings_date": "2026-11-16", "outcome_status": "GRADED", "move_pct": 3.0, "gap_pct": 1.5, "outcome_class": "UP", "hit": True}, {"symbol": "BBB", "monday_earnings_date": "2026-11-16", "outcome_status": "GRADED", "move_pct": 2.0, "gap_pct": 0.5, "outcome_class": "UP", "hit": False}]}


def test_not_configured_and_no_variant_folder_are_reported_never_invented(tmp_path):
    a = SL.load_monday_bmo(None, T)
    assert a["configured"] is False and a["available"] is False and a["rows"] == [] and a["variant"] == "MONDAY_BMO_FRIDAY"
    b = SL.load_monday_bmo(str(tmp_path), T)
    assert b["configured"] is True and b["available"] is False and b["rows"] == [] and "No variant pass has run yet" in b["note"]


def test_rows_carry_every_field_the_tab_shows_and_the_accuracy_is_the_variants_own(tmp_path):
    live = make(tmp_path, [pred("AAA", "BUY"), pred("BBB", "SELL", bull=2, bear=16)], accuracy=ACC)
    out = SL.load_monday_bmo(str(live), T)
    assert out["available"] is True and out["ledger"]["ok"] is True and out["ledger"]["records"] == 2 and out["counts"] == {"LIVE": 2}
    by = {r["symbol"]: r for r in out["rows"]}
    a = by["AAA"]
    assert a["company"] == "AAA Inc" and a["monday_earnings_date"] == "2026-11-16" and a["signal"] == "BUY" and a["bull"] == 16 and a["bear"] == 2 and a["coverage"] == "30/40"
    assert a["predicted_at_et"] == "2026-11-13 14:05 EST" and a["friday_entry_deadline_et"] == "2026-11-13 15:30 EST" and a["hours_to_deadline"] == 1.0 and a["variant"] == "MONDAY_BMO_FRIDAY" and a["label"] == "LIVE"
    assert a["outcome_status"] == "GRADED" and a["move_pct"] == 3.0 and a["gap_pct"] == 1.5 and a["hit"] is True and by["BBB"]["hit"] is False and by["BBB"]["signal"] == "SELL"
    acc = out["accuracy"]
    assert acc["buy_accuracy"]["rate"] == 1.0 and acc["sell_accuracy"]["rate"] == 0.0 and acc["weekend_gap_risk"]["mean_abs_gap_pct"] == 1.25 and acc["separate_from_frozen_model"] is True and acc["claim_allowed"] is False
    assert "secret_field" not in acc                                                                  # whitelisted keys only


def test_after_the_friday_deadline_rows_are_historical_and_a_missed_window_is_shown(tmp_path):
    missed = {"schema": "smart_move.monday_bmo_friday_prediction.v1", "record_type": "WINDOW_MISSED", "variant": "MONDAY_BMO_FRIDAY", "symbol": "CCC", "company": "CCC Inc", "event_date": "2026-11-16", "monday_earnings_date": "2026-11-16", "session": "BMO",
              "window_cutoff_utc": "2026-11-13T20:30:00+00:00", "friday_entry_deadline_utc": "2026-11-13T20:30:00+00:00", "friday_entry_deadline_et": "2026-11-13 15:30 EST", "noted_at": "2026-11-13T21:00:00+00:00", "predicted": False, "scan_id": "run_y",
              "model_version": "score40-v1", "reason": "NO_PREDICTION_BEFORE_THE_FRIDAY_DEADLINE", "evidence_items_at_close": 0}
    live = make(tmp_path, [pred("AAA", "BUY"), missed])
    out = SL.load_monday_bmo(str(live), T + timedelta(hours=3))
    by = {r["symbol"]: r for r in out["rows"]}
    assert by["AAA"]["label"] == "HISTORICAL" and by["AAA"]["hours_to_deadline"] is None and by["AAA"]["outcome_status"] is None
    assert by["CCC"]["label"] == "WINDOW MISSED" and by["CCC"]["signal"] is None and "nothing is back-filled" in by["CCC"]["missing"][0]


def test_a_broken_variant_chain_shows_no_row(tmp_path):
    live = make(tmp_path, [pred("AAA", "BUY"), pred("BBB", "SELL")])
    p = live / SL.MONDAY_BMO_SUBDIR / SL.LEDGER
    p.write_text(p.read_text(encoding="utf-8").replace('"bull": 16', '"bull": 20', 1), encoding="utf-8")
    out = SL.load_monday_bmo(str(live), T)
    assert out["available"] is True and out["ledger"]["ok"] is False and out["rows"] == []


def test_the_variant_folder_does_not_change_the_frozen_live_view_and_the_route_reads_only(tmp_path, monkeypatch):
    a, b = tmp_path / "plain", tmp_path / "with_variant"
    shutil.copytree(LIVE_FIX, a)
    shutil.copytree(LIVE_FIX, b)
    write_ledger(b / SL.MONDAY_BMO_SUBDIR / SL.LEDGER, [pred("AAA", "BUY")])
    t2 = datetime(2026, 11, 2, 21, 30, tzinfo=UTC)
    assert SL.load(str(a), t2) == SL.load(str(b), t2)                                              # the frozen LIVE scanner never sees the variant
    assert SL.load_monday_bmo(str(a), t2)["available"] is False
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from earnings_monitor import routes
    before = {str(p): p.read_bytes() for p in b.rglob("*") if p.is_file()}
    monkeypatch.setattr(routes, "now", lambda: T)
    monkeypatch.setenv("SMART_MOVE_LIVE_DIR", str(b))
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    j = c.get("/api/earnings-monitor/smart-move/monday-bmo").json()
    assert j["status"] == "OK" and j["available"] and len(j["rows"]) == 1 and j["rows"][0]["variant"] == "MONDAY_BMO_FRIDAY"
    assert {str(p): p.read_bytes() for p in b.rglob("*") if p.is_file()} == before
    assert len(c.get("/api/earnings-monitor/smart-move/live").json()["rows"]) == len(SL.load(str(a), T)["rows"])
