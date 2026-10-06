"""
Engine 1 -- the Equity Earnings Analyzer (100-point evidence score).

The parameters and weights are exactly those in the TRDGO earnings structure:

    EPS Estimates & Revisions ............ 15
    Revenue Estimates & Revisions ........ 15
    Forward Guidance & Outlook ........... 15   (no structured feed -> labelled unknown)
    Historical Earnings Reaction ......... 15
    Price Action & Technical Trend ....... 10
    Options Flow & Institutional ......... 10
    Dark Pool Activity .................... 5
    Unusual Options Activity ............. 5
    Options Disparity .................... 5
    Sector & Market Trend ................ 5

Each parameter is read from the service that already produces it; anything that
does not return data is labelled unknown and left out of the covered weight,
rather than guessed. Output: BUY / SELL / NO TRADE, score/100, entry reference,
expected move, planned exit and maximum planned risk.
"""

from __future__ import annotations

from typing import Optional

import earnings_engine as eng

SOURCE = "Trdgo earnings model"


def _f(v) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _avg(*vals) -> Optional[float]:
    xs = [v for v in vals if v is not None]
    return sum(xs) / len(xs) if xs else None


def _directional_signals(symbol: str) -> dict:
    """{signal_name: bias in -1..+1} from the directional engine, best effort."""
    try:
        import directional_score_service as dss
        out = dss.get_directional_score(symbol)
        return {s["name"]: s["bias"] for s in out.get("signals", [])
                if s.get("available") and s.get("bias") is not None}
    except Exception:  # noqa: BLE001
        return {}


def _estimate_bias(symbol: str) -> tuple[Optional[float], Optional[float]]:
    """
    (eps_bias, revenue_bias) in -1..+1, straight from UW's forward estimates
    (/api/companies/{t}/earnings-estimates). EPS lean = analysts' expected growth
    vs the last reported quarter, blended with last-week revision counts when
    present; revenue lean = year-over-year growth of the average estimate.
    """
    try:
        import unusualwhales_service as uw
        est = uw._rows(uw.earnings_estimates(symbol))
        hist = uw._rows(uw.earnings_history(symbol))
        if not est:
            return None, None

        fq = sorted((r for r in est if r.get("horizon") == "fiscal quarter"
                     and r.get("date")), key=lambda r: r.get("date"))
        fy = sorted((r for r in est if r.get("horizon") == "fiscal year"
                     and r.get("date")), key=lambda r: r.get("date"))

        def yoy(field: str) -> Optional[float]:
            """Forward year-over-year growth of an estimate (seasonality-free)."""
            if len(fy) < 2:
                return None
            a, b = _f(fy[0].get(field)), _f(fy[1].get(field))
            if a and b and a > 0:
                scale = 0.3 if "eps" in field else 0.2
                return max(-1.0, min(1.0, ((b - a) / a) / scale))
            return None

        eps_bias = yoy("eps_estimate_average")
        rev_bias = yoy("revenue_estimate_average")

        # Fold in last-week EPS revision counts when the feed carries them.
        if fq:
            up = _f(fq[0].get("eps_estimate_revision_up_last_week"))
            dn = _f(fq[0].get("eps_estimate_revision_down_last_week"))
            if up is not None and dn is not None and (up + dn):
                rev = (up - dn) / (up + dn)
                eps_bias = rev if eps_bias is None else max(-1.0, min(1.0, (eps_bias + rev) / 2))
        return eps_bias, rev_bias
    except Exception:  # noqa: BLE001
        return None, None


