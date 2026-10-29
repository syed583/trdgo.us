"""All-in-One -- every earnings model's live call for each upcoming name, one row per
stock, one column per model.

NO separate calculation: each cell is read straight from that model's own tab scan,
through the SAME swr cache key and the SAME scan() function the tab's route uses. So
a cell can only differ from its tab if the tab itself changed between two reads.

Cell = {signal, score, coverage, tier}; signal is exactly the tab's signal
(BUY / SELL / NEUTRAL / LOW_DATA) or PENDING when the model has not scored the name.
A cell is flagged `changed` when its signal differs from the previous scan.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Callable, Optional

SOURCE = "All-in-One — every model, one sheet"

# Column order + the EXACT tab names used in the sidebar.
MODELS = [
    ("claude", "Claude"),
    ("claude_pro", "Claude Pro"),
    ("gemini", "Gemini"),
    ("grok", "Grok"),
    ("chatgpt", "ChatGPT"),
    ("meta", "Meta"),
    ("kimi", "Kimi"),
    ("ped", "Pre-Earnings Direction"),
]

# model id -> (tab swr key template, scan callable) -- identical to api_routes.py.
def _sources() -> dict[str, tuple[str, Callable[[int], dict]]]:
    import claude_last_service as v3
    import claude_pro_service as cp
    import gemini_service as gm
    import grok_service as gk
    import chatgpt_service as cg
    import meta_service as mt
    import kimi_service as ki
    import pre_earnings_direction_service as ped
    return {
        "claude": ("v3:scan:v2:{d}", v3.scan),
        "claude_pro": ("claudepro:scan:{d}", cp.scan),
        "gemini": ("gemini:scan:{d}", gm.scan),
        "grok": ("grok:scan:{d}", gk.scan),
        "chatgpt": ("chatgpt:scan:{d}", cg.scan),
        "meta": ("meta:scan:{d}", mt.scan),
        "kimi": ("kimi:scan:{d}", ki.scan),
        "ped": ("ped:scan:{d}", ped.scan),
    }


# model_predictions overlay uses the older model ids.
_OVERLAY_ID = {"claude": "claude_new"}


def _cell(r: Optional[dict]) -> dict:
    if not r or r.get("score") is None or r.get("signal") in (None, "PENDING"):
        return {"signal": "PENDING", "score": None, "coverage": None, "tier": None}
    sc = r.get("score")
    return {"signal": r.get("signal"),
            "score": round(sc, 1) if isinstance(sc, (int, float)) else None,
            "coverage": r.get("coverage"), "tier": r.get("tier")}


def _et(epoch: Optional[float]) -> Optional[str]:
    if not epoch:
        return None
    try:
        from zoneinfo import ZoneInfo
        d = datetime.fromtimestamp(epoch, ZoneInfo("America/New_York"))
        return d.strftime("%b %d, %I:%M %p ET").replace(" 0", " ")
    except Exception:  # noqa: BLE001
        return None


def tab_scan(model: str, d: int) -> tuple[dict, Optional[float]]:
    """(scan result, time it was built) exactly as the model's own tab sees it."""
    import swr
    key_t, fn = _sources()[model]
    key = key_t.format(d=d)
    res = swr.serve(key, lambda: fn(d), 90.0) or {}
    return res, swr.held_at(key)


def _row_days_to(m: dict):
    """Countdown on the US-Eastern date. A model's own scan row counts with the server clock (UTC+1), which is a day short overnight."""
    try:
        import earnings_clock
        d = earnings_clock.days_to(m.get("date"))
        return m.get("days_to") if d is None else d
    except Exception:  # noqa: BLE001 - a label must never break the scan
        return m.get("days_to")


def _inputs_built_map(symbols: list[str]) -> Optional[dict]:
    """{symbol: build epoch of its earn:equity cache entry}, or None when EARN_ROW_FRESHNESS is off (the default).

    One batched read, not one per symbol. Lets each row show the age of the data behind its BUY/SELL, which the row's own
    `updated_at` (the scan's build time) cannot."""
    import os
    if os.environ.get("EARN_ROW_FRESHNESS", "").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    try:
        import db_cache
        held = db_cache.get_many([f"earn:equity:{s}" for s in symbols])
        return {s: (held.get(f"earn:equity:{s}") or (None,))[0] for s in symbols}
    except Exception:  # noqa: BLE001 - a label must never break the scan
        return None


