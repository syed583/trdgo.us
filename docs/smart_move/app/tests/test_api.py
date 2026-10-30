"""Smart Move dashboard API tests: read-only, honest labelling, values equal to the saved evidence. No network, no database."""
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_local_server as srv  # noqa: E402
import smart_move_routes as R  # noqa: E402

SM = ROOT / "docs" / "smart_move"
client = TestClient(srv.app)
FIXED_NOW = datetime(2026, 10, 30, 13, 30, tzinfo=timezone.utc)          # 27 minutes after the last saved vendor response
LATER = datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(R, "_now", lambda: FIXED_NOW)


def walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


def test_meta_is_read_only_labelled_and_hash_checked():
    m = client.get("/api/smart-move/meta").json()
    assert m["status"] == "OK" and m["read_only"] is True and m["live_predictions"] is False and "No live signals" in m["disclaimer"]
    assert m["engine"]["prereg_sha256"] == "b25bd8d7889461561405ad8ec3c225b00a9202e9c75bf4b88e3e14ef7e31eb87" and m["engine"]["max_points"] == 40
    assert m["pack"]["integrity_verified"] and set(m["datasets"]) == {"CURRENT_CALENDAR", "HISTORICAL_REPLAY", "SYNTHETIC"} and len(m["limitations"]) >= 6
    assert m["current_calendar"]["stale"] is False and 0 <= m["current_calendar"]["evidence_age_hours"] < 1 and m["current_calendar"]["symbols"] == 271


def test_current_calendar_matches_the_saved_p1_report_and_never_invents_a_signal():
    d = client.get("/api/smart-move/calendar?limit=1000").json()
    p1 = json.loads((SM / "p1p2" / "p1_report.json").read_text())
    assert d["dataset"] == "CURRENT_CALENDAR" and "Not a prediction" in d["label"] and d["counts"]["total"] == 271
    uw_nov2 = [r for r in d["rows"] if r["earnings_date"] == "2026-11-02" and r["calendar"]["uw_flag"] == "company"]
    confirmed = [r for r in uw_nov2 if r["calendar"]["status_at_observation"] == "VENDOR_CONFIRMED"]
    assert len(uw_nov2) == 129 and len(confirmed) == p1["would_be_status_uw_symbols_for_2026_11_02"]["VENDOR_CONFIRMED"] == 122
    assert d["summary"]["continuation"] == {"BUY": 0, "SELL": 0, "NO_TRADE": 271} and d["summary"]["contrarian"]["NO_TRADE"] == 271
    for r in d["rows"]:
        for v in ("continuation", "contrarian"):
            e = r["engine"][v]
            assert e["signal"] == "NO_TRADE" and e["reason_codes"] and e["max"] == 40 and e["available"] <= 40
        assert {m["factor"] for m in r["missing_factors"]} >= {"F3", "F5", "F7"} and r["observations"]
        for o in r["observations"]:
            assert len(o["raw_sha256"]) == 64 and o["observed_at"].startswith("2026-10-30T13:0")


def test_vendor_confirmed_rows_carry_the_flag_vendors_and_the_not_company_confirmation_note():
    rows = client.get("/api/smart-move/calendar?q=AAPL").json()["rows"]
    r = [x for x in rows if x["symbol"] == "AAPL"][0]
    assert r["company"] and r["sector"] == "Technology" and "mapped to XLK" in r["sector_note"] and r["available_points"] == 5
    assert r["calendar"]["status_at_observation"] == "VENDOR_CONFIRMED" and r["calendar"]["uw_flag"] == "company" and "not direct company confirmation" in r["calendar"]["note"]
    assert {o["source"] for o in r["observations"]} >= {"UW_CALENDAR", "FINNHUB"}


def test_names_seen_by_one_vendor_are_estimated_and_unsectored():
    d = client.get("/api/smart-move/calendar?status=ESTIMATED&limit=1000").json()
    assert d["counts"]["filtered"] >= 100 and all(r["calendar"]["status_at_observation"] == "ESTIMATED" for r in d["rows"])
    nosec = [r for r in d["rows"] if not r["sector"]]
    assert nosec and all("NO_SECTOR_MAPPING" in json.dumps(r["missing_factors"]) or "unavailable" in r["sector_note"] for r in nosec[:20])


def test_staleness_is_judged_on_the_calendar_clock(monkeypatch):
    monkeypatch.setattr(R, "_now", lambda: LATER)
    d = client.get("/api/smart-move/calendar?limit=1000").json()
    assert d["status_counts_now"].get("STALE", 0) >= 200 and d["rows"][0]["calendar"]["evidence_age_hours"] > 100
    assert client.get("/api/smart-move/meta").json()["current_calendar"]["stale"] is True
    assert all(r["calendar"]["status_at_observation"] in ("VENDOR_CONFIRMED", "ESTIMATED") for r in d["rows"])         # the observation-time judgement is unchanged


def test_filters():
    base = client.get("/api/smart-move/calendar?limit=1000").json()["counts"]["total"]
    one = client.get("/api/smart-move/calendar?date_from=2026-11-02&date_to=2026-11-02&kind=AMC&limit=1000").json()
    assert 0 < one["counts"]["filtered"] < base and all(r["earnings_date"] == "2026-11-02" and r["kind"] == "AMC" for r in one["rows"])
    assert client.get("/api/smart-move/calendar?signal=BUY").json()["counts"]["filtered"] == 0
    assert client.get("/api/smart-move/calendar?missing=F8&limit=1000").json()["counts"]["filtered"] > 0
    assert client.get("/api/smart-move/calendar?status=VENDOR_CONFIRMED&limit=1000").json()["counts"]["filtered"] == 122
    assert client.get("/api/smart-move/calendar?signal=HOLD").status_code == 422


