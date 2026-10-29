"""Phase 2B: the regular-close historical-moves method is opt-in; with the flag off nothing changes (byte-for-byte against the original
function); the new method uses regular closes only, fails closed, and the shadow / method log never alter what is served."""
import random
import sys
import types
from datetime import date, datetime, timedelta, timezone

import pytest

import historical_moves as hm


# ------------------------------------------------------------------------------ the ORIGINAL function, verbatim (38ca61d)
def _f(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def original_historical_moves(uw, symbol, limit=0):
    try:
        timing = {}
        for r in uw._rows(uw.earnings_history(symbol)):
            d = r.get("report_date")
            if d and r.get("reported_eps") not in (None, ""):
                timing[d] = (r.get("report_time") or "").strip().lower()
        dates = sorted(timing)
        if not dates:
            return []
        bars = uw._rows(uw.candles(symbol, "1d", limit=2500))
        bars = [b for b in bars if b.get("date") and _f(b.get("close")) is not None]
        bars.sort(key=lambda b: b["date"])
        closes = [(b["date"], _f(b["close"])) for b in bars]
        out = []
        for d in dates:
            idx = next((i for i, (bd, _) in enumerate(closes) if bd >= d), None)
            if idx is None or idx == 0:
                continue
            after_close = "post" in timing.get(d, "")
            if after_close:
                if idx + 1 >= len(closes):
                    continue
                base, react = closes[idx][1], closes[idx + 1][1]
            else:
                base, react = closes[idx - 1][1], closes[idx][1]
            if base:
                out.append({"date": d, "label": d[:7], "move_pct": round((react - base) / base * 100, 2)})
        return out[-limit:] if limit and limit > 0 else out
    except Exception:  # noqa: BLE001
        return []


# ------------------------------------------------------------------------------ provider-shaped fakes
def row(day, mt, close):
    return {"date": day, "market_time": mt, "open": str(close), "close": str(close), "volume": 100}


# trading days (Mon-Fri) 2026-10-19 .. 2026-10-28 ; provider order: dates newest first, within a date pr, po, r
DAYS = [("2026-10-28", 40.0, 40.5, 42.21), ("2026-10-27", 39.0, 40.0424, 39.94), ("2026-10-26", 38.65, 38.3194, 36.81),
        ("2026-10-23", 36.0, 36.5, 36.2), ("2026-10-22", 31.2, 31.3, 31.5), ("2026-10-21", 30.5, 30.6, 31.0),
        ("2026-10-20", 29.0, 29.2, 30.0), ("2026-10-19", 28.0, 28.1, 28.4)]


def candles(days=DAYS, skip_regular=()):
    out = []
    for day, pr, po, r in days:
        out += [row(day, "pr", pr), row(day, "po", po)]
        if day not in skip_regular:
            out.append(row(day, "r", r))
    return out


class FakeUw:
    def __init__(self, hist, bars):
        self.hist, self.bars, self.calls = hist, bars, []

    def earnings_history(self, symbol):
        self.calls.append("earnings")
        return {"status": "OK", "data": self.hist}

    def candles(self, symbol, size="1d", limit=300, date=""):
        self.calls.append("candles")
        return {"status": "OK", "data": self.bars}

    @staticmethod
    def _rows(payload):
        d = payload.get("data")
        return d if isinstance(d, list) else []


HIST = [{"report_date": "2026-10-20", "report_time": "premarket", "reported_eps": "1.0"},
        {"report_date": "2026-10-26", "report_time": "postmarket", "reported_eps": "2.0"},
        {"report_date": "2026-11-30", "report_time": "premarket", "reported_eps": ""}]       # not reported yet: ignored


@pytest.fixture
def uw(monkeypatch):
    u = FakeUw(HIST, candles())
    monkeypatch.setitem(sys.modules, "unusualwhales_service", u)
    for k in ("HISTORICAL_MOVES_METHOD", "HISTORICAL_MOVES_SHADOW"):
        monkeypatch.delenv(k, raising=False)
    return u


# ------------------------------------------------------------------------------ default = unchanged
def test_default_method_is_legacy_and_flags_are_off(uw):
    assert hm.method() == hm.LEGACY and not hm.shadow_enabled()


@pytest.mark.parametrize("val", ["", "legacy", "LEGACY", "bogus", "0", "regular_closee"])
def test_anything_but_an_explicit_opt_in_is_legacy(uw, monkeypatch, val):
    monkeypatch.setenv("HISTORICAL_MOVES_METHOD", val)
    assert hm.method() == hm.LEGACY


@pytest.mark.parametrize("val", ["regular_close", "REGULAR_CLOSE", "regular", "r"])
def test_explicit_opt_in_values(uw, monkeypatch, val):
    monkeypatch.setenv("HISTORICAL_MOVES_METHOD", val)
    assert hm.method() == hm.REGULAR


def test_with_the_flag_off_output_equals_the_original_function_on_provider_shaped_data(uw):
    import earnings_trade_service as ets
    got = ets._historical_moves("AMKR")
    assert got == original_historical_moves(uw, "AMKR") and got                    # same list, same rounding, same order
    assert ets._historical_moves("AMKR", limit=1) == original_historical_moves(uw, "AMKR", 1)
    assert got[1] == {"date": "2026-10-26", "label": "2026-10", "move_pct": -0.86}  # the known legacy value (pr -> po of the report day)


def test_with_the_flag_off_output_equals_the_original_function_on_random_data(monkeypatch):
    rnd = random.Random(11)
    for trial in range(60):
        days, d0 = [], date(2026, 1, 5)
        for i in range(60):
            d = d0 + timedelta(days=i)
            if d.weekday() >= 5:
                continue
            base = 50 + rnd.uniform(-8, 8)
            days.append((d.isoformat(), base, base * rnd.uniform(.97, 1.03), base * rnd.uniform(.95, 1.05)))
        days.reverse()
        bars = []
        for day, pr, po, r in days:
            kinds = rnd.choice([("pr", "po", "r"), ("r",), ("po", "r"), ("pr", "r")])
            vals = {"pr": pr, "po": po, "r": r}
            bars += [row(day, k, vals[k]) for k in kinds]
        hist = [{"report_date": rnd.choice(days)[0], "report_time": rnd.choice(["premarket", "postmarket", "", "amc", "bmo"]),
                 "reported_eps": rnd.choice(["1", "", None, "0.5"])} for _ in range(rnd.randint(0, 6))]
        u = FakeUw(hist, bars)
        monkeypatch.setitem(sys.modules, "unusualwhales_service", u)
        monkeypatch.delenv("HISTORICAL_MOVES_METHOD", raising=False)
        assert hm.historical_moves("X") == original_historical_moves(u, "X"), trial


def test_provider_request_pattern_is_unchanged(uw):
    hm.historical_moves("AMKR")
    assert uw.calls == ["earnings", "candles"]
    uw.calls.clear()
    uw.hist = [{"report_date": "2026-11-30", "report_time": "premarket", "reported_eps": ""}]
    assert hm.historical_moves("AMKR") == [] and uw.calls == ["earnings"]          # no candle request when nothing was reported


def test_any_failure_still_returns_an_empty_list(monkeypatch):
    class Boom:
        def earnings_history(self, s):
            raise RuntimeError("down")
    monkeypatch.setitem(sys.modules, "unusualwhales_service", Boom())
    assert hm.historical_moves("X") == []


# ------------------------------------------------------------------------------ the regular-close method
def test_regular_close_method_uses_regular_closes_for_bmo_and_amc(uw, monkeypatch):
    monkeypatch.setenv("HISTORICAL_MOVES_METHOD", "regular_close")
    got = {m["date"]: m["move_pct"] for m in hm.historical_moves("AMKR")}
    assert got["2026-10-26"] == round((39.94 - 36.81) / 36.81 * 100, 2) == 8.5            # AMC: report-day close -> next session close
    assert got["2026-10-20"] == round((30.0 - 28.4) / 28.4 * 100, 2)                      # BMO: prior regular close -> report-day regular close
    assert set(got) == {"2026-10-20", "2026-10-26"}


def test_regular_close_skips_an_event_with_a_missing_regular_close_and_never_guesses(uw, monkeypatch):
    monkeypatch.setenv("HISTORICAL_MOVES_METHOD", "regular_close")
    uw.bars = candles(skip_regular={"2026-10-27"})                  # next session after the AMC report has only pr / po rows
    got = {m["date"] for m in hm.historical_moves("AMKR")}
    assert "2026-10-26" not in got and "2026-10-20" in got
    uw.bars = candles(skip_regular={"2026-10-20"})                  # the BMO reaction session itself has no regular row
    assert "2026-10-20" not in {m["date"] for m in hm.historical_moves("AMKR")}


def test_regular_close_does_not_skip_over_a_missing_session_to_a_later_day(uw, monkeypatch):
    monkeypatch.setenv("HISTORICAL_MOVES_METHOD", "regular_close")
    uw.bars = candles(skip_regular={"2026-10-26", "2026-10-27"})    # report day and next day both lack a regular close
    uw.hist = [{"report_date": "2026-10-26", "report_time": "postmarket", "reported_eps": "2"}]
    assert hm.historical_moves("AMKR") == []                        # must not pair 10-23's close with 10-28's


def test_unlabelled_rows_are_never_used_by_the_regular_close_method(uw, monkeypatch):
    monkeypatch.setenv("HISTORICAL_MOVES_METHOD", "regular_close")
    uw.bars = [{**b, "market_time": None} for b in candles()]
    assert hm.historical_moves("AMKR") == []
    assert original_historical_moves(uw, "AMKR")                    # while the legacy method would happily use them


def test_limit_applies_to_both_methods(uw, monkeypatch):
    monkeypatch.setenv("HISTORICAL_MOVES_METHOD", "regular_close")
    assert [m["date"] for m in hm.historical_moves("AMKR", limit=1)] == ["2026-10-26"]


# ------------------------------------------------------------------------------ shadow + method log never change what is served
class Cache:
    def __init__(self):
        self.store = {}

    def put(self, key, value):
        self.store[key] = value

    def get(self, key):
        return (1.0, self.store[key]) if key in self.store else None


@pytest.fixture
def cache(monkeypatch):
    c = Cache()
    monkeypatch.setitem(sys.modules, "db_cache", c)
    hm._shadow_last.clear()
    return c


def test_shadow_is_off_by_default_and_writes_nothing(uw, cache):
    hm.historical_moves("AMKR")
    assert cache.store == {}


def test_shadow_keeps_both_methods_and_serves_the_configured_one(uw, cache, monkeypatch):
    monkeypatch.setenv("HISTORICAL_MOVES_SHADOW", "1")
    served = hm.historical_moves("AMKR")
    calls = list(uw.calls)
    assert served == original_historical_moves(uw, "AMKR")                             # still legacy
    rec = cache.store["moves:shadow:AMKR"]
    assert rec["served"] == "legacy" and {d: m for d, m, k in rec["legacy"]}["2026-10-26"] == -0.86 and {d: m for d, m, k in rec["regular_close"]}["2026-10-26"] == 8.5
    assert [k for d, m, k in rec["legacy"] if d == "2026-10-26"] == ["AMC"] and [k for d, m, k in rec["legacy"] if d == "2026-10-20"] == ["BMO"]
    assert calls == ["earnings", "candles"]                                            # no extra provider requests for the shadow


def test_shadow_is_throttled_and_never_raises(uw, cache, monkeypatch):
    monkeypatch.setenv("HISTORICAL_MOVES_SHADOW", "1")
    hm.historical_moves("AMKR")
    cache.store.clear()
    hm.historical_moves("AMKR")
    assert cache.store == {}                                                           # within the throttle window
    monkeypatch.setitem(sys.modules, "db_cache", types.SimpleNamespace(put=lambda *a: (_ for _ in ()).throw(RuntimeError("db down"))))
    hm._shadow_last.clear()
    assert hm.historical_moves("AMKR") == original_historical_moves(uw, "AMKR")        # a failing cache cannot break the answer


def test_method_log_records_only_changes(cache, monkeypatch):
    monkeypatch.delenv("HISTORICAL_MOVES_METHOD", raising=False)
    assert hm.log_method_if_changed() is False and cache.store == {}                   # legacy and nothing recorded = no change
    monkeypatch.setenv("HISTORICAL_MOVES_METHOD", "regular_close")
    assert hm.log_method_if_changed() is True and hm.log_method_if_changed() is False
    monkeypatch.setenv("HISTORICAL_MOVES_METHOD", "legacy")
    assert hm.log_method_if_changed() is True
    assert [h["method"] for h in hm.method_history()] == ["regular_close", "legacy"]


def test_method_at_splits_history_by_time():
    t = lambda h: datetime(2026, 11, 3, h, tzinfo=timezone.utc)
    hist = [{"method": "regular_close", "at": "2026-11-03T10:00:00+00:00"}, {"method": "legacy", "at": "2026-11-03T14:00:00+00:00"}]
    assert hm.method_at(t(9), hist) == "legacy" and hm.method_at(t(11), hist) == "regular_close" and hm.method_at(t(15), hist) == "legacy"
    assert hm.method_at(t(9), []) == "legacy"


def test_capture_and_scoreboard_expose_the_method(monkeypatch):
    import model_prediction_service as mp
    import earnings_scoreboard as sb
    monkeypatch.setattr(sb, "scoreboard", lambda models, definition="last", **k: {"models": []})
    monkeypatch.setitem(sys.modules, "db_cache", Cache())
    out = mp.scoreboard()
    assert out["input_method_now"] == "legacy" and out["input_method_history"] == [] and out["models"] == []


# ------------------------------------------------------------------------------ the read-only shadow report
def _load_report():
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location("moves_shadow_report_under_test",
                                                  pathlib.Path(__file__).resolve().parent.parent / "tools" / "moves_shadow_report.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_shadow_report_compares_the_two_methods_by_timing():
    r = _load_report()
    recs = {
        "AMKR": {"served": "legacy", "legacy": [["2026-10-20", 2.1, "BMO"], ["2026-10-26", -0.86, "AMC"], ["2026-07-20", 1.0, "BMO"]],
                 "regular_close": [["2026-10-20", 5.6, "BMO"], ["2026-10-26", 8.5, "AMC"]]},          # 2026-07-20 refused (no regular close)
        "BANR": {"served": "legacy", "legacy": [["2026-10-21", 0.0, "AMC"]], "regular_close": [["2026-10-21", 3.01, "AMC"]]},
        "BAD": {"unexpected": True},
    }
    out = r.analyse(recs)
    assert out["symbols"] == 2 and out["legacy_events"] == 4 and out["events_compared"] == 3 and out["refused_by_regular_close_method"] == 1
    assert out["AMC"]["events"] == 2 and out["AMC"]["sign_flips"] == 1 and out["AMC"]["class_changes"] == 2
    assert out["BMO"]["events"] == 1 and out["ALL"]["mean_abs_regular"] > out["ALL"]["mean_abs_legacy"]


def test_shadow_report_is_read_only_and_makes_no_provider_requests():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "tools" / "moves_shadow_report.py").read_text(encoding="utf-8")
    for word in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", ".commit(", "unusualwhales", "candles("):
        assert word not in src, word
    assert "SET TRANSACTION READ ONLY" in src and src.count("write_text(") == 1


def test_shadow_report_loads_rows_and_the_method_log():
    r = _load_report()
    rows = [("moves:shadow:AMKR", '{"served":"legacy","legacy":[["2026-10-26",-0.86,"AMC"]],"regular_close":[["2026-10-26",8.5,"AMC"]]}', 100.0),
            ("moves:method:history", '[{"method":"regular_close","at":"2026-11-03T10:00:00+00:00"}]', 50.0)]
    recs, hist, ages = r.load(lambda sql: rows)
    assert list(recs) == ["AMKR"] and hist[0]["method"] == "regular_close" and ages == [100.0]
