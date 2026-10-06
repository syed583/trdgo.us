"""
Tradgo Call -- the 15-parameter directional model (short-term, a few days to
~3-4 weeks). Each parameter scores -1..+1 and is multiplied by its weight; the
signed points sum to the direction score. UNKNOWN data is excluded from the sum
(it lowers coverage/confidence) rather than silently counting as neutral.

Data: Unusual Whales (options tape, OI, dark pool, candles, short interest,
SEC/corporate events) + Finnhub (analyst recommendations, peers, insider
sentiment, earnings/IPO calendars, news) + our computed signals (relative
strength, abnormal volume / A-D, market regime).
"""

from __future__ import annotations

from typing import Optional

SOURCE = "Tradgo Call v2"


def _f(v) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _avg(*vals) -> Optional[float]:
    xs = [v for v in vals if v is not None]
    return None if not xs else sum(xs) / len(xs)


def _closes(symbol: str, n: int) -> list[float]:
    import unusualwhales_service as uw
    bars = [b for b in uw._rows(uw.candles(symbol, "1d", limit=n + 10))
            if b.get("close") not in (None, "")]
    bars.sort(key=lambda b: b.get("date") or "")
    return [float(b["close"]) for b in bars]


def _ohlcv(symbol: str, n: int) -> list[dict]:
    import unusualwhales_service as uw
    bars = [b for b in uw._rows(uw.candles(symbol, "1d", limit=n + 10))
            if b.get("close") not in (None, "") and b.get("volume") not in (None, "")]
    bars.sort(key=lambda b: b.get("date") or "")
    return bars


def _vix_level() -> Optional[float]:
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


def _ret(symbol: str, n: int = 20) -> Optional[float]:
    c = _closes(symbol, n + 1)
    return None if len(c) < n + 1 else (c[-1] - c[-n - 1]) / c[-n - 1]


# -------------------------------------------------------------- param biases
def _b_price_levels(sig) -> tuple[Optional[float], str]:
    b = _avg(*(sig(n) for n in ("price_action", "ema_trend", "rsi", "key_levels")))
    return (None if b is None else _clamp(b),
            "Trend, moving averages, RSI and option walls.")


def _b_abnormal_volume(symbol: str) -> tuple[Optional[float], str]:
    bars = _ohlcv(symbol, 40)
    if len(bars) < 21:
        return None, "Not enough volume history."
    close = [float(b["close"]) for b in bars]
    vol = [float(b["volume"]) for b in bars]
    high = [float(b.get("high") or b["close"]) for b in bars]
    low = [float(b.get("low") or b["close"]) for b in bars]
    chg5 = (close[-1] - close[-6]) / close[-6] if close[-6] else 0.0
    avgv = sum(vol[-21:-1]) / 20 if len(vol) >= 21 else None
    ratio = (vol[-1] / avgv) if avgv else 1.0
    # Accumulation/Distribution: money-flow multiplier * volume, last 10 days slope.
    ad = 0.0
    ads = []
    for i in range(max(0, len(bars) - 15), len(bars)):
        rng = high[i] - low[i]
        mfm = ((close[i] - low[i]) - (high[i] - close[i])) / rng if rng else 0.0
        ad += mfm * vol[i]
        ads.append(ad)
    ad_dir = 0.0
    if len(ads) >= 6 and (max(ads) - min(ads)):
        ad_dir = (ads[-1] - ads[-6]) / (max(ads) - min(ads))
    sign = 1.0 if chg5 > 0 else -1.0 if chg5 < 0 else 0.0
    volpart = sign * min(abs(chg5) / 0.03, 1.0) * min(max(ratio - 1.0, 0.0), 1.0)
    bias = _avg(volpart, _clamp(ad_dir))
    return (None if bias is None else _clamp(bias),
            f"5-day move {chg5 * 100:+.1f}% on {ratio:.1f}x volume; A/D "
            f"{'rising' if ad_dir > 0.1 else 'falling' if ad_dir < -0.1 else 'flat'}.")


