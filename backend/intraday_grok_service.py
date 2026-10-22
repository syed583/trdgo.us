"""Grok Intraday -- short-term directional model (10 live signals).

A fast intraday/short-term lean built from the live equity signals (options flow,
unusual activity, participation, trend, levels, volatility, momentum), weighted
toward the signals that move a stock on the day rather than fundamentals:

  Options Flow (sweeps+prem)  34   Price Trend & Structure     24
  Unusual Activity (filtered) 10   Volume + Put/Call             8
  Gamma / Key Option Levels    8   Near-Term OI Change           6
  Event / Catalyst             5   RSI / Momentum                3
  Options Disparity (C/P)      2   Expected Move                 0 (sizing only)

All-positive 5-step scale, 0-100, BUY>=55 / NEUTRAL / SELL<=45. Reads the warmed
equity signals so the whole universe scores in one pass with no extra calls.
"""
from __future__ import annotations

import tab_settings as _ts
from datetime import date, datetime
from typing import Optional

import tab_settings as cfg
import claude_last_service as cl

SOURCE = "Grok Intraday — Short-term Direction (10 signals)"

_LABEL = {
    "options_flow": "Options Flow (Sweeps + Premium)",
    "trend_price": "Price Trend & Structure",
    "unusual_activity": "Unusual Activity (Filtered)",
    "volume_pcr": "Volume + Put/Call",
    "key_levels": "Gamma / Key Option Levels",
    "oi_change_expiry": "Near-Term OI Change",
    "event_radar": "Event / Catalyst",
    "rsi_momentum": "RSI / Momentum",
    "disparity": "Options Disparity (C/P)",
    "volatility_em": "Expected Move (sizing only)",
}
# Display/scoring order follows the ranked weighting (highest first).
_ORDER = list(_LABEL.keys())


def score_one(symbol: str, eq: dict, trade: Optional[dict] = None,
              row: Optional[dict] = None) -> dict:
    biases = {p.get("name"): p.get("bias") for p in (eq.get("params") or [])}
    sig = eq.get("signals") or {}
    row = row or {}
    W = cfg.INTRADAY_GROK_WEIGHTS

    raw: dict[str, Optional[float]] = {
        "options_flow": sig.get("options_flow", biases.get("options_positioning")),
        "unusual_activity": sig.get("unusual_activity", biases.get("unusual_options")),
        "volume_pcr": sig.get("volume_pcr"),
        "trend_price": cl._avg([sig.get("ema_trend"),
                                sig.get("price_action", biases.get("price_action"))]),
        "key_levels": sig.get("key_levels"),
        "oi_change_expiry": cl._avg([sig.get("daily_oi_change"), sig.get("flow_by_expiry")]),
        "volatility_em": cl._avg([sig.get("implied_volatility"), sig.get("expected_move")]),
        "rsi_momentum": sig.get("rsi"),
        "disparity": sig.get("disparity", biases.get("disparity")),
        "event_radar": sig.get("event_radar"),
    }

    params: list[dict] = []
    for name in _ORDER:
        qb = cl._quant(raw.get(name))
        params.append({"name": name, "label": _LABEL[name], "max": W[name],
                       "points": round(cl._pts(W[name], qb), 2), "bias": qb})

    score = round(sum(p["points"] for p in params), 1)
    cut = cfg.V3_CUTOFFS
    signal = "BUY" if score >= cut["buy"] else "SELL" if score <= cut["sell"] else "NEUTRAL"

    covered = sum(p["max"] for p in params if p["bias"] is not None)
    pos_w = sum(p["max"] for p in params if (p["bias"] or 0) > 0.05)
    neg_w = sum(p["max"] for p in params if (p["bias"] or 0) < -0.05)
    coverage = round(covered / 100.0, 3)
    side = 1 if score >= 50 else -1
    agreement = round(((pos_w if side > 0 else neg_w) / covered), 3) if covered else 0.0
    conf = cfg.V3_CONFIDENCE
    low_conf = coverage < conf["min_coverage"] or agreement < conf["min_agreement"]

    days_to = None
    d = row.get("date")
    if d:
        try:
            days_to = (datetime.fromisoformat(str(d)[:10]).date() - date.today()).days
        except Exception:  # noqa: BLE001
            days_to = None

    return {"symbol": symbol, "company": row.get("company"),
            "date": d, "date_label": row.get("date_label"),
            "time_label": row.get("short_label"), "days_to": days_to,
            "report_timing": row.get("time_label") or row.get("timing_label"),
            "prior_close": row.get("prior_close"), "market_cap": row.get("market_cap"),
            "sector": row.get("sector"),
            "score": score, "signal": signal, "params": params,
            "coverage": round(coverage * 100, 1), "agreement": round(agreement * 100, 1),
            "low_confidence": low_conf}