def test_replay_primary_is_the_pre_registered_source_and_matches_the_saved_summary():
    saved = json.loads((SM / "shadow_impl/results/real_replay2/replay2.json").read_text())
    for exp, key in (("primary", "A_provider_history | valid_evidence_only"), ("sec_strict", "B_strict_BMO_certain_plus_AMC_reconciled | valid_evidence_only"),
                     ("sec_moderate", "B_moderate_adds_AMC_unconfirmed | valid_evidence_only"), ("sec_strict_f7", "B_strict_BMO_certain_plus_AMC_reconciled | F7_sensitivity_on_P2_events")):
        d = client.get("/api/smart-move/replay?experiment=%s&limit=1000" % exp).json()
        s = saved["experiments"][key]["summary"]
        assert d["summary"]["continuation"] == {k: s["signals_continuation"].get(k, 0) for k in ("BUY", "SELL", "NO_TRADE")}
        assert d["summary"]["contrarian"] == {k: s["signals_contrarian"].get(k, 0) for k in ("BUY", "SELL", "NO_TRADE")}
        assert d["counts"]["total"] == s["events_in_dev_scope"]
    p = client.get("/api/smart-move/replay").json()
    assert p["primary"] is True and p["exploratory"] is False and p["f7_unverified"] is False and "Not a live prediction" in p["label"]
    for exp in ("sec_strict", "sec_moderate"):
        e = client.get("/api/smart-move/replay?experiment=" + exp).json()
        assert e["exploratory"] is True and e["primary"] is False and "EXPLORATORY" in e["experiment_label"] and "Not the pre-registered" in e["experiment_label"]
    f = client.get("/api/smart-move/replay?experiment=primary_f7").json()
    assert f["f7_unverified"] is True and "UNVERIFIED" in f["experiment_label"] and f["counts"]["total"] == 40
    assert client.get("/api/smart-move/replay?experiment=nope").status_code == 404


def test_replay_never_hides_missing_evidence_and_has_no_outcomes():
    d = client.get("/api/smart-move/replay?limit=1000").json()
    assert all(r["engine"]["continuation"]["signal"] == "NO_TRADE" for r in d["rows"])                    # provider history is too short for F3 in the dev window
    assert all(any(m["factor"] == "F3" for m in r["missing_factors"]) and any(m["factor"] == "F7" for m in r["missing_factors"]) for r in d["rows"])
    assert all(r["engine"]["continuation"]["reason_codes"] for r in d["rows"])
    banned = {"move_pct", "outcome", "hit", "direction", "pnl", "return_pct"}
    for exp in R.EXPERIMENTS:
        keys = set(walk(client.get("/api/smart-move/replay?experiment=%s&limit=1000" % exp).json()))
        assert not (keys & banned)
    sec = client.get("/api/smart-move/replay?experiment=sec_moderate&signal=BUY&limit=1000").json()
    assert sec["counts"]["filtered"] > 0 and all(r["available_points"] >= 24 for r in sec["rows"])         # a BUY needs at least 60 % of 40 available


def test_synthetic_is_labelled_and_matches_its_file():
    d = client.get("/api/smart-move/synthetic").json()
    syn = json.loads((SM / "shadow_impl/results/synthetic_season_results.json").read_text())
    assert "invented" in d["label"].lower() and "not real" in d["label"].lower()
    assert d["summary"]["continuation"] == {k: syn["signals"]["continuation"].get(k, 0) for k in ("BUY", "SELL", "NO_TRADE")} and d["counts"]["total"] == 28


def test_stock_detail_combines_datasets_and_404s():
    d = client.get("/api/smart-move/stock/aapl").json()
    assert d["symbol"] == "AAPL" and d["current_calendar"] and d["current_calendar"][0]["subscores"] and "limitations" in d
    sub = d["current_calendar"][0]["subscores"]["continuation"]
    assert "F8_market" in sub and "F8_sector" in sub
    rep = client.get("/api/smart-move/stock/ATR").json()
    assert rep["historical_replay"] and all(r["dataset"] == "HISTORICAL_REPLAY" for r in rep["historical_replay"])
    assert client.get("/api/smart-move/stock/ZZZZ").status_code == 404


def test_every_route_is_read_only():
    for path in ("/api/smart-move/meta", "/api/smart-move/calendar", "/api/smart-move/replay", "/api/smart-move/synthetic", "/api/smart-move/stock/AAPL"):
        for method in ("post", "put", "delete", "patch"):
            assert getattr(client, method)(path).status_code == 405
    src = (ROOT / "backend" / "smart_move_routes.py").read_text()
    for banned in ("urllib", "import requests", "httpx", "sqlalchemy", "psycopg", "open(", "INSERT", "UPDATE "):
        assert banned not in src


def test_a_tampered_pack_makes_the_api_refuse(tmp_path, monkeypatch):
    shutil.copytree(R.DATA_DIR, tmp_path / "pack")
    f = tmp_path / "pack" / "calendar_observations.json"
    f.write_text(f.read_text().replace("company", "estimation", 1))
    monkeypatch.setattr(R, "DATA_DIR", tmp_path / "pack")
    R.pack.cache_clear()
    try:
        r = client.get("/api/smart-move/meta")
        assert r.status_code == 503 and r.json()["detail"]["status"] == "PACK_UNAVAILABLE"
    finally:
        R.pack.cache_clear()
        R.current_rows.cache_clear()
