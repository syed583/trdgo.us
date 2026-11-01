"""Smart Move accuracy breakdowns. Descriptive only: every group carries its sample size and a Wilson 95% interval, and the claim flag comes from the
frozen grader's minimum sample (100 decided events per variant over at least 8 earnings weeks). SIMULATION rows are refused here, never mixed in.
rows: dicts with variant, signal (BUY/SELL/NO_TRADE), move_pct (None = ungraded), mode, earnings_date and optional sector, kind, coverage (points/40), adv20, bull, bear."""
from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "shadow_impl"))
from smvshadow import grader as G  # noqa: E402

FLAT = G.FLAT_BAND


def _hit(sig, mv):
    return (sig == "BUY" and mv > FLAT) or (sig == "SELL" and mv < -FLAT)


def _bucket(key, r):
    if key == "coverage":
        c = r.get("coverage")
        return "unknown" if c is None else "40/40" if c >= 40 else "24-39" if c >= 24 else "<24"
    if key == "liquidity":
        a = r.get("adv20")
        return "unknown" if a is None else ">=200M" if a >= 200e6 else "20-200M" if a >= 20e6 else "<20M"
    if key == "score":
        s = max(r.get("bull") or 0, r.get("bear") or 0)
        return "unknown" if r.get("bull") is None else "14-17" if s < 18 else "18+"
    return str(r.get(key) or "unknown")


def _group(rs):
    dec = [r for r in rs if r["signal"] in ("BUY", "SELL") and r.get("move_pct") is not None]
    k = sum(1 for r in dec if _hit(r["signal"], r["move_pct"]))
    mv = [r["move_pct"] for r in rs if r.get("move_pct") is not None]
    lo_hi = G.wilson(k, len(dec)) if dec else None
    return {"events": len(rs), "decided": len(dec), "hits": k, "hit_rate": round(k / len(dec), 3) if dec else None, "ci95": lo_hi,
            "mean_move_pct": round(statistics.mean(mv), 3) if mv else None, "ungraded": sum(1 for r in rs if r.get("move_pct") is None)}


def breakdown(rows: list) -> dict:
    if any(r.get("mode") == "SIMULATION" for r in rows):
        raise ValueError("SIMULATION rows must not be analysed as real performance")
    out = {"variants": {}, "claim_allowed": False, "claim_text": "NO ACCURACY CLAIM: descriptive statistics only; not a guarantee of profit"}
    ok = []
    for v in ("continuation", "contrarian"):
        rs = [r for r in rows if r["variant"] == v]
        weeks = {date.fromisoformat(str(r["earnings_date"])[:10]).isocalendar()[:2] for r in rs if r["signal"] in ("BUY", "SELL") and r.get("move_pct") is not None}
        g = _group(rs)
        sig = {s: _group([r for r in rs if r["signal"] == s]) for s in ("BUY", "SELL")}
        no_trade = sum(1 for r in rs if r["signal"] == "NO_TRADE")
        dims = {}
        for key in ("sector", "kind", "coverage", "liquidity", "score"):
            groups = defaultdict(list)
            for r in rs:
                if r["signal"] in ("BUY", "SELL"):
                    groups[_bucket(key, r)].append(r)
            dims[key] = {b: _group(x) for b, x in sorted(groups.items())}
        met = g["decided"] >= G.MIN_DECIDED_PER_VARIANT and len(weeks) >= G.MIN_EARNINGS_WEEKS
        ok.append(met)
        out["variants"][v] = {"signal_counts": {k: sum(1 for r in rs if r["signal"] == k) for k in ("BUY", "SELL", "NO_TRADE")}, "overall": g, "by_signal": sig, "no_trade": no_trade, "no_trade_frequency": round(no_trade / len(rs), 3) if rs else None,
                              "earnings_weeks": len(weeks), "minimum_sample_met": met, "by": dims}
    out["minimum_sample_met_both"] = all(ok)
    return out


def rows_from_records(preds: list, outcomes: list, *, mode: str = "LIVE", sector_lookup: dict | None = None, kind_lookup: dict | None = None) -> list:
    """Analytics rows from stored prediction versions and outcomes. Latest version per event+variant; late captures and out-of-window predictions are excluded
    (same rule as the grader). Metadata comes from the record's own `meta`; an older record without it falls back to the saved calendar sector only,
    and liquidity stays unknown. Nothing is guessed."""
    import smart_move_tracking as T
    latest = {}
    for p in preds:
        if (p.get("mode") or "LIVE") != mode:
            continue
        k = (p["symbol"], str(p["earnings_date"])[:10])
        if k not in latest or (p.get("version", 1), p["captured_at"]) > (latest[k].get("version", 1), latest[k]["captured_at"]):
            latest[k] = p
    om = {(o["symbol"], o["earnings_date"]): o for o in outcomes if o.get("mode") == mode}
    rows = []
    for (sym, ed), p in latest.items():
        if T.is_late(p):
            continue
        meta = p.get("meta") or {}
        o = om.get((sym, ed))
        for v in T.VARIANTS:
            e = p["engine"].get(v)
            if not e or not T.in_window(p, v):
                continue
            rows.append({"variant": v, "signal": e["signal"], "move_pct": o["move_pct"] if o else None, "mode": mode, "earnings_date": ed, "symbol": sym,
                         "sector": meta.get("sector") or (sector_lookup or {}).get(sym), "kind": meta.get("kind") or (kind_lookup or {}).get((sym, ed)), "coverage": e.get("available"),
                         "adv20": meta.get("adv20"), "bull": e.get("bull"), "bear": e.get("bear")})
    return rows
