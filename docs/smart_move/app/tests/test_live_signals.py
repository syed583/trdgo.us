"""Live earnings signals: mapping, freshness labels, coverage, the idempotent post-batch job and the read-only API. Fixtures only (real saved engine rows, synthetic rows labelled as such); no provider, no network."""
import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).parent))
import smart_move_batch_marker as BM  # noqa: E402
import smart_move_live_signals as LS  # noqa: E402
import smart_move_live_signals_routes as RT  # noqa: E402
from test_batch_marker import NOW as BATCH_NOW, make_run  # noqa: E402

UTC = timezone.utc
REAL = json.loads((Path(__file__).parent / "fixtures" / "engine_rows_real_sample.json").read_text(encoding="utf-8"))["rows"]
GEN = datetime(2026, 11, 2, 22, 0, tzinfo=UTC)


def eng(cont="NO_TRADE", contra="NO_TRADE", codes=(), blocked=(), **kw):
    mk = lambda s: {"signal": s, "reason_codes": list(codes), "bull": 6 if s == "BUY" else 0, "bear": 6 if s == "SELL" else 0, "available": 30, "max": 40}  # noqa: E731
    return dict({"symbol": "SYN", "company": "SYNTHETIC CORP", "earnings_date": "2026-11-04", "kind": "BMO", "blocked_by": list(blocked), "engine": {"continuation": mk(cont), "contrarian": mk(contra)},
                 "evidence": [{"kind": "uw_news", "retrieved_at": (GEN - timedelta(hours=2)).isoformat()}]}, **kw)


def row_from(engine_row, **kw):
    models = LS.smart_move_models(engine_row)
    return LS.build_row(symbol=engine_row["symbol"], company=engine_row["company"], event_date=engine_row["earnings_date"], session=engine_row["kind"], models=models, generated_at=GEN,
                        evidence_times=[x["retrieved_at"] for x in engine_row["evidence"]], batch_id="collect-1", marker_valid=True, **kw)


# ---------------------------------------------------------------- BUY / SELL / NO TRADE mapping
def test_a_valid_engine_buy_or_sell_is_shown_but_only_for_the_primary_variant():
    buy = row_from(eng(cont="BUY", contra="SELL"))
    assert buy["signal"] == "BUY" and buy["signal_kind"] == "MODEL_SIGNAL" and buy["consensus"]["models_valid"] == 1                 # the contrarian variant is displayed, never mixed in
    assert [m["primary"] for m in buy["models"]] == [True, False] and buy["confidence"] is None and "no existing model" in buy["confidence_note"]
    assert row_from(eng(cont="SELL"))["signal"] == "SELL"


def test_no_trade_distinguishes_missing_inputs_from_a_neutral_model():
    insufficient = row_from(eng(codes=("INSUFFICIENT_COVERAGE", "NOT_TRADABLE")))
    assert insufficient["signal"] == "NO_TRADE" and insufficient["signal_kind"] == "INSUFFICIENT_INPUTS" and "not enough of the model's required inputs" in insufficient["reason"]
    assert any("missing data, not a neutral view" in w for w in insufficient["warnings"])
    neutral = row_from(eng())
    assert neutral["signal"] == "NO_TRADE" and neutral["signal_kind"] == "MODEL_NEUTRAL" and "no edge" in neutral["reason"] and not any("missing data" in w for w in neutral["warnings"])
    blocked = row_from(eng(blocked=("CALENDAR_ESTIMATED", "SINGLE_VENDOR")))
    assert blocked["signal_kind"] == "INSUFFICIENT_INPUTS" and "only estimated" in blocked["reason"]


def test_real_saved_engine_rows_never_become_a_recommendation():
    rows = [row_from(r) for r in REAL]
    assert len(rows) == 8 and all(r["signal"] == "NO_TRADE" for r in rows) and all(r["signal_kind"] == "INSUFFICIENT_INPUTS" for r in rows)
    assert all(r["confidence"] is None for r in rows)


