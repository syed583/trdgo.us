"""Claude Pro -- refined pre-earnings DIRECTION model (critique-driven).

Changes vs the earlier flat models:
  * Correlated positioning signals (options flow, skew, C/P disparity, dark pool)
    are MERGED into one 20-pt Positioning block so they are not triple-counted.
  * The three overlapping technicals (price structure, EMA, run-up) are merged
    into one Trend/Run-up block.
  * MAGNITUDE is pulled out of the direction score into a separate `move_size`
    field (a correct direction can still lose to IV crush).
  * More weight on expectations/fundamentals (40); dark pool cut to 3.

Six blocks (sum 100), each the sum of its weighted sub-signals, so the detail
breakdown shows the sub-signals inside each block. All-positive 5-step scale,
BUY>=55 / NEUTRAL / SELL<=45. Inputs from the warmed equity signals + trade
cache; estimate dispersion is Finnhub (detail-only).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import tab_settings as cfg
import universe_service
import claude_last_service as cl
import meta_service as mt   # _implied_vs_hist_bias
import grok_service as gk   # _analyst_bias (dispersion, Finnhub)
import kimi_service as ki   # market_bias (SPY + VIX)

SOURCE = "Claude Pro — Refined Direction (6 blocks)"

_SUBLABEL = {
    "eps_revisions": "EPS revisions", "revenue_revisions": "Revenue revisions",
    "guidance": "Guidance (beat-and-raise)", "estimate_dispersion": "Estimate dispersion",
    "implied_vs_hist": "Implied vs historical", "options_flow": "Options flow",
    "unusual": "Unusual activity", "skew_disparity": "Skew / disparity",
    "dark_pool": "Dark pool", "price_structure": "Price structure", "ema_trend": "EMA trend",
    "runup": "Run-up into print", "hist_reaction": "Historical reaction", "peers": "Peer read-through",
    "short_interest": "Short interest", "insider": "Insider buys", "spy_vix": "SPY + VIX regime",
}


def _runup_bias(spark) -> Optional[float]:
    """10-session drift into the print. NOTE: the critique suggests a large run-up
    may deserve a NEGATIVE score (bar is higher) -- left positive pending a
    backtest; flip the sign here if testing says so."""
    closes = [c for c in (cl._f(c) for c in (spark or [])) if c is not None]
    if len(closes) < 11:
        return None
    return cl._clamp((closes[-1] - closes[-11]) / closes[-11] / 0.06)


def _move_size(trade: dict, sig: dict) -> dict:
    """Separate MAGNITUDE read (not direction): expected move % and whether it is
    rich vs the stock's historical move. High = big move priced in (IV-crush risk
    for long options)."""
    em = (trade or {}).get("expected_move") or {}
    im_pct = cl._f(em.get("percent"))
    mags = [abs(x) for x in (cl._f(m.get("move_pct")) for m in (trade or {}).get("historical_moves") or [])
            if x is not None]
    hist = (sum(mags) / len(mags)) if mags else None
    ratio = (im_pct / hist) if (im_pct and hist) else None
    size = min(100, round((im_pct or 0) * 10)) if im_pct is not None else None  # 10% -> 100
    return {"move_size": size, "implied_move_pct": round(im_pct, 2) if im_pct else None,
            "hist_move_pct": round(hist, 2) if hist else None,
            "im_ratio": round(ratio, 2) if ratio else None}


def score_one(symbol: str, eq: dict, trade: Optional[dict] = None,
              row: Optional[dict] = None, with_analyst: bool = True,
              mkt: Optional[float] = "__compute__") -> dict:
    biases = {p.get("name"): p.get("bias") for p in (eq.get("params") or [])}
    sig = eq.get("signals") or {}
    trade = trade or {}
    row = row or {}
    if mkt == "__compute__":
        mkt = ki.market_bias()

    pa = sig.get("price_action", biases.get("price_action"))
    raw: dict[str, Optional[float]] = {
        "eps_revisions": biases.get("eps_estimates"),
        "revenue_revisions": biases.get("revenue_estimates"),
        "guidance": biases.get("guidance"),
        "estimate_dispersion": (gk._analyst_bias(symbol) if with_analyst else None),
        "implied_vs_hist": mt._implied_vs_hist_bias(trade),
        "options_flow": sig.get("options_flow", biases.get("options_positioning")),
        "unusual": sig.get("unusual_activity", biases.get("unusual_options")),
        "skew_disparity": sig.get("disparity", biases.get("disparity")),
        "dark_pool": biases.get("dark_pool"),
        "price_structure": pa,
        "ema_trend": sig.get("ema_trend"),
        "runup": _runup_bias(trade.get("spark")),
        "hist_reaction": cl._hist_reaction_bias(trade.get("historical_moves")),
        "peers": biases.get("peer_readthrough"),
        "short_interest": cl._short_interest_bias(eq.get("short_interest"), pa),
        "insider": sig.get("insider_activity", biases.get("short_insider")),
        "spy_vix": mkt,
    }

    params: list[dict] = []
    all_subs: list[dict] = []
    for bname, bcfg in cfg.CLAUDE_PRO_BLOCKS.items():
        subs = []
        for sname, sweight in bcfg["subs"].items():
            qb = cl._quant(raw.get(sname))
            subs.append({"name": _SUBLABEL.get(sname, sname), "max": sweight,
                         "points": round(cl._pts(sweight, qb), 2), "bias": qb})
        all_subs.extend(subs)
        params.append({"name": bname, "label": bcfg["label"], "sub": subs})

    # Missing sub-parameters are excluded and the rest rescaled to 100 (no neutral
    # default); blocks are then summarised from their present subs.
    score, cov_pct = cl.finalize(all_subs, None, row)
    for p in params:
        subs = p["sub"]
        present = [s_ for s_ in subs if s_["bias"] is not None]
        p["max"] = round(sum(s_["max"] for s_ in present), 2)
        p["base_max"] = round(sum(s_["base_max"] for s_ in subs), 2)
        p["points"] = round(sum(s_["points"] for s_ in present), 2) if present else None
        p["excluded"] = not present
        p["bias"] = (round(sum(s_["bias"] for s_ in present) / len(present), 3)
                     if present else None)

    cut = cfg.V3_CUTOFFS
    signal = "BUY" if score >= cut["buy"] else "SELL" if score <= cut["sell"] else "NEUTRAL"

    tier = None
    if cl.low_data(cov_pct):           # below the minimum data coverage: no BUY/SELL
        signal, tier = "LOW_DATA", "Low data"
    covered = sum(p["max"] for p in params if p["bias"] is not None)
    pos_w = sum(p["max"] for p in params if (p["bias"] or 0) > 0.05)
    neg_w = sum(p["max"] for p in params if (p["bias"] or 0) < -0.05)
    coverage = round(cov_pct / 100.0, 3)
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

    ms = _move_size(trade, sig)
    return {"symbol": symbol, "company": row.get("company"),
            "date": d, "date_label": row.get("date_label"),
            "time_label": row.get("short_label"), "days_to": days_to,
            "report_timing": row.get("time_label") or row.get("timing_label"),
            "prior_close": row.get("prior_close"), "market_cap": row.get("market_cap"),
            "sector": row.get("sector"),
            "score": score, "signal": signal, "tier": tier, "params": params,
            "coverage": round(coverage * 100, 1), "agreement": round(agreement * 100, 1),
            "low_confidence": low_conf, **ms}


def scan(days: int = 15) -> dict:
    """Score every name reporting in the next ~`days`, ranked by score."""
    import swr
    import uw_earnings_calendar as calmod

    rows = (calmod.calendar(days=days) or {}).get("rows") or []
    rows = universe_service.restrict(rows)      # top-1000 liquid names only
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
                res = score_one(s, eq, trades.get(f"earn:trade:default:{s}"), r,
                                with_analyst=False, mkt=mkt)
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
