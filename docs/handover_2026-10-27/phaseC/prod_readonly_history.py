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
    cur.execute("SELECT pg_is_in_recovery(), current_setting('transaction_read_only'), has_schema_privilege(current_user, 'public', 'CREATE'), "
                "has_schema_privilege(current_user, 'public', 'USAGE')")
    rec, ro, can_create, can_use = cur.fetchone()
    out["is_replica"], out["session_read_only"] = rec, ro
    cur.execute("SELECT r.rolsuper, r.rolcreatedb, r.rolcreaterole, r.rolbypassrls FROM pg_roles r WHERE r.rolname = current_user")
    sup, cdb, crole, byp = cur.fetchone()
    perms = {"schema_public_usage": can_use, "schema_public_create": can_create, "superuser": sup, "createdb": cdb, "createrole": crole,
             "bypass_rls": byp}
    for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        cur.execute("SELECT has_table_privilege(current_user, 'public.demo_trades', %s)", (priv,))
        perms["demo_trades_" + priv.lower()] = cur.fetchone()[0]
    out["permissions_of_the_connecting_role"] = perms           # role name and credentials are deliberately not printed
    out["can_create_in_public"] = can_create
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
    per = defaultdict(lambda: {"trades": 0, "PASS": 0, "FAIL": 0, "NOT VERIFIABLE": 0, "fail_reasons": Counter(), "nv_reasons": Counter(),
                               "examples": defaultdict(list)})
    for (id_, strat, sym, side, shares, sd, entry, stop, target, score, status) in cur.fetchall():
        fails, nv = [], []
        # 1. stored score vs the unchanged thresholds and the trade's own side
        if score is None:
            nv.append("no entry score stored")
        elif side == "LONG" and score < buy:
            fails.append(f"LONG opened at a score below the BUY cutoff ({buy:g})")
        elif side == "SHORT" and score > sell:
            fails.append(f"SHORT opened at a score above the SELL cutoff ({sell:g})")
        # 2. stored entry price
        if entry is None or entry <= 0:
            fails.append("entry price missing or not positive")
        # 3. stored stop / target bracket
        if stop is None or target is None:
            nv.append("stop or target not stored")
        elif entry is not None and entry > 0 and not ((stop < entry < target) if side == "LONG" else (target < entry < stop)):
            fails.append("stop/target do not bracket the entry")
        # 4. size convention
        if shares != 100:
            fails.append("size is not 100 shares")
        verdict = "FAIL" if fails else "NOT VERIFIABLE" if nv else "PASS"
        p = per[strat]
        p["trades"] += 1
        p[verdict] += 1
        for r_ in fails:
            p["fail_reasons"][r_] += 1
        for r_ in nv:
            p["nv_reasons"][r_] += 1
        for r_ in fails + nv:
            if len(p["examples"][r_]) < 3:
                p["examples"][r_].append({"id": id_, "symbol": sym, "date": str(sd), "side": side, "score": score, "status": status})
    conn.rollback()
    conn.close()
    order = ["chatgpt", "grok", "meta", "kimi", "gemini", "claude_pro"]
    out["window"] = {"from": str(since), "plus": "all OPEN rows"}
    out["stored_value_checks"] = ("entry score vs the unchanged thresholds and the trade's side; entry price; stop/target bracket; 100-share size. "
                                  "These use only what the trade row stored.")
    out["parameter_weight_verification"] = {
        "status": "NOT VERIFIABLE for every historical trade",
        "why": "the per-parameter readings, biases and contributions behind a score were never stored with the trade (only score_at_entry); "
               "they cannot be reconstructed afterwards and none are invented here",
        "trades_not_verifiable": None}
    out["per_strategy"] = {k: {"trades": v["trades"], "PASS": v["PASS"], "FAIL": v["FAIL"], "NOT VERIFIABLE": v["NOT VERIFIABLE"],
                               "fail_reasons": dict(v["fail_reasons"]), "not_verifiable_reasons": dict(v["nv_reasons"]),
                               "examples": dict(v["examples"]),
                               "parameter_weight_verification": f"NOT VERIFIABLE ({v['trades']} of {v['trades']})"}
                           for k, v in sorted(per.items(), key=lambda kv: (order.index(kv[0]) if kv[0] in order else 99, kv[0]))}
    tot = {k: sum(v[k] for v in per.values()) for k in ("trades", "PASS", "FAIL", "NOT VERIFIABLE")}
    out["total"] = tot
    out["parameter_weight_verification"]["trades_not_verifiable"] = tot["trades"]
    Path(a.out).mkdir(parents=True, exist_ok=True)
    f = Path(a.out) / f"history-readonly-{datetime.now().strftime('%Y%m%d-%H%M')}.json"
    f.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"PostgreSQL {out['server_version']} (connection port {port}; read-only session: {out['session_read_only']}; replica: {out['is_replica']})")
    print(f"permissions of the connecting role: {out['permissions_of_the_connecting_role']}")
    print(f"demo_trades: {total} rows, {size / 1e6:.1f} MB; V2 evidence columns present: {out['v2_evidence_columns_present'] or 'none'}; "
          f"intraday tables: {[t for t in out['relevant_tables'] if t != 'demo_trades'] or 'none'}")
    print(f"rows by status: {out['rows_by_status']}; OPEN duplicates (strategy, symbol): {out['open_duplicates_strategy_symbol']}")
    print(f"window: session_date >= {since} plus every OPEN row.  STORED-VALUE checks (score / side / price / bracket / size):")
    print(f"{'model':11} {'trades':>6} {'PASS':>6} {'FAIL':>6} {'NOT VERIFIABLE':>15}  parameter-weight check")
    for k, v in out["per_strategy"].items():
        print(f"{k:11} {v['trades']:>6} {v['PASS']:>6} {v['FAIL']:>6} {v['NOT VERIFIABLE']:>15}  {v['parameter_weight_verification']}")
        for r_, n in v["fail_reasons"].items():
            print(f"{'':11}   FAIL: {n} x {r_}")
        for r_, n in v["not_verifiable_reasons"].items():
            print(f"{'':11}   NOT VERIFIABLE: {n} x {r_}")
    print(f"{'TOTAL':11} {tot['trades']:>6} {tot['PASS']:>6} {tot['FAIL']:>6} {tot['NOT VERIFIABLE']:>15}")
    print("PARAMETER-WEIGHT verification: NOT VERIFIABLE for all trades (the contributions were never stored).")
    print(f"report: {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