def _eq_cached(symbol: str) -> Optional[dict]:
    """Equity signals for the board. Reuse the shared earn:equity cache (already
    warmed for the big liquid names) so the board is fast; compute+cache only for
    a name that has none yet."""
    import swr
    v = swr.peek(f"earn:equity:{symbol}")
    if v and v.get("params") and "signals" in v:
        return v
    import earnings_equity_service as eq_svc
    return swr.serve(f"earn:equity:{symbol}", lambda: eq_svc.get_analysis(symbol), _ts.EARN_CACHE_TTL)


def _price_atr(symbol: str):
    """(price, atr14_daily) for entry/stop/target, lightly cached."""
    import swr
    import unusualwhales_service as uw
    import intraday_indicators as ind

    def _px():
        q = uw.get_quote(symbol) or {}
        return cl._f(q.get("price")) or cl._f(q.get("close"))

    def _atr():
        return ind.atr14_daily(uw.get_daily_bars(symbol, "3 M") or [])

    price = swr.serve(f"grokintra:px:{symbol}", _px, 120.0)
    atr = swr.serve(f"grokintra:atr:{symbol}", _atr, 6 * 3600.0)
    return price, atr


def _levels(r: dict) -> None:
    """Attach entry / now / tp / sl to a row (entry = price; +-0.5 ATR stop,
    +-1.0 ATR target, 2:1), matching the Trdgo Stock board columns."""
    try:
        price, atr = _price_atr(r["symbol"])
    except Exception:  # noqa: BLE001
        price = atr = None
    if price is None:
        return
    r["entry"] = round(price, 2)
    r["now"] = round(price, 2)
    if atr and r["signal"] in ("BUY", "SELL"):
        sgn = 1 if r["signal"] == "BUY" else -1
        r["sl"] = round(price - sgn * 0.5 * atr, 2)
        r["tp"] = round(price + sgn * 1.0 * atr, 2)


def board() -> dict:
    """Live board over the liquid universe (same ~38 names as Trdgo Stock), scored
    with the Grok 10-signal model. Top Buyers / Top Sellers / Neutral by score.
    Scored in parallel; per-name signals cached 10 min."""
    import ai_trade_service as ats
    from concurrent.futures import ThreadPoolExecutor
    syms = [s.upper() for s in getattr(ats, "UNIVERSE", [])]

    def _one(s: str):
        try:
            eq = _eq_cached(s)
            if eq and eq.get("params"):
                r = score_one(s, eq, None, {"symbol": s})
                _levels(r)
                return r
        except Exception:  # noqa: BLE001
            return None
        return None

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for r in ex.map(_one, syms):
            if r:
                rows.append(r)
    buyers = sorted([r for r in rows if r["signal"] == "BUY"], key=lambda r: r["score"], reverse=True)
    sellers = sorted([r for r in rows if r["signal"] == "SELL"], key=lambda r: r["score"])
    neutral = [r for r in rows if r["signal"] == "NEUTRAL"]
    try:
        import intraday_indicators as ind
        phase = ind.market_phase()
    except Exception:  # noqa: BLE001
        phase = None
    return {"status": "OK", "source": SOURCE, "phase": phase,
            "buyers": buyers, "sellers": sellers, "neutral": neutral,
            "counts": {"buy": len(buyers), "sell": len(sellers), "neutral": len(neutral)},
            "universe": len(syms), "scored": len(rows)}


def scan(days: int = 15) -> dict:
    """Score every name reporting in the next ~`days`, ranked by score."""
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
    try:
        import finviz_service as fv
        fmap = fv.earnings_map()
    except Exception:  # noqa: BLE001
        fmap = {}

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
        eq = eqs.get(f"earn:equity:{s}")
        res = None
        if eq and eq.get("params"):
            try:
                res = score_one(s, eq, None, r)
                scored += 1
            except Exception:  # noqa: BLE001
                res = None
        if res is None:
            missing.append(s)
            res = {"symbol": s, "company": r.get("company"), "date": r.get("date"),
                   "date_label": r.get("date_label"), "time_label": r.get("short_label"),
                   "report_timing": r.get("time_label") or r.get("timing_label"),
                   "days_to": _days_to(r), "prior_close": r.get("prior_close"),
                   "market_cap": r.get("market_cap"), "sector": r.get("sector"),
                   "score": None, "signal": "PENDING", "params": [],
                   "coverage": None, "agreement": None, "low_confidence": None}
        fvi = fmap.get(s)
        if fvi and fvi.get("time") and str(fvi.get("date"))[:10] == str(res.get("date"))[:10]:
            res["report_clock"] = fvi["time"]
        out.append(res)
    out.sort(key=lambda x: (x["score"] if x["score"] is not None else -1), reverse=True)
    return {"status": "OK", "source": SOURCE, "rows": out,
            "universe": len(seen), "scored": scored, "missing": missing[:60]}
