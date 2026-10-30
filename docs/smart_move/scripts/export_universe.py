"""READ-ONLY export of the Smart Move pilot universe: one row per symbol that has at least one GRADED earnings outcome.

    python export_universe.py [--root C:\\apps\\us-stock-reader] [--out-dir C:\\audit-reports\\smart_move_universe]

Aggregates only (counts, one price, one volume figure, dates): no credentials, no user data, no free text. One connection, session set to
read-only, 30 s statement timeout, SELECTs only; no provider request. Writes ONE new csv plus its .sha256 (never overwritten).
The liquidity figure avg_volume_20 is looked up in the already stored swr_cache value of earn:equity:<SYMBOL> (a read; the cache is not
changed); symbols without it are exported with an empty value and the share is printed, nothing is guessed.
"""
import argparse
import base64
import csv
import gzip
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

FIELDS = ["symbol", "n_graded", "n_bmo", "n_amc", "last_base_close", "first_graded_date", "last_graded_date", "avg_volume_20", "equity_cache_updated_epoch"]
VOL_KEYS = ("avg_volume_20",)


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


def decode(stored):
    if stored.startswith("gz1:"):
        stored = gzip.decompress(base64.b64decode(stored[4:])).decode("utf-8")
    return json.loads(stored)


def find_number(obj, keys, depth=0):
    if depth > 6:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
        for v in obj.values():
            r = find_number(v, keys, depth + 1)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj[:60]:
            r = find_number(v, keys, depth + 1)
            if r is not None:
                return r
    return None


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
    target = out_dir / ("universe_%s.csv" % datetime.now().strftime("%Y%m%d-%H%M%S"))
    if target.exists():
        sys.exit("STOPPED: %s exists (never overwritten)" % target)
    cur.execute(
        "select symbol, count(*), count(*) filter (where timing = 'BMO'), count(*) filter (where timing = 'AMC'),"
        " (array_agg(base_close order by earnings_date desc))[1], min(earnings_date)::text, max(earnings_date)::text"
        " from earnings_event_outcomes where status = 'GRADED' group by symbol order by symbol")
    rows = cur.fetchall()
    syms = [r[0] for r in rows]
    vols, stamps = {}, {}
    for i in range(0, len(syms), 100):
        chunk = syms[i:i + 100]
        cur.execute("select cache_key, value, updated_at from swr_cache where cache_key = any(%s)", (["earn:equity:" + s for s in chunk],))
        for key, value, upd in cur.fetchall():
            s = key.split(":", 2)[2]
            stamps[s] = upd
            try:
                vols[s] = find_number(decode(value), VOL_KEYS)
            except Exception:
                vols[s] = None
    with open(target, "w", newline="", encoding="ascii") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(FIELDS)
        for s, n, nb, na, bc, d0, d1 in rows:
            w.writerow([s, n, nb, na, "" if bc is None else round(float(bc), 4), d0, d1,
                        "" if vols.get(s) is None else round(vols[s], 1), "" if s not in stamps else stamps[s]])
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    Path(str(target) + ".sha256").write_text("%s  %s\n" % (digest, target.name), encoding="ascii")
    have = sum(1 for s in syms if vols.get(s) is not None)
    print("universe export saved:", target)
    print("symbols with >=1 GRADED outcome:", len(syms), "| with avg_volume_20 in the stored earn:equity cache:", have)
    print("SHA-256:", digest)


if __name__ == "__main__":
    main()