def scan(days: int = 15) -> dict:
    import swr

    per: dict[str, dict[str, dict]] = {}
    when: dict[str, Optional[float]] = {}
    meta: dict[str, dict] = {}
    for model, _ in MODELS:
        try:
            res, at = tab_scan(model, days)
        except Exception:  # noqa: BLE001
            res, at = {}, None
        when[model] = at
        rows = {}
        for r in res.get("rows") or []:
            s = (r.get("symbol") or "").upper()
            if s:
                rows[s] = r
                meta.setdefault(s, r)
        per[model] = rows

    prev = swr.peek("allmodels:last") or {}
    try:
        import finviz_service as fv
        fmap = fv.earnings_map()
    except Exception:  # noqa: BLE001
        fmap = {}

    out: list[dict] = []
    snapshot: dict[str, dict] = {}
    inputs_built = _inputs_built_map(list(meta))
    for s, m in meta.items():
        cells = {k: _cell(per[k].get(s)) for k, _ in MODELS}
        psig = prev.get(s) or {}
        changed_any = False
        for k, c in cells.items():
            last = psig.get(k)
            c["changed"] = bool(last and c["signal"] not in ("PENDING",) and last != c["signal"])
            changed_any = changed_any or c["changed"]
        snapshot[s] = {k: c["signal"] for k, c in cells.items()}
        buy = sum(1 for c in cells.values() if c["signal"] == "BUY")
        sell = sum(1 for c in cells.values() if c["signal"] == "SELL")
        # Row freshness: the OLDEST scan feeding a scored cell (conservative).
        stamps = [when[k] for k, c in cells.items() if c["signal"] != "PENDING" and when.get(k)]
        upd = min(stamps) if stamps else None
        row = {"symbol": s, "company": m.get("company"), "date": m.get("date"),
               "date_label": m.get("date_label"), "days_to": _row_days_to(m),
               "report_timing": m.get("report_timing") or m.get("time_label"),
               "sector": m.get("sector"), "cells": cells, "changed": changed_any,
               "consensus": {"buy": buy, "sell": sell, "text": f"{buy} BUY · {sell} SELL"},
               "updated_at": upd, "updated_et": _et(upd)}
        if inputs_built is not None:
            # `updated_at` above is when the SCAN was assembled (seconds ago) and says nothing about the data underneath it.
            # These say how old the cached analysis behind the calls is.
            built = inputs_built.get(s)
            row["inputs_updated_at"] = built
            row["inputs_age_h"] = None if built is None else round((__import__("time").time() - built) / 3600.0, 1)
        fvi = fmap.get(s)
        if fvi and fvi.get("time") and str(fvi.get("date"))[:10] == str(row.get("date"))[:10]:
            row["report_clock"] = fvi["time"]
        out.append(row)

    try:
        swr._values["allmodels:last"] = (__import__("time").time(), snapshot)
    except Exception:  # noqa: BLE001
        pass

    out.sort(key=lambda r: (r["date"] or "￿", r["symbol"]))

    # Historical overlay: each model's verified accuracy, each company's last real
    # post-earnings move, and each model's previous call -- stored predictions only.
    model_stats: dict = {}
    try:
        import model_prediction_service as mp
        ov = mp.overlay([r["symbol"] for r in out])
        stats = ov.get("model_stats", {})
        pv = ov.get("prev", {})
        last_move = ov.get("last_move", {})
        for k, _ in MODELS:
            if stats.get(_OVERLAY_ID.get(k, k)):
                model_stats[k] = stats[_OVERLAY_ID.get(k, k)]
        for row in out:
            lm = last_move.get(row["symbol"])
            if lm:
                row["last_move"] = lm
            for mdl, cell in row["cells"].items():
                p = (pv.get(_OVERLAY_ID.get(mdl, mdl)) or {}).get(row["symbol"])
                if p:
                    cell["prev"] = p
    except Exception:  # noqa: BLE001
        pass

    scored = sum(1 for r in out if any(c["signal"] != "PENDING" for c in r["cells"].values()))
    return {"status": "OK", "source": SOURCE, "models": MODELS, "rows": out,
            "model_stats": model_stats, "universe": len(meta), "scored": scored}
