"""READ-ONLY report on the shadow comparison written by HISTORICAL_MOVES_SHADOW=1.

Reads the cache rows ``moves:shadow:<SYMBOL>`` (both the legacy and the regular-close moves, computed from the same provider rows by the
running application) and the ``moves:method:history`` log, and prints how far the two methods differ: mean |move|, sign flips,
BUY/SELL/NEUTRAL class changes (flat band 0.5 %), by report timing, plus how many legacy events the regular-close method refuses
(missing regular close). No provider requests. One SELECT in a READ ONLY transaction. Writes only --out JSON (optional).
Usage (backend dir, production venv): python <path>\\moves_shadow_report.py [--out <file>.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

FLAT = 0.5
SQL = ("SELECT cache_key, value, updated_at FROM swr_cache WHERE cache_key LIKE '%moves:shadow:%' "
       "OR cache_key LIKE '%moves:method:history'")


def cls(v):
    return "BUY" if v > FLAT else ("SELL" if v < -FLAT else "NEUTRAL")


def mean_abs(vals):
    vals = [abs(v) for v in vals]
    return round(sum(vals) / len(vals), 3) if vals else None


def decode(text_value, gz_decode=None):
    s = text_value
    if s.startswith("gz1:") and gz_decode:
        return gz_decode(s)
    return json.loads(s)


def analyse(records):
    """records: {symbol: shadow record}. Pure function."""
    events, refused, legacy_total, syms = [], 0, 0, 0
    served = {}
    for sym, rec in records.items():
        if not isinstance(rec, dict) or "legacy" not in rec or "regular_close" not in rec:
            continue
        syms += 1
        served[rec.get("served")] = served.get(rec.get("served"), 0) + 1
        reg = {d: (m, k) for d, m, k in rec["regular_close"]}
        for d, m, k in rec["legacy"]:
            legacy_total += 1
            if d not in reg:
                refused += 1
                continue
            events.append({"symbol": sym, "date": d, "kind": reg[d][1], "legacy": m, "regular": reg[d][0]})
    out = {"symbols": syms, "served_method_counts": served, "legacy_events": legacy_total, "events_compared": len(events),
           "refused_by_regular_close_method": refused}
    for kind in (None, "BMO", "AMC"):
        sel = [e for e in events if kind is None or e["kind"] == kind]
        if not sel:
            continue
        out[kind or "ALL"] = {
            "events": len(sel), "mean_abs_legacy": mean_abs([e["legacy"] for e in sel]), "mean_abs_regular": mean_abs([e["regular"] for e in sel]),
            "sign_flips": sum(1 for e in sel if abs(e["legacy"]) > 0.2 and abs(e["regular"]) > 0.2 and (e["legacy"] > 0) != (e["regular"] > 0)),
            "class_changes": sum(1 for e in sel if cls(e["legacy"]) != cls(e["regular"])),
            "exact_match_within_0.05": sum(1 for e in sel if abs(e["legacy"] - e["regular"]) <= 0.05)}
    return out


def load(run_sql):
    records, history = {}, []
    try:
        import db_cache
        gz = db_cache._decode
        ns = db_cache.namespace()
    except Exception:  # noqa: BLE001
        gz, ns = None, ""
    ages = []
    for key, value, updated in run_sql(SQL):
        try:
            v = decode(value, gz)
        except Exception:  # noqa: BLE001
            continue
        if key.endswith("moves:method:history"):
            history = v
        else:
            records[key.rsplit("moves:shadow:", 1)[1]] = v
            ages.append(float(updated))
    return records, history, ages


def _run_ro(sql):
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        return [tuple(r) for r in db.execute(text(sql)).all()]
    finally:
        db.rollback()
        db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if os.path.exists(os.path.join(os.getcwd(), "database.py")) and os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())
    import database  # noqa: F401  (loads backend/.env)
    if (os.environ.get("APP_ENV") or "").strip().lower() != "production":
        sys.exit("APP_ENV is not 'production': refusing.")
    import time
    records, history, ages = load(_run_ro)
    out = analyse(records)
    out["method_history"] = history
    if ages:
        out["oldest_record_age_hours"] = round((time.time() - min(ages)) / 3600, 1)
        out["newest_record_age_hours"] = round((time.time() - max(ages)) / 3600, 1)
    print(json.dumps(out, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
        print("wrote", a.out)


if __name__ == "__main__":
    main()
