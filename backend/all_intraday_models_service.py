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


def _sig_from_score(score) -> Optional[str]:
    """Same rule as every other column (BUY>=55 / SELL<=45), so the Trdgo column
    is directional too -- its own call is gated to WITHHELD/NO-EDGE pre-market."""
    if not isinstance(score, (int, float)):
        return None
    return "BUY" if score >= 55 else "SELL" if score <= 45 else "NEUTRAL"


def _cell(signal: Optional[str], score) -> dict:
    return {"signal": signal, "score": (round(score, 1) if isinstance(score, (int, float)) else None)}


def _row_cells(sym: str, s: dict, eq: Optional[dict], trade: Optional[dict],
               pm_map: Optional[dict] = None) -> dict:
    import claude_pro_intraday_service as cpi
    import kimi_intraday_service as kin
    import chatgpt_intraday_service as cgi
    cells: dict[str, dict] = {}

    # Trdgo Stock -- mirror what the Trdgo Stock PAGE shows, exactly. The page's
    # RTH board only lists confirmed signals (strict gates + 2-scan), so a name it
    # does not flag is NEUTRAL here too -- not its raw score. Pre-market pm_map
    # holds all names (the pre-market board).
    if pm_map and sym in pm_map:
        sig, sc = pm_map[sym]
        cells["trdgo"] = _cell(sig, sc)
    else:
        cells["trdgo"] = _cell("NEUTRAL", s.get("score"))

    def safe(fn):
        try:
            r = fn()
            return _cell(r.get("signal"), r.get("score")) if r else _cell(None, None)
        except Exception:  # noqa: BLE001
            return _cell(None, None)

    # Data-safety gate (same rule as the six boards): a model whose inputs are stale, or whose live
    # engine is unhealthy, shows WAIT here -- never a BUY/SELL built on old data.
    import intraday_safety as safety
    try:
        import intraday_indicators as _ind
        phase = _ind.market_phase()
    except Exception:  # noqa: BLE001
        phase = None
    import tab_settings as _cfg
    im_age = safety.age_s(f"im2:{sym}")
    live_ok = (s.get("status") in (None, "OK") and im_age is not None
               and im_age <= _cfg.INTRADAY_FRESHNESS["engine_score"][1])

    # Live-engine models (re-bucket the im2 score).
    cells["claude_pro"] = safe(lambda: cpi.score_from_model(sym, s)) if live_ok else _cell(safety.WAIT, None)
    cells["kimi"] = safe(lambda: kin.score_from_model(sym, s)) if live_ok else _cell(safety.WAIT, None)
    cells["chatgpt"] = safe(lambda: cgi._score(sym, s, eq)) if live_ok else _cell(safety.WAIT, None)

    # Equity-signal models (need earn:equity).
    if eq and eq.get("params"):
        import intraday_grok_service as ig
        import meta_intraday_service as mi
        import gemini_intraday_service as gin
        eq_eff, _eq_age, _src = safety.effective_eq(sym, eq, phase)
        if not eq_eff["_meta"]["missing_critical"]:          # every critical input fresh for its own kind of data
            cells["grok"] = safe(lambda: ig.score_one(sym, eq_eff, None, {}))
            cells["meta"] = safe(lambda: mi.score_one(sym, eq_eff, None, {}))
            cells["gemini"] = safe(lambda: gin.score_one(sym, eq_eff, trade, {}))
        else:
            for k in ("grok", "meta", "gemini"):
                cells[k] = _cell(safety.WAIT, None)
    else:
        for k in ("grok", "meta", "gemini"):
            cells[k] = _cell(None, None)
    return cells


def scan() -> dict:
    import swr
    import ai_trade_service as ats
    syms = [s.upper() for s in getattr(ats, "UNIVERSE", [])]
    prev = swr.peek("allintra:last") or {}

    # Before the open, the Trdgo Stock page shows the pre-market board; mirror it
    # in the Trdgo column so the two always agree.
    # The Trdgo column must mirror the Trdgo Stock page EXACTLY. The page keys off
    # the signal board's own phase: pre/closed -> the pre-market board (pm:board);
    # otherwise -> the live RTH signal board (im2:board). Build one symbol->(sig,
    # score) map from whichever the page is showing.
    pm_map: dict = {}
    try:
        im2board = swr.peek("im2:board") or {}
        page_phase = im2board.get("phase")
        if page_phase in ("pre", "closed"):
            pm = swr.peek("pm:board") or {}
            rows_pm = [r for grp in ("buyers", "sellers", "neutral") for r in (pm.get(grp) or [])]
            if not rows_pm:
                try:
                    import intraday_premarket as ipm
                    b = ipm.board()
                    swr.serve("pm:board", lambda: b, 120.0)
                    rows_pm = [r for grp in ("buyers", "sellers", "neutral")
                               for r in (b.get(grp) or [])]
                except Exception:  # noqa: BLE001
                    rows_pm = []
            for r in rows_pm:
                pm_map[r["symbol"]] = (r.get("signal"), r.get("score"))
        else:
            # RTH -- use the live signal board's confirmed calls (same as the page).
            for r in im2board.get("buyers") or []:
                pm_map[r["symbol"]] = (_dir((r.get("signal") or {}).get("state")), r.get("score"))
            for r in im2board.get("sellers") or []:
                pm_map[r["symbol"]] = (_dir((r.get("signal") or {}).get("state")), r.get("score"))
            for grp in ("no_edge", "withheld"):
                for r in im2board.get(grp) or []:
                    pm_map[r["symbol"]] = ("NEUTRAL", r.get("score"))
    except Exception:  # noqa: BLE001
        pm_map = {}
    out: list[dict] = []
    snapshot: dict[str, dict] = {}
    for sym in syms:
        s = swr.peek(f"im2:{sym}")
        if not s:
            continue
        eq = swr.peek(f"earn:equity:{sym}")
        trade = swr.peek(f"earn:trade:default:{sym}")
        cells = _row_cells(sym, s, eq, trade, pm_map)
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
