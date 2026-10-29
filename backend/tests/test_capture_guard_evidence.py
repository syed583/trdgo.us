"""Prediction capture guard (freshness + provisional release cut-off) and append-only evidence.

Everything runs against an in-memory SQLite database and mocked caches: no production database, no provider. The PostgreSQL
DDL of the evidence tables is NOT exercised here (no PostgreSQL in the isolated environment); see the delivery notes."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import model_prediction_service as mps
import prediction_evidence_service as pes

NAMES = ["eps_estimates", "revenue_estimates", "guidance", "historical_reaction", "price_action", "sector_market",
         "options_positioning", "unusual_options", "disparity", "dark_pool", "peer_readthrough", "short_insider"]
WEIGHTS = dict(zip(NAMES, [16, 12, 8, 6, 14, 8, 10, 5, 4, 3, 8, 6]))


def utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def equity_payload(biases=None):
    biases = biases or {}
    params = [{"name": n, "label": n, "weight": WEIGHTS[n], "available": True, "bias": biases.get(n, 0.3),
               "points": round(WEIGHTS[n] * biases.get(n, 0.3), 2), "leaning": "Bullish", "unavailable_reason": "", "evidence": {}}
              for n in NAMES]
    return {"engine": "EQUITY", "profile": "default", "decision": "BUY", "score": 65.0, "coverage_pct": 100.0, "confidence": 80.0,
            "params": params, "signals": {"price_action": 0.2}, "short_interest": {"pct_float": None, "days_to_cover": 3.1, "as_of": None}}


# Set TRDGO_PG_TEST_URL to a LOCAL throw-away PostgreSQL (127.0.0.1 / localhost only) to run this whole file against PostgreSQL with the
# production DDL of model_predictions; unset (default) it runs on in-memory SQLite.
PG_URL = os.environ.get("TRDGO_PG_TEST_URL", "")


@pytest.fixture
def env(monkeypatch):
    if PG_URL:
        assert ("127.0.0.1" in PG_URL or "localhost" in PG_URL) and "supabase" not in PG_URL, "PostgreSQL test URL must be local"
        eng = create_engine(PG_URL)
        with eng.begin() as c:
            c.execute(text("DROP TABLE IF EXISTS prediction_evidence, earnings_input_snapshots, model_predictions CASCADE"))
        pes._ready_for.clear()
        monkeypatch.setattr(mps, "_ready", False)       # the real (production) CREATE TABLE of model_predictions runs
    else:
        eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        with eng.begin() as c:
            c.execute(text("CREATE TABLE model_predictions (id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT NOT NULL, symbol TEXT NOT NULL, "
                           "earnings_date DATE, report_timing TEXT, snapshot_date DATE NOT NULL, predicted_at TEXT, signal TEXT NOT NULL, "
                           "score REAL, UNIQUE (model, symbol, earnings_date, snapshot_date))"))
        monkeypatch.setattr(mps, "_ensure_table", lambda: True)
    Session = sessionmaker(bind=eng)
    import database
    monkeypatch.setattr(database, "SessionLocal", Session)
    import historical_moves
    monkeypatch.setattr(historical_moves, "log_method_if_changed", lambda: False)
    monkeypatch.delenv("EARN_CAPTURE_GUARD", raising=False)
    monkeypatch.delenv("EARN_EVIDENCE", raising=False)
    state = {"rows": [], "cache": {}, "scan_days": []}

    import all_models_service as am

    def fake_scan(days=15):
        state["scan_days"].append(days)
        return {"rows": state["rows"]}
    monkeypatch.setattr(am, "scan", fake_scan)
    import swr
    monkeypatch.setattr(swr, "peek", lambda k: (state["cache"].get(k) or (None, None))[0])
    monkeypatch.setattr(swr, "held_at", lambda k: (state["cache"].get(k) or (None, None))[1])

    class Env:
        engine = eng
        sess = Session

        def put_cache(self, sym, now, age_h, biases=None):
            state["cache"][f"earn:equity:{sym}"] = (equity_payload(biases), now.timestamp() - age_h * 3600)

        def set_rows(self, *rows):
            state["rows"] = list(rows)

        scan_days = state["scan_days"]

        def predictions(self):
            with eng.begin() as c:
                return c.execute(text("SELECT model, symbol, earnings_date, snapshot_date, signal, score FROM model_predictions ORDER BY id")).fetchall()

        def evidence(self):
            with eng.begin() as c:
                return c.execute(text("SELECT model, symbol, status, guard_mode, guard_decision, revision_no, supersedes_id, signal, score, "
                                      "snapshot_hash FROM prediction_evidence ORDER BY id")).fetchall()

        def tables(self):
            from sqlalchemy import inspect
            return set(inspect(eng).get_table_names())
    return Env()


def row(sym, ed, timing, claude=("BUY", 80.0), grok=("BUY", 75.0)):
    return {"symbol": sym, "date": ed, "report_timing": timing,
            "cells": {"claude": {"signal": claude[0], "score": claude[1]}, "grok": {"signal": grok[0], "score": grok[1]}}}


NOW = utc(2026, 10, 29, 4, 26)              # the production capture batch: 00:26 ET on 29 Oct


def test_default_shadow_mode_writes_exactly_what_it_always_wrote_and_judges_without_blocking(env):
    env.set_rows(row("AAA", "2026-10-29", "After Close"), row("BBB", "2026-10-29", "After Close"))
    env.put_cache("AAA", NOW, 3.0)
    env.put_cache("BBB", NOW, 40.0)                     # stale for a name reporting today
    n = mps.capture(days=21, now=NOW)
    assert n == 4                                        # nothing blocked in shadow mode
    assert mps.last_capture_report["mode"] == "shadow"
    assert mps.last_capture_report["rows_judged"]["STALE"] == 2 and mps.last_capture_report["rows_judged"]["OK"] == 2
    assert env.scan_days == [21]                         # window untouched in shadow mode
    assert "prediction_evidence" not in env.tables()     # evidence is off by default: nothing created


def test_enforce_skips_stale_inputs_and_missing_caches_but_keeps_fresh_ones(env, monkeypatch):
    monkeypatch.setenv("EARN_CAPTURE_GUARD", "enforce")
    env.set_rows(row("AAA", "2026-10-29", "After Close"), row("BBB", "2026-10-29", "After Close"), row("CCC", "2026-10-29", "After Close"))
    env.put_cache("AAA", NOW, 3.0)
    env.put_cache("BBB", NOW, 40.0)
    n = mps.capture(days=21, now=NOW)                    # CCC has no cache at all
    assert n == 2
    assert {p[1] for p in env.predictions()} == {"AAA"}
    rep = mps.last_capture_report["rows_judged"]
    assert rep["OK"] == 2 and rep["STALE"] == 2 and rep["NO_CACHE"] == 2


def test_enforce_never_captures_after_the_provisional_release_cutoff(env, monkeypatch):
    monkeypatch.setenv("EARN_CAPTURE_GUARD", "enforce")
    noon = utc(2026, 10, 29, 16, 0)                     # 12:00 ET on the report day
    env.set_rows(row("BMO1", "2026-10-29", "Before Open"), row("AMC1", "2026-10-29", "After Close"),
                 row("UNK1", "2026-10-29", None), row("UNK2", "2026-10-30", None), row("BMO2", "2026-10-30", "Before Open"))
    for s in ("BMO1", "AMC1", "UNK1", "UNK2", "BMO2"):
        env.put_cache(s, noon, 2.0)
    mps.capture(days=21, now=noon)
    assert {p[1] for p in env.predictions()} == {"AMC1", "UNK2", "BMO2"}


def test_before_open_name_is_accepted_overnight_but_not_after_the_morning_window(env, monkeypatch):
    monkeypatch.setenv("EARN_CAPTURE_GUARD", "enforce")
    env.set_rows(row("BMO1", "2026-10-29", "Before Open"))
    env.put_cache("BMO1", NOW, 3.0)
    assert mps.capture(days=21, now=NOW) == 2
    later = utc(2026, 10, 29, 12, 0)                    # 08:00 ET: inside the ambiguous window
    env.put_cache("BMO1", later, 3.0)
    env.set_rows(row("BMO2", "2026-10-29", "Before Open"))
    env.put_cache("BMO2", later, 3.0)
    assert mps.capture(days=21, now=later) == 0


def test_enforce_shrinks_the_window_to_the_warmers_window(env, monkeypatch):
    monkeypatch.setenv("EARN_CAPTURE_GUARD", "enforce")
    monkeypatch.setattr(mps, "capture_window_days", lambda: 14)
    mps.capture(days=21, now=NOW)
    assert env.scan_days == [14]
    mps.capture(days=7, now=NOW)
    assert env.scan_days == [14, 7]                      # never widened, only narrowed


def test_snapshot_date_keeps_its_legacy_server_local_meaning(env):
    env.set_rows(row("AAA", "2026-10-30", "After Close"))
    env.put_cache("AAA", NOW, 2.0)
    assert mps.capture(days=21, now=NOW) == 2
    assert {str(p[3]) for p in env.predictions()} == {str(mps._snapshot_day(NOW))}


def test_enforce_uses_the_us_eastern_date_and_still_refuses_the_evening_after_the_release(env, monkeypatch):
    monkeypatch.setenv("EARN_CAPTURE_GUARD", "enforce")
    evening = utc(2026, 10, 29, 1, 6)                   # 21:06 ET on 28 Oct
    env.set_rows(row("AAA", "2026-10-28", "After Close"), row("BBB", "2026-10-27", "After Close"))
    env.put_cache("AAA", evening, 2.0)
    env.put_cache("BBB", evening, 2.0)
    assert mps.capture(days=21, now=evening) == 0        # AAA: still the 28th in New York but 5 h after the release; BBB: date passed
    assert mps.last_capture_report["rows_judged"]["POST_CUTOFF"] == 2


def test_shadow_mode_keeps_the_legacy_server_date_eligibility(env):
    # Legacy rule: an event dated before the server's local date is skipped. Shadow mode must not widen that.
    old = utc(2026, 10, 29, 1, 6)
    local_day = mps._snapshot_day(old)
    ed = local_day
    env.set_rows(row("AAA", str(ed), "After Close"), row("BBB", str(date.fromordinal(ed.toordinal() - 1)), "After Close"))
    env.put_cache("AAA", old, 2.0)
    env.put_cache("BBB", old, 2.0)
    assert mps.capture(days=21, now=old) == 2            # only AAA (dated today) is captured; BBB (yesterday) is skipped as before
    assert {p[1] for p in env.predictions()} == {"AAA"}


def test_first_write_wins_and_models_are_independent(env, monkeypatch):
    monkeypatch.setenv("EARN_CAPTURE_GUARD", "off")
    env.set_rows(row("AAA", "2026-10-29", "After Close", claude=("BUY", 80.0), grok=("BUY", 75.0)))
    assert mps.capture(days=21, now=NOW) == 2
    env.set_rows(row("AAA", "2026-10-29", "After Close", claude=("SELL", 40.0), grok=("BUY", 75.0)))
    assert mps.capture(days=21, now=NOW) == 0
    stored = {p[0]: (p[4], p[5]) for p in env.predictions()}
    assert stored == {"claude_new": ("BUY", 80.0), "grok": ("BUY", 75.0)}


def test_guard_off_judges_nothing(env, monkeypatch):
    monkeypatch.setenv("EARN_CAPTURE_GUARD", "off")
    env.set_rows(row("AAA", "2026-10-29", "After Close"))
    assert mps.capture(days=21, now=NOW) == 2
    assert mps.last_capture_report["rows_judged"] == {"OK": 0, "STALE": 0, "NO_CACHE": 0, "POST_CUTOFF": 0}


# ---------------------------------------------------------------- evidence
def test_evidence_records_inputs_once_and_every_decision(env, monkeypatch):
    monkeypatch.setenv("EARN_EVIDENCE", "on")
    monkeypatch.setenv("EARN_CAPTURE_GUARD", "enforce")
    env.set_rows(row("AAA", "2026-10-29", "After Close"), row("BBB", "2026-10-29", "After Close"))
    env.put_cache("AAA", NOW, 3.0, {"disparity": 0.0, "unusual_options": 1.0})
    env.put_cache("BBB", NOW, 40.0)
    mps.capture(days=21, now=NOW)
    ev = env.evidence()
    assert len(ev) == 4                                           # 2 events x 2 models
    by = {(e[0], e[1]): e for e in ev}
    assert by[("claude_new", "AAA")][2] == "CAPTURED" and by[("claude_new", "AAA")][4] == "OK"
    assert by[("claude_new", "BBB")][2] == "SKIPPED" and by[("claude_new", "BBB")][4] == "STALE"
    assert by[("claude_new", "AAA")][9] == by[("grok", "AAA")][9]                 # same input snapshot, stored once
    with env.engine.begin() as c:
        snaps = c.execute(text("SELECT payload_json FROM earnings_input_snapshots")).fetchall()
    assert len(snaps) == 2
    body = json.loads([s[0] for s in snaps if '"symbol":"AAA"' in s[0]][0])
    p = {x["name"]: x for x in body["params"]}
    assert p["disparity"]["flags"] == ["ZERO_BIAS_MAY_BE_A_DEFAULT"] and p["unusual_options"]["flags"] == ["SATURATED"]
    assert p["eps_estimates"]["weight"] == 16 and p["eps_estimates"]["source"].startswith("Unusual Whales")
    assert body["short_interest"]["as_of"] is None and body["cache_updated_epoch"] is not None


def test_a_revision_is_appended_and_the_original_row_is_never_changed(env, monkeypatch):
    monkeypatch.setenv("EARN_EVIDENCE", "on")
    monkeypatch.setenv("EARN_CAPTURE_GUARD", "shadow")
    env.set_rows(row("AAA", "2026-10-29", "After Close", claude=("BUY", 80.0), grok=("BUY", 75.0)))
    env.put_cache("AAA", NOW, 3.0)
    mps.capture(days=21, now=NOW)
    mps.capture(days=21, now=NOW)                                 # identical decision: no new evidence
    assert len(env.evidence()) == 2
    later = utc(2026, 10, 29, 16, 26)
    env.set_rows(row("AAA", "2026-10-29", "After Close", claude=("SELL", 44.0), grok=("BUY", 75.0)))
    env.put_cache("AAA", later, 2.0, {"price_action": -0.9})
    mps.capture(days=21, now=later)
    ev = [e for e in env.evidence() if e[0] == "claude_new"]
    assert [(e[7], e[5]) for e in ev] == [("BUY", 1), ("SELL", 2)]
    assert ev[1][6] is not None                                   # supersedes the first row
    assert ev[0][9] != ev[1][9]                                   # different inputs, different snapshot
    # the stored prediction keeps the FIRST call (first-write-wins) while the evidence shows the revision beside it
    assert [(p[0], p[4]) for p in env.predictions() if p[0] == "claude_new"] == [("claude_new", "BUY")]


def test_evidence_tables_reject_update_and_delete(env, monkeypatch):
    monkeypatch.setenv("EARN_EVIDENCE", "on")
    env.set_rows(row("AAA", "2026-10-29", "After Close"))
    env.put_cache("AAA", NOW, 3.0)
    mps.capture(days=21, now=NOW)
    for stmt in ("UPDATE prediction_evidence SET signal = 'SELL'", "DELETE FROM prediction_evidence",
                 "UPDATE earnings_input_snapshots SET symbol = 'X'", "DELETE FROM earnings_input_snapshots"):
        with env.engine.connect() as c:                     # one connection per statement: a failed statement aborts a PostgreSQL transaction
            with pytest.raises(Exception) as exc:
                c.execute(text(stmt))
            assert "append-only" in str(exc.value)
            c.rollback()
    with env.engine.connect() as c:                          # and the rows are all still there
        assert c.execute(text("SELECT count(*) FROM prediction_evidence")).scalar() == 2


def test_history_returns_original_and_revisions_with_their_inputs(env, monkeypatch):
    monkeypatch.setenv("EARN_EVIDENCE", "on")
    env.set_rows(row("AAA", "2026-10-29", "After Close", claude=("BUY", 80.0)))
    env.put_cache("AAA", NOW, 3.0)
    mps.capture(days=21, now=NOW)
    later = utc(2026, 10, 29, 16, 26)
    env.set_rows(row("AAA", "2026-10-29", "After Close", claude=("NEUTRAL", 60.0)))
    env.put_cache("AAA", later, 2.0)
    mps.capture(days=21, now=later)
    db = env.sess()
    try:
        h = pes.history(db, "claude_new", "AAA", date(2026, 10, 29))
    finally:
        db.close()
    assert [x["signal"] for x in h] == ["BUY", "NEUTRAL"] and h[1]["supersedes_id"] == h[0]["id"]
    assert h[0]["inputs"]["params"][0]["name"] == "eps_estimates" and h[0]["timing_basis"] == "PROVISIONAL_WINDOW_UNVERIFIED"


def test_an_evidence_failure_never_changes_what_was_captured(env, monkeypatch):
    monkeypatch.setenv("EARN_EVIDENCE", "on")
    monkeypatch.setattr(pes, "record", lambda db, entries: (_ for _ in ()).throw(RuntimeError("evidence store down")))
    env.set_rows(row("AAA", "2026-10-29", "After Close"))
    env.put_cache("AAA", NOW, 3.0)
    assert mps.capture(days=21, now=NOW) == 2


def test_evidence_snapshot_hash_is_content_addressed():
    a = pes.build_snapshot("AAA", equity_payload(), "earn:equity:AAA", 1000.0)
    b = pes.build_snapshot("AAA", equity_payload(), "earn:equity:AAA", 1000.0)
    c = pes.build_snapshot("AAA", equity_payload({"price_action": -0.5}), "earn:equity:AAA", 1000.0)
    assert a["hash"] == b["hash"] != c["hash"]


def test_midnight_utc_stamps_are_never_eligible_for_lead_time(env):
    import earnings_scoreboard as sb
    assert sb.classify(date(2026, 10, 29), "After Close", utc(2026, 10, 29, 0, 0)) == sb.RETRO
    assert sb.classify(date(2026, 10, 29), "After Close", utc(2026, 10, 29, 4, 26)) == sb.BEFORE


def test_all_models_rows_can_show_the_age_of_the_data_behind_them(monkeypatch):
    import all_models_service as am
    assert am._inputs_built_map(["AAA"]) is None                  # off by default: no extra database reads
    monkeypatch.setenv("EARN_ROW_FRESHNESS", "on")
    import db_cache
    monkeypatch.setattr(db_cache, "get_many", lambda keys: {"earn:equity:AAA": (1000.0, {"params": []})})
    assert am._inputs_built_map(["AAA", "BBB"]) == {"AAA": 1000.0, "BBB": None}
