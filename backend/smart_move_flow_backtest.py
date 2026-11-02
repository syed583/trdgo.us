"""Point-in-time backtest of the options-flow rule over different lookback windows, using ONLY saved evidence (no request).

For every stock with a saved flow-alerts response and a saved earnings payload, and for every PAST earnings event that has a reported 1-day post-earnings move:
  * cutoff = the event's assumed release minus 24 h (the frozen capture-window end: BMO 06:00 ET, AMC 16:00 ET on the report date);
  * only alerts created AT OR BEFORE the cutoff, within L days of it, expiring on/after the report date, single-leg, volume/OI >= 1, side-attributable, are used (anything later is leakage and is counted, never used);
  * the same rule as V2 (>= 3 alerts, >= $500k attributed premium, |net ratio| >= 0.25 -> BUY/SELL by sign) is applied; the outcome is the sign of the reported move (flat band +-0.5%, the frozen grader's band).
Honest limits: a flow response holds only the latest <= 100 alerts per stock, so older events have few alerts; samples are small; the move is the provider's `post_earnings_move_1d`. Output is descriptive: coverage, decided n, hits, Wilson 95% interval,
first-half (older events) vs second-half (newer events) so a window is not chosen on the same events it is judged on. No window is selected automatically.
"""
from __future__ import annotations

import gzip
import json
import sys
from collections import defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "docs" / "smart_move" / "shadow_impl"))
from smvshadow import grader as G  # noqa: E402

ET = ZoneInfo("America/New_York")
FLAT = G.FLAT_BAND
WINDOWS = (5, 10, 15, 30)
MIN_ALERTS, MIN_PREM, STRONG, MODERATE = 3, 500_000.0, 0.5, 0.25


def _t(x):
    return datetime.fromisoformat(str(x).replace("Z", "+00:00"))


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def cutoff_for(report_date: str, report_time: str):
    """Frozen capture-window end: release - 24 h. premarket = BMO 06:00 ET, postmarket = AMC 16:00 ET. None when the timing is unknown."""
    kind = {"premarket": "BMO", "postmarket": "AMC"}.get(str(report_time).lower())
    if not kind:
        return None, None
    d = datetime.fromisoformat(report_date).date()
    rel = datetime.combine(d, time(6, 0) if kind == "BMO" else time(16, 0), ET)
    return kind, (rel - timedelta(hours=24)).astimezone(ZoneInfo("UTC"))


def flow_signal(alerts, report_date: str, cutoff, lookback_days: int):
    """Returns (state, signal, detail). state: NO_ALERTS | NONE_IN_WINDOW | NOT_ENOUGH | QUALIFIES; leakage counted but never used."""
    if not alerts:
        return "NO_ALERTS", None, {"leaked": 0}
    bull = bear = 0.0
    used = leaked = in_win = 0
    for a in alerts:
        t = _t(a["created_at"])
        if t > cutoff:
            leaked += 1
            continue
        if (cutoff - t).total_seconds() > lookback_days * 86400:
            continue
        in_win += 1
        if a.get("has_multileg") or str(a.get("expiry", ""))[:10] < report_date:
            continue
        voi = _f(a.get("volume_oi_ratio"))
        if voi is None or voi < 1.0:
            continue
        ask, bid = _f(a.get("total_ask_side_prem")) or 0.0, _f(a.get("total_bid_side_prem")) or 0.0
        if ask + bid == 0:
            continue
        w = 1.5 if a.get("has_sweep") else 1.0
        kind = str(a.get("type", "")).lower()
        if kind == "call":
            bull += ask * w
            bear += bid * w
        elif kind == "put":
            bear += ask * w
            bull += bid * w
        else:
            continue
        used += 1
    if in_win == 0:
        return "NONE_IN_WINDOW", None, {"leaked": leaked}
    tot = bull + bear
    if used < MIN_ALERTS or tot < MIN_PREM:
        return "NOT_ENOUGH", None, {"leaked": leaked, "used": used}
    ratio = (bull - bear) / tot
    sig = None if abs(ratio) < MODERATE else ("BUY" if ratio > 0 else "SELL")
    return "QUALIFIES", sig, {"leaked": leaked, "used": used, "ratio": round(ratio, 3)}


