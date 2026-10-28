"""The larger capped sample tool, tested with a fake provider. The AMKR / BANR candle rows below are the REAL rows the
production dry run printed on 2026-10-29 (market_time pr / po / r, newest date first, pr-po-r within a date)."""
import importlib.util
import pathlib
import re
import sys
import types
from datetime import date, datetime, timezone

import pytest

UTC = timezone.utc
TOOL = pathlib.Path(__file__).resolve().parent.parent / "tools" / "outcome_sample.py"
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def load_tool():
    spec = importlib.util.spec_from_file_location("outcome_sample_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def r(day, mt, close, vol, total=None):
    return {"date": day, "market_time": mt, "open": str(close), "close": str(close), "volume": vol, "total_volume": total or vol}


AMKR = [r("2026-10-28", "pr", "41.22", 120733), r("2026-10-28", "po", "41.67", 1625800, 8263457), r("2026-10-28", "r", "42.21", 8236995, 6637657),
        r("2026-10-27", "pr", "38.5", 369808), r("2026-10-27", "po", "40.0424", 1292846, 12669733), r("2026-10-27", "r", "39.94", 12669733, 11376887),
        r("2026-10-26", "pr", "38.65", 403326), r("2026-10-26", "po", "38.3194", 4478299, 21618562), r("2026-10-26", "r", "36.81", 21618562, 17140263),
        r("2026-10-23", "pr", "38.51", 65106), r("2026-10-23", "r", "39.3", 4887131, 727872)]
BANR = [r("2026-10-23", "r", "70.28", 359897, 38096),
        r("2026-10-22", "pr", "68.04", 501), r("2026-10-22", "po", "70.1", 62175, 326886), r("2026-10-22", "r", "70.1", 326927, 264711),
        r("2026-10-21", "po", "68.05", 61916, 243441), r("2026-10-21", "r", "68.05", 243451, 181525),
        r("2026-10-20", "po", "68.17", 42961, 325085), r("2026-10-20", "r", "68.17", 325469, 282124),
        r("2026-10-19", "po", "67", 65777, 149093), r("2026-10-19", "r", "67", 149119, 83316)]


def snap(sym, earn, timing, signal, move=None, evaluated=None, model="claude_new", pred=None):
    p = pred or datetime(earn.year, earn.month, earn.day - 1, 4, 21, tzinfo=UTC)
    return {"model": model, "symbol": sym, "earnings_date": earn, "report_timing": timing, "snapshot_date": p.date(), "predicted_at": p,
            "evaluated_at": evaluated, "actual_move_pct": move, "signal": signal, "score": 60.0}


def real_snaps():
    stamp = datetime(2026, 10, 27, 4, 18, tzinfo=UTC)
    return [snap("AMKR", date(2026, 10, 26), "After Close", "BUY", move=-0.86, evaluated=stamp),
            snap("BANR", date(2026, 10, 21), "After Close", "SELL", move=0.0, evaluated=datetime(2026, 10, 22, 8, 56, tzinfo=UTC)),
            snap("NEWCO", date(2026, 10, 27), "After Close", "BUY", evaluated=stamp)]


def test_real_rows_amkr_and_banr_regular_close_outcomes_and_reasons():
    t = load_tool()
    snaps = real_snaps()
    events = t.completed_events(snaps, NOW)
    assert {e["symbol"] for e in events} == {"AMKR", "BANR", "NEWCO"}
    fetched = {"AMKR": AMKR, "BANR": BANR, "NEWCO": AMKR}
    per_symbol, recs, outcomes = t.analyse(events, fetched, snaps, NOW)
    by = {x["symbol"]: x for x in recs if x["symbol"] != "NEWCO"}
    a, b = by["AMKR"], by["BANR"]
    assert (a["base_close"], a["reaction_close"], a["verified_move"]) == (36.81, 39.94, 8.5)
    assert a["legacy_logic_replica"] == -0.86 and a["stored_legacy"] == -0.86
    assert a["vs_stored"].startswith("legacy row-selection error") and a["class_change"] is True and a["verified"] is True
    assert (b["base_close"], b["reaction_close"], b["verified_move"]) == (68.05, 70.1, 3.01)
    assert b["legacy_logic_replica"] == 0.0 and b["vs_stored"].startswith("legacy row-selection error") and b["class_change"] is True
    assert a["current_fetcher_equals_verified"] and b["current_fetcher_equals_verified"]
    new = next(x for x in recs if x["symbol"] == "NEWCO")
    assert new["vs_stored"] == "no stored legacy value" and new["status"] == "GRADED"
    assert set(outcomes) == {("AMKR", date(2026, 10, 26)), ("BANR", date(2026, 10, 21)), ("NEWCO", date(2026, 10, 27))}
    s = t.summarise(recs)
    assert s["by_timing"]["AMC"]["stored_differs"] == 2 and s["by_timing"]["AMC"]["class_changes"] == 2 and s["by_timing"]["AMC"]["no_stored_value"] == 1


def test_data_quality_flags_exclude_an_outcome_from_the_verified_set():
    t = load_tool()
    # AMC report 27 Oct; the series jumps +100% into the reaction day (a split-like ratio) and a regular row sits on a Saturday
    rows = [r("2026-10-23", "r", "50", 1000), r("2026-10-26", "r", "51", 1000), r("2026-10-27", "r", "50", 1000),
            r("2026-10-28", "r", "100", 1000), r("2026-10-25", "r", "50", 900)]
    snaps = [snap("SPLT", date(2026, 10, 27), "After Close", "BUY")]
    events = t.completed_events(snaps, NOW)
    per_symbol, recs, outcomes = t.analyse(events, {"SPLT": rows}, snaps, NOW)
    f = per_symbol["SPLT"]["flags"]
    assert f["regular_rows_on_non_trading_days"] == ["2026-10-25"] and f["jumps"] and f["possible_split"]
    assert recs[0]["status"] == "GRADED" and recs[0]["data_flag"] is True and recs[0]["verified"] is False
    assert outcomes == {}                                         # flagged data never reaches the verified accuracy


def test_missing_session_thin_volume_and_incomplete_latest_day_are_reported():
    t = load_tool()
    rows = [r("2026-10-20", "r", "10", 1000), r("2026-10-21", "r", "10.1", 1000), r("2026-10-23", "r", "10.2", 1000),
            r("2026-10-26", "r", "10.1", 10), r("2026-10-27", "r", "10.3", 1000), r("2026-10-28", "r", "10.4", 1000)]
    mc, eo, sb = t.load_embedded()
    closes, fb = t.regular_closes(rows)
    fl = t.quality(rows, closes, NOW)
    assert "2026-10-22" in fl["missing_regular_sessions"] and "2026-10-24" not in fl["missing_regular_sessions"]
    assert "2026-10-26" in fl["thin_volume_dates"]
    today_rows = [r("2026-10-29", "r", "10.5", 500)]
    c2, _ = t.regular_closes(rows + today_rows)
    live = datetime(2026, 10, 29, 15, 0, tzinfo=UTC)                  # 11:00 ET: today's session is still open
    assert t.quality(rows + today_rows, c2, live)["latest_session_not_complete"] == "2026-10-29"
    assert t.quality(rows + today_rows, c2, datetime(2026, 10, 29, 21, 0, tzinfo=UTC))["latest_session_not_complete"] is None


def test_symbol_selection_balanced_share_class_first_and_capped():
    t = load_tool()
    mc, eo, sb = t.load_embedded()

    def ev(sym, d, kind, eligible=True):
        base, react = eo.reaction_window(d, kind)
        return {"symbol": sym, "earnings_date": d, "kind": kind, "legacy_move": None, "stamped": True, "eligible": eligible, "base": base, "react": react}
    nm = lambda p, i: p + chr(65 + i // 26) + chr(65 + i % 26)
    events = [ev(nm("A", i), date(2026, 10, 20 + i % 6), "AMC") for i in range(60)] + [ev(nm("B", i), date(2026, 10, 20 + i % 6), "BMO") for i in range(60)]
    events += [ev("BIO.B", date(2026, 10, 22), "BMO"), ev("TAP.A", date(2026, 10, 22), "BMO"), ev("WSO.B", date(2026, 10, 27), "BMO")]
    s = t.pick_symbols(events, 40)
    assert len(s) == len(set(s)) == 40 and s[:3] == ["BIO.B", "TAP.A", "WSO.B"]
    kinds = [next(e["kind"] for e in events if e["symbol"] == x) for x in s[3:]]
    assert abs(kinds.count("AMC") - kinds.count("BMO")) <= 1
    assert len(t.pick_symbols(events, 100000)) <= t.HARD_CEILING and t.pick_symbols(events, 0) == []
    assert t.pick_symbols(events, 40) == s


class FakeUW:
    def __init__(self, answers=None, rows=None):
        self.answers, self.rows, self.calls, self.bg, self.bg_log = answers or {}, rows or {}, [], False, []

    def is_background(self):
        return self.bg

    def set_background(self, v):
        self.bg_log.append(v)
        self.bg = v

    def candles(self, symbol, size, limit=0, date=""):
        self.calls.append((symbol, size, limit))
        a = self.answers.get(symbol, "OK")
        return {"status": a, "data": self.rows.get(symbol, AMKR) if a == "OK" else None,
                "detail": "no ticker" if a == "NO_DATA" else None}

    def _rows(self, out):
        return out.get("data") or []


def test_fetch_obeys_the_request_limit_the_hard_ceiling_and_reports_share_class_answers():
    t = load_tool()
    uw = FakeUW({"WSO.B": "NO_DATA"})
    syms = ["WSO.B"] + [f"S{i:03d}" for i in range(200)]
    fetched, made = t.fetch_all(syms, uw, pause=2.0, cap=100000, sleep=lambda s: None)
    assert made == t.HARD_CEILING == len(uw.calls) and all(c[1] == "1d" for c in uw.calls)
    assert fetched["WSO.B"].startswith("NO_DATA (no ticker)") and uw.bg_log == [True, False]
    uw2 = FakeUW({"S001": "RATE_LIMITED"})
    f2, m2 = t.fetch_all(["S000", "S001", "S002"], uw2, sleep=lambda s: None)
    assert m2 == 2 and "RATE_LIMITED" in f2["S001"] and "skipped" in f2["S002"]


def test_provisional_accuracy_uses_only_eligible_snapshots_and_verified_outcomes():
    t = load_tool()
    snaps = real_snaps()
    snaps.append(snap("AMKR", date(2026, 10, 26), "After Close", "SELL", pred=datetime(2026, 10, 27, 1, 0, tzinfo=UTC)))      # post-release SELL ignored
    events = t.completed_events(snaps, NOW)
    _, recs, outcomes = t.analyse(events, {"AMKR": AMKR, "BANR": BANR, "NEWCO": AMKR}, snaps, NOW)
    acc = t.accuracy(snaps, outcomes, NOW)["claude_new"]
    assert acc["BUY"][:2] == [2, 2]            # AMKR +8.5 and NEWCO (AMKR rows, 27 Oct report: -> reaction 28 Oct 42.21 vs 39.94) both rose
    assert acc["SELL"][:2] == [1, 0]           # BANR SELL, the stock rose
    assert acc["graded"] == 3 and acc["baseline"]["up"] == 3


def test_main_end_to_end_with_fake_provider_and_database(tmp_path, monkeypatch, capsys):
    t = load_tool()
    monkeypatch.setitem(sys.modules, "database", types.ModuleType("database"))
    monkeypatch.setenv("APP_ENV", "production")
    uw = FakeUW({"WSO.B": "NO_DATA"}, {"AMKR": AMKR, "BANR": BANR, "NEWCO": AMKR})
    monkeypatch.setitem(sys.modules, "unusualwhales_service", uw)
    monkeypatch.setattr(t.time, "sleep", lambda s: None)
    snaps = real_snaps() + [snap("WSO.B", date(2026, 10, 27), "Before Open", "BUY")]
    seen = []
    monkeypatch.setattr(t, "_run_ro", lambda sql: (seen.append(sql.split()[0]), snaps)[1])

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(t, "datetime", _Now)
    monkeypatch.setattr(sys, "argv", ["outcome_sample.py", "--out", str(tmp_path / "s.json"), "--max-requests", "3"])
    t.main()
    text = capsys.readouterr().out
    for needle in ("symbols planned: 3 (limit 3", "provider requests made: 3", "SUMMARY:", "SHARE-CLASS SYMBOLS", "WSO.B", "NO_DATA",
                   "LARGEST DIFFERENCES", "PROVISIONAL ACCURACY ON THIS SAMPLE"):
        assert needle in text, needle
    assert seen == ["SELECT"] and len(uw.calls) == 3 and (tmp_path / "s.json").exists()


def test_the_tool_only_reads_and_only_asks_for_daily_candles():
    full = TOOL.read_text(encoding="utf-8")
    src = full.split("def load_embedded", 1)[1]
    for word in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "CREATE ", "TRUNCATE", "commit("):
        assert word not in src, word
    assert src.count("SET TRANSACTION READ ONLY") == 1 and "import swr" not in src and "db_cache" not in src
    assert set(re.findall(r"\buw\.([A-Za-z_]+)\(", src)) == {"is_background", "set_background", "candles", "_rows"}
    assert "HARD_CEILING = 150" in full and src.count("write_text(json") == 1
    t = load_tool()
    mc, eo, sb = t.load_embedded()
    with pytest.raises(RuntimeError, match="disabled in the sample tool"):
        eo.default_bars_fetcher("X")
