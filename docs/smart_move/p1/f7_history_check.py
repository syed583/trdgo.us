"""F7 HISTORY CHECK (READ ONLY): for every vendor-confirmed event in a collector evidence store, does the newest evidence retrieved by the event's cutoff predate the event's own prediction window?
It reads two files (cal_obs.jsonl, evidence.jsonl) from --store-dir, writes NOTHING, sends no request and imports no network code. It answers, per event and per kind of evidence (own price bars, own earnings history, SPY, sector ETF),
"retrieved before the window opened" - the exact condition under which the frozen scorer reports RETRIEVED_BEFORE_WINDOW.

    python f7_history_check.py --store-dir <collector store folder> [--date 2026-11-06]

Run it on a COPY of the store or directly: it only opens the files for reading."""
import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
for up in (HERE.parents[3] / "backend", HERE.parent):
    sys.path.insert(0, str(up))
sys.path.insert(0, str(HERE.parents[2] / "smart_move" / "shadow_impl"))

UTC = timezone.utc


def read_jsonl(p: Path) -> list:
    out = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def check(store_dir, date_filter=None, as_of=None) -> dict:
    import smart_move_event_records as ER
    import smart_move_live as L
    from smvshadow import cutoff as co
    store = Path(store_dir)
    cal, ev = read_jsonl(store / "cal_obs.jsonl"), read_jsonl(store / "evidence.jsonl")
    now = as_of or max((datetime.fromisoformat(r["observed_at"]) for r in cal), default=datetime.now(UTC))          # which events existed / were confirmed: judged as of the newest calendar observation
    ev_now = max((datetime.fromisoformat(r["retrieved_at"]) for r in ev), default=now)                                # what evidence the store holds: everything it ever retrieved up to each event's cutoff
    sector = {}
    for r in cal:
        if r.get("sector"):
            sector[r["symbol"]] = r["sector"]
    by_key = {}
    for r in ev:
        by_key.setdefault((r["kind"], r["symbol"]), []).append(r)
    rows, tally = [], Counter()
    for rec in ER.records_from_saved(cal, now, {}):
        if not rec["event_date"] or rec["session"] not in ("BMO", "AMC") or not rec["prediction_eligible"]:
            continue
        if date_filter and rec["event_date"] != date_filter:
            continue
        p = co.plan_event(datetime.fromisoformat(rec["event_date"]).date(), rec["session"])
        start, cut = p["window_start_utc"], p["cutoff_utc"]
        etf = L.SECTOR_ETF.get(sector.get(rec["symbol"]))
        status = {}
        for label, kind, sym in (("own_bars", "ohlc", rec["symbol"]), ("own_earnings", "earnings", rec["symbol"]), ("SPY", "ohlc", "SPY"), ("sector_ETF", "ohlc", etf)):
            if sym is None:
                status[label] = "NO_SECTOR_ETF"
                continue
            got = [r for r in by_key.get((kind, sym), []) if r["retrieved_at"] <= min(cut, ev_now).isoformat()]
            if not got:
                status[label] = "NONE_BY_CUTOFF"
            else:
                newest = max(r["retrieved_at"] for r in got)
                status[label] = "BEFORE_WINDOW" if newest < start.isoformat() else "IN_WINDOW"
        for label, s in status.items():
            tally[(label, s)] += 1
        rows.append({"symbol": rec["symbol"], "event": "%s %s" % (rec["event_date"], rec["session"]), "window_start": start.isoformat(), "cutoff": cut.isoformat(), **status})
    affected = [r for r in rows if "BEFORE_WINDOW" in r.values()]
    return {"events_checked": len(rows), "events_with_any_before_window": len(affected), "events_with_shared_before_window": len([r for r in rows if r["SPY"] == "BEFORE_WINDOW" or r["sector_ETF"] == "BEFORE_WINDOW"]),
            "events_with_own_before_window": len([r for r in rows if r["own_bars"] == "BEFORE_WINDOW" or r["own_earnings"] == "BEFORE_WINDOW"]),
            "tally": {"%s=%s" % k: v for k, v in sorted(tally.items())}, "affected": affected[:200]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--store-dir", required=True)
    ap.add_argument("--date", default=None)
    ap.add_argument("--app", default=None, help="application root (e.g. C:\\apps\\us-stock-reader) when this file is run from another folder; only its code is imported")
    a = ap.parse_args()
    if a.app:
        sys.path.insert(0, str(Path(a.app) / "backend"))
        sys.path.insert(0, str(Path(a.app) / "docs" / "smart_move" / "shadow_impl"))
    print(json.dumps(check(a.store_dir, a.date), indent=1))
