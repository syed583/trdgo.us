"""V1 versus V2 comparison on the SAME events and the SAME capture timestamps. Descriptive and walk-forward; INCONCLUSIVE unless the pre-registered minimum sample is met
(100 decided events per variant over at least 8 earnings weeks) for BOTH models. It never states that V2 is better; it reports differences with intervals.
rows: dicts with symbol, earnings_date, kind, sector, variant, move_pct (None = no graded outcome), v1_signal, v2_signal, adv20 (optional)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "shadow_impl"))
from smvshadow import grader as G  # noqa: E402

MIN_N, MIN_WEEKS = G.MIN_DECIDED_PER_VARIANT, G.MIN_EARNINGS_WEEKS


def _wk(r):
    return date.fromisoformat(str(r["earnings_date"])[:10]).isocalendar()[:2]


def _stats(rows, key):
    graded = [r for r in rows if r.get("move_pct") is not None]
    dec = [r for r in graded if r[key] in ("BUY", "SELL")]
    hits = [r for r in dec if (r[key] == "BUY" and r["move_pct"] > G.FLAT_BAND) or (r[key] == "SELL" and r["move_pct"] < -G.FLAT_BAND)]
    nets = [G.net_return(1 if r[key] == "BUY" else -1, r["move_pct"], r.get("adv20"), 1.0) for r in dec]

    def side(s):
        d = [r for r in dec if r[key] == s]
        return {"n": len(d), "hits": sum(1 for r in d if r in hits)}
    return {"graded": len(graded), "decided": len(dec), "coverage": round(len(dec) / len(graded), 3) if graded else None, "no_trade": sum(1 for r in graded if r[key] == "NO_TRADE"),
            "hit_rate": round(len(hits) / len(dec), 3) if dec else None, "ci95": G.wilson(len(hits), len(dec)) if dec else None, "buy": side("BUY"), "sell": side("SELL"),
            "net_mean_pct_with_cost": round(sum(nets) / len(nets), 3) if nets else None}


def _weeks(rows, key):
    return {_wk(r) for r in rows if r.get("move_pct") is not None and r[key] in ("BUY", "SELL")}


def walk_forward(rows, key, folds=3):
    """Consecutive blocks of earnings weeks in time order; each block is scored on its own weeks only. No model is fitted here, so this shows stability over time, not a fit."""
    weeks = sorted({_wk(r) for r in rows if r.get("move_pct") is not None})
    if len(weeks) < folds:
        return []
    cut = [weeks[i * len(weeks) // folds] for i in range(folds)] + [(9999, 99)]
    return [{"weeks": [list(cut[i]), list(cut[i + 1])], **_stats([r for r in rows if cut[i] <= _wk(r) < cut[i + 1]], key)} for i in range(folds)]


def compare(rows: list, *, simulated: bool = False) -> dict:
    out = {"simulated": simulated, "variants": {}, "verdict": None}
    ok_all = True
    for v in ("continuation", "contrarian"):
        rs = [r for r in rows if r["variant"] == v]
        a, b = _stats(rs, "v1_signal"), _stats(rs, "v2_signal")
        enough = all(x["decided"] >= MIN_N for x in (a, b)) and all(len(_weeks(rs, k)) >= MIN_WEEKS for k in ("v1_signal", "v2_signal"))
        ok_all = ok_all and enough
        by = {}
        for dim in ("sector", "kind"):
            by[dim] = {g: {"v1": _stats([r for r in rs if (r.get(dim) or "unknown") == g], "v1_signal"), "v2": _stats([r for r in rs if (r.get(dim) or "unknown") == g], "v2_signal")}
                       for g in sorted({r.get(dim) or "unknown" for r in rs})}
        out["variants"][v] = {"events": len(rs), "v1": a, "v2": b, "sample_sufficient": enough,
                              "missing_v2_signal_rate": round(sum(1 for r in rs if r["v2_signal"] is None) / len(rs), 3) if rs else None,
                              "walk_forward": {"v1": walk_forward(rs, "v1_signal"), "v2": walk_forward(rs, "v2_signal")}, "by": by}
    out["verdict"] = ("INCONCLUSIVE: minimum sample (100 decided per variant for both models over 8+ earnings weeks) not met" if not ok_all
                      else "DESCRIPTIVE ONLY: compare the intervals; overlap means no demonstrated difference")
    if simulated:
        out["verdict"] = "SIMULATED DATA: tests the comparison code only. " + out["verdict"]
    return out
