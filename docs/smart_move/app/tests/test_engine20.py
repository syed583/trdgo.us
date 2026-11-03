"""engine20: selection, budgeted plan, governed execution and the report on the frozen engine's own output. A stub fetcher counts every call; no network, no credentials."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_collection_runner as RUNNER  # noqa: E402
import smart_move_engine20 as E20  # noqa: E402
import smart_move_live as L  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 11, 2, 20, 30, tzinfo=UTC)                       # 15:30 ET: the session is still open; the latest completed session is Oct 30
SECTORS = {"AAA": "Technology", "BBB": "Technology", "CCC": "Healthcare", "DDD": "Energy", "EEE": "Energy"}
CAPS = {"AAA": 500e9, "BBB": 300e9, "CCC": 200e9, "DDD": 100e9, "EEE": 50e9}


def cal_rows(sym, date="2026-11-04", at=NOW - timedelta(hours=1), cap=None, flag="company"):
    base = {"symbol": sym, "date": date, "clock": None, "mode": "LIVE", "observed_at": at.isoformat(), "request_id": "r", "company": sym + " INC", "sector": SECTORS[sym], "marketcap": str(int(CAPS[sym]))}
    return [dict(base, source="UW_CALENDAR", label="premarket", vendor_flag=flag, raw_sha256="u" + sym), dict(base, source="FINNHUB", label="bmo", vendor_flag=None, raw_sha256="f" + sym)]


def bars_body(n=60, last="2026-10-30"):
    from datetime import date as D
    end = D.fromisoformat(last)
    rows, d = [], end
    while len(rows) < n:
        if d.weekday() < 5:
            rows.append({"date": d.isoformat(), "open": "100", "high": "102", "low": "99", "close": "101", "volume": 1_000_000, "total_volume": 1_000_000, "market_time": "r"})
        d -= timedelta(days=1)
    return {"data": sorted(rows, key=lambda r: r["date"])}


class Stub(L.Fetcher):
    mode = "SIMULATION"

    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def fetch(self, kind, symbol, params):
        self.calls.append((kind, symbol, dict(params)))
        if symbol in self.fail:
            raise gv.ProviderError("transient", "boom")
        if kind == "ohlc":
            return json.dumps(bars_body()).encode()
        if kind == "earnings":
            return json.dumps({"data": [{"report_date": "2026-08-01", "report_time": "premarket", "ending_fiscal_quarter": "2026-06-30", "post_earnings_move_1d": "0.02"}]}).encode()
        raise AssertionError("unexpected kind " + kind)


def make(tmp_path, fetcher=None, symbols=("AAA", "BBB", "CCC", "DDD", "EEE"), authorized=50, retries=0):
    clock = gv.FakeClock(NOW)
    cfg = L.LiveConfig(mode="SIMULATION", store_dir=tmp_path / "store", refresh_enabled=True, authorized_daily=authorized, spacing_s=0.0, max_retries=retries)
    svc = L.RefreshService(cfg, fetcher or Stub(), clock)
    for s in symbols:
        for r in cal_rows(s):
            svc.store.append("cal_obs", r)
    return svc


def select(svc, n=3):
    rows = L.LiveScorer(svc).score_all(NOW)
    caps = {o["symbol"]: float(o["marketcap"]) for o in svc.store.read("cal_obs")}
    return E20.select_top(rows, caps, NOW, n=n, event_date="2026-11-04", session="BMO")


# ---------------------------------------------------------------- selection
def test_selection_takes_confirmed_open_window_events_ranked_by_market_cap(tmp_path):
    svc = make(tmp_path)
    s = select(svc, n=3)
    assert [x["symbol"] for x in s] == ["AAA", "BBB", "CCC"] and [x["rank"] for x in s] == [1, 2, 3] and all(x["session"] == "BMO" and x["event_date"] == "2026-11-04" for x in s)
    assert s[0]["etf"] == "XLK" and s[2]["etf"] == "XLV"


def test_unconfirmed_wrong_date_or_closed_window_events_are_never_selected(tmp_path):
    svc = make(tmp_path, symbols=("AAA",))
    for r in cal_rows("BBB", flag="estimation"):
        svc.store.append("cal_obs", r)                                                     # estimated, not confirmed
    for r in cal_rows("CCC", date="2026-11-03"):
        svc.store.append("cal_obs", r)                                                     # another date
    assert [x["symbol"] for x in select(svc, n=5)] == ["AAA"]
    rows = L.LiveScorer(svc).score_all(NOW)
    assert E20.select_top(rows, CAPS, datetime(2026, 11, 3, 12, 0, tzinfo=UTC), n=5, event_date="2026-11-04", session="BMO") == []         # window closed at 11:00Z Nov 3


# ---------------------------------------------------------------- plan and budget
def test_the_plan_funds_spy_then_whole_stocks_then_etfs_and_reports_what_does_not_fit(tmp_path):
    svc = make(tmp_path)
    sel = select(svc, n=5)
    full = E20.plan_tasks(svc, sel, NOW, budget=50)
    kinds = [(t["kind"], t["symbol"]) for t in full["funded"]]
    assert kinds[0] == ("ohlc", "SPY") and kinds[1:3] == [("ohlc", "AAA"), ("earnings", "AAA")] and {"XLK", "XLV", "XLE"} <= {s for k, s in kinds}
    assert full["attempts_planned"] == 1 + 10 + 3 and full["spare"] == 50 - 14 and full["unfunded"] == [] and len(full["stocks_fully_funded"]) == 5
    tight = E20.plan_tasks(svc, sel, NOW, budget=8)                                      # SPY + 3 whole stocks + 1 ETF slot: ETFs lose to stocks, no stock is left half-funded
    assert tight["attempts_planned"] == 8 and [t["symbol"] for t in tight["funded"]].count("DDD") == 0 and ("ohlc", "DDD") in [(t["kind"], t["symbol"]) for t in tight["unfunded"]]
    assert all(("earnings", s) in [(t["kind"], t["symbol"]) for t in tight["funded"]] for s in ("AAA", "BBB", "CCC") if ("ohlc", s) in [(t["kind"], t["symbol"]) for t in tight["funded"]])
    f7 = E20.plan_tasks(svc, sel[:2], NOW, budget=50, with_f7=True)
    assert {k for k, _ in [(t["kind"], t["symbol"]) for t in f7["funded"]]} >= {"vol_context", "iv_rank"}


def test_fresh_evidence_is_reused_and_never_requested_again(tmp_path):
    f = Stub()
    svc = make(tmp_path, f)
    sel = select(svc, n=2)
    plan = E20.plan_tasks(svc, sel, NOW, budget=50)
    res = E20.run(svc, plan["funded"], now=NOW, ceiling=50, state_dir=tmp_path / "st")
    assert res["ok"] == len(plan["funded"]) and res["failed"] == 0 and res["halted"] is None
    again = E20.plan_tasks(svc, sel, NOW, budget=50)
    assert [t for t in again["funded"] if t["symbol"] in ("AAA", "BBB")] == [] and set(again["already_fresh_stocks"]) == {"AAA", "BBB"}


# ---------------------------------------------------------------- governed execution
def test_run_stores_raw_evidence_append_only_through_the_governor(tmp_path):
    f = Stub()
    svc = make(tmp_path, f)
    sel = select(svc, n=2)
    plan = E20.plan_tasks(svc, sel, NOW, budget=50)
    E20.run(svc, plan["funded"], now=NOW, ceiling=50, state_dir=tmp_path / "st")
    ev = svc.store.read("evidence")
    assert {(e["kind"], e["symbol"]) for e in ev} >= {("ohlc", "SPY"), ("ohlc", "AAA"), ("earnings", "AAA"), ("ohlc", "BBB"), ("earnings", "BBB")}
    assert all(e["raw_sha256"] and e["retrieved_at"] and e["mode"] == "SIMULATION" for e in ev) and [c[0] for c in f.calls][:1] == ["ohlc"]
    assert json.loads(svc.store.get(next(e for e in ev if e["kind"] == "earnings")["raw_sha256"]))["data"][0]["ending_fiscal_quarter"] == "2026-06-30"


def test_the_governor_caps_attempts_including_failures_and_retries_are_off(tmp_path):
    f = Stub(fail=("BBB",))
    svc = make(tmp_path, f, authorized=5)
    sel = select(svc, n=5)
    plan = E20.plan_tasks(svc, sel, NOW, budget=50)
    res = E20.run(svc, plan["funded"], now=NOW, ceiling=5, state_dir=tmp_path / "st")
    assert len(f.calls) <= 5 and res["requests_attempted"] <= 5 and res["halted"] in ("CEILING_REACHED", None)
    assert [c for c in f.calls if c[1] == "BBB"].__len__() <= 2                       # a failed task is tried once; there is no second attempt of the same request


def test_run_stops_on_the_kill_switch_and_on_repeated_failures(tmp_path):
    svc = make(tmp_path, Stub(), authorized=50)
    sel = select(svc, n=3)
    plan = E20.plan_tasks(svc, sel, NOW, budget=50)
    svc.engage_kill_switch("test")
    assert E20.run(svc, plan["funded"], now=NOW, ceiling=50, state_dir=tmp_path / "s1")["halted"] == "KILL_SWITCH"
    svc2 = make(tmp_path / "two", Stub(fail=("SPY", "AAA", "BBB", "CCC")))
    sel2 = select(svc2, n=3)
    res = E20.run(svc2, E20.plan_tasks(svc2, sel2, NOW, budget=50)["funded"], now=NOW, ceiling=50, state_dir=tmp_path / "s2")
    assert res["halted"] == "CONSECUTIVE_FAILURES" and res["failed"] == 3 and res["ok"] == 0


# ---------------------------------------------------------------- the report classifies the frozen engine's own output
def row(sig="NO_TRADE", codes=(), avail=0, adv=None, missing=(), window=("2026-11-02T11:00:00+00:00", "2026-11-03T11:00:00+00:00")):
    return {"symbol": "SYN", "company": "SYN INC", "earnings_date": "2026-11-04", "kind": "BMO", "adv20": adv, "missing_factors": [{"factor": f, "reason": r} for f, r in missing], "evidence": [],
            "plan": {"window_start_utc": window[0], "cutoff_utc": window[1]}, "engine": {"continuation": {"signal": sig, "reason_codes": list(codes), "bull": 15 if sig == "BUY" else 2, "bear": 1, "available": avail, "max": 40},
                                                                                      "contrarian": {"signal": "NO_TRADE", "reason_codes": [], "bull": 0, "bear": 0, "available": avail, "max": 40}}}


def test_classification_keeps_missing_data_apart_from_a_genuine_no_trade_and_never_invents_a_signal():
    assert E20.classify(row("BUY", ["RULE"], 30, 5e7), NOW)["result"] == "BUY"
    assert E20.classify(row("SELL", ["RULE"], 30, 5e7), NOW)["result"] == "SELL"
    g = E20.classify(row("NO_TRADE", ["BELOW_MARGIN"], 30, 5e7), NOW)
    assert g["result"] == "NO TRADE (genuine)" and g["liquidity"] == "PASS" and g["coverage_points"] == 30 and g["model_version"] == "score40-v1"
    conflict = E20.classify(row("NO_TRADE", ["CONFLICT"], 30, 5e7), NOW)
    assert conflict["result"] == "NO TRADE (genuine)"
    miss = E20.classify(row("NO_TRADE", ["ILLIQUID_OR_UNKNOWN", "INSUFFICIENT_COVERAGE"], 0, None, missing=(("F5", "NO_FRESH_PRICE_EVIDENCE"), ("F3", "NO_FRESH_HISTORY_OR_PRICE"))), NOW)
    assert miss["result"] == "PENDING FINAL REFRESH" and miss["liquidity"] == "UNKNOWN" and "fresh market-close data" in miss["reason"]
    low = E20.classify(row("NO_TRADE", ["INSUFFICIENT_COVERAGE", "ILLIQUID_OR_UNKNOWN"], 10, 5e6, missing=(("F7", "NOT_CAPTURED_LIVE_IN_WINDOW"),)), NOW)
    assert low["result"] == "NO VALID PREDICTION" and low["liquidity"] == "FAIL"
    late = E20.classify(row("NO_TRADE", ["OUT_OF_WINDOW"], 30, 5e7, window=("2026-11-01T11:00:00+00:00", "2026-11-02T11:00:00+00:00")), NOW)
    assert late["result"] == "NO VALID PREDICTION" and late["window"] == "CLOSED"


def test_report_covers_every_selected_stock_even_when_it_cannot_be_scored(tmp_path):
    svc = make(tmp_path)
    sel = select(svc, n=2) + [{"rank": 3, "symbol": "ZZZ", "company": "Z", "event_date": "2026-11-04", "session": "BMO"}]
    rep = E20.report(svc, sel, NOW)
    by = {r["symbol"]: r for r in rep["rows"]}
    assert set(by) == {"AAA", "BBB", "ZZZ"} and by["ZZZ"]["result"] == "NO VALID PREDICTION" and rep["model_version"] == "score40-v1"
    assert all(r["result"] in ("NO VALID PREDICTION", "PENDING FINAL REFRESH") for r in rep["rows"])             # nothing collected yet: no signal of any kind


# ---------------------------------------------------------------- the runner profile
def test_the_engine20_profile_is_a_zero_request_dry_run_with_the_hard_50_ceiling_and_the_live_gate(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    (store / "cal_obs.jsonl").write_text("\n".join(json.dumps(r) for s in ("AAA", "BBB") for r in cal_rows(s)) + "\n", encoding="utf-8")
    assert RUNNER.main(["--store-dir", str(store), "--state-dir", str(tmp_path / "st"), "--profile", "engine20", "--select-date", "2026-11-04", "--select-session", "BMO", "--as-of", NOW.isoformat()]) == 0
    plan = json.loads((tmp_path / "st" / "engine20_plan.json").read_text(encoding="utf-8"))
    assert plan["mode"] == "DRY_RUN" and plan["provider_requests"] == 0 and plan["ceiling"] == 50 and [s["symbol"] for s in plan["selected"]] == ["AAA", "BBB"] and not (tmp_path / "st" / "attempts.jsonl").exists()
    assert RUNNER.main(["--store-dir", str(store), "--state-dir", str(tmp_path / "st2"), "--mode", "live", "--arm", "--authorize-requests", "51", "--profile", "engine20"]) == 6
    assert RUNNER.main(["--store-dir", str(store), "--state-dir", str(tmp_path / "st3"), "--mode", "live", "--arm", "--authorize-requests", "50", "--profile", "engine20"]) == 6      # no env flag, no attestation


def test_the_module_has_no_provider_client_and_no_scoring():
    src = (ROOT / "backend" / "smart_move_engine20.py").read_text(encoding="utf-8")
    imports = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
    assert not [ln for ln in imports if any(w in ln for w in ("requests", "httpx", "urllib", "socket", "unusualwhales", "InfraFetcher", "smart_move_v2"))]
    import hashlib
    h = lambda p: hashlib.sha256((ROOT / p).read_bytes().replace(b"\r\n", b"\n")).hexdigest()  # noqa: E731
    assert h("docs/smart_move/shadow_impl/smvshadow/scorer.py").startswith("62e09aa0ed785e2a") and h("backend/smart_move_v2.py").startswith("de2d8f9612934338")


def test_pinned_symbol_list_keeps_the_owner_order_and_never_substitutes_another_stock(tmp_path):
    svc = make(tmp_path)
    rows = L.LiveScorer(svc).score_all(NOW)
    caps = {o["symbol"]: float(o["marketcap"]) for o in svc.store.read("cal_obs")}
    got = E20.select_top(rows, caps, NOW, n=20, event_date="2026-11-04", session="BMO", only=["DDD", "AAA", "ZZZ"])
    assert [g["symbol"] for g in got] == ["DDD", "AAA"]                      # owner order kept, the unknown ZZZ is absent and nothing else (BBB, CCC) takes its place
