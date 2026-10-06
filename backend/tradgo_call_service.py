"""
Tradgo Call -- the short-term direction model (a few days to 3-4 weeks).

A re-weighted 100-point reading of the general directional signals, plus three
signals the base model lacks: Relative Strength vs SPY, Volume Confirmation and
Market Regime. The score runs -100..+100 (all 100 points vote on direction;
volatility is reported for sizing only and casts no vote).

Weights follow the revised short-term spec:
  Market & Price 80  (Options Tape 44, Trend & Price Action 28, Volume 4, Regime 4)
  Company & Ownership 20
"""

from __future__ import annotations

from typing import Optional

SOURCE = "Tradgo Call model"


def _f(v) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _closes(symbol: str, n: int) -> list[float]:
    import unusualwhales_service as uw
    bars = [b for b in uw._rows(uw.candles(symbol, "1d", limit=n + 10))
            if b.get("close") not in (None, "")]
    bars.sort(key=lambda b: b.get("date") or "")
    return [float(b["close"]) for b in bars]


def _bars(symbol: str, n: int) -> list[dict]:
    import unusualwhales_service as uw
    bars = [b for b in uw._rows(uw.candles(symbol, "1d", limit=n + 10))
            if b.get("close") not in (None, "") and b.get("volume") not in (None, "")]
    bars.sort(key=lambda b: b.get("date") or "")
    return bars


def _vix_level() -> Optional[float]:
    """Spot VIX if the feed carries it, else the VIXY ETF close as a proxy."""
    try:
        import live_market_service as market
        idx = market.get_indices() or {}
        vix = next((i for i in (idx.get("indices") or []) if i.get("label") == "VIX"), None)
        v = _f((vix or {}).get("value") or (vix or {}).get("price"))
        if v is not None:
            return v
    except Exception:  # noqa: BLE001
        pass
    try:
        c = _closes("VIXY", 3)
        return c[-1] if c else None
    except Exception:  # noqa: BLE001
        return None


def _rel_strength(symbol: str) -> tuple[Optional[float], str]:
    """20-day return of the stock minus SPY's -- leadership vs the market."""
    def ret(sym: str) -> Optional[float]:
        c = _closes(sym, 21)
        return None if len(c) < 21 else (c[-1] - c[-21]) / c[-21]
    rs, rspy = ret(symbol), ret("SPY")
    if rs is None or rspy is None:
        return None, "No 20-day price history for the stock or SPY."
    diff = rs - rspy
    return (_clamp(diff / 0.10),
            f"20-day return {rs * 100:+.1f}% vs SPY {rspy * 100:+.1f}% ({diff * 100:+.1f}% rel).")


def _volume_confirmation(symbol: str) -> tuple[Optional[float], str]:
    """Recent price move weighted by whether it came on above-average volume."""
    bars = _bars(symbol, 30)
    if len(bars) < 21:
        return None, "Fewer than ~21 sessions of volume history."
    closes = [float(b["close"]) for b in bars]
    vols = [float(b["volume"]) for b in bars]
    chg = (closes[-1] - closes[-6]) / closes[-6] if closes[-6] else 0.0   # 5-day move
    avg = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else None
    ratio = (vols[-1] / avg) if avg else 1.0
    sign = 1.0 if chg > 0 else -1.0 if chg < 0 else 0.0
    bias = sign * min(abs(chg) / 0.03, 1.0) * min(max(ratio - 1.0, 0.0), 1.0)
    return (_clamp(bias),
            f"5-day move {chg * 100:+.1f}% on {ratio:.1f}x average volume.")


def _market_regime() -> tuple[Optional[float], str]:
    """SPY vs its 50- and 200-day averages, dampened by the VIX level."""
    c = _closes("SPY", 210)
    if len(c) < 200:
        return None, "Not enough SPY history for the 200-day average."
    spy, ma50, ma200 = c[-1], sum(c[-50:]) / 50, sum(c[-200:]) / 200
    score = (0.5 if spy > ma50 else -0.5) + (0.5 if spy > ma200 else -0.5)
    vix = _vix_level()
    vtxt = ""
    if vix is not None:
        if vix > 25:
            score -= 0.4
        elif vix < 15:
            score += 0.2
        vtxt = f", VIX {vix:.1f}"
    above = "above" if spy > ma50 else "below"
    return _clamp(score), f"SPY {above} its 50/200-day averages{vtxt}."