def test_production_scan_vocabulary_maps_honestly():
    scan = lambda **k: dict({"symbol": "SYN", "date": "2026-11-04", "time_label": "Before Open", "score": 71.0, "coverage": 0.9}, **k)  # noqa: E731
    pm = LS.production_model
    assert (pm("ped", scan(signal="BUY"))["signal"], pm("ped", scan(signal="SELL"))["signal"]) == ("BUY", "SELL")
    n, low, pend = pm("ped", scan(signal="NEUTRAL")), pm("ped", scan(signal="LOW_DATA")), pm("ped", scan(signal="PENDING"))
    assert (n["signal"], n["kind"]) == ("NO_TRADE", "MODEL_NEUTRAL") and (low["kind"], pend["kind"]) == ("INSUFFICIENT_INPUTS", "INSUFFICIENT_INPUTS")
    gated = pm("claude_last", scan(signal="NEUTRAL", raw_signal="BUY"))
    assert gated["kind"] == "GATED" and gated["reason_codes"] == ["IMPLIED_MOVE_GATE"] and pm("x", scan(signal="WEIRD")) is None


def test_unanimity_filter_disagreement_and_single_model_wording():
    m = lambda name, sig, kind=None: {"name": name, "signal": sig, "kind": kind, "score": None, "reason_codes": [], "source": "PRODUCTION_SCAN", "primary": True}  # noqa: E731
    assert LS.headline([m("a", "BUY"), m("b", "BUY")])[0] == "BUY"
    s, k, why = LS.headline([m("a", "BUY"), m("b", "SELL")])
    assert (s, k) == ("NO_TRADE", "MODELS_DISAGREE") and "1 BUY, 1 SELL" in why
    assert LS.headline([m("a", "BUY"), m("b", "NO_TRADE", "INSUFFICIENT_INPUTS")]) [0] == "BUY"                              # a model without valid inputs does not veto, and is not counted as a vote
    assert LS.headline([m("a", "BUY"), m("b", "NO_TRADE", "MODEL_NEUTRAL")])[1] == "MODELS_DISAGREE"
    assert "(single model)" in LS.headline([m("a", "SELL")])[2]
    assert LS.headline([]) [1] == "NO_VALID_PREDICTION" and LS.headline([m("a", "NO_TRADE", "INSUFFICIENT_INPUTS")])[1] == "INSUFFICIENT_INPUTS"


# ---------------------------------------------------------------- freshness and status labels
def test_data_status_labels_never_present_old_synthetic_or_unverified_data_as_live():
    base = eng(cont="BUY")
    live = row_from(base)
    assert live["data_status"] == "LIVE" and live["freshness"]["age_hours"] == 2.0
    assert row_from(dict(base, evidence=[{"retrieved_at": (GEN - timedelta(hours=50)).isoformat()}]))["data_status"] == "STALE"
    assert row_from(dict(base, earnings_date="2026-11-01"))["data_status"] == "HISTORICAL"
    assert row_from(dict(base, evidence=[]))["data_status"] == "UNVERIFIED"
    unv = LS.build_row(symbol="S", company=None, event_date="2026-11-04", session="BMO", models=LS.smart_move_models(base), generated_at=GEN, evidence_times=[GEN - timedelta(hours=1)], marker_valid=False)
    assert unv["data_status"] == "UNVERIFIED"                                                                                    # batch evidence without a verified completion marker
    syn = row_from(base, synthetic=True)
    assert syn["data_status"] == "SYNTHETIC" and LS.refresh_status(syn, GEN + timedelta(days=9))["data_status"] == "SYNTHETIC"


def test_status_is_re_evaluated_at_read_time_and_never_upgraded():
    r = row_from(eng(cont="BUY"))
    assert LS.refresh_status(r, GEN + timedelta(hours=40))["data_status"] == "STALE"
    assert LS.refresh_status(r, GEN + timedelta(days=3))["data_status"] == "HISTORICAL"
    assert LS.refresh_status(r, GEN, marker_valid=False)["data_status"] == "UNVERIFIED"
    old = dict(r, data_status="STALE")
    assert LS.refresh_status(old, GEN)["data_status"] == "STALE"


