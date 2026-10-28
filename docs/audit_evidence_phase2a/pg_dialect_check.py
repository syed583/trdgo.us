"""PostgreSQL dialect check of the Phase 2A SQL on the DEV database (never production), in a throwaway schema that
is dropped at the end. Exercises: outcomes table DDL, upsert that never rewrites GRADED, event discovery, legacy sync,
scoreboard reads (timestamptz), and the read-only runner guard."""
import os
import sys
import uuid
from datetime import date, datetime, timezone

os.environ["APP_ENV"] = "development"
MAIN = r"C:\Users\initi\Downloads\US-STOCKS\backend"
sys.path.insert(0, MAIN)
import database as d                                    # dev-resolved
assert d.PROD_REF not in (d.DATABASE_URL or ""), "would be PRODUCTION - abort"
S = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(S, "wt_sig", "backend"))
from sqlalchemy import text
import earnings_outcomes as eo
import earnings_scoreboard as sb

schema = "p2a_" + uuid.uuid4().hex[:8]
db = d.SessionLocal()
UTC = timezone.utc
ok = True


def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name, extra)


try:
    db.execute(text(f"CREATE SCHEMA {schema}"))
    db.execute(text(f"SET search_path TO {schema}, public"))
    db.execute(text(f"CREATE TABLE {schema}.model_predictions (LIKE public.model_predictions INCLUDING DEFAULTS)"))
    db.execute(text(f"ALTER TABLE {schema}.model_predictions ADD PRIMARY KEY (id)")) if False else None
    db.commit()
    now = datetime(2026, 10, 29, 22, 0, tzinfo=UTC)

    def ins(model, sym, earn, timing, snap, pred, sig, evaluated=None):
        db.execute(text("INSERT INTO model_predictions (model, symbol, earnings_date, report_timing, snapshot_date, predicted_at, signal, score, evaluated_at)"
                        " VALUES (:m, :s, :e, :t, :sn, :p, :sg, 60, :ev)"),
                   {"m": model, "s": sym, "e": earn, "t": timing, "sn": snap, "p": pred, "sg": sig, "ev": evaluated})
    e = date(2026, 10, 28)
    ins("m1", "AAA", e, "Before Open", date(2026, 10, 26), datetime(2026, 10, 26, 4, 21, tzinfo=UTC), "BUY")
    ins("m1", "AAA", e, "Before Open", date(2026, 10, 28), datetime(2026, 10, 28, 19, 58, tzinfo=UTC), "SELL")
    ins("m1", "BBB", e, "After Close", date(2026, 10, 27), datetime(2026, 10, 27, 4, 21, tzinfo=UTC), "BUY",
        evaluated=datetime(2026, 10, 29, 4, 30, tzinfo=UTC))                      # legacy premature stamp
    ins("m2", "BBB", e, "After Close", date(2026, 10, 27), datetime(2026, 10, 27, 4, 21, tzinfo=UTC), "SELL")
    db.commit()
    prices = {"AAA": {date(2026, 10, 27): 100.0, date(2026, 10, 28): 103.0},
              "BBB": {date(2026, 10, 28): 200.0, date(2026, 10, 29): 190.0}}

    s = eo.run(now=now, fetcher=lambda x: prices[x], db=db)
    check("run graded both events", s["graded_new"] == 2, s)
    out = eo.load_outcomes(db)
    check("outcome AAA +3.0 BUY", out[("AAA", e)]["move_pct"] == 3.0 and out[("AAA", e)]["direction"] == "BUY")
    check("outcome BBB -5.0 SELL", out[("BBB", e)]["move_pct"] == -5.0 and out[("BBB", e)]["direction"] == "SELL")
    prices["AAA"][date(2026, 10, 28)] = 1.0
    eo.run(now=datetime(2026, 11, 20, tzinfo=UTC), fetcher=lambda x: prices[x], db=db)
    check("GRADED never rewritten (upsert WHERE guard)", eo.load_outcomes(db)[("AAA", e)]["move_pct"] == 3.0)
    n = eo.sync_legacy_columns(db, now)
    rows = {(r.model, r.symbol): r for r in db.execute(text("SELECT model, symbol, correct, evaluated_at, actual_move_pct FROM model_predictions"))}
    check("legacy sync fills never-evaluated rows only", n == 3 and rows[("m1", "BBB")].actual_move_pct is None and rows[("m2", "BBB")].correct == 1, f"updated={n}")
    res = sb.scoreboard({"m1": "M1", "m2": "M2"}, db=db, now=now)
    m1 = next(x for x in res["models"] if x["model"] == "m1")
    check("scoreboard: post-release SELL excluded; AAA BUY right, BBB BUY wrong", m1["buy"]["graded"] == 2 and m1["buy"]["correct"] == 1 and m1["sell"]["graded"] == 0 and m1["rows_by_class"].get("AFTER_RELEASE") == 1,
          f"buy={m1['buy']['graded']}/{m1['buy']['correct']} excluded_rows={m1['rows_by_class']}")
    # the read-only runner used by the verifier tools refuses writes on this dialect
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        db.execute(text("INSERT INTO earnings_event_outcomes (symbol, earnings_date, status) VALUES ('ZZZ', :e, 'GRADED')"), {"e": e})
        check("read-only transaction refuses writes", False)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        check("read-only transaction refuses writes", "read-only" in str(exc).lower())
finally:
    db.rollback()
    db.execute(text("SET search_path TO public"))
    db.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    db.commit()
    left = db.execute(text("SELECT count(*) FROM information_schema.schemata WHERE schema_name = :s"), {"s": schema}).scalar()
    check("throwaway schema dropped", left == 0)
    db.close()
print("ALL PASS" if ok else "FAILURES")
sys.exit(0 if ok else 1)
