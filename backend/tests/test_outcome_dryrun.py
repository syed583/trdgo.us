"""The provider dry run: sample selection, faithful replica of the legacy index logic, three-way comparison, request
discipline (one GET per symbol, cap, stop on a bad answer, background priority). A fake provider is used throughout."""
import importlib.util
import pathlib
import re
import sys
import types
from datetime import date, datetime, timezone

import pytest

UTC = timezone.utc
TOOL = pathlib.Path(__file__).resolve().parent.parent / "tools" / "outcome_dryrun.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("outcome_dryrun_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def row(day, mt, close, vol):
    return {"date": day, "market_time": mt, "close": str(close), "open": str(close), "total_volume": vol, "volume": vol}


# After Close report Tue 27 Oct: regular closes 100.0 -> 106.0, after-hours 108.0 on the earnings
MULTI = [row("2026-10-26", "pr", 98.0, 400), row("2026-10-26", "r", 99.0, 7000), row("2026-10-26", "po", 99.2, 7100),
         row("2026-10-27", "pr", 99.5, 1000), row("2026-10-27", "r", 100.0, 9000), row("2026-10-27", "po", 108.0, 9500),
         row("2026-10-28", "pr", 107.5, 500), row("2026-10-28", "r", 106.0, 8000), row("2026-10-28", "po", 106.4, 8300)]
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def ev(sym, d, kind, move=None, stamped=False):
    t = load_tool()
    mc, eo, sb = t.load_embedded()
    base, react = eo.reaction_window(d, kind)
    return {"symbol": sym, "earnings_date": d, "kind": kind, "legacy_move": move, "stamped": stamped, "base": base, "react": react}


def test_three_readings_of_the_same_rows():
    t = load_tool()
    sample = [{**ev("X", date(2026, 10, 27), "AMC", 0.0, True), "group": "g"}]
    r = t.analyse(sample, {"X": MULTI}, NOW)[0]
    assert r["candle_shape"]["dates_with_n_rows"] == {3: 3} and r["candle_shape"]["market_time_labels"] == {"pr": 3, "r": 3, "po": 3}
    assert r["CURRENT_fetcher"]["move"] == -1.48                        # last row per date: after-hours contaminates the base
    assert r["FIXED_regular_close"]["move"] == 6.0 and r["FIXED_regular_close"]["status"] == "GRADED"
    assert r["stored_legacy_move"] == 0.0 and r["expected_reaction_date"] == "2026-10-28"


def test_legacy_logic_replica_matches_the_real_legacy_function(monkeypatch):
    import earnings_trade_service as ets
    import unusualwhales_service as uw
    t = load_tool()
    for report_time, kind in (("postmarket", "AMC"), ("premarket", "BMO")):
        monkeypatch.setattr(uw, "earnings_history", lambda s, rt=report_time: {"status": "OK", "data": [
            {"report_date": "2026-10-27", "report_time": rt, "reported_eps": "1.0"}]})
        monkeypatch.setattr(uw, "candles", lambda s, size, limit=0, date="": {"status": "OK", "data": MULTI})
        real = ets._historical_moves("X")
        assert real and t.legacy_move(MULTI, date(2026, 10, 27), kind) == real[0]["move_pct"]
    # single-row-per-date data: both agree as well
    single = [row("2026-10-26", None, 99.0, 1), row("2026-10-27", None, 100.0, 1), row("2026-10-28", None, 106.0, 1)]
    monkeypatch.setattr(uw, "earnings_history", lambda s: {"status": "OK", "data": [
        {"report_date": "2026-10-27", "report_time": "postmarket", "reported_eps": "1.0"}]})
    monkeypatch.setattr(uw, "candles", lambda s, size, limit=0, date="": {"status": "OK", "data": single})
    assert t.legacy_move(single, date(2026, 10, 27), "AMC") == ets._historical_moves("X")[0]["move_pct"] == 6.0


