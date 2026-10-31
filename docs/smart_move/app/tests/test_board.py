"""Earnings board: honest display statuses from saved data only. No request, no scoring, no write."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_board as B  # noqa: E402
import smart_move_local_server as srv  # noqa: E402

UTC = timezone.utc
PLAN = {"window_start_utc": "2026-10-31T21:00:00+00:00", "cutoff_utc": "2026-11-01T21:00:00+00:00", "assumed_release_et": "2026-11-02T16:00:00-05:00", "basis": "NOMINAL_AMC"}
BEFORE, INSIDE, AFTER = (datetime(2026, 10, 31, 12, 0, tzinfo=UTC), datetime(2026, 11, 1, 12, 0, tzinfo=UTC), datetime(2026, 11, 1, 22, 0, tzinfo=UTC))


def eng(sig, bull, bear, avail, codes):
    return {"signal": sig, "bull": bull, "bear": bear, "available": avail, "reason_codes": codes}


def classify(e, miss=(), evaluated=True, now=INSIDE, plan=PLAN):
    return B.classify(e, evaluated=evaluated, missing=list(miss), plan=plan, now=now)["status"]


def test_buy_and_sell_only_when_the_engine_rule_fired_with_full_coverage():
    assert classify(eng("BUY", 17, 1, 40, ["RULE"])) == "BUY"
    assert classify(eng("SELL", 2, 16, 30, ["RULE"])) == "SELL"
    # a BUY that lacks its rule code, has a gate code or too few points is never shown as a signal
    assert classify(eng("BUY", 17, 1, 40, [])) == "DATA_INCOMPLETE"
    assert classify(eng("BUY", 17, 1, 40, ["RULE", "STALE_EVIDENCE"])) == "DATA_INCOMPLETE"
    assert classify(eng("SELL", 2, 16, 20, ["RULE"])) == "DATA_INCOMPLETE"


def test_no_trade_is_grey_only_when_the_engine_decided_and_missing_data_is_never_neutral():
    assert classify(eng("NO_TRADE", 9, 6, 40, ["BELOW_MARGIN"])) == "NO_TRADE"
    assert classify(eng("NO_TRADE", 15, 15, 40, ["CONFLICT"])) == "NO_TRADE"
    # missing data or low coverage is yellow, whatever the engine's own NO_TRADE says
    assert classify(eng("NO_TRADE", 0, 0, 12, ["INSUFFICIENT_COVERAGE"]), ["F7:NOT_CAPTURED", "F8:X"]) == "DATA_INCOMPLETE"
    assert classify(eng("NO_TRADE", 5, 2, 30, ["ILLIQUID_OR_UNKNOWN"]), ["F5:PROVIDER_EMPTY"]) == "DATA_INCOMPLETE"
    assert classify(eng("NO_TRADE", 5, 2, 40, ["UNKNOWN_TIMING"])) == "DATA_INCOMPLETE"
    assert classify(eng("NO_TRADE", 0, 0, 40, ["OUT_OF_WINDOW"])) == "DATA_INCOMPLETE"
    # a stock whose price history is present but whose liquidity is under the floor is an explicit NO TRADE, not missing data
    r = B.classify(eng("NO_TRADE", 5, 2, 40, ["ILLIQUID_OR_UNKNOWN"]), evaluated=True, missing=[], plan=PLAN, now=INSIDE)
    assert r["status"] == "NO_TRADE" and "$20 million" in r["why"]


def test_pending_until_a_capture_exists_then_incomplete_after_the_window_closes():
    assert classify(None, evaluated=False, now=BEFORE) == "PENDING"
    assert classify(None, evaluated=False, now=INSIDE) == "PENDING"
    assert classify(None, evaluated=False, now=AFTER) == "DATA_INCOMPLETE"
    assert classify(None, evaluated=False, plan=None) == "DATA_INCOMPLETE"


def test_factor_states_never_call_uncaptured_data_available():
    pend = B.factor_states([], False)
    assert {f["state"] for f in pend} == {"NOT_CAPTURED"}
    got = {f["factor"]: f["state"] for f in B.factor_states(["F7:NOT_CAPTURED", {"factor": "F8", "reason": "NO_SECTOR_MAPPING", "partial": True}][:1], True)}
    assert got == {"F3": "AVAILABLE", "F5": "AVAILABLE", "F7": "MISSING", "F8": "AVAILABLE"}
    part = {f["factor"]: f["state"] for f in B.factor_states([{"factor": "F8", "reason": "NO_SECTOR_MAPPING", "partial": True}], True)}
    assert part["F8"] == "PARTIAL"


def saved(sym, eligible=True, date="2026-11-02", kind="AMC", company="ACME INC"):
    return {"symbol": sym, "company": company, "earnings_date": date, "kind": kind, "sector": "Technology", "plan": PLAN, "missing_factors": [{"factor": "F3", "reason": "NO_PRICE_EVIDENCE_SAVED"}],
            "calendar": {"eligible_for_shadow": eligible, "status_at_observation": "VENDOR_CONFIRMED" if eligible else "ESTIMATED", "calendar_observed_at": "2026-10-30T15:14:00+00:00"}}


def run_result(sym, c, k, avail=40, miss=()):
    return {"symbol": sym, "company": sym, "earnings_date": "2026-11-02", "kind": "AMC", "calendar_status": "VENDOR_CONFIRMED", "available_points": avail, "missing_factors": list(miss),
            "continuation": c, "contrarian": k, "capture_ts": "2026-10-31T21:06:00+00:00"}


def test_board_lists_every_eligible_upcoming_event_and_ranks_by_status(tmp_path):
    rows = [saved("AAA"), saved("BBB"), saved("CCC"), saved("DDD"), saved("EEE"), saved("OLD", date="2026-10-30"), saved("EST", eligible=False)]
    runs = {("AAA", "2026-11-02"): run_result("AAA", eng("BUY", 17, 1, 40, ["RULE"]), eng("NO_TRADE", 1, 17, 40, ["BELOW_MARGIN"])),
            ("BBB", "2026-11-02"): run_result("BBB", eng("SELL", 1, 17, 40, ["RULE"]), eng("BUY", 17, 1, 40, ["RULE"])),
            ("CCC", "2026-11-02"): run_result("CCC", eng("NO_TRADE", 0, 0, 12, ["INSUFFICIENT_COVERAGE"]), eng("NO_TRADE", 0, 0, 12, ["INSUFFICIENT_COVERAGE"]), 12, ["F7:NOT_CAPTURED", "F5:PROVIDER_EMPTY"])}
    b = B.build_board(rows, [], runs, now=INSIDE)
    got = {r["symbol"]: r["status"] for r in b["rows"]}
    assert got == {"AAA": "BUY", "BBB": "SELL", "CCC": "DATA_INCOMPLETE", "DDD": "PENDING", "EEE": "PENDING"}        # ineligible and past events are not listed
    assert b["counts"] == {"BUY": 1, "SELL": 1, "NO_TRADE": 0, "DATA_INCOMPLETE": 1, "PENDING": 2} and b["total"] == 5
    assert [r["symbol"] for r in b["rows"]][:3] == ["AAA", "BBB", "CCC"]
    # variants are classified separately and never merged: BBB is SELL for continuation and BUY for contrarian
    bbb = next(r for r in b["rows"] if r["symbol"] == "BBB")
    assert bbb["variants"]["continuation"]["status"] == "SELL" and bbb["variants"]["contrarian"]["status"] == "BUY"
    c = B.build_board(rows, [], runs, now=INSIDE, variant="contrarian")
    assert {r["symbol"]: r["status"] for r in c["rows"]}["BBB"] == "BUY"
    aaa = next(r for r in b["rows"] if r["symbol"] == "AAA")
    assert aaa["points"] == {"available": 40, "max": 40, "bull": 17, "bear": 1, "min_to_decide": 24} and aaa["release"]["label"] == "After close (AMC)" and aaa["company"] == "ACME INC"
    ddd = next(r for r in b["rows"] if r["symbol"] == "DDD")
    assert ddd["points"]["available"] is None and {f["state"] for f in ddd["factors"]} == {"NOT_CAPTURED"} and ddd["evaluation_source"] == "SAVED_CALENDAR"


def test_a_saved_calendar_row_alone_can_never_produce_a_signal():
    b = B.build_board([saved("ZZZ")], [], {}, now=INSIDE)
    assert {r["status"] for r in b["rows"]} == {"PENDING"}
    assert {r["status"] for r in B.build_board([saved("ZZZ")], [], {}, now=AFTER)["rows"]} == {"DATA_INCOMPLETE"}


def test_newest_run_report_wins_and_unreadable_or_self_test_folders_are_skipped(tmp_path):
    def rep(name, sig, ts, extra=None):
        d = tmp_path / name
        d.mkdir()
        r = run_result("AAA", eng(sig, 17, 1, 40, ["RULE"]), eng("NO_TRADE", 0, 0, 40, ["BELOW_MARGIN"]))
        r["capture_ts"] = ts
        (d / "audit_report.json").write_text(json.dumps({"results": [r], **(extra or {})}))
    rep("run_a", "BUY", "2026-10-31T21:06:00+00:00")
    rep("run_b", "SELL", "2026-11-01T10:00:00+00:00", {"simulation": True})
    (tmp_path / "run_broken").mkdir()
    (tmp_path / "run_broken" / "audit_report.json").write_text("{not json")
    (tmp_path / "selftest_task").mkdir()
    (tmp_path / "selftest_task" / "audit_report.json").write_text(json.dumps({"results": [run_result("ZZZ", eng("BUY", 17, 1, 40, ["RULE"]), eng("BUY", 17, 1, 40, ["RULE"]))]}))
    got = B.load_run_results(tmp_path)
    assert set(got) == {("AAA", "2026-11-02")} and got[("AAA", "2026-11-02")]["_run"] == "run_b"
    b = B.build_board([saved("AAA")], [], got, now=INSIDE)
    assert b["rows"][0]["evidence_mode"] == "SIMULATION" and b["rows"][0]["status"] == "SELL"
    assert B.load_run_results(tmp_path / "missing") == {} and B.load_run_results(None) == {}


def test_api_shows_the_saved_universe_as_pending_or_incomplete_and_is_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("SMART_MOVE_RUN_ROOT", str(tmp_path))                     # no run folders: nothing evaluated
    monkeypatch.setenv("SMART_MOVE_BOARD_NOW", "2026-10-31T12:00:00+00:00")
    c = TestClient(srv.app)
    r = c.get("/api/smart-move/board").json()
    assert r["read_only"] and r["sends_requests"] is False and r["clock_override"] is True
    assert r["total"] >= 100 and set(x["status"] for x in r["rows"]) == {"PENDING"} and r["counts"]["BUY"] == 0 and r["counts"]["SELL"] == 0
    assert all(x["company"] for x in r["rows"][:20]) and all(x["release"]["kind"] in ("BMO", "AMC") for x in r["rows"])
    assert c.get("/api/smart-move/board?status=BUY").json()["shown"] == 0
    assert c.get("/api/smart-move/board?variant=bogus").status_code == 422
    sym = r["rows"][0]["symbol"]
    assert c.get("/api/smart-move/board?q=%s" % sym.lower()).json()["shown"] >= 1
    for m in ("post", "put", "delete"):
        assert getattr(c, m)("/api/smart-move/board").status_code in (404, 405)
    monkeypatch.setenv("SMART_MOVE_BOARD_NOW", "2026-11-01T22:00:00+00:00")        # every window closed with no capture
    assert set(x["status"] for x in c.get("/api/smart-move/board").json()["rows"]) == {"DATA_INCOMPLETE"}


def test_a_new_buy_appears_after_a_refresh_with_no_other_change(tmp_path, monkeypatch):
    monkeypatch.setenv("SMART_MOVE_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("SMART_MOVE_BOARD_NOW", "2026-11-01T12:00:00+00:00")
    c = TestClient(srv.app)
    first = c.get("/api/smart-move/board").json()
    amc = [x for x in first["rows"] if x["release"]["kind"] == "AMC"]
    bmo = [x for x in first["rows"] if x["release"]["kind"] == "BMO"]
    sym = amc[0]["symbol"]
    assert amc[0]["status"] == "PENDING" and bmo and {x["status"] for x in bmo} == {"DATA_INCOMPLETE"}      # the before-open windows closed at 06:00 ET on 1 Nov with no capture
    d = tmp_path / "run_20261031_1705ET"
    d.mkdir()
    base = next(x for x in first["rows"] if x["symbol"] == sym)
    r = run_result(sym, eng("BUY", 17, 1, 40, ["RULE"]), eng("NO_TRADE", 1, 17, 40, ["BELOW_MARGIN"]))
    r["earnings_date"] = base["earnings_date"]
    (d / "audit_report.json").write_text(json.dumps({"results": [r]}))
    after = c.get("/api/smart-move/board").json()
    row = next(x for x in after["rows"] if x["symbol"] == sym)
    assert row["status"] == "BUY" and row["evaluation_source"] == "RUN_REPORT" and after["counts"]["BUY"] == 1


def test_the_board_cannot_reach_a_provider_the_scorer_or_a_writer():
    src = "".join((ROOT / "backend" / f).read_text() for f in ("smart_move_board.py", "smart_move_board_routes.py"))
    for forbidden in ("unusualwhales_service", "finnhub_service", "finviz_service", "InfraFetcher", "httpx", "urlopen", "requests.", "scorer.score", "sc.score", "write_text", "open(", "engage_kill_switch", ".append(\"predictions\""):
        assert forbidden not in src, forbidden