def _b_rel_strength(symbol: str) -> tuple[Optional[float], str]:
    rs, rspy = _ret(symbol, 20), _ret("SPY", 20)
    if rs is None or rspy is None:
        return None, "No 20-day history for the stock or SPY."
    diff = rs - rspy
    peer_txt = ""
    try:
        import finnhub_service as fh
        peers = fh.peers(symbol)[:4]
        prets = [p for p in (_ret(x, 20) for x in peers) if p is not None]
        if prets:
            pavg = sum(prets) / len(prets)
            diff = (diff + (rs - pavg)) / 2
            peer_txt = f", {sum(1 for p in prets if rs > p)}/{len(prets)} peers beaten"
    except Exception:  # noqa: BLE001
        pass
    return (_clamp(diff / 0.10),
            f"20-day {rs * 100:+.1f}% vs SPY {rspy * 100:+.1f}%{peer_txt}.")


def _b_market_regime() -> tuple[Optional[float], str]:
    c = _closes("SPY", 210)
    if len(c) < 200:
        return None, "Not enough SPY history."
    spy, ma50, ma200 = c[-1], sum(c[-50:]) / 50, sum(c[-200:]) / 200
    score = (0.5 if spy > ma50 else -0.5) + (0.5 if spy > ma200 else -0.5)
    vix = _vix_level()
    vt = ""
    if vix is not None:
        score += -0.4 if vix > 25 else 0.2 if vix < 15 else 0.0
        vt = f", VIX {vix:.1f}"
    return _clamp(score), f"SPY {'above' if spy > ma50 else 'below'} 50/200-day{vt}."


def _b_analyst(symbol: str) -> tuple[Optional[float], str]:
    try:
        import finnhub_service as fh
        r = fh.recommendation(symbol)
    except Exception:  # noqa: BLE001
        r = None
    if not r:
        return None, "No analyst recommendation trend."
    sb, b, h, s, ss = (r.get("strongBuy", 0), r.get("buy", 0), r.get("hold", 0),
                       r.get("sell", 0), r.get("strongSell", 0))
    tot = sb + b + h + s + ss
    if not tot:
        return None, "No analyst ratings."
    net = (sb + 0.5 * b - 0.5 * s - ss) / tot
    return _clamp(net * 1.6), f"{sb} strong-buy / {b} buy / {h} hold / {s} sell / {ss} strong-sell."


def _b_insider(symbol: str, sig) -> tuple[Optional[float], str]:
    parts, txt = [], []
    try:
        import finnhub_service as fh
        isent = fh.insider_sentiment(symbol)
        if isent and isent.get("mspr") is not None:
            parts.append(_clamp(float(isent["mspr"]) / 50.0))
            txt.append(f"insider MSPR {float(isent['mspr']):+.0f}")
    except Exception:  # noqa: BLE001
        pass
    f13 = sig("fund_flows")
    if f13 is not None:
        parts.append(f13)
        txt.append("13F flow")
    b = _avg(*parts)
    return (None if b is None else _clamp(b),
            "; ".join(txt) if txt else "No insider / 13F signal.")


def _b_short_squeeze(symbol: str) -> tuple[Optional[float], str]:
    try:
        import uw_screener_service as scr
        si = scr.short_interest(symbol)
    except Exception:  # noqa: BLE001
        si = {}
    if (si or {}).get("status") != "OK":
        return None, "No short-interest reading."
    dtc = _f(si.get("days_to_cover"))
    pof = _f(si.get("short_percent_of_float"))
    sqz = []
    if dtc is not None:
        sqz.append(min(1.0, dtc / 10.0))
    if pof is not None:
        p = pof / 100.0 if pof > 1 else pof
        sqz.append(min(1.0, p / 0.20))
    if not sqz:
        return None, "No short-interest detail."
    return _clamp(0.5 * (sum(sqz) / len(sqz))), (
        f"Days-to-cover {dtc if dtc is not None else '--'}, "
        f"{(pof if pof and pof <= 1 else (pof or 0) ):.0f}% of float short.")