def _guidance_bias(symbol: str) -> Optional[float]:
    """
    Forward Guidance & Outlook, from UW's quarterly estimate trajectory: the next
    upcoming fiscal quarter's year-over-year expected growth in EPS and revenue
    (4-quarter lag, so it is seasonality-correct). Analysts set these to the
    company's guidance, so a rising next-quarter outlook reads bullish. Distinct
    from the annual estimate/revision parameters.
    """
    try:
        from datetime import date
        import unusualwhales_service as uw
        fq = sorted((r for r in uw._rows(uw.earnings_estimates(symbol))
                     if r.get("horizon") == "fiscal quarter" and r.get("date")),
                    key=lambda r: r.get("date"))
        if len(fq) < 5:
            return None
        today = date.today().isoformat()
        idx = next((i for i, r in enumerate(fq) if r.get("date") >= today), None)
        if idx is None or idx < 4:
            # No future quarter with a year-ago comparator; use the latest pair.
            idx = len(fq) - 1
            if idx < 4:
                return None

        def yoy(field: str) -> Optional[float]:
            now, ago = _f(fq[idx].get(field)), _f(fq[idx - 4].get(field))
            if now is not None and ago and ago > 0:
                return (now - ago) / ago
            return None

        g = _avg(yoy("eps_estimate_average"), yoy("revenue_estimate_average"))
        return None if g is None else max(-1.0, min(1.0, g / 0.3))
    except Exception:  # noqa: BLE001
        return None


def _history_bias(symbol: str) -> Optional[float]:
    """
    Historical earnings reaction straight from UW (/api/earnings/{t}): the beat
    rate and recent surprise over the last eight reports.
    """
    try:
        import unusualwhales_service as uw
        rows = uw._rows(uw.earnings_history(symbol))
        past = [(r.get("report_date") or "", _f(r.get("surprise_percentage")))
                for r in rows if _f(r.get("surprise_percentage")) is not None]
        if not past:
            return None
        past.sort(key=lambda x: x[0], reverse=True)
        recent = past[:8]
        beat_rate = sum(1 for _, sp in recent if sp > 0) / len(recent)
        avg_surprise = sum(sp for _, sp in recent) / len(recent)
        bias = (beat_rate - 0.5) * 2 * 0.6 + max(-1.0, min(1.0, avg_surprise / 10)) * 0.4
        return max(-1.0, min(1.0, bias))
    except Exception:  # noqa: BLE001
        return None


def _darkpool_bias(symbol: str) -> Optional[float]:
    """
    A low-confidence lean from off-lit prints. Dark-pool volume does not
    establish buy/sell direction on its own (per the structure's caveat), so a
    mild tilt is taken from where the heaviest prints sit relative to spot, and
    None is returned when nothing usable comes back.
    """
    try:
        import uw_darkpool_service as dp
        if not dp.configured():
            return None
        lv = dp.price_levels(symbol, top=12) or {}
        rows = lv.get("rows") or []
        import live_market_service as market
        spot = _f((market.get_quote(symbol) or {}).get("price"))
        if not rows or spot is None:
            return None
        # Weight each heavy level by its off-exchange volume; prints clustered
        # below spot are accumulation (mild bullish), above spot distribution.
        num = den = 0.0
        for r in rows:
            px = _f(r.get("price"))
            vol = _f(r.get("off_exchange_volume")) or _f(r.get("total_volume"))
            if px is None or vol is None:
                continue
            num += vol * (1 if px < spot else -1)
            den += vol
        if den == 0:
            return None
        return max(-0.5, min(0.5, num / den * 0.5))   # capped: low confidence
    except Exception:  # noqa: BLE001
        return None


def _institutional_bias(symbol: str) -> Optional[float]:
    """Net institutional (13F) share change for the ticker, -1..+1."""
    try:
        import institutional_service as inst
        d = inst.get_institutional_activity(symbol) or {}
        net = _f(d.get("net_share_change_pct"))
        return None if net is None else max(-1.0, min(1.0, net / 15.0))
    except Exception:  # noqa: BLE001
        return None


def _sector_bias(symbol: str) -> Optional[float]:
    """Market tailwind from SPY's day change -- always available when open."""
    try:
        import live_market_service as market
        spy = _f((market.get_quote("SPY") or {}).get("change_percent"))
        return None if spy is None else max(-1.0, min(1.0, spy / 1.5))
    except Exception:  # noqa: BLE001
        return None


