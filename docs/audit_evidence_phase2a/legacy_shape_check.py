import os, sys
sys.path.insert(0, os.getcwd())
import database
if (os.environ.get("APP_ENV") or "").strip().lower() != "production":
    sys.exit("APP_ENV is not production: refusing")
from sqlalchemy import text
db = database.SessionLocal()
try:
    db.execute(text("SET TRANSACTION READ ONLY"))
    q1 = """
    WITH ev AS (
      SELECT DISTINCT ON (symbol, earnings_date) symbol, earnings_date, report_timing, actual_move_pct
      FROM model_predictions
      WHERE model = 'claude_new' AND earnings_date IS NOT NULL AND actual_move_pct IS NOT NULL
      ORDER BY symbol, earnings_date, snapshot_date DESC)
    SELECT coalesce(report_timing, '(none)') AS timing, count(*) AS n,
           count(*) FILTER (WHERE actual_move_pct = 0) AS exact_zero,
           count(*) FILTER (WHERE abs(actual_move_pct) <= 0.1) AS within_0_1,
           count(*) FILTER (WHERE abs(actual_move_pct) <= 0.5) AS within_0_5,
           round(percentile_cont(0.5) WITHIN GROUP (ORDER BY abs(actual_move_pct))::numeric, 2) AS median_abs,
           round(avg(abs(actual_move_pct))::numeric, 2) AS mean_abs,
           round(max(abs(actual_move_pct))::numeric, 2) AS max_abs
    FROM ev GROUP BY 1 ORDER BY 1"""
    print("LEGACY OUTCOME SHAPE (one row per event):")
    for r in db.execute(text(q1)):
        print("  ", dict(r._mapping))
    print("PRICE_BARS TABLE:")
    for r in db.execute(text("SELECT timeframe, count(*) AS n, min(timestamp) AS first_ts, max(timestamp) AS last_ts FROM price_bars GROUP BY timeframe ORDER BY timeframe")):
        print("  ", dict(r._mapping))
    n = db.execute(text("SELECT count(*) FROM price_bars")).scalar()
    print("  total rows:", n)
finally:
    db.rollback()
    db.close()
