"""The read-only replay tool (sample JSON -> corrected scoreboard) and the guarantee that legacy outcome data cannot reach the
verified accuracy figures."""
import importlib.util
import inspect
import json
import pathlib
import sys
import types
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import earnings_outcomes as eo
import earnings_scoreboard as sb

UTC = timezone.utc
TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools"
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def load(name):
    spec = importlib.util.spec_from_file_location(name + "_under_test", TOOLS / (name + ".py"))
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


def snap(model, sym, earn, timing, signal, pred, move=None, correct=None, evaluated=None):
    return {"model": model, "symbol": sym, "earnings_date": earn, "report_timing": timing, "snapshot_date": pred.date(),
            "predicted_at": pred, "evaluated_at": evaluated, "actual_move_pct": move, "signal": signal, "score": 60.0}


def snaps():
    pre = datetime(2026, 10, 25, 4, 21, tzinfo=UTC)
    out = []
    e = date(2026, 10, 26)
    for i, m in enumerate(("claude_new", "claude_pro", "gemini", "grok", "chatgpt", "meta", "kimi")):
        out.append(snap(m, "AMKR", e, "After Close", ("BUY", "SELL", "NEUTRAL")[i % 3], pre, move=-0.86, evaluated=NOW))
    out.append(snap("ped", "AMKR", e, "After Close", "BUY", datetime(2026, 10, 26, 0, 0, tzinfo=UTC)))          # midnight stamp: retrospective
    out.append(snap("gemini", "AMKR", e, "After Close", "SELL", datetime(2026, 10, 27, 15, 0, tzinfo=UTC)))       # later than the release
    out.append(snap("claude_new", "WSOB", date(2026, 10, 27), "Before Open", "BUY", pre))                         # outcome pending
    return out


def make_sample():
    t = load("outcome_sample")
    ss = snaps()
    events = t.completed_events(ss, NOW)
    per_symbol, recs, outcomes = t.analyse(events, {"AMKR": AMKR, "WSOB": [r("2026-10-12", "r", "40.1", 1000), r("2026-10-09", "r", "40.0", 1000)]}, ss, NOW)
    return json.loads(json.dumps({"generated_at": NOW.isoformat(), "provider_requests_made": 2, "symbols": per_symbol, "events": recs,
                                  "provisional_accuracy_sample": t.accuracy(ss, outcomes, NOW)}, default=str)), ss


def run_replay(monkeypatch, tmp_path, sample, ss):
    t = load("outcome_replay")
    p = tmp_path / "sample.json"
    p.write_text(json.dumps(sample), encoding="utf-8")
    monkeypatch.setitem(sys.modules, "database", types.ModuleType("database"))
    monkeypatch.setenv("APP_ENV", "production")
    seen = []
    monkeypatch.setattr(t, "_run_ro", lambda sql: (seen.append(sql), ss)[1])

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(t, "datetime", _Now)
    monkeypatch.setattr(sys, "argv", ["outcome_replay.py", "--sample", str(p), "--out", str(tmp_path / "o.json")])
    t.main()
    return seen, json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))


def test_replay_matches_the_sample_files_own_figures_for_all_eight_models(tmp_path, monkeypatch, capsys):
    sample, ss = make_sample()
    seen, out = run_replay(monkeypatch, tmp_path, sample, ss)
    text_out = capsys.readouterr().out
    assert "CROSS-CHECK RESULT: all eight models identical" in text_out and out["cross_check_mismatch"] == []
    assert len(out["per_model_sample_events"]) == 8
    assert len(seen) == 1 and seen[0].startswith("SELECT")
    c = out["per_model_sample_events"]["claude_new"]
    assert c["graded"] == 1 and c["missing_outcome"] == 1 and c["buy"][:2] == [1, 1]            # AMKR +8.5% -> BUY right; WSOB pending
    g = out["per_model_sample_events"]["gemini"]
    assert g["excluded"]["post_release_only"] == 0 and g["neutral_calls"] == 1                    # its later SELL snapshot is ignored
    p = out["per_model_sample_events"]["ped"]
    assert p["eligible"] == 0 and p["excluded"]["retrospective_only"] == 1 and p["graded"] == 0   # explains a model with no graded events
    assert "candle rows by market_time across" in text_out and "dates that needed the unlabelled fallback: 0" in text_out


def test_replay_detects_a_difference(tmp_path, monkeypatch, capsys):
    sample, ss = make_sample()
    sample["provisional_accuracy_sample"]["gemini"]["BUY"][1] += 1
    seen, out = run_replay(monkeypatch, tmp_path, sample, ss)
    assert out["cross_check_mismatch"] == ["gemini"] and "DIFFERENT for ['gemini']" in capsys.readouterr().out


def test_replay_never_selects_legacy_columns_and_cannot_write_or_call_providers():
    src = (TOOLS / "outcome_replay.py").read_text(encoding="utf-8").split("def load_embedded", 1)[1]
    sql = src.split("SQL_SNAPS_NO_OUTCOME = (", 1)[1].split(")", 1)[0]
    for col in ("actual_move_pct", "actual_direction", "correct", "evaluated_at"):
        assert col not in sql
    for word in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "CREATE ", "TRUNCATE", "commit(", "unusualwhales", "candles("):
        assert word not in src, word
    assert src.count("SET TRANSACTION READ ONLY") == 1 and src.count("write_text(json") == 1