# Revised weights. (name, label, weight). Volatility is sizing-only: weight 0.
_MARKET = [
    ("options_flow", "Options Flow", 14),
    ("unusual_activity", "Unusual Activity", 7),
    ("disparity", "Options Disparity", 5),
    ("volume_pcr", "Volume & Put/Call Ratio", 7),
    ("key_levels", "Key Option Levels", 6),
    ("daily_oi_change", "Daily OI Change", 5),
    ("ema_trend", "EMA / Trend", 11),
    ("rsi", "RSI (14)", 5),
    ("price_action", "Price Action", 5),
    ("relative_strength", "Relative Strength vs SPY & Sector", 7),
    ("volume_confirmation", "Volume Confirmation", 4),
    ("market_regime", "Market Regime", 4),
]
_COMPANY = [
    ("insider_activity", "Insider & Ownership", 10),
    ("fund_flows", "Fund Buying & Selling", 3),
    ("earnings_results", "Earnings Results", 4),
    ("merger_activity", "Mergers & Deals", 1),
    ("funding_activity", "Funding & Debt", 1),
    ("event_radar", "Analyst Rating Changes", 1),
]
# Options Flow absorbs these overlapping readings from the base model.
_FLOW_ABSORB = ("options_flow", "flow_by_expiry", "oi_positioning")


def get_call(symbol: str) -> dict:
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"status": "INVALID_SYMBOL", "symbol": symbol, "source": SOURCE}

    # Base directional signals (cached) give us the per-signal biases + details.
    try:
        import directional_score_service as dss
        base = dss.get_directional_score(symbol)
    except Exception:  # noqa: BLE001
        base = {}
    sigs = {s.get("name"): s for s in (base.get("signals") or [])}

    def bias_of(name: str) -> Optional[float]:
        s = sigs.get(name) or {}
        return _f(s.get("bias")) if s.get("available") else None

    def detail_of(name: str) -> str:
        return (sigs.get(name) or {}).get("detail") or ""

    # Options Flow absorbs flow-by-expiry and OI positioning.
    def flow_bias() -> Optional[float]:
        vals = [b for b in (bias_of(n) for n in _FLOW_ABSORB) if b is not None]
        return None if not vals else _clamp(sum(vals) / len(vals))

    # The three new signals, computed once.
    rs_b, rs_d = _rel_strength(symbol)
    vc_b, vc_d = _volume_confirmation(symbol)
    mr_b, mr_d = _market_regime()
    extra = {
        "relative_strength": (rs_b, rs_d),
        "volume_confirmation": (vc_b, vc_d),
        "market_regime": (mr_b, mr_d),
    }

    def build(rows: list) -> list[dict]:
        out = []
        for name, label, weight in rows:
            if name == "options_flow":
                b, d = flow_bias(), detail_of("options_flow")
            elif name in extra:
                b, d = extra[name]
            else:
                b, d = bias_of(name), detail_of(name)
            pts = round((b or 0.0) * weight, 2) if b is not None else 0.0
            out.append({
                "name": name, "label": label, "weight": weight,
                "available": b is not None,
                "bias": None if b is None else round(b, 3),
                "points": pts,
                "points_label": (f"{pts:+.1f} of {weight}" if b is not None else "no data"),
                "leaning": (None if b is None else
                            "Bullish" if b > 0.15 else "Bearish" if b < -0.15 else "Neutral"),
                "detail": d,
            })
        return out

    market_params = build(_MARKET)
    company_params = build(_COMPANY)
    params = market_params + company_params

    present = sum(p["weight"] for p in params if p["available"])
    possible = sum(p["weight"] for p in params) or 1
    coverage = round(present / possible * 100, 1)
    score = round(sum(p["points"] for p in params), 1)   # -100..+100

    # Direction from the signed score, with a dead-band for Neutral.
    if present == 0:
        decision = "NO DATA"
    elif score >= 15:
        decision = "BUY"
    elif score <= -15:
        decision = "SELL"
    else:
        decision = "NEUTRAL"

    # Volatility / expected move: sizing only, reported but not scored.
    vol = {
        "implied_volatility": (sigs.get("implied_volatility") or {}).get("detail"),
        "expected_move": (sigs.get("expected_move") or {}).get("detail"),
    }

    # Earnings gate: flag if a report falls in a typical short-term hold.
    earnings_gate = None
    try:
        import uw_earnings_calendar as uwcal
        pv = uwcal.preview(symbol) or {}
        from datetime import date, datetime
        nd = pv.get("next_report")
        if nd:
            days = (datetime.fromisoformat(nd).date() - date.today()).days
            if 0 <= days <= 21:
                earnings_gate = {
                    "days_to": days, "date": nd, "label": pv.get("next_report_label"),
                    "note": "Earnings fall inside a typical hold -- use the "
                            "pre-earnings model and size for the event.",
                }
    except Exception:  # noqa: BLE001
        pass

    return {
        "status": "OK" if present else "NO_DATA",
        "symbol": symbol, "source": SOURCE, "model": "TRADGO_CALL",
        "score": score, "decision": decision,
        "coverage_pct": coverage, "present": present, "possible": possible,
        "market_params": market_params,
        "company_params": company_params,
        "volatility": vol,
        "earnings_gate": earnings_gate,
        "note": ("Short-term swing model (days to ~3-4 weeks). All 100 points "
                 "vote on direction; volatility is for sizing only. Starting "
                 "weights -- calibrate against realised 5/10/20-day returns. "
                 "Research and decision-support, not investment advice."),
    }
