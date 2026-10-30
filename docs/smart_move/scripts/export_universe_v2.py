"""READ-ONLY export of the Smart Move pilot universe, version 2: one row per symbol that has a recorded prediction for an earnings event
whose date is today or earlier, whatever its grading status (so the grader's alphabetical order cannot shape the universe).

    python export_universe_v2.py [--root C:\\apps\\us-stock-reader] [--out-dir C:\\audit-reports\\smart_move_universe]

Aggregates only: counts, dates, one price when an outcome exists. No credentials, no user data, no provider request. One connection, session
read-only, 30 s statement timeout, SELECTs only. Writes ONE new csv plus its .sha256 (never overwritten). Timing per event = the latest
non-empty report_timing label of that event's prediction rows ('Before Open' -> BMO, 'After Close' -> AMC, anything else counted as no timing).
"""
import argparse
import csv
import hashlib
import os
import sys
from datetime import datetime
from pathlib import Path

FIELDS = ["symbol", "n_graded", "n_bmo", "n_amc", "last_base_close", "first_graded_date", "last_graded_date", "avg_volume_20",
          "equity_cache_updated_epoch", "n_events", "n_no_timing", "first_event_date", "last_event_date"]

SQL = """
with ev as (
  select symbol, earnings_date,
         (array_agg(report_timing order by predicted_at desc) filter (where report_timing is not null and report_timing <> ''))[1] as lab
  from model_predictions
  where earnings_date is not null and earnings_date <= current_date
  group by symbol, earnings_date),
evk as (
  select symbol, earnings_date,
         case when lab ilike 'before%' or lab ilike 'bmo%' or lab ilike 'pre%' then 'BMO'
              when lab ilike 'after%' or lab ilike 'amc%' or lab ilike 'post%' then 'AMC' else null end as kind
  from ev)
select k.symbol,
       count(o.symbol) filter (where o.status = 'GRADED') as n_graded,
       count(*) filter (where k.kind = 'BMO') as n_bmo,
       count(*) filter (where k.kind = 'AMC') as n_amc,
       (array_agg(o.base_close order by k.earnings_date desc) filter (where o.status = 'GRADED'))[1] as last_base_close,
       min(k.earnings_date) filter (where o.status = 'GRADED')::text,
       max(k.earnings_date) filter (where o.status = 'GRADED')::text,
       count(*) as n_events,
       count(*) filter (where k.kind is null) as n_no_timing,
       min(k.earnings_date)::text, max(k.earnings_date)::text
from evk k left join earnings_event_outcomes o on o.symbol = k.symbol and o.earnings_date = k.earnings_date
group by k.symbol order by k.symbol
"""


def db_url(root):
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        env = Path(root) / "backend" / ".env"
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines() if env.exists() else []:
            if line.strip().startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not url:
        sys.exit("STOPPED: no DATABASE_URL")
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"C:\apps\us-stock-reader")
    ap.add_argument("--out-dir", default=r"C:\audit-reports\smart_move_universe")
    a = ap.parse_args()
    import psycopg
    conn = psycopg.connect(db_url(a.root), connect_timeout=15, prepare_threshold=None, autocommit=True)
    cur = conn.cursor()
    cur.execute("SET default_transaction_read_only = on")
    cur.execute("SET statement_timeout = '30s'")
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / ("universe_v2_%s.csv" % datetime.now().strftime("%Y%m%d-%H%M%S"))
    if target.exists():
        sys.exit("STOPPED: %s exists (never overwritten)" % target)
    cur.execute(SQL)
    rows = cur.fetchall()
    with open(target, "w", newline="", encoding="ascii") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(FIELDS)
        for s, ng, nb, na, bc, g0, g1, ne, nn, e0, e1 in rows:
            w.writerow([s, ng, nb, na, "" if bc is None else round(float(bc), 4), g0 or "", g1 or "", "", "", ne, nn, e0, e1])
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    Path(str(target) + ".sha256").write_text("%s  %s\n" % (digest, target.name), encoding="ascii")
    print("universe v2 export saved:", target)
    print("symbols with a prediction for an event dated <= today:", len(rows), "| with >=1 graded:", sum(1 for r in rows if r[1]),
          "| with a price:", sum(1 for r in rows if r[4] is not None), "| alphabetical range:", rows[0][0] if rows else "-", "..", rows[-1][0] if rows else "-")
    print("SHA-256:", digest)


if __name__ == "__main__":
    main()