"""The Phase 2B read-only replay tool: reproduces the legacy `_historical_moves` bug on provider-shaped candles, counts and caps provider
GETs, stops on a non-OK answer, and never needs the database."""
import importlib.util
import pathlib
import sys
import types

import pytest

import earnings_outcomes as eo
import tab_settings

TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools"


def load_tool():
    spec = importlib.util.spec_from_file_location("moves_replay_under_test", TOOLS / "moves_replay.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def row(day, mt, close):
    return {"date": day, "market_time": mt, "close": str(close), "open": str(close), "volume": 100}


# provider order: within a date pr, po, r ; dates newest first (as the real feed)
def candles_for(days):
    out = []
    for day, pr, po, r in days:
        out += [row(day, "pr", pr), row(day, "po", po), row(day, "r", r)]
    return out


# 2026-10-20 Tue (BMO report), 2026-10-21 Wed, 2026-10-26 Mon (AMC report), 2026-10-27 Tue
DAYS = [("2026-10-27", 39.0, 40.0, 39.94), ("2026-10-26", 38.65, 38.3194, 36.81), ("2026-10-23", 36.0, 36.5, 36.2),
        ("2026-10-21", 30.5, 30.6, 31.0), ("2026-10-20", 29.0, 29.2, 30.0), ("2026-10-19", 28.0, 28.1, 28.4)]


class FakeUw:
    def __init__(self, status="OK", fail_on=None):
        self.calls, self.status, self.fail_on, self.bg = [], status, fail_on, False
        self.hist = [{"report_date": "2026-10-20", "report_time": "premarket", "reported_eps": "1.0"},
                     {"report_date": "2026-10-26", "report_time": "postmarket", "reported_eps": "2.0"}]

    def get(self, path, params=None):
        self.calls.append(path)
        if self.fail_on and self.fail_on in path:
            return {"status": "RATE_LIMITED", "data": None}
        if path.endswith("/earnings"):
            return {"status": self.status, "data": self.hist}
        return {"status": self.status, "data": candles_for(DAYS)}

    def earnings_history(self, symbol):
        return self.get(f"/api/stock/{symbol}/earnings")

    def candles(self, symbol, size="1d", limit=300, date=""):
        return self.get(f"/api/stock/{symbol}/ohlc/{size}", {"limit": limit})

    @staticmethod
    def _rows(payload):
        d = payload.get("data")
        return d if isinstance(d, list) else []

    def is_background(self):
        return self.bg

    def set_background(self, on=True):
        self.bg = on


@pytest.fixture
def env(monkeypatch):
    uw = FakeUw()
    monkeypatch.setitem(sys.modules, "unusualwhales_service", uw)
    import earnings_trade_service as ets
    import claude_last_service as cl
    return uw, ets, cl, load_tool()


def go(env, symbols, cap=100, uw=None):
    u, ets, cl, tool = env
    uw = uw or u
    sys.modules["unusualwhales_service"] = uw            # what earnings_trade_service imports inside _historical_moves
    return tool.run(symbols, uw, ets, eo.regular_session_closes, eo.direction_of, cl._hist_reaction_bias, tab_settings.V3_IM_POINTS, cap, 0, sleep=lambda s: None)


def test_legacy_function_reproduces_the_row_selection_bug_and_the_replay_computes_the_regular_close_moves(env):
    per, made, statuses, stopped = go(env, ["AMKR"])
    v = per["AMKR"]
    ev = {e["date"]: e for e in v["events"]}
    # AMC 2026-10-26: legacy base = first row of the report date (pre-market 38.65), reaction = the next ROW (post-market 38.3194)
    assert ev["2026-10-26"]["legacy"] == round((38.3194 - 38.65) / 38.65 * 100, 2) == -0.86
    # corrected: regular close of the report day (36.81) -> regular close of the next session (39.94)
    assert ev["2026-10-26"]["corrected"] == round((39.94 - 36.81) / 36.81 * 100, 2) == 8.5
    assert ev["2026-10-26"]["class_legacy"] == "SELL" and ev["2026-10-26"]["class_corrected"] == "BUY"
    # BMO 2026-10-20: legacy reaction = pre-market row (29.0) vs prior regular close (28.4); corrected = regular 30.0 vs 28.4
    assert ev["2026-10-20"]["legacy"] == round((29.0 - 28.4) / 28.4 * 100, 2)
    assert ev["2026-10-20"]["corrected"] == round((30.0 - 28.4) / 28.4 * 100, 2)
    assert v["mean_abs_corrected_all"] > v["mean_abs_legacy_all"]
    assert made == 2 and stopped is None and [s for _, s in statuses] == ["OK", "OK"]


def test_summary_counts_sign_flips_class_changes_and_bucket_shifts(env):
    per, *_ = go(env, ["AMKR"])
    tool = env[3]
    s = tool.summarise(per)
    assert s["events_compared"] == 2 and s["AMC"]["class_changes_flat_0.5"] == 1 and s["AMC"]["sign_flips"] == 1
    assert s["ALL"]["mean_abs_corrected"] > s["ALL"]["mean_abs_legacy"]
    b = per["AMKR"]["im_buckets"]
    assert [x["implied_x_corrected_avg"] for x in b] == [0.8, 1.0, 1.2, 1.5]
    assert all(x["ratio_legacy"] > x["ratio_corrected"] for x in b)          # understated history inflates the ratio


def test_request_cap_is_hard_and_a_symbol_is_only_started_when_two_requests_fit(env):
    uw = env[0]
    per, made, statuses, stopped = go(env, ["A", "B", "C"], cap=5)
    assert made == 4 and len(uw.calls) == 4                                   # A and B only; C would need 2 more than the cap
    assert per["C"] == {"skipped": "request cap reached"}


def test_stops_at_the_first_non_ok_answer_and_makes_no_further_requests(env):
    u = FakeUw(fail_on="/ohlc/")
    per, made, statuses, stopped = go(env, ["A", "B", "C"], uw=u)
    assert stopped and "RATE_LIMITED" in stopped and made == 2
    assert per["B"] == {"skipped": stopped} and per["C"] == {"skipped": stopped}


def test_no_data_symbols_are_recorded_not_fatal(env):
    u = FakeUw(status="NO_DATA")
    per, made, statuses, stopped = go(env, ["A", "B"], uw=u)
    assert stopped is None and made == 4 and "NO_DATA" in per["A"]["provider"]


def test_symbol_selection_is_balanced_unique_and_limits_share_classes():
    tool = load_tool()
    ev = [{"symbol": s, "kind": k} for s, k in [("AAA", "BMO"), ("BBB", "BMO"), ("CCC", "AMC"), ("DDD", "AMC"), ("WSO.B", "AMC"), ("BF.B", "BMO"), ("X.Y", "BMO"), ("AAA", "AMC")]]
    sel = tool.select_symbols(ev, 6)
    assert len(sel) == len(set(sel)) == 6 and sum(1 for s in sel if "." in s) <= 2
    assert sel[:5] == ["AAA", "BBB", "CCC", "BF.B", "DDD"]            # alternates BMO / AMC, skips duplicates


def test_tool_never_touches_the_database_or_writes_anything_but_its_output():
    src = (TOOLS / "moves_replay.py").read_text(encoding="utf-8")
    for word in ("SessionLocal", "sqlalchemy", "INSERT ", "UPDATE ", "DELETE ", "execute(", ".commit("):
        assert word not in src, word
    assert src.count("write_text(json") == 1 and "HARD_CEILING = 100" in src