def _b_news_accel(symbol: str) -> tuple[Optional[float], str]:
    try:
        import uw_news_adapter as news
        snt = news.get_sentiment(symbol) or {}
        pos, neg = _f(snt.get("positive")), _f(snt.get("negative"))
        if pos is not None and neg is not None and (pos + neg):
            return _clamp((pos - neg) / (pos + neg)), (
                f"{int(pos)} positive vs {int(neg)} negative scored headlines.")
    except Exception:  # noqa: BLE001
        pass
    # Fallback: Finnhub company-news count (intensity only, direction unknown).
    try:
        import finnhub_service as fh
        n = len(fh.company_news(symbol, 7))
        if n:
            return 0.0, f"{n} headlines in 7 days (tone not scored)."
    except Exception:  # noqa: BLE001
        pass
    return None, "No scored news tone."


def _b_catalyst(symbol: str, sig) -> tuple[Optional[float], str]:
    """A fresh scheduled event within ~7 days (earnings / FDA). Direction from the
    estimate/analyst lean; neutral if present but undirected; None if none near."""
    days, label = None, None
    try:
        import uw_earnings_calendar as uwcal
        from datetime import date, datetime
        pv = uwcal.preview(symbol) or {}
        nd = pv.get("next_report")
        if nd:
            days = (datetime.fromisoformat(nd).date() - date.today()).days
            label = f"earnings {pv.get('next_report_label') or nd}"
    except Exception:  # noqa: BLE001
        pass
    if days is None:
        try:
            import finnhub_service as fh
            ec = fh.earnings_calendar(symbol)
            if ec and ec.get("date"):
                from datetime import date as _d, datetime as _dt
                days = (_dt.fromisoformat(ec["date"]).date() - _d.today()).days
                label = f"earnings {ec['date']}"
        except Exception:  # noqa: BLE001
            pass
    if days is None or not (0 <= days <= 7):
        return None, ("No verified catalyst in the next 7 days."
                      if days is None else f"Next catalyst in {days}d (outside 1-7d).")
    # Direction from analyst lean + estimate revisions.
    dir_b, _ = _b_analyst(symbol)
    try:
        import earnings_equity_service as eq
        eps_b, _rev = eq._estimate_bias(symbol)
        dir_b = _avg(dir_b, eps_b)
    except Exception:  # noqa: BLE001
        pass
    return (_clamp(dir_b) if dir_b is not None else 0.0,
            f"{label} in {days}d" + ("" if dir_b is None else " -- leaning from analysts/estimates."))