def run(store_dir) -> dict:
    sd = Path(store_dir)

    def body(sha):
        return json.loads(gzip.open(sd / "payloads" / sha[:2] / sha[2:4] / (sha + ".json.gz"), "rt").read())
    flow, earn = {}, {}
    for ln in (sd / "v2_evidence.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(ln)
        if r["status"] == "OK" and r["kind"] == "uw_flow_alerts":
            flow[r["symbol"]] = r
    for ln in (sd / "evidence.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(ln)
        if r["kind"] == "earnings":
            earn[r["symbol"]] = r
    events = []
    for sym, fr in flow.items():
        if sym not in earn:
            continue
        alerts = body(fr["raw_sha256"]).get("data") or []
        for row in body(earn[sym]["raw_sha256"]).get("data") or []:
            mv = _f(row.get("post_earnings_move_1d"))
            if mv is None:
                continue
            kind, cut = cutoff_for(str(row["report_date"])[:10], row.get("report_time"))
            if cut is None:
                continue
            events.append((sym, str(row["report_date"])[:10], cut, mv * 100.0, alerts))
    events.sort(key=lambda e: e[1])
    half = len(events) // 2
    out = {"events_total": len(events), "stocks": len({e[0] for e in events}), "windows": {}, "note": "descriptive only; no window is selected; flat band +-%.1f%% (frozen grader)" % FLAT}
    for L in WINDOWS:
        res = {"states": defaultdict(int), "leaked_alerts_excluded": 0, "decided": [], "all_signals": []}
        for i, (sym, rd, cut, mv, alerts) in enumerate(events):
            state, sig, det = flow_signal(alerts, rd, cut, L)
            res["states"][state] += 1
            res["leaked_alerts_excluded"] += det.get("leaked", 0)
            if sig:
                hit = (sig == "BUY" and mv > FLAT) or (sig == "SELL" and mv < -FLAT)
                res["decided"].append({"sym": sym, "date": rd, "sig": sig, "move": round(mv, 2), "hit": hit, "half": 0 if i < half else 1})
        d = res["decided"]
        k = sum(1 for x in d if x["hit"])
        halves = {}
        for h in (0, 1):
            dd = [x for x in d if x["half"] == h]
            kk = sum(1 for x in dd if x["hit"])
            halves["older_events" if h == 0 else "newer_events"] = {"decided": len(dd), "hits": kk, "ci95": G.wilson(kk, len(dd)) if dd else None}
        out["windows"][str(L)] = {"states": dict(res["states"]), "qualifying_events": res["states"]["QUALIFIES"], "coverage_of_events": round(res["states"]["QUALIFIES"] / len(events), 4) if events else None,
                                  "decided_buy_sell": len(d), "hits": k, "hit_rate": round(k / len(d), 3) if d else None, "ci95": G.wilson(k, len(d)) if d else None, "walk_forward_halves": halves,
                                  "leaked_alerts_excluded": res["leaked_alerts_excluded"], "minimum_sample_met": len(d) >= G.MIN_DECIDED_PER_VARIANT}
    out["verdict"] = ("INCONCLUSIVE: no window reaches %d decided events; the windows cannot be ranked on accuracy" % G.MIN_DECIDED_PER_VARIANT) if not any(w["minimum_sample_met"] for w in out["windows"].values()) else "see per-window intervals; overlapping intervals mean no demonstrated difference"
    return out


def main(argv=None):
    store = (argv or sys.argv[1:] or [r"C:\Users\initi\SmartMoveRun\v2_runs\v2_20261101\store"])[0]
    out = run(store)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
