"""READ-ONLY coverage audit of the earnings-related tables for the Smart Move research (counts, date ranges, missing fields).

    python coverage_audit.py [--root C:\\apps\\us-stock-reader] [--out-dir C:\\audit-reports\\smart_move_coverage]

One connection, session set to read-only (default_transaction_read_only=on), 30 s statement timeout, SELECT aggregates only: no row content,
no secrets and no provider request is read or written. A missing table or column is recorded, never fatal. The only file written is a NEW
json (never overwritten). DATABASE_URL from the environment, else backend\\.env of --root (read in this process, never printed).
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

TABLES = ["model_predictions", "ped_scores", "earnings_signal_snapshots", "earnings_event_outcomes", "straddle_snap_v2", "estimate_snapshots",
          "estimate_revisions", "score_snapshots", "pe_events", "pe_predictions", "pe_grades", "earnings_calendar", "earnings_events",
          "fundamental_snapshots", "price_bars", "institutional_holdings", "provider_fetch_log", "intraday_signal_audit", "swr_cache"]
TIMEISH = re.compile(r"(date|time|_at$|^ts$|timestamp|snapshot)", re.I)
MAX_COLS = 60


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
    ap.add_argument("--out-dir", default=r"C:\audit-reports\smart_move_coverage")
    a = ap.parse_args()
    import psycopg
    conn = psycopg.connect(db_url(a.root), connect_timeout=15, prepare_threshold=None, autocommit=True)
    cur = conn.cursor()
    cur.execute("SET default_transaction_read_only = on")
    cur.execute("SET statement_timeout = '30s'")
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / ("coverage_%s.json" % datetime.now().strftime("%Y%m%d-%H%M%S"))
    if target.exists():
        sys.exit("STOPPED: %s exists (never overwritten)" % target)

    def q(sql, *args):
        try:
            cur.execute(sql, args)
            return cur.fetchall()
        except Exception as e:
            return "ERROR %s" % type(e).__name__

    def scalar(sql, *args):
        r = q(sql, *args)
        return r[0][0] if isinstance(r, list) and r else r

    report = {"taken_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "db_now": str(scalar("select now()")), "tables": {}}
    for t in TABLES:
        info = {"exists": bool(scalar("select to_regclass(%s) is not null", "public." + t))}
        report["tables"][t] = info
        if not info["exists"]:
            continue
        info["rows"] = scalar('select count(*) from public."%s"' % t)
        cols = q("select column_name, data_type from information_schema.columns where table_schema='public' and table_name=%s order by ordinal_position", t)
        info["columns"] = {}
        if isinstance(cols, list):
            for name, dtype in cols[:MAX_COLS]:
                c = {"type": dtype, "non_null": scalar('select count("%s") from public."%s"' % (name, t))}
                if TIMEISH.search(name) and dtype in ("date", "timestamp with time zone", "timestamp without time zone", "text", "character varying"):
                    r = q('select min("%s")::text, max("%s")::text from public."%s" where "%s" is not null' % (name, name, t, name))
                    if isinstance(r, list) and r:
                        c["min"], c["max"] = r[0]
                info["columns"][name] = c
    T = report["tables"]

    def has(t, *cols):
        return T.get(t, {}).get("exists") and all(c in T[t].get("columns", {}) for c in cols)

    s = report["special"] = {}
    if has("model_predictions", "model", "symbol", "earnings_date", "snapshot_date"):
        s["model_predictions_by_model"] = q("select model, count(*), count(distinct (symbol, earnings_date)), count(distinct snapshot_date), min(snapshot_date)::text, max(snapshot_date)::text from model_predictions group by model order by 1")
        s["model_predictions_lead_days"] = q("select least(greatest(earnings_date::date - snapshot_date::date, -1), 9) as lead, count(*), count(distinct (symbol, earnings_date)) from model_predictions group by 1 order by 1")
        if has("model_predictions", "report_timing"):
            s["model_predictions_timing"] = q("select coalesce(report_timing,'NULL'), count(*) from model_predictions group by 1 order by 1")
        if has("model_predictions", "actual_direction"):
            s["model_predictions_graded"] = q("select count(*) filter (where actual_direction is not null), count(*) filter (where evaluated_at is not null), count(distinct (symbol, earnings_date)) filter (where actual_direction is not null) from model_predictions")
    if has("straddle_snap_v2", "symbol", "snapshot_date"):
        s["straddle_snap_v2"] = q("select count(*), count(distinct symbol), count(distinct snapshot_date), min(snapshot_date)::text, max(snapshot_date)::text, count(*) filter (where report_date is not null), count(*) filter (where status is not null) from straddle_snap_v2")
    if has("estimate_snapshots", "symbol"):
        s["estimate_snapshots"] = q("select count(*), count(distinct symbol) from estimate_snapshots")
    if has("estimate_revisions", "symbol"):
        s["estimate_revisions"] = q("select count(*), count(distinct symbol) from estimate_revisions")
    if has("score_snapshots", "symbol", "snapshot_date"):
        s["score_snapshots"] = q("select count(*), count(distinct symbol), count(distinct snapshot_date), min(snapshot_date)::text, max(snapshot_date)::text from score_snapshots")
    if has("pe_events", "timestamp_status"):
        s["pe_events_timestamp_status"] = q("select timestamp_status, count(*) from pe_events group by 1 order by 1")
    if has("pe_predictions", "model_version"):
        s["pe_predictions_by_version"] = q("select model_version, count(*) from pe_predictions group by 1 order by 1")
    if has("earnings_calendar", "symbol", "earnings_date"):
        s["earnings_calendar"] = q("select count(*), count(distinct symbol), min(earnings_date)::text, max(earnings_date)::text, count(*) filter (where report_time is not null and report_time <> ''), count(*) filter (where eps_actual is not null) from earnings_calendar")
    if has("earnings_event_outcomes", "status"):
        s["earnings_event_outcomes_status"] = q("select status, count(*) from earnings_event_outcomes group by 1 order by 1")
    if has("price_bars", "timeframe"):
        s["price_bars_by_timeframe"] = q("select timeframe, count(*), count(distinct symbol) from price_bars group by 1 order by 1")
    if has("swr_cache", "cache_key", "updated_at"):
        s["swr_cache_by_prefix"] = q("select split_part(cache_key, ':', 1) || ':' || split_part(cache_key, ':', 2) as prefix, count(*), min(updated_at)::text, max(updated_at)::text from swr_cache group by 1 order by 2 desc limit 40")

    target.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print("coverage report saved:", target)
    print("table".ljust(30), "rows".rjust(10), " columns(with any NULL share)")
    for t in TABLES:
        i = T[t]
        if not i["exists"]:
            print(t.ljust(30), "MISSING".rjust(10))
            continue
        nulls = [n for n, c in i.get("columns", {}).items() if isinstance(i["rows"], int) and i["rows"] and c["non_null"] < i["rows"]]
        print(t.ljust(30), str(i["rows"]).rjust(10), " ", ("nulls in: " + ", ".join(nulls[:8])) if nulls else "no NULLs")
    print(json.dumps(s, indent=1, default=str)[:6000])


if __name__ == "__main__":
    main()