def get_call(symbol: str) -> dict:
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"status": "INVALID_SYMBOL", "symbol": symbol, "source": SOURCE}

    try:
        import directional_score_service as dss
        base = dss.get_directional_score(symbol)
    except Exception:  # noqa: BLE001
        base = {}
    sigs = {s.get("name"): s for s in (base.get("signals") or [])}

    def sig(name: str) -> Optional[float]:
        s = sigs.get(name) or {}
        return _f(s.get("bias")) if s.get("available") else None

    def detail(name: str) -> str:
        return (sigs.get(name) or {}).get("detail") or ""

    # Compute each parameter: (bias, detail).
    price_b = _b_price_levels(sig)
    avol_b = _b_abnormal_volume(symbol)
    rs_b = _b_rel_strength(symbol)
    regime_b = _b_market_regime()
    analyst_b = _b_analyst(symbol)
    insider_b = _b_insider(symbol, sig)
    short_b = _b_short_squeeze(symbol)
    news_b = _b_news_accel(symbol)
    catalyst_b = _b_catalyst(symbol, sig)

    options_oi = _avg(sig("options_flow"), sig("daily_oi_change"), sig("oi_positioning"))
    sec_b = _avg(sig("merger_activity"), sig("funding_activity"), sig("management_change"))
    darkpool = None
    try:
        import earnings_equity_service as eq
        darkpool = eq._darkpool_bias(symbol)
    except Exception:  # noqa: BLE001
        darkpool = None
    # Institutional composite: alignment of options + OI + volume + RS + dark pool.
    comp_vals = [v for v in (sig("options_flow"), sig("oi_positioning"),
                             avol_b[0], rs_b[0], darkpool) if v is not None]
    comp_b = (_clamp(sum(comp_vals) / len(comp_vals)) if len(comp_vals) >= 3 else None)
    ma_b = sig("merger_activity")

    SPEC = [
        ("catalyst", "Upcoming Verified Catalyst / Event Radar", 15, catalyst_b[0], catalyst_b[1]),
        ("options_oi", "Options Flow + Fresh OI Positioning", 10, options_oi,
         detail("options_flow") or "Net flow and overnight OI build."),
        ("sec_filings", "SEC Filings + Material Corporate Changes", 9, sec_b,
         "Material 8-K/6-K, financing, M&A or management changes."),
        ("price_levels", "Price Action + Key Levels", 9, price_b[0], price_b[1]),
        ("news_accel", "News & Sentiment Acceleration", 8, news_b[0], news_b[1]),
        ("abnormal_volume", "Abnormal Volume / Accumulation-Distribution", 8, avol_b[0], avol_b[1]),
        ("inst_composite", "Institutional Footprint Composite", 8, comp_b,
         f"Alignment across {len(comp_vals)} of 5 institutional signals."),
        ("dark_pool", "Dark Pool / Off-Exchange / Block Footprint", 7,
         None if darkpool is None else _clamp(darkpool), detail("dark_pool") or "Off-lit participation."),
        ("rel_strength", "Relative Strength + Sector / Peer Confirmation", 6, rs_b[0], rs_b[1]),
        ("unusual", "Unusual Options Activity", 5, sig("unusual_activity"),
         detail("unusual_activity") or "Sweeps/blocks above baseline."),
        ("insider", "Disclosed Insider / Institutional Activity", 4, insider_b[0], insider_b[1]),
        ("analyst", "Analyst Revisions / Upgrade-Downgrade", 4, analyst_b[0], analyst_b[1]),
        ("short_squeeze", "Short Interest / Squeeze Conditions", 3, short_b[0], short_b[1]),
        ("ipo_ma", "IPO / M&A / Strategic Linkage", 2, ma_b,
         detail("merger_activity") or "Deal / listing linkage (strategic graph n/a)."),
        ("regime", "Market Regime / Event Risk", 2, regime_b[0], regime_b[1]),
    ]

    params = []
    for name, label, weight, b, det in SPEC:
        pts = round((b or 0.0) * weight, 2) if b is not None else 0.0
        params.append({
            "name": name, "label": label, "weight": weight,
            "available": b is not None,
            "bias": None if b is None else round(b, 3),
            "points": pts,
            "points_label": (f"{pts:+.1f} of {weight}" if b is not None else "no data"),
            "leaning": (None if b is None else "Bullish" if b > 0.15
                        else "Bearish" if b < -0.15 else "Neutral"),
            "detail": det,
        })

    present = sum(p["weight"] for p in params if p["available"])
    possible = sum(p["weight"] for p in params) or 1
    coverage = round(present / possible * 100, 1)
    score = round(sum(p["points"] for p in params), 1)   # -100..+100 (unknown excluded)

    if present < 25:
        decision = "LOW DATA"
    elif score >= 15:
        decision = "BUY"
    elif score <= -15:
        decision = "SELL"
    else:
        decision = "NEUTRAL"

    return {
        "status": "OK" if present else "NO_DATA",
        "symbol": symbol, "source": SOURCE, "model": "TRADGO_CALL_15",
        "score": score, "decision": decision,
        "coverage_pct": coverage, "present": present, "possible": possible,
        "params": params,
        "note": ("15-parameter short-term direction model (days to ~3-4 weeks). "
                 "Each parameter scores -1..+1 x its weight; unknown data is "
                 "excluded (it lowers coverage, not the direction). UW + Finnhub "
                 "data. Starting weights -- calibrate against realised returns. "
                 "Research and decision-support, not investment advice."),
    }
