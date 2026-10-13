"""Tab B -- Short Straddle Scanner.

Ranks upcoming-earnings names by how inflated their option premium / IV is
before the report (the bigger the expected IV crush, the better the sell). Four
banded filters (100 pts): IV Rank 40, Implied Move % 25, Implied-vs-historical
20, Liquidity 15. Action SELL >= 70 / WATCH 50-69 / SKIP, plus a bullish/
bearish/neutral tilt and hard exclusions. It does NOT predict direction.

Reuses the warmed earn:trade:default entry for the implied move %, the average
historical earnings move and the tilt; reads IV rank (uw_volatility_service)
and the ATM straddle spread (earnings_options_service) fresh, cached per symbol
as tabb:{SYM}.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import tab_settings as cfg

SOURCE = "Tab B — Straddle Scanner"


def _f(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _band(table, value) -> int:
    if value is None:
        return 0
    for thr, pts in table:
        if value >= thr:
            return pts
    return 0


def _liquidity_pts(spread_pct: Optional[float]) -> int:
    """Spread as a % of the straddle premium. (OI isn't exposed by the straddle
    builder, so this uses spread only; a spread over the low band scores 0.)"""
    if spread_pct is None:
        return 0
    L = cfg.TAB_B_LIQUIDITY
    full = cfg.TAB_B_WEIGHTS["liquidity"]
    if spread_pct <= L["full_spread"]:
        return full
    if spread_pct <= L["mid_spread"]:
        return 10
    if spread_pct <= L["low_spread"]:
        return 5
    return 0


def compute(symbol: str) -> dict:
    """The per-symbol scan row (cached as tabb:{SYM})."""
    import swr
    import uw_volatility_service as volsvc
    import earnings_options_service as opt

    symbol = symbol.upper()
    d = swr.peek(f"earn:trade:default:{symbol}") or {}
    em = d.get("expected_move") or {}
    implied = _f(em.get("percent"))
    spot = _f(em.get("current")) or _f(d.get("price"))

    hm = d.get("historical_moves") or []
    hvals = [abs(_f(m.get("move_pct"))) for m in hm if _f(m.get("move_pct")) is not None]
    avg_hist = round(sum(hvals) / len(hvals), 2) if hvals else None
    ratio = round(implied / avg_hist, 2) if (implied and avg_hist) else None

    iv_rank = None
    try:
        v = volsvc.get_volatility(symbol)
        iv_rank = _f((v.get("stats") or {}).get("iv_rank"))
        if iv_rank is not None and iv_rank <= 1.5:   # some symbols report 0..1
            iv_rank = round(iv_rank * 100, 1)
    except Exception:  # noqa: BLE001
        iv_rank = None

    spread_pct = None
    try:
        st = opt._atm_straddle(symbol, spot)
        sp = (st or {}).get("spread_pct")
        spread_pct = round(sp * 100, 2) if sp is not None else None
    except Exception:  # noqa: BLE001
        spread_pct = None

    iv_pts = _band(cfg.TAB_B_BANDS["iv_rank"], iv_rank)
    im_pts = _band(cfg.TAB_B_BANDS["implied_move"], implied)
    r_pts = _band(cfg.TAB_B_BANDS["impl_vs_hist"], ratio)
    liq_pts = _liquidity_pts(spread_pct)
    score = iv_pts + im_pts + r_pts + liq_pts

    # Hard exclusions -- skip regardless of score, but show the reason.
    date_ok = bool((d.get("earnings") or {}).get("date"))
    excl = None
    if ratio is not None and ratio < cfg.TAB_B_MIN_IMPL_VS_HIST:
        excl = "Implied move below the stock's usual earnings move"
    elif liq_pts == 0:
        excl = "Illiquid — straddle spread too wide"
    elif not date_ok:
        excl = "Earnings date not confirmed"

    cut = cfg.TAB_B_CUTOFFS
    if excl:
        action = "SKIP"
    elif score >= cut["sell"]:
        action = "SELL"
    elif score >= cut["watch"]:
        action = "WATCH"
    else:
        action = "SKIP"

    # Direction tilt (where to centre the short strikes), from the equity call.
    dec = (d.get("overall_decision") or (d.get("equity") or {}).get("decision") or "").upper()
    tilt = "bullish" if "BUY" in dec else "bearish" if "SELL" in dec else "neutral"

    return {
        "symbol": symbol, "iv_rank": iv_rank, "implied_move": implied,
        "impl_vs_hist": ratio, "avg_hist_move": avg_hist, "spread_pct": spread_pct,
        "oi": None, "score": score, "action": action, "tilt": tilt,
        "exclusion": excl,
        "filters": [
            {"name": "iv_rank", "label": "IV Rank", "points": iv_pts, "max": cfg.TAB_B_WEIGHTS["iv_rank"], "value": iv_rank},
            {"name": "implied_move", "label": "Implied Move %", "points": im_pts, "max": cfg.TAB_B_WEIGHTS["implied_move"], "value": implied},
            {"name": "impl_vs_hist", "label": "Implied ÷ Hist", "points": r_pts, "max": cfg.TAB_B_WEIGHTS["impl_vs_hist"], "value": ratio},
            {"name": "liquidity", "label": "Liquidity", "points": liq_pts, "max": cfg.TAB_B_WEIGHTS["liquidity"], "value": spread_pct},
        ],
    }


def _cached(symbol: str) -> Optional[dict]:
    import swr
    return swr.serve(f"tabb:{symbol.upper()}", lambda: compute(symbol), 21600.0)


def scan(days: int = 15, warm: int = 0) -> dict:
    """Rank the upcoming-earnings universe. Reads cached tabb:{SYM}; `warm` > 0
    computes that many missing ones inline (the endpoint warms in background)."""
    import swr
    import uw_earnings_calendar as cal

    rows = (cal.calendar(days=days) or {}).get("rows") or []
    seen: set[str] = set()
    uniq: list[dict] = []
    for r in rows:
        s = (r.get("symbol") or "").upper()
        if s and s not in seen:
            seen.add(s)
            uniq.append(r)
    cached = swr.peek_many([f"tabb:{s}" for s in seen])

    def _days_to(r: dict):
        d = r.get("date")
        if not d:
            return None
        try:
            return (datetime.fromisoformat(str(d)[:10]).date() - date.today()).days
        except Exception:  # noqa: BLE001
            return None

    out: list[dict] = []
    missing: list[str] = []
    scored = 0
    for r in uniq:
        s = r["symbol"].upper()
        t = cached.get(f"tabb:{s}")
        meta = {"company": r.get("company"), "date": r.get("date"),
                "date_label": r.get("date_label"), "time_label": r.get("short_label"),
                "days_to": _days_to(r)}
        if t:
            out.append({**t, **meta})
            scored += 1
        else:
            # Every upcoming name shows; the scan fills in as it warms.
            missing.append(s)
            out.append({"symbol": s, "score": None, "action": "PENDING",
                        "tilt": None, "iv_rank": None, "implied_move": None,
                        "impl_vs_hist": None, "spread_pct": None, "exclusion": None,
                        **meta})

    for s in missing[:max(0, warm)]:
        try:
            _cached(s)
        except Exception:  # noqa: BLE001
            pass

    out.sort(key=lambda x: (x["score"] if x["score"] is not None else -1), reverse=True)
    return {"status": "OK", "source": SOURCE, "rows": out,
            "universe": len(seen), "scored": scored, "missing": missing[:60]}
