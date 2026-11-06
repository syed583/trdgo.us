"""Pre-Earnings Direction capture: new predictions carry the genuine UTC capture time and report timing; the date-only
backfill can neither pre-empt nor rewrite them; old date-only history stays RETROSPECTIVE; other models are unchanged."""
import inspect
import sys
import types
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import earnings_outcomes as eo
import earnings_scoreboard as sb
import model_prediction_service as mp

UTC = timezone.utc
MODELS8 = {"claude_new": "Claude", "claude_pro": "Claude Pro", "gemini": "Gemini", "grok": "Grok", "chatgpt": "ChatGPT",
           "meta": "Meta", "kimi": "Kimi", "ped": "Pre-Earnings Direction"}


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Session = sessionmaker(bind=engine)
    s = Session()
    s.execute(text(
        "CREATE TABLE model_predictions (id INTEGER PRIMARY KEY AUTOINCREMENT, model VARCHAR(24) NOT NULL,"
        " symbol VARCHAR(20) NOT NULL, earnings_date DATE, report_timing VARCHAR(16), snapshot_date DATE NOT NULL,"
        " predicted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, signal VARCHAR(10) NOT NULL, score DOUBLE PRECISION,"
        " actual_move_pct DOUBLE PRECISION, actual_direction VARCHAR(10), correct SMALLINT, evaluated_at TIMESTAMP,"
        " UNIQUE (model, symbol, earnings_date, snapshot_date))"))
    eo.ensure_table(s)
    s.commit()
    fake_db = types.ModuleType("database")
    fake_db.SessionLocal = Session
    monkeypatch.setitem(sys.modules, "database", fake_db)
    monkeypatch.setattr(mp, "_ensure_table", lambda: True)
    yield s, Session
    s.close()


def stub_scan(monkeypatch, edate, timing, symbols=("AMKR",)):
    cells = {k: {"signal": "BUY", "score": 61} for k in ("claude", "claude_pro", "gemini", "grok", "chatgpt", "meta", "kimi", "ped")}
    rows = [{"symbol": sym, "date": edate.isoformat(), "report_timing": timing, "cells": cells} for sym in symbols]
    am = types.ModuleType("all_models_service")
    am.scan = lambda days=15: {"rows": rows}
    am.MODELS = [(k, k) for k in cells]
    monkeypatch.setitem(sys.modules, "all_models_service", am)


def stored(s, model="ped"):
    return s.execute(text("SELECT model, symbol, earnings_date, report_timing, snapshot_date, predicted_at, signal, score "
                          "FROM model_predictions WHERE model = :m ORDER BY id"), {"m": model}).all()


def as_dt(v):
    v = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    return v if v.tzinfo else v.replace(tzinfo=UTC)


# ------------------------------------------------------------- new PED predictions are genuine and timed ----
def test_capture_stores_the_real_utc_time_the_earnings_date_and_the_report_timing(env, monkeypatch):
    s, _ = env
    edate = date.today() + timedelta(days=3)
    stub_scan(monkeypatch, edate, "After Close")
    t0 = datetime.now(UTC) - timedelta(seconds=2)
    assert mp.capture(days=15) == 8
    t1 = datetime.now(UTC) + timedelta(seconds=2)
    (r,) = stored(s)
    assert r.symbol == "AMKR" and eo._d(r.earnings_date) == edate and r.report_timing == "After Close" and r.signal == "BUY"
    at = as_dt(r.predicted_at)
    assert t0 <= at <= t1 and (at.hour, at.minute, at.second, at.microsecond) != (0, 0, 0, 0)
    assert sb.classify(edate, r.report_timing, at) == sb.BEFORE            # captured on an earlier ET date than the release


def test_every_model_gets_the_same_capture_time_and_other_models_are_unchanged(env, monkeypatch):
    s, _ = env
    stub_scan(monkeypatch, date.today() + timedelta(days=2), "Before Open")
    mp.capture(days=15)
    allrows = s.execute(text("SELECT model, signal, score, report_timing, predicted_at FROM model_predictions")).all()
    assert {r.model for r in allrows} == set(MODELS8)                           # 'claude' column is stored as claude_new
    assert len({str(r.predicted_at) for r in allrows}) == 1                     # one capture instant, as the DB default gave before
    assert all(r.signal == "BUY" and r.score == 61 and r.report_timing == "Before Open" for r in allrows)


def test_a_midnight_utc_clock_reading_is_nudged_so_it_is_never_mistaken_for_date_only():
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 3, 0, 0, 0, 0, tzinfo=UTC)
    orig = mp.datetime
    mp.datetime = Frozen
    try:
        ts = mp.capture_timestamp()
    finally:
        mp.datetime = orig
    assert ts == datetime(2026, 11, 3, 0, 0, 0, 1, tzinfo=UTC) and ts.tzinfo is not None
    assert sb.classify(date(2026, 11, 5), "Before Open", ts) == sb.BEFORE