def test_session_labels_and_missing_session_warning():
    assert row_from(eng())["session_label"] == "Before Open" and row_from(eng(kind="AMC"))["session_label"] == "After Close"
    unk = row_from(eng(kind="UNKNOWN"))
    assert unk["session_label"] == "Unknown" and "announcement session unknown" in unk["warnings"]


# ---------------------------------------------------------------- coverage
def test_coverage_counts_each_category_per_date_without_claiming_full_coverage():
    rows = [row_from(eng(cont="BUY", symbol="B1")), row_from(eng(cont="SELL", symbol="S1", earnings_date="2026-11-03")), row_from(eng(symbol="N1")), row_from(eng(codes=("INSUFFICIENT_COVERAGE",), symbol="X1"))]
    c = LS.coverage(rows, ["2026-11-03", "2026-11-04"])
    assert c["TOTAL"]["eligible_companies"] == 4 and (c["TOTAL"]["buy"], c["TOTAL"]["sell"], c["TOTAL"]["no_trade"]) == (1, 1, 2) and c["TOTAL"]["no_valid_prediction"] == 1
    assert c["2026-11-03"]["sell"] == 1 and c["2026-11-04"]["eligible_companies"] == 3 and c["TOTAL"]["no_trade_reasons"] == {"MODEL_NEUTRAL": 1, "INSUFFICIENT_INPUTS": 1}


# ---------------------------------------------------------------- the post-batch job
def sealed(tmp_path):
    run, store = make_run(tmp_path)
    BM.seal(run, store, BATCH_NOW, "Syed")
    return run, store


def test_the_job_refuses_without_a_completion_marker_even_when_the_files_exist(tmp_path):
    run, store = make_run(tmp_path)
    out = tmp_path / "out"
    r = LS.process_batch(run, store, out, BATCH_NOW, engine_rows=[eng(cont="BUY")])
    assert r == {"status": "REFUSED", "reason": "NO_COMPLETION_MARKER", "added": 0} and not (out / "signals.jsonl").exists()
    log = [json.loads(ln) for ln in (out / "processing_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert log[0]["reason"] == "NO_COMPLETION_MARKER"


def test_the_job_refuses_a_tampered_or_no_longer_valid_batch(tmp_path):
    run, store = sealed(tmp_path)
    with (run / "attempts.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": BATCH_NOW.isoformat(), "result": "OK"}) + "\n")
    assert LS.process_batch(run, store, tmp_path / "o1", BATCH_NOW, engine_rows=[eng(cont="BUY")])["reason"] == "MARKER_HASH_MISMATCH:attempts.jsonl"
    run2, store2 = sealed(tmp_path / "two")
    (store2 / "KILL").unlink()                                                                                                 # the kill switch is no longer engaged
    r = LS.process_batch(run2, store2, tmp_path / "o2", BATCH_NOW, engine_rows=[eng(cont="BUY")])
    assert r["status"] == "REFUSED" and r["reason"].startswith("BATCH_NO_LONGER_VALIDATES") and "kill switch re-engaged" in r["reason"]