def test_sample_selection_is_small_varied_unique_and_capped():
    t = load_tool()
    events = []
    def name(prefix, i):                                   # alphabetic fake symbols (digits would count as share-class)
        return prefix + chr(65 + i // 26) + chr(65 + i % 26)
    for i in range(40):
        events.append(ev(name("A", i), date(2026, 10, 20 + i % 7 if i % 7 < 6 else 20), "AMC", None if i % 2 else 0.0, bool(i % 3)))
    for i in range(60):
        events.append(ev(name("B", i), date(2026, 10, 21 + i % 5), "BMO", [None, 0.05, 3.5, 0.3, 7.0][i % 5], i % 2 == 0))
    events.append(ev("BIO.B", date(2026, 10, 22), "BMO", None, True))
    s = t.pick_sample(events)
    syms = [x["symbol"] for x in s]
    assert len(s) == len(set(syms)) <= 25 and 15 <= len(s)
    groups = {x["group"] for x in s}
    assert {"AMC legacy-graded (invalid candidates)", "BMO legacy near zero (<=0.5%)", "BMO legacy large (>2%)",
            "BMO stamped without a move", "share-class symbol"} <= groups
    assert "BIO.B" in syms
    assert t.pick_sample(events) == s                                     # deterministic


class FakeUW:
    def __init__(self, answers):
        self.answers, self.calls, self.bg, self.bg_log = answers, [], False, []

    def is_background(self):
        return self.bg

    def set_background(self, v):
        self.bg_log.append(v)
        self.bg = v

    def candles(self, symbol, size, limit=0, date=""):
        self.calls.append((symbol, size, limit))
        a = self.answers.get(symbol, "OK")
        return {"status": a, "data": MULTI if a == "OK" else None}

    def _rows(self, out):
        return out.get("data") or []


def test_fetch_is_sequential_paced_background_and_capped():
    t = load_tool()
    uw = FakeUW({})
    pauses = []
    syms = [f"S{i:02d}" for i in range(30)]
    fetched, made = t.fetch_all(syms, uw, pause=2.0, cap=25, sleep=lambda s: pauses.append(s))
    assert made == 25 and len(uw.calls) == 25 and all(c[1] == "1d" for c in uw.calls)       # one daily-candle GET per symbol
    assert pauses and set(pauses) == {2.0}
    assert fetched["S24"] == MULTI and "cap" in fetched["S25"] and uw.bg_log == [True, False]   # background, then restored


def test_fetch_stops_at_the_first_bad_answer():
    t = load_tool()
    uw = FakeUW({"S02": "RATE_LIMITED"})
    fetched, made = t.fetch_all(["S00", "S01", "S02", "S03", "S04"], uw, sleep=lambda s: None)
    assert made == 3 and [c[0] for c in uw.calls] == ["S00", "S01", "S02"]
    assert "RATE_LIMITED" in fetched["S02"] and "skipped" in fetched["S03"] and "skipped" in fetched["S04"]
    uw2 = FakeUW({"S01": "NO_DATA"})
    f2, m2 = t.fetch_all(["S00", "S01", "S02"], uw2, sleep=lambda s: None)
    assert m2 == 3 and "NO_DATA" in f2["S01"] and f2["S02"] == MULTI          # an unknown symbol string does not stop the run


def test_main_end_to_end_with_a_fake_provider_and_database(tmp_path, monkeypatch, capsys):
    t = load_tool()
    monkeypatch.setitem(sys.modules, "database", types.ModuleType("database"))
    monkeypatch.setenv("APP_ENV", "production")
    uw = FakeUW({})
    monkeypatch.setitem(sys.modules, "unusualwhales_service", uw)
    monkeypatch.setattr(t.time, "sleep", lambda s: None)
    rows = []
    for sym, kind, mv in (("AAA", "Before Open", 0.0), ("BBB", "After Close", 0.0), ("CCC", "After Close", None), ("DDD", "Before Open", None)):
        rows.append({"model": "claude_new", "symbol": sym, "earnings_date": date(2026, 10, 27), "report_timing": kind,
                     "snapshot_date": date(2026, 10, 26), "predicted_at": datetime(2026, 10, 26, 4, 21, tzinfo=UTC),
                     "evaluated_at": datetime(2026, 10, 28, 4, 0, tzinfo=UTC), "actual_move_pct": mv})
    seen = []
    monkeypatch.setattr(t, "_run_ro", lambda sql: (seen.append(sql.split()[0]), rows)[1])
    monkeypatch.setattr(sys, "argv", ["outcome_dryrun.py", "--out", str(tmp_path / "d.json")])

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(t, "datetime", _Now)
    t.main()
    text = capsys.readouterr().out
    assert "CANDLE SHAPE across the fetched symbols" in text and "provider requests made: 4" in text
    assert seen == ["SELECT"] and len(uw.calls) == 4 and (tmp_path / "d.json").exists()


def test_the_tool_can_only_read_and_only_asks_for_daily_candles():
    src = TOOL.read_text(encoding="utf-8").split("def load_embedded", 1)[1]
    for word in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "CREATE ", "TRUNCATE", "commit(", "write_text(json"):
        if word == "write_text(json":
            assert src.count("write_text(json") == 1
        else:
            assert word not in src, word
    assert src.count("SET TRANSACTION READ ONLY") == 1 and "import swr" not in src and "db_cache" not in src
    calls = set(re.findall(r"\buw\.([A-Za-z_]+)\(", src))
    assert calls == {"is_background", "set_background", "candles", "_rows"}, calls
    assert "MAX_REQUESTS = 25" in TOOL.read_text(encoding="utf-8")
    t = load_tool()
    mc, eo, sb = t.load_embedded()
    with pytest.raises(RuntimeError, match="disabled in the dry run"):
        eo.default_bars_fetcher("X")