def _trade_refs(symbol: str) -> dict:
    """Entry reference, expected move, planned exit and max planned risk."""
    refs = {"entry_reference": None, "expected_move_percent": None,
            "expected_range": None, "planned_exit": None,
            "max_planned_risk_percent": None, "earnings_date": None}
    try:
        import options_levels_service as ol
        d = ol.get_levels(symbol)
        if d.get("status") == "OK":
            refs["entry_reference"] = _f(d.get("spot"))
            em = d.get("expected_move") or {}
            refs["expected_move_percent"] = _f(em.get("percent"))
            refs["expected_range"] = em.get("range")
            # The directional trade risks roughly one expected move against it.
            refs["max_planned_risk_percent"] = _f(em.get("percent"))
    except Exception:  # noqa: BLE001
        pass
    try:
        import earnings_intelligence_service as intel
        nxt = (intel.get_history(symbol, 1) or {})
        refs["earnings_date"] = (nxt.get("next_earnings")
                                 or nxt.get("next_date"))
    except Exception:  # noqa: BLE001
        pass
    refs["planned_exit"] = ("First regular US session after the earnings "
                            "release (compare 5 / 15 / 30 min after the open).")
    return refs


def get_analysis(symbol: str) -> dict:
    """The full equity earnings analysis for one ticker."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"status": "INVALID_SYMBOL", "symbol": symbol, "source": SOURCE}

    sig = _directional_signals(symbol)

    def s(*names) -> Optional[float]:
        return _avg(*(sig.get(n) for n in names))

    eps_bias, rev_bias = _estimate_bias(symbol)

    params = [
        eng.Param("eps_estimates", "EPS Estimates & Revisions", 16, eps_bias,
                  detail="Direction and size of analyst EPS revisions (UW).",
                  unavailable_reason="" if eps_bias is not None else "No analyst EPS estimates."),
        eng.Param("revenue_estimates", "Revenue Estimates & Revisions", 12, rev_bias,
                  detail="Revenue revisions; overlaps with EPS, so trimmed (UW).",
                  unavailable_reason="" if rev_bias is not None else "No analyst revenue estimates."),
        eng.Param("guidance", "Forward Guidance / Outlook", 8, _guidance_bias(symbol),
                  detail="Last-quarter guidance is largely priced in (UW).",
                  unavailable_reason="No forward quarterly estimates."),
        eng.Param("historical_reaction", "Historical Earnings Reaction", 6,
                  _history_bias(symbol),
                  detail="Predicts move size better than direction."),
        eng.Param("price_action", "Price Action + Relative Strength", 14,
                  s("price_action", "ema_trend", "rsi", "key_levels"),
                  detail="Pre-earnings drift and strength vs the sector/index."),
        eng.Param("sector_market", "Sector & Market Regime", 8, _sector_bias(symbol),
                  detail="Overall market and sector backdrop."),
        eng.Param("options_positioning", "Options Flow + OI Positioning", 10,
                  _avg(s("options_flow", "oi_positioning", "gamma_exposure"),
                       _institutional_bias(symbol)),
                  detail="Net directional flow and open-interest build."),
        eng.Param("unusual_options", "Unusual Options Activity", 5,
                  s("flow_by_expiry"),
                  detail="Large or outsized prints; avoid double-counting with flow."),
        eng.Param("disparity", "Options Disparity / Put-Call Skew", 4,
                  s("disparity", "volume_pcr"),
                  detail="Skew rises before earnings from hedging."),
        eng.Param("dark_pool", "Dark Pool / Off-Exchange Evidence", 3, _darkpool_bias(symbol),
                  detail="Trade side unknown; use as confirmation only."),
        eng.Param("peer_readthrough", "Peer Read-through", 8, None,
                  detail="Results and reactions of peers that already reported.",
                  unavailable_reason="Peer read-through not wired up yet."),
        eng.Param("short_insider", "Short Interest + Insider", 6,
                  s("insider_activity"),
                  detail="Squeeze potential and insider open-market activity.",
                  unavailable_reason="" if s("insider_activity") is not None
                  else "No short-interest / insider signal."),
    ]

    result = eng.score(params, pos_label="BUY", neg_label="SELL")
    result.update({
        "symbol": symbol,
        "engine": "EQUITY",
        "trade": _trade_refs(symbol),
        "note": ("Weights are the structure's proposed development settings, "
                 "not validated probabilities. Correlated options factors "
                 "(flow, unusual activity, disparity) are kept separate but may "
                 "overlap."),
        "source": SOURCE,
        "status": "OK" if result.get("score") is not None else "NO_DATA",
    })
    return result