@pytest.mark.parametrize("edate_offset, timing, captured_et, expected", [
    (1, "Before Open", (10, 0), sb.BEFORE),          # captured the day before: before the release either way
    (1, None, (23, 30), sb.BEFORE),                  # timing unknown but an earlier ET date: still demonstrably before
    (0, "After Close", (15, 30), sb.BEFORE),         # same day, 15:30 ET, release at/after 16:00
    (0, "After Close", (16, 30), sb.AFTER),
    (0, "Before Open", (5, 30), sb.BEFORE),          # 05:30 ET, before the provisional 06:00 earliest release
    (0, "Before Open", (7, 30), sb.AMBIGUOUS),       # between 06:00 and 09:30: cannot be shown to precede the release
    (0, "Before Open", (10, 0), sb.AFTER),
    (0, None, (9, 0), sb.AMBIGUOUS),                 # same day and no timing: never eligible
])
def test_eligibility_requires_a_capture_demonstrably_before_the_announcement(env, monkeypatch, edate_offset, timing, captured_et, expected):
    s, _ = env
    from zoneinfo import ZoneInfo
    edate = date.today() + timedelta(days=edate_offset)
    stub_scan(monkeypatch, edate, timing)
    et_day = edate if edate_offset == 0 else edate - timedelta(days=1)
    at = datetime(et_day.year, et_day.month, et_day.day, captured_et[0], captured_et[1], 7, 123456, tzinfo=ZoneInfo("America/New_York"))
    monkeypatch.setattr(mp, "capture_timestamp", lambda: at.astimezone(UTC))
    mp.capture(days=15)
    (r,) = stored(s)
    assert sb.classify(eo._d(r.earnings_date), r.report_timing, as_dt(r.predicted_at)) == expected
    # and the scoreboard agrees: only BEFORE_RELEASE becomes an eligible, gradable PED prediction
    rows = [{"model": "ped", "symbol": r.symbol, "earnings_date": eo._d(r.earnings_date), "report_timing": r.report_timing,
             "predicted_at": as_dt(r.predicted_at), "signal": r.signal, "score": r.score}]
    out = {(r.symbol, eo._d(r.earnings_date)): {"status": eo.GRADED, "move_pct": 3.0, "direction": "BUY", "timing": "BMO"}}
    m = sb.build(rows, out, {"ped": "PED"}, datetime.now(UTC) + timedelta(days=30))["models"][0]
    assert m["events_eligible"] == (1 if expected == sb.BEFORE else 0)
    assert m["buy"]["graded"] == (1 if expected == sb.BEFORE else 0)


# ------------------------------------------------------------- old date-only history stays RETROSPECTIVE ----
def test_date_only_history_is_retrospective_unverifiable_and_noted():
    old = [{"model": "ped", "symbol": "AAA", "earnings_date": date(2026, 10, 21), "report_timing": None,
            "predicted_at": datetime(2026, 10, 20, 0, 0, 0, tzinfo=UTC), "signal": "BUY", "score": 70.0}]
    out = {("AAA", date(2026, 10, 21)): {"status": eo.GRADED, "move_pct": 3.0, "direction": "BUY", "timing": "BMO"}}
    m = sb.build(old, out, {"ped": "PED"}, datetime(2026, 10, 29, tzinfo=UTC))["models"][0]
    assert m["events_eligible"] == 0 and m["events_graded"] == 0 and m["excluded"]["retrospective_only"] == 1
    assert m["rows_by_class"] == {"RETROSPECTIVE": 1} and "date-only" in m["history_note"]
    # a model with any eligible event gets no note
    ok = old + [{**old[0], "predicted_at": datetime(2026, 10, 20, 4, 21, 7, tzinfo=UTC), "report_timing": "Before Open"}]
    assert sb.build(ok, out, {"ped": "PED"}, datetime(2026, 10, 29, tzinfo=UTC))["models"][0]["history_note"] is None


def test_capture_never_rewrites_an_existing_record(env, monkeypatch):
    s, _ = env
    edate = date.today() + timedelta(days=2)
    midnight = datetime(date.today().year, date.today().month, date.today().day, tzinfo=UTC)
    s.execute(text("INSERT INTO model_predictions (model, symbol, earnings_date, report_timing, snapshot_date, predicted_at, signal, score)"
                   " VALUES ('ped', 'AMKR', :e, NULL, :d, :p, 'SELL', 40)"), {"e": edate, "d": date.today(), "p": midnight})
    s.commit()
    stub_scan(monkeypatch, edate, "After Close")
    mp.capture(days=15)
    (r,) = stored(s)
    assert r.signal == "SELL" and r.score == 40 and r.report_timing is None and as_dt(r.predicted_at) == midnight   # untouched


# ------------------------------------------------------------- the backfill cannot pre-empt or recreate genuine records ----
class RecordingDb:
    def __init__(self):
        self.calls = []

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params or {}))
        return types.SimpleNamespace(rowcount=0)

    def commit(self): pass
    def rollback(self): pass
    def close(self): pass


def test_backfill_copies_only_past_dates_and_only_inserts(monkeypatch):
    rec = RecordingDb()
    fake_db = types.ModuleType("database")
    fake_db.SessionLocal = lambda: rec
    monkeypatch.setitem(sys.modules, "database", fake_db)
    monkeypatch.setattr(mp, "_ensure_table", lambda: True)
    mp.backfill_ped()
    (sql, params), = rec.calls
    assert "snapshot_date < :today" in sql and params == {"today": date.today()}
    assert "ON CONFLICT (model, symbol, earnings_date, snapshot_date) DO NOTHING" in sql
    for word in ("DO UPDATE", "UPDATE ", "DELETE ", "DROP "):
        assert word not in sql.upper().replace("ON CONFLICT", "")
    assert "NULL, snapshot_date" in sql and "snapshot_date::timestamptz" in sql          # date-only stamp, no timing: stays RETROSPECTIVE
    assert "date-only" in inspect.getdoc(mp.backfill_ped) and "RETROSPECTIVE" in inspect.getdoc(mp.backfill_ped)


def test_the_other_backfill_and_main_loop_are_untouched():
    src = inspect.getsource(mp.backfill_claude_pm)
    assert "'claude_pm'" in src and "snapshot_date::timestamptz" in src            # unchanged (a retired id the scoreboard does not score)
    assert "claude_pm" not in dict(mp._model_labels())
