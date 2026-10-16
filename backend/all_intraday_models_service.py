"""All Intraday Models -- every intraday board's call per universe name, one sheet.

Rows = the liquid universe; columns = the intraday models (Trdgo Stock v2, Grok,
Meta, Claude Pro, Kimi, Gemini, ChatGPT). Each cell = that model's signal + score.
Reads the shared caches (im2 live score, earn:equity, earn:trade) in one pass.
"""
from __future__ import annotations

from typing import Optional

SOURCE = "All Intraday Models — one sheet"

MODELS = [
    ("trdgo", "Trdgo Stock"),
    ("grok", "Grok"),
    ("meta", "Meta"),
    ("claude_pro", "Claude Pro"),
    ("kimi", "Kimi"),
    ("gemini", "Gemini"),
    ("chatgpt", "ChatGPT"),
]


def _dir(call: Optional[str]) -> str:
    c = (call or "").upper()
    if "BUY" in c:
        return "BUY"
    if "SELL" in c:
        return "SELL"
    return "NEUTRAL"


def _cell(signal: Optional[str], score) -> dict:
    return {"signal": signal, "score": (round(score, 1) if isinstance(score, (int, float)) else None)}


def _row_cells(sym: str, s: dict, eq: Optional[dict], trade: Optional[dict]) -> dict:
    import claude_pro_intraday_service as cpi
    import kimi_intraday_service as kin
    import chatgpt_intraday_service as cgi
    cells: dict[str, dict] = {}

    # Trdgo Stock v2 -- straight from the live score.
    cells["trdgo"] = _cell(_dir(s.get("call")), s.get("score"))

    def safe(fn):
        try:
            r = fn()
            return _cell(r.get("signal"), r.get("score")) if r else _cell(None, None)
        except Exception:  # noqa: BLE001
            return _cell(None, None)

    # Live-engine models (re-bucket the im2 score).
    cells["claude_pro"] = safe(lambda: cpi.score_from_model(sym, s))
    cells["kimi"] = safe(lambda: kin.score_from_model(sym, s))
    cells["chatgpt"] = safe(lambda: cgi._score(sym, s, eq))

    # Equity-signal models (need earn:equity).
    if eq and eq.get("params"):
        import intraday_grok_service as ig
        import meta_intraday_service as mi
        import gemini_intraday_service as gin
        cells["grok"] = safe(lambda: ig.score_one(sym, eq, None, {}))
        cells["meta"] = safe(lambda: mi.score_one(sym, eq, None, {}))
        cells["gemini"] = safe(lambda: gin.score_one(sym, eq, trade, {}))
    else:
        for k in ("grok", "meta", "gemini"):
            cells[k] = _cell(None, None)
    return cells


def scan() -> dict:
    import swr
    import ai_trade_service as ats
    syms = [s.upper() for s in getattr(ats, "UNIVERSE", [])]
    prev = swr.peek("allintra:last") or {}
    out: list[dict] = []
    snapshot: dict[str, dict] = {}
    for sym in syms:
        s = swr.peek(f"im2:{sym}")
        if not s:
            continue
        eq = swr.peek(f"earn:equity:{sym}")
        trade = swr.peek(f"earn:trade:default:{sym}")
        cells = _row_cells(sym, s, eq, trade)
        psig = prev.get(sym) or {}
        changed_any = False
        for k, c in cells.items():
            last = psig.get(k)
            c["changed"] = bool(last and c.get("signal") and last != c["signal"])
            changed_any = changed_any or c["changed"]
        snapshot[sym] = {k: c.get("signal") for k, c in cells.items()}
        out.append({"symbol": sym, "cells": cells, "changed": changed_any,
                    "spot": s.get("spot")})
    try:
        swr._values["allintra:last"] = (__import__("time").time(), snapshot)
    except Exception:  # noqa: BLE001
        pass
    out.sort(key=lambda r: r["symbol"])
    return {"status": "OK", "source": SOURCE, "models": MODELS, "rows": out,
            "universe": len(syms), "scored": len(out)}
