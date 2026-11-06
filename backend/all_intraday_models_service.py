"""All Intraday Models -- every intraday board's call per universe name, one sheet.

Rows = the liquid universe; columns = the four intraday models (Grok, Meta, Kimi,
Gemini). Each cell = that model's signal + score, built from the shared equity
signals (earn:equity) through the same per-input freshness rule the boards use.
"""
from __future__ import annotations

from typing import Optional

SOURCE = "All Intraday Models — one sheet"

MODELS = [
    ("grok", "Grok"),
    ("meta", "Meta"),
    ("kimi", "Kimi"),
    ("gemini", "Gemini"),
]


def _cell(signal: Optional[str], score) -> dict:
    return {"signal": signal, "score": (round(score, 1) if isinstance(score, (int, float)) else None)}


def _row_cells(sym: str, eq: Optional[dict], trade: Optional[dict], phase: Optional[str]) -> dict:
    import intraday_safety as safety
    cells: dict[str, dict] = {}

    def safe(fn):
        try:
            r = fn()
            return _cell(r.get("signal"), r.get("score")) if r else _cell(None, None)
        except Exception:  # noqa: BLE001
            return _cell(None, None)

    if eq and eq.get("params"):
        import intraday_grok_service as ig
        import meta_intraday_service as mi
        import kimi_intraday_service as kin
        import gemini_intraday_service as gin
        eq_eff, _eq_age, _src = safety.effective_eq(sym, eq, phase)
        if not eq_eff["_meta"]["missing_critical"]:          # every critical input fresh for its own kind of data
            cells["grok"] = safe(lambda: ig.score_one(sym, eq_eff, None, {}))
            cells["meta"] = safe(lambda: mi.score_one(sym, eq_eff, None, {}))
            cells["kimi"] = safe(lambda: kin.score_one(sym, eq_eff, None, {}))
            cells["gemini"] = safe(lambda: gin.score_one(sym, eq_eff, trade, {}))
        else:
            for k, _ in MODELS:
                cells[k] = _cell(safety.WAIT, None)
    else:
        for k, _ in MODELS:
            cells[k] = _cell(None, None)
    return cells


def scan() -> dict:
    import swr
    import ai_trade_service as ats
    syms = [s.upper() for s in getattr(ats, "UNIVERSE", [])]
    prev = swr.peek("allintra:last") or {}
    try:
        import intraday_indicators as _ind
        phase = _ind.market_phase()
    except Exception:  # noqa: BLE001
        phase = None
    out: list[dict] = []
    snapshot: dict[str, dict] = {}
    for sym in syms:
        eq = swr.peek(f"earn:equity:{sym}")
        if not eq:
            continue                                        # never scored: no row rather than an invented one
        trade = swr.peek(f"earn:trade:default:{sym}")
        cells = _row_cells(sym, eq, trade, phase)
        psig = prev.get(sym) or {}
        changed_any = False
        for k, c in cells.items():
            last = psig.get(k)
            c["changed"] = bool(last and c.get("signal") and last != c["signal"])
            changed_any = changed_any or c["changed"]
        snapshot[sym] = {k: c.get("signal") for k, c in cells.items()}
        spot = None
        s = swr.peek(f"im2:{sym}")
        if s:
            spot = s.get("spot")
        out.append({"symbol": sym, "cells": cells, "changed": changed_any, "spot": spot})
    try:
        swr._values["allintra:last"] = (__import__("time").time(), snapshot)
    except Exception:  # noqa: BLE001
        pass
    out.sort(key=lambda r: r["symbol"])
    return {"status": "OK", "source": SOURCE, "models": MODELS, "rows": out,
            "universe": len(syms), "scored": len(out)}