def test_the_job_stores_signals_once_is_idempotent_and_logs_failures(tmp_path):
    run, store = sealed(tmp_path)
    out = tmp_path / "out"
    engine_rows = [eng(cont="BUY", symbol="B1"), eng(symbol="N1"), eng(codes=("INSUFFICIENT_COVERAGE",), symbol="X1"), {"symbol": "BROKEN"}]
    prod = {"ped": [{"symbol": "B1", "company": "SYNTHETIC CORP", "date": "2026-11-04", "time_label": "Before Open", "signal": "BUY", "score": 70, "coverage": 0.9, "held_at": (BATCH_NOW - timedelta(hours=1)).isoformat()},
                    {"symbol": "Z9", "date": "2026-11-04", "signal": "NONSENSE"}]}
    r1 = LS.process_batch(run, store, out, BATCH_NOW, engine_rows=engine_rows, production_scans=prod)
    assert r1["status"] == "PROCESSED" and r1["added"] == 4 and r1["failed"] == 2 and r1["batch_id"] == "collect-1"
    rows = [json.loads(ln) for ln in (out / "signals.jsonl").read_text(encoding="utf-8").splitlines()]
    by = {x["symbol"]: x for x in rows}
    assert by["B1"]["signal"] == "BUY" and {m["name"] for m in by["B1"]["models"]} == {"smart_move_continuation", "smart_move_contrarian", "ped"} and by["B1"]["batch_id"] == "collect-1"
    assert by["N1"]["signal_kind"] == "MODEL_NEUTRAL" and by["X1"]["signal_kind"] == "INSUFFICIENT_INPUTS" and "BROKEN" not in by
    assert by["Z9"]["signal"] == "NO_TRADE" and by["Z9"]["signal_kind"] == "NO_VALID_PREDICTION" and by["Z9"]["consensus"]["models_valid"] == 0       # an uninterpretable model output is shown as "no valid prediction", never as a view
    r2 = LS.process_batch(run, store, out, BATCH_NOW, engine_rows=engine_rows, production_scans=prod)
    assert r2["added"] == 0 and r2["duplicates_skipped"] == 4 and len((out / "signals.jsonl").read_text(encoding="utf-8").splitlines()) == 4
    log = [json.loads(ln) for ln in (out / "processing_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(log) == 2 and {f.get("error") for f in log[0]["failures"]} >= {"UNRECOGNISED_SIGNAL:NONSENSE"}


def test_no_input_rows_means_no_signals_and_nothing_is_invented(tmp_path):
    run, store = sealed(tmp_path)
    r = LS.process_batch(run, store, tmp_path / "out", BATCH_NOW, engine_rows=[], production_scans={})
    assert r["status"] == "PROCESSED" and r["added"] == 0 and r["events"] == 0 and not (tmp_path / "out" / "signals.jsonl").exists()


# ---------------------------------------------------------------- API
app = FastAPI()
app.include_router(RT.router)
client = TestClient(app)
U = "/api/smart-move/live-signals"


def write_rows(d, rows):
    d.mkdir(parents=True, exist_ok=True)
    (d / "signals.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def fresh_rows():
    now = datetime.now(UTC)
    mk = lambda sym, e, d="2026-11-04": dict(LS.build_row(symbol=sym, company=sym + " CORP", event_date=(now.date() + timedelta(days=1)).isoformat(), session="BMO", models=LS.smart_move_models(e), generated_at=now,  # noqa: E731
                                                          evidence_times=[now - timedelta(hours=1)], batch_id=None, marker_valid=True, now=now))
    return [mk("BUYX", eng(cont="BUY")), mk("SELLX", eng(cont="SELL")), mk("NEUT", eng()), mk("MISS", eng(codes=("INSUFFICIENT_COVERAGE",)))]


def test_api_filters_orders_and_reports_coverage(tmp_path, monkeypatch):
    write_rows(tmp_path, fresh_rows())
    monkeypatch.setenv("SMART_MOVE_SIGNALS_DIR", str(tmp_path))
    monkeypatch.delenv("SMART_MOVE_BATCH_RUN_DIR", raising=False)
    r = client.get(U + "/rows").json()
    assert r["status"] == "OK" and r["total"] == 4 and [x["symbol"] for x in r["rows"]] == ["BUYX", "SELLX", "MISS", "NEUT"] and r["read_only"] is True
    assert all(x["confidence"] is None and x["data_status"] == "LIVE" for x in r["rows"])
    g = lambda **q: client.get(U + "/rows", params=q).json()  # noqa: E731
    assert [x["symbol"] for x in g(signal="BUY")["rows"]] == ["BUYX"] and g(signal="NO_TRADE")["total"] == 2 and g(q="sell")["total"] == 1 and g(session="AMC")["total"] == 0 and g(data_status="LIVE")["total"] == 4
    assert client.get(U + "/rows", params={"signal": "HOLD"}).status_code == 422 and client.get(U + "/rows", params={"limit": 9999}).status_code == 422
    s = client.get(U + "/summary").json()
    assert s["coverage"]["TOTAL"]["eligible_companies"] == 4 and (s["coverage"]["TOTAL"]["buy"], s["coverage"]["TOTAL"]["sell"], s["coverage"]["TOTAL"]["no_trade"]) == (1, 1, 2) and any("covers only part" in n for n in s["notes"])
    assert client.post(U + "/rows").status_code == 405


def test_api_marks_batch_rows_unverified_when_the_marker_does_not_verify_and_handles_empty_and_bad_stores(tmp_path, monkeypatch):
    rows = fresh_rows()
    for r in rows:
        r["batch_id"] = "collect-1"
    write_rows(tmp_path / "sig", rows)
    monkeypatch.setenv("SMART_MOVE_SIGNALS_DIR", str(tmp_path / "sig"))
    monkeypatch.delenv("SMART_MOVE_BATCH_RUN_DIR", raising=False)
    assert {x["data_status"] for x in client.get(U + "/rows").json()["rows"]} == {"UNVERIFIED"}                              # batch rows with no marker folder configured
    run, store = make_run(tmp_path / "b")
    monkeypatch.setenv("SMART_MOVE_BATCH_RUN_DIR", str(run))
    assert {x["data_status"] for x in client.get(U + "/rows").json()["rows"]} == {"UNVERIFIED"}                              # a run folder without a marker
    monkeypatch.delenv("SMART_MOVE_SIGNALS_DIR")
    assert client.get(U + "/summary").json()["status"] == "NO_SIGNALS" and client.get(U + "/rows").json() == {"status": "NO_SIGNALS", "detail": "No signal store is configured (SMART_MOVE_SIGNALS_DIR).", "rows": [], "total": 0}
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "signals.jsonl").write_text("{nope\n", encoding="utf-8")
    monkeypatch.setenv("SMART_MOVE_SIGNALS_DIR", str(tmp_path / "bad"))
    assert client.get(U + "/rows").json()["status"] == "UNREADABLE"
    monkeypatch.setenv("SMART_MOVE_SIGNALS_DIR", str(tmp_path / "missing"))
    assert client.get(U + "/summary").json()["status"] == "NO_SIGNALS"


def test_api_returns_the_newest_generation_per_event_and_ages_rows(tmp_path, monkeypatch):
    now = datetime.now(UTC)
    old = LS.build_row(symbol="AGED", company=None, event_date=(now.date() + timedelta(days=1)).isoformat(), session="BMO", models=LS.smart_move_models(eng(cont="BUY")), generated_at=now - timedelta(hours=60),
                       evidence_times=[now - timedelta(hours=60)], marker_valid=True, now=now - timedelta(hours=60))
    new = dict(old, generated_at=now.isoformat(), signal="NO_TRADE", signal_kind="MODEL_NEUTRAL", dedupe_key="n")
    write_rows(tmp_path, [old, new])
    monkeypatch.setenv("SMART_MOVE_SIGNALS_DIR", str(tmp_path))
    rows = client.get(U + "/rows").json()["rows"]
    assert len(rows) == 1 and rows[0]["signal"] == "NO_TRADE" and rows[0]["data_status"] == "STALE"                           # newest generation wins; its 60 h old evidence is stale


# ---------------------------------------------------------------- boundaries
def test_no_provider_code_and_no_scoring_in_the_new_modules():
    for name in ("smart_move_live_signals.py", "smart_move_live_signals_routes.py", "smart_move_batch_marker.py"):
        src = (ROOT / "backend" / name).read_text(encoding="utf-8")
        imports = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
        assert not [ln for ln in imports if any(w in ln for w in ("requests", "httpx", "urllib", "socket", "unusualwhales", "smart_move_live ", "smart_move_v2", "scorer", "InfraFetcher", "smart_move_collection_runner"))], name
    h = lambda p: __import__("hashlib").sha256((ROOT / p).read_bytes().replace(b"\r\n", b"\n")).hexdigest()  # noqa: E731
    assert h("docs/smart_move/shadow_impl/smvshadow/scorer.py").startswith("62e09aa0ed785e2a") and h("backend/smart_move_v2.py").startswith("de2d8f9612934338")
