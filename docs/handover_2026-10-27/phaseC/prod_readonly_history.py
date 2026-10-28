"""READ-ONLY report on the EXISTING paper trades and the database facts that matter for the audit-table migration.

Safe by construction:
  * one connection, opened READ ONLY (psycopg `read_only = True`, plus SET TRANSACTION READ ONLY): the server rejects any write;
  * only SELECT / SHOW statements exist in this file; it imports no application module except the constants in tab_settings;
  * the connection string comes from the environment or backend\\.env and is NEVER printed (only the port number and server version);
  * it does not touch, close, delete or recompute any trade.

Re-checks per trade what history allows: entry score against the unchanged thresholds and the trade's own side, entry/stop/target
bracket, size 100. It cannot re-derive per-parameter contributions (never stored) and does not pretend to.

    python prod_readonly_history.py --root C:\\apps\\us-stock-reader --days 14 --out C:\\audit-reports
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


def load_url(root: Path):
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        env = root / "backend" / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip().startswith("DATABASE_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not url:
        sys.exit("STOPPED: no DATABASE_URL in the environment or backend\\.env")
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def cutoffs(root: Path):
    try:
        sys.path.insert(0, str(root / "backend"))
        import tab_settings as cfg
        return float(cfg.V3_CUTOFFS["buy"]), float(cfg.V3_CUTOFFS["sell"]), "tab_settings.V3_CUTOFFS"
    except Exception:  # noqa: BLE001
        return 55.0, 45.0, "built-in default 55 / 45"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"C:\apps\us-stock-reader")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--out", default=r"C:\audit-reports")
    a = ap.parse_args()
    root = Path(a.root)
    url = load_url(root)
    buy, sell, src = cutoffs(root)
    import psycopg
    from urllib.parse import urlparse
    port = urlparse(url).port
    conn = psycopg.connect(url, connect_timeout=15, prepare_threshold=None)
    conn.read_only = True
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "thresholds": {"buy": buy, "sell": sell, "from": src},
           "read_only": True}
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    cur.execute("SHOW server_version")
    out["server_version"] = cur.fetchone()[0]
    cur.execute("SELECT version()")
    out["server_banner"] = cur.fetchone()[0][:80]
    out["connection_port"] = port                      # 6543 = Supabase transaction pooler, 5432 = session pooler / direct
    cur.execute("SELECT pg_is_in_recovery(), current_setting('transaction_read_only'), has_schema_privilege(current_user, 'public', 'CREATE')")
    rec, ro, can_create = cur.fetchone()
    out["is_replica"], out["session_read_only"], out["can_create_in_public"] = rec, ro, can_create
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_name LIKE 'intraday_%' OR table_name = 'demo_trades' ORDER BY 1")
    out["relevant_tables"] = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'demo_trades' ORDER BY ordinal_position")
    cols = [r[0] for r in cur.fetchall()]
    out["demo_trades_columns"] = cols
    out["v2_evidence_columns_present"] = [c for c in ("entry_session", "signal_event_id", "snapshot_id", "entry_quote", "risk_checks", "validation",
                                                      "exit_price_source", "fill_assumption") if c in cols]
    cur.execute("SELECT pg_total_relation_size('demo_trades'), (SELECT COUNT(*) FROM demo_trades)")
    size, total = cur.fetchone()
    out["demo_trades_bytes"], out["demo_trades_rows"] = size, total
    cur.execute("SELECT pg_get_userbyid(c.relowner) = current_user FROM pg_class c WHERE c.relname = 'demo_trades' AND c.relkind = 'r'")
    r = cur.fetchone()
    out["current_user_owns_demo_trades"] = None if r is None else r[0]
    cur.execute("SELECT status, COUNT(*) FROM demo_trades GROUP BY status ORDER BY 2 DESC")
    out["rows_by_status"] = {k: v for k, v in cur.fetchall()}
    cur.execute("SELECT COUNT(*) FROM (SELECT strategy, symbol FROM demo_trades WHERE status = 'OPEN' GROUP BY 1, 2 HAVING COUNT(*) > 1) d")
    out["open_duplicates_strategy_symbol"] = cur.fetchone()[0]
    since = date.today() - timedelta(days=a.days)
    cur.execute("SELECT id, strategy, symbol, side, shares, session_date, entry_price, stop, target, score_at_entry, status FROM demo_trades "
                "WHERE session_date >= %s OR status = 'OPEN' ORDER BY id", (since,))
    per = defaultdict(lambda: {"trades": 0, "pass": 0, "fail": 0, "reasons": Counter(), "examples": defaultdict(list)})
    for (id_, strat, sym, side, shares, sd, entry, stop, target, score, status) in cur.fetchall():
        reasons = []
        if score is None:
            reasons.append("no entry score stored")
        elif side == "LONG" and score < buy:
            reasons.append(f"LONG opened below the BUY cutoff ({buy:g})")
        elif side == "SHORT" and score > sell:
            reasons.append(f"SHORT opened above the SELL cutoff ({sell:g})")
        if not entry or entry <= 0:
            reasons.append("no usable entry price")
        elif stop is not None and target is not None:
            if not ((stop < entry < target) if side == "LONG" else (target < entry < stop)):
                reasons.append("stop/target do not bracket the entry")
        else:
            reasons.append("stop or target missing")
        if shares != 100:
            reasons.append("size is not 100 shares")
        p = per[strat]
        p["trades"] += 1
        p["pass" if not reasons else "fail"] += 1
        for r_ in reasons:
            p["reasons"][r_] += 1
            if len(p["examples"][r_]) < 3:
                p["examples"][r_].append({"id": id_, "symbol": sym, "date": str(sd), "score": score, "status": status})
    conn.rollback()
    conn.close()
    out["window"] = {"from": str(since), "plus": "all OPEN rows", "note": "entry score, side, bracket and size only; per-parameter contributions "
                     "were never stored and are NOT invented"}
    out["per_strategy"] = {k: {"trades": v["trades"], "pass": v["pass"], "fail": v["fail"], "reasons": dict(v["reasons"]),
                               "examples": dict(v["examples"])} for k, v in sorted(per.items())}
    out["total"] = {"trades": sum(v["trades"] for v in per.values()), "pass": sum(v["pass"] for v in per.values()),
                    "fail": sum(v["fail"] for v in per.values())}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    f = Path(a.out) / f"history-readonly-{datetime.now().strftime('%Y%m%d-%H%M')}.json"
    f.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"server {out['server_version']} (port {port}); demo_trades {total} rows, {size / 1e6:.1f} MB; "
          f"V2 evidence columns present: {out['v2_evidence_columns_present'] or 'none'}; can CREATE in public: {can_create}")
    print(f"rows by status: {out['rows_by_status']}; open duplicates (strategy, symbol): {out['open_duplicates_strategy_symbol']}")
    print(f"{'model':12} {'trades':>7} {'pass':>6} {'fail':>6}  reasons")
    for k, v in out["per_strategy"].items():
        print(f"{k:12} {v['trades']:>7} {v['pass']:>6} {v['fail']:>6}  {v['reasons'] or '-'}")
    print(f"TOTAL        {out['total']['trades']:>7} {out['total']['pass']:>6} {out['total']['fail']:>6}\nreport: {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