# ----------------------------------------------------------------------- legacy data cannot feed the verified figures ----
MODELS = {"m1": "M1"}


def _db():
    db = sessionmaker(bind=create_engine("sqlite+pysqlite:///:memory:"))()
    db.execute(text(
        "CREATE TABLE model_predictions (id INTEGER PRIMARY KEY AUTOINCREMENT, model VARCHAR(24) NOT NULL,"
        " symbol VARCHAR(20) NOT NULL, earnings_date DATE, report_timing VARCHAR(16), snapshot_date DATE NOT NULL,"
        " predicted_at TIMESTAMP NOT NULL, signal VARCHAR(10) NOT NULL, score DOUBLE PRECISION,"
        " actual_move_pct DOUBLE PRECISION, actual_direction VARCHAR(10), correct SMALLINT, evaluated_at TIMESTAMP)"))
    eo.ensure_table(db)
    return db


def test_verified_scoreboard_ignores_poisoned_legacy_columns():
    db = _db()
    e = date(2026, 10, 21)
    for sym, sig, legacy_move in (("AAA", "BUY", -9.0), ("BBB", "SELL", +9.0)):
        # legacy columns say the opposite of the truth and claim a correct call
        db.execute(text("INSERT INTO model_predictions (model, symbol, earnings_date, report_timing, snapshot_date, predicted_at, signal,"
                        " score, actual_move_pct, actual_direction, correct, evaluated_at) VALUES ('m1', :s, :e, 'Before Open', :sn, :p, :g, 60,"
                        " :mv, :ad, 1, :ev)"), {"s": sym, "e": e, "sn": date(2026, 10, 20), "p": datetime(2026, 10, 20, 4, 21), "g": sig,
                                               "mv": legacy_move, "ad": sig, "ev": datetime(2026, 10, 22)})
    # an event with ONLY a legacy outcome and no row in earnings_event_outcomes
    db.execute(text("INSERT INTO earnings_event_outcomes (symbol, earnings_date, timing, status, move_pct, direction, attempts)"
                    " VALUES ('AAA', :e, 'BMO', 'GRADED', 4.0, 'BUY', 1)"), {"e": e})
    db.commit()
    m = sb.scoreboard(MODELS, db=db, now=NOW)["models"][0]
    assert (m["buy"]["graded"], m["buy"]["correct"]) == (1, 1)                  # AAA graded from the verified table (+4.0 -> BUY right)
    assert m["sell"]["graded"] == 0                                             # BBB's legacy "correct" is not used
    assert m["events_graded"] == 1 and m["events_missing_outcome"] == 1


def test_build_ignores_legacy_keys_on_snapshot_rows():
    e = date(2026, 10, 21)
    base = {"model": "m1", "symbol": "AAA", "earnings_date": e, "report_timing": "Before Open",
            "predicted_at": datetime(2026, 10, 20, 4, 21, tzinfo=UTC), "signal": "BUY", "score": 60.0}
    poisoned = {**base, "actual_move_pct": -50.0, "actual_direction": "SELL", "correct": 0, "evaluated_at": NOW}
    out = {(" AAA", e): None}
    outcomes = {("AAA", e): {"status": eo.GRADED, "move_pct": 3.0, "direction": "BUY", "timing": "BMO"}}
    assert sb.build([base], outcomes, MODELS, NOW) == sb.build([poisoned], outcomes, MODELS, NOW)
    assert out  # (keeps the dict used above referenced)


def test_verified_path_never_touches_legacy_columns_or_the_legacy_scoreboard():
    import api_routes
    import model_prediction_service as mp
    for fn in (sb.load_rows, sb.scoreboard, sb.build, eo.events_to_review, eo.load_outcomes):
        s = inspect.getsource(fn)
        for col in ("actual_move_pct", "actual_direction", "evaluated_at", "correct = ", "scoreboard_legacy", "_latest_per_event_sql"):
            assert col not in s, (fn.__name__, col)
    assert "sb.scoreboard(" in inspect.getsource(mp.scoreboard) and "legacy" not in inspect.getsource(mp.scoreboard)
    route = inspect.getsource(api_routes.model_accuracy_scoreboard)
    assert "mp.scoreboard()" in route and "legacy" not in route and "modacc:scoreboard:v2" in route
    assert "modacc:scoreboard:legacy" in inspect.getsource(api_routes.model_accuracy_scoreboard_legacy)
    # the model-stats overlay on the All Models page is built from the VERIFIED scoreboard
    assert "scoreboard()" in inspect.getsource(mp.overlay).split("prow", 1)[0]


def test_overlay_model_stats_come_from_the_verified_scoreboard(monkeypatch):
    import model_prediction_service as mp
    monkeypatch.setattr(mp, "_ensure_table", lambda: True)
    verified = sb.build([{"model": "m1", "symbol": "AAA", "earnings_date": date(2026, 10, 21), "report_timing": "Before Open",
                          "predicted_at": datetime(2026, 10, 20, 4, 21, tzinfo=UTC), "signal": "BUY", "score": 60.0}],
                        {("AAA", date(2026, 10, 21)): {"status": eo.GRADED, "move_pct": 3.0, "direction": "BUY", "timing": "BMO"}}, MODELS, NOW)
    monkeypatch.setattr(mp, "scoreboard", lambda *a, **k: verified)
    out = mp.overlay([])
    st = out["model_stats"]["m1"]
    assert st["directional_n"] == 1 and st["directional_accuracy"] == 100.0 and st["verified"] == 1
