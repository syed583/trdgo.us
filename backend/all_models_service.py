"""All-in-One -- every pre-earnings model's call for each upcoming name, in one
matrix (Excel-style). Rows = stocks reporting soon; columns = the models; each
cell = that model's signal (BUY/SELL/NEUTRAL) + score. A cell is flagged
`changed` when its signal differs from the previous scan, so status changes stand
out.

All models read the same warmed caches (earn:equity signals + earn:trade), so the
whole matrix is built in one pass with no extra per-name API calls.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import claude_last_service as cl
import grok_service as gk
import meta_service as mt
import chatgpt_service as cg
import kimi_service as ki
import gemini_service as gm
import claude_pro_service as cp

SOURCE = "All-in-One — every model, one sheet"

# Column order + display labels (the owner's list).
MODELS = [
    ("claude_p0", "Claude +0"),
    ("claude_pm", "Claude +-"),
    ("claude_new", "Claude New"),
    ("claude_new_pz", "Claude New +0"),
    ("grok", "Grok"),
    ("meta", "Meta"),
    ("chatgpt", "ChatGPT"),
    ("kimi", "Kimi"),
    ("gemini", "Gemini"),
    ("claude_pro", "Claude Pro"),
]


def _dir(decision: Optional[str]) -> str:
    """Map a trade-engine decision to BUY/SELL/NEUTRAL."""
    d = (decision or "").upper()
    if d in ("BUY", "STRONG BUY", "LONG"):
        return "BUY"
    if d in ("SELL", "STRONG SELL", "SHORT"):
        return "SELL"
    return "NEUTRAL"


def _cell(signal: Optional[str], score) -> dict:
    return {"signal": signal, "score": (round(score, 1) if isinstance(score, (int, float)) else None)}


def _row_cells(sym: str, eq: dict, trade: dict, mkt: Optional[float]) -> dict:
    cells: dict[str, dict] = {}
    # Claude +0 / +- come from the trade engine's equity score + overall decision.
    tr = trade or {}
    eqscore = ((tr.get("equity") or {}).get("score"))
    cells["claude_p0"] = _cell(_dir(tr.get("overall_decision")), eqscore)
    if eqscore is not None:
        pm = "BUY" if eqscore >= 58 else "SELL" if eqscore <= 42 else "NEUTRAL"
    else:
        pm = None
    cells["claude_pm"] = _cell(pm, eqscore)

    # The seven shared-framework direction models.
    def safe(fn):
        try:
            r = fn()
            return _cell(r.get("signal"), r.get("score"))
        except Exception:  # noqa: BLE001
            return _cell(None, None)

    cn = safe(lambda: cl.score_one(sym, eq, tr, {}))
    cells["claude_new"] = cn
    cells["claude_new_pz"] = dict(cn)   # same engine as Claude New
    cells["grok"] = safe(lambda: gk.score_one(sym, eq, tr, {}, with_analyst=False))
    cells["meta"] = safe(lambda: mt.score_one(sym, eq, tr, {}))
    cells["chatgpt"] = safe(lambda: cg.score_one(sym, eq, tr, {}, with_analyst=False))
    cells["kimi"] = safe(lambda: ki.score_one(sym, eq, tr, {}, with_analyst=False, mkt=mkt))
    cells["gemini"] = safe(lambda: gm.score_one(sym, eq, tr, {}))
    cells["claude_pro"] = safe(lambda: cp.score_one(sym, eq, tr, {}, with_analyst=False, mkt=mkt))
    return cells


def scan(days: int = 15) -> dict:
    import swr
    import uw_earnings_calendar as calmod

    rows = (calmod.calendar(days=days) or {}).get("rows") or []
    seen: set[str] = set()
    uniq: list[dict] = []
    for r in rows:
        s = (r.get("symbol") or "").upper()
        if s and s not in seen:
            seen.add(s)
            uniq.append(r)
    eqs = swr.peek_many([f"earn:equity:{s}" for s in seen])
    trades = swr.peek_many([f"earn:trade:default:{s}" for s in seen])
    mkt = ki.market_bias()
    try:
        import finviz_service as fv
        fmap = fv.earnings_map()
    except Exception:  # noqa: BLE001
        fmap = {}
    prev = swr.peek("allmodels:last") or {}

    def _days_to(r: dict):
        d = r.get("date")
        if not d:
            return None
        try:
            return (datetime.fromisoformat(str(d)[:10]).date() - date.today()).days
        except Exception:  # noqa: BLE001
            return None

    out: list[dict] = []
    snapshot: dict[str, dict] = {}
    scored = 0
    for r in uniq:
        s = r["symbol"].upper()
        eq = eqs.get(f"earn:equity:{s}")
        trade = trades.get(f"earn:trade:default:{s}")
        if not (eq and eq.get("params")):
            continue   # only show names we can actually score across the models
        cells = _row_cells(s, eq, trade or {}, mkt)
        scored += 1
        # Flag status changes vs the previous scan.
        psig = prev.get(s) or {}
        changed_any = False
        for k, c in cells.items():
            last = psig.get(k)
            c["changed"] = bool(last and c.get("signal") and last != c["signal"])
            changed_any = changed_any or c["changed"]
        snapshot[s] = {k: c.get("signal") for k, c in cells.items()}
        row = {"symbol": s, "company": r.get("company"), "date": r.get("date"),
               "date_label": r.get("date_label"), "days_to": _days_to(r),
               "report_timing": r.get("time_label") or r.get("timing_label"),
               "sector": r.get("sector"), "cells": cells, "changed": changed_any}
        fvi = fmap.get(s)
        if fvi and fvi.get("time") and str(fvi.get("date"))[:10] == str(row.get("date"))[:10]:
            row["report_clock"] = fvi["time"]
        out.append(row)

    # Remember this scan's signals (in-process) so the next scan can diff against
    # it and flag status changes.
    try:
        swr._values["allmodels:last"] = (__import__("time").time(), snapshot)
    except Exception:  # noqa: BLE001
        pass

    out.sort(key=lambda r: (r["date"] or "￿", r["symbol"]))
    return {"status": "OK", "source": SOURCE, "models": MODELS, "rows": out,
            "universe": len(seen), "scored": scored}
