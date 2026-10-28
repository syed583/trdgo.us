"""PostgreSQL check of the PED capture / backfill SQL on the DEV database (never production), in a throwaway schema that is
dropped at the end. Proves: backfill copies only past dates at the date-only stamp, never today's; capture stores a genuine
timestamptz + timing; a second backfill never overwrites; classification of the resulting rows."""
import os
import sys
import types
import uuid
from datetime import date, datetime, timedelta, timezone

os.environ["APP_ENV"] = "development"
MAIN = r"C:\Users\initi\Downloads\US-STOCKS\backend"
sys.path.insert(0, MAIN)
import database as d                                    # dev-resolved
assert d.PROD_REF not in (d.DATABASE_URL or ""), "would be PRODUCTION - abort"
S = os.path.dirname(os.path.abspath(__file__))
for cand in (os.path.join(S, "..", "..", "backend"), os.path.join(S, "wt_sig", "backend")):
    if os.path.exists(os.path.join(cand, "model_prediction_service.py")):
        sys.path.insert(0, cand)
        break
from sqlalchemy import text
import earnings_outcomes as eo
import earnings_scoreboard as sb
import model_prediction_service as mp

UTC = timezone.utc
schema = "ped_" + uuid.uuid4().hex[:8]
db = d.SessionLocal()
ok = True


def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name, extra)


class Shared:
    """Hands the SAME session (search_path = throwaway schema) to the service functions; close() is a no-op."""
    def __init__(self, s): self.s = s
    def execute(self, *a, **k): return self.s.execute(*a, **k)
    def commit(self): self.s.commit()
    def rollback(self): self.s.rollback()
    def close(self): pass


try:
    db.execute(text(f"CREATE SCHEMA {schema}"))
    db.execute(text(f"SET search_path TO {schema}, public"))
    db.execute(text(f"CREATE TABLE {schema}.model_predictions (LIKE public.model_predictions INCLUDING ALL)"))
    db.execute(text(f"CREATE TABLE {schema}.ped_scores (symbol VARCHAR(20) NOT NULL, snapshot_date DATE NOT NULL, score DOUBLE PRECISION,"
                    " signal VARCHAR(10), earnings_date DATE, PRIMARY KEY (symbol, snapshot_date))"))
    db.commit()
    fake = types.ModuleType("database")
    fake.SessionLocal = lambda: Shared(db)
    sys.modules["database"] = fake
    mp._ensure_table = lambda: True

    today = date.today()
    edate = today + timedelta(days=3)
    db.execute(text("INSERT INTO ped_scores VALUES ('OLD1', :d, 70, 'BUY', :e), ('OLD1', :t, 71, 'BUY', :e), ('NEW1', :t, 65, 'SELL', :e)"),
               {"d": today - timedelta(days=2), "t": today, "e": edate})
    db.commit()

    n1 = mp.backfill_ped()
    rows = db.execute(text(f"SELECT symbol, snapshot_date, predicted_at, report_timing FROM {schema}.model_predictions WHERE model='ped'")).all()
    check("backfill copied only the past date", n1 == 1 and [(r.symbol, r.snapshot_date) for r in rows] == [("OLD1", today - timedelta(days=2))], rows)
    check("backfilled stamp is date-only midnight UTC with no timing",
          rows[0].report_timing is None and rows[0].predicted_at.astimezone(UTC).time().isoformat() == "00:00:00")

    am = types.ModuleType("all_models_service")
    cells = {"ped": {"signal": "SELL", "score": 65}, "grok": {"signal": "BUY", "score": 60}}
    am.scan = lambda days=15: {"rows": [{"symbol": "NEW1", "date": edate.isoformat(), "report_timing": "After Close", "cells": cells},
                                       {"symbol": "OLD1", "date": edate.isoformat(), "report_timing": "Before Open", "cells": cells}]}
    sys.modules["all_models_service"] = am
    t0 = datetime.now(UTC)
    n2 = mp.capture(days=15)
    t1 = datetime.now(UTC)
    caps = {r.symbol: r for r in db.execute(text(f"SELECT symbol, snapshot_date, predicted_at, report_timing, signal FROM {schema}.model_predictions"
                                                  f" WHERE model='ped' AND snapshot_date=:t"), {"t": today})}
    check("capture inserted today's PED rows (backfill had not pre-empted them)", n2 == 4 and set(caps) == {"NEW1", "OLD1"}, n2)
    r = caps["NEW1"]
    check("captured_at is a genuine timestamptz inside the run window", t0 <= r.predicted_at <= t1 and r.report_timing == "After Close", r.predicted_at)

    n3 = mp.backfill_ped()
    after = db.execute(text(f"SELECT count(*) FROM {schema}.model_predictions WHERE model='ped'")).scalar()
    r2 = db.execute(text(f"SELECT predicted_at, report_timing FROM {schema}.model_predictions WHERE model='ped' AND symbol='NEW1' AND snapshot_date=:t"), {"t": today}).one()
    check("second backfill adds nothing and overwrites nothing", n3 == 0 and after == 3 and r2.predicted_at == r.predicted_at and r2.report_timing == "After Close")

    allrows = [{"model": "ped", "symbol": x.symbol, "earnings_date": edate if x.symbol else None, "report_timing": x.report_timing,
                "predicted_at": x.predicted_at, "signal": x.signal, "score": 1.0}
               for x in db.execute(text(f"SELECT symbol, report_timing, predicted_at, signal FROM {schema}.model_predictions WHERE model='ped'"))]
    classes = sorted(sb.classify(edate, x["report_timing"], x["predicted_at"]) for x in allrows)
    check("classification: 2 genuine BEFORE_RELEASE, 1 date-only RETROSPECTIVE", classes == ["BEFORE_RELEASE", "BEFORE_RELEASE", "RETROSPECTIVE"], classes)
finally:
    try:
        db.rollback()
        db.execute(text("SET search_path TO public"))
        db.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        db.commit()
        print("throwaway schema dropped")
    except Exception as exc:  # noqa: BLE001
        print("cleanup problem:", exc)
    db.close()
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
