"""Intraday Scoring Model v2 -- ONE shared scorer for both screens.

The Analysis page and the Trdgo Stock Today view both call score(symbol); for a
ticker at the same moment they get the SAME numbers (short per-symbol cache), so
the two screens can never disagree. Gated behind INTRADAY_MODEL=v2; v1 is
untouched and still the default.

The 100-point model: each parameter returns a SIGNED value in [-max, +max]
(fully bearish .. fully bullish). Net = sum (range -100..+100). Display score =
50 + Net/2 (0..100; 50 = neutral). A parameter with no real data source for this
ticker returns has_data=False and contributes 0 -- and its weight is NOT counted
toward coverage, so thin names fail the G1 coverage gate (>=70) and show WITHHELD
rather than a fake signal.

Point-in-time gates G1,G3,G4,G5,G6,G7 are computed here. The time window (G2),
2-scan confirmation (G8), hysteresis and entry/stop/trail/EOD exits live in the
signal engine layer (intraday_signal_v2) which wraps this scorer with state.
"""
from __future__ import annotations

from typing import Optional

import intraday_indicators as ind
import tab_settings as cfg          # thresholds live here (IM2_* / IM_* below as fallback)

MODEL = "INTRADAY-v2"

# --- tunables (overridable from tab_settings; defaults are the spec's) --------
_D = {
    "RVOL_FULL": 2.0, "RVOL_NONE": 1.0,
    "RS_FULL_PCT": 1.0,                       # rel strength +/-1% -> full
    "PC_BULL": 0.6, "PC_BEAR_SOFT": 1.0, "PC_BEAR": 1.3,
    "RSI_BULL_LO": 55, "RSI_BULL_HI": 70, "RSI_OB": 75,
    "RSI_BEAR_HI": 45, "RSI_BEAR_LO": 30, "RSI_OS": 25,
    "UNUSUAL_MAX_DTE": 14,
    "COVERAGE_MIN": 70,
    "LIQ_MIN_PRICE": 10.0, "LIQ_MIN_AVGVOL": 1_000_000,
    "BUY": 70, "STRONG_BUY": 80, "SELL": 30, "STRONG_SELL": 20,
}


def _s(key):
    return getattr(cfg, f"IM2_{key}", _D[key])


MAX = {                                   # per-parameter max points (sum = 100)
    "market": 10, "sector": 5,
    "vwap_pos": 10, "ema_trend": 8, "orb": 7, "rsi": 5, "rvol": 5, "rel_strength": 5,
    "flow": 12, "unusual": 8, "pc_ratio": 5, "levels": 5, "disparity": 5,
    "gap": 5, "news": 5,
}
GROUP = {
    "A": ("market", "sector"),
    "B": ("vwap_pos", "ema_trend", "orb", "rsi", "rvol", "rel_strength"),
    "C": ("flow", "unusual", "pc_ratio", "levels", "disparity"),
    "D": ("gap", "news"),
}


def _f(v):
    return ind._f(v)


def _clamp(v, lo=-1.0, hi=1.0):
    return max(lo, min(hi, v))


def _today_et():
    """The current US trading-session date in America/New_York. The server runs
    on Dubai time, so never use date.today() for 'today' on these screens."""
    return ind.now_et().date()


def _et_date(ts):
    """ET calendar date of a timestamp ('...Z'/epoch), or None."""
    dt = ind._et(ts)
    return dt.date() if dt else None


def _p(name, value, has_data, detail=""):
    m = MAX[name]
    val = round(max(-m, min(m, value)), 2) if has_data and value is not None else 0.0
    return {"name": name, "max": m, "value": val,
            "has_data": bool(has_data), "detail": detail}


# --------------------------------------------------------------------------- #
#  Data gathering (one fetch per source, shared across parameters)
# --------------------------------------------------------------------------- #
_STALE_MAX_AGE = 300.0            # reuse last-good bars only if < 5 min old


def _fetch_bars_once(symbol: str, timeout: float = 9.0) -> list:
    """One capped fetch of 5-min bars. The UW endpoint intermittently stalls ~30s
    and returns [] for a random symbol, so cap it (a timed-out worker finishes on
    its own) rather than let one slow symbol block a 38-name scan."""
    import unusualwhales_service as uw
    import concurrent.futures as _cf
    try:
        with _cf.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(lambda: uw.get_intraday_bars(symbol, "5m") or []).result(timeout=timeout)
    except Exception:  # noqa: BLE001
        return []


def bars_meta(symbol: str, critical: bool = False) -> tuple[list, bool]:
    """(bars, stale). Critical symbols (SPY / QQQ / sector ETFs) retry up to 2x on
    empty; stock bars retry once. If still empty, reuse the last-good bars only
    when they are < 5 min old (stale=True) -- otherwise ([], False) = no data.
    Cached ~30s so the whole scan shares one fetch per symbol."""
    import swr
    import time as _t
    symbol = symbol.upper()

    def _build():
        attempts = 3 if critical else 2          # (1 try + 2 retries) / (1 try + 1 retry)
        b = []
        for _ in range(attempts):
            b = _fetch_bars_once(symbol)
            if b:
                break
        if b:
            swr.serve(f"im2:lastbars:{symbol}", lambda: {"bars": b, "ts": _t.time()}, 0.0)
            return {"bars": b, "stale": False}
        last = swr.peek(f"im2:lastbars:{symbol}")   # reuse last good if fresh enough
        if last and last.get("bars") and (_t.time() - (last.get("ts") or 0)) < _STALE_MAX_AGE:
            return {"bars": last["bars"], "stale": True}
        return {"bars": [], "stale": False}

    res = swr.serve(f"im2:bars:{symbol}", _build, 30.0) or {}
    return res.get("bars") or [], bool(res.get("stale"))


def _bars(symbol: str, critical: bool = False) -> list:
    return bars_meta(symbol, critical)[0]


def _index_state(symbol: str) -> Optional[int]:
    """+1 bullish / -1 bearish / 0 mixed for SPY or QQQ (price vs VWAP + EMA cross).
    None when bars are unavailable. SPY/QQQ are critical -> retry harder."""
    bars = _bars(symbol, critical=True)
    sess = ind.session_bars(bars)
    if not sess:
        return None
    px = ind.last_price(bars)
    vw = ind.vwap(sess)
    e9, e21 = ind.ema_pair(bars)
    if px is None or vw is None or e9 is None or e21 is None:
        return None
    above = px > vw
    up = e9 > e21
    if above and up:
        return 1
    if (not above) and (not up):
        return -1
    return 0


# --------------------------------------------------------------------------- #
#  Group A -- market context
# --------------------------------------------------------------------------- #
def _market() -> dict:
    spy = _index_state("SPY")
    qqq = _index_state("QQQ")
    vals = [v for v in (spy, qqq) if v is not None]
    if not vals:
        return _p("market", None, False)
    avg = sum(vals) / len(vals)           # -1..+1
    detail = f"SPY {spy:+d} / QQQ {qqq:+d}" if (spy is not None and qqq is not None) else ""
    return _p("market", avg * MAX["market"], True, detail)


def _sector(symbol: str) -> dict:
    try:
        import intraday_inputs as ii
        etf = ii._TICKER_ETF.get(symbol.upper())
        if not etf:
            import company_profile_service as cp
            prof = (cp.get_profiles([symbol]) or {}).get(symbol) or {}
            etf = ii._sector_etf(prof.get("industry") or prof.get("sector"))
        if not etf:
            return _p("sector", None, False, "no sector ETF")
        bars = _bars(etf, critical=True)         # sector ETF is critical too
        sess = ind.session_bars(bars)
        px, vw = ind.last_price(bars), ind.vwap(sess)
        chg = ind.pct_change_today(bars)
        if px is None or vw is None or chg is None:
            return _p("sector", None, False, f"{etf}: no data")
        above = px > vw
        up = chg > 0
        v = (1 if (above and up) else -1 if ((not above) and (not up)) else 0) * MAX["sector"]
        return _p("sector", v, True, f"{etf} {chg:+.2f}% {'>' if above else '<'}VWAP")
    except Exception:  # noqa: BLE001
        return _p("sector", None, False)


# --------------------------------------------------------------------------- #
#  Group B -- price & trend (stock bars)
# --------------------------------------------------------------------------- #
def _price_trend(bars: list, spy_chg: Optional[float]) -> dict:
    out = {}
    sess = ind.session_bars(bars)
    px = ind.last_price(bars)
    vw = ind.vwap(sess)
    slope = ind.vwap_slope(sess)
    e9, e21 = ind.ema_pair(bars)
    rsi = ind.rsi(bars)
    rvol = ind.rvol_tod(bars)
    chg = ind.pct_change_today(bars)
    orh, orl = ind.opening_range(sess)

    # 3) VWAP position
    if px is None or vw is None:
        out["vwap_pos"] = _p("vwap_pos", None, False)
    else:
        above = px > vw
        rising = (slope or 0) > 0.0005
        falling = (slope or 0) < -0.0005
        if above:
            v = MAX["vwap_pos"] if rising else MAX["vwap_pos"] * 0.5
        else:
            v = -MAX["vwap_pos"] if falling else -MAX["vwap_pos"] * 0.5
        out["vwap_pos"] = _p("vwap_pos", v, True,
                             f"px {'>' if above else '<'}VWAP, {'rising' if rising else 'falling' if falling else 'flat'}")

    # 4) EMA trend
    if e9 is None or e21 is None or px is None:
        out["ema_trend"] = _p("ema_trend", None, False)
    else:
        if e9 > e21 and px > e9:
            v = MAX["ema_trend"]
        elif e9 < e21 and px < e9:
            v = -MAX["ema_trend"]
        else:
            v = (MAX["ema_trend"] * 0.5) * (1 if e9 > e21 else -1)
        out["ema_trend"] = _p("ema_trend", v, True, f"EMA9 {'>' if e9 > e21 else '<'} EMA21")

    # 5) Opening-range breakout (needs volume confirm via rvol>=1)
    if orh is None or orl is None or px is None:
        out["orb"] = _p("orb", None, False)
    else:
        vol_ok = (rvol or 0) >= 1.0
        if px > orh and vol_ok:
            v = MAX["orb"]
        elif px < orl and vol_ok:
            v = -MAX["orb"]
        else:
            v = 0.0
        out["orb"] = _p("orb", v, True, f"OR {orl:.2f}-{orh:.2f}")

    # 6) RSI(14)
    if rsi is None:
        out["rsi"] = _p("rsi", None, False)
    else:
        if rsi > _s("RSI_OB"):
            v = 0.0
        elif rsi >= _s("RSI_BULL_LO"):
            v = MAX["rsi"]
        elif rsi >= 50:
            v = 2.0
        elif rsi < _s("RSI_OS"):
            v = 0.0
        elif rsi <= _s("RSI_BEAR_HI"):
            v = -MAX["rsi"]
        else:
            v = -2.0
        out["rsi"] = _p("rsi", v, True, f"RSI {rsi}")

    # 7) Relative volume in the direction of the move
    if rvol is None or chg is None:
        out["rvol"] = _p("rvol", None, False)
    else:
        sign = 1 if chg > 0 else -1 if chg < 0 else 0
        if rvol >= _s("RVOL_FULL"):
            mag = 1.0
        elif rvol < _s("RVOL_NONE"):
            mag = 0.0
        else:
            mag = (rvol - _s("RVOL_NONE")) / (_s("RVOL_FULL") - _s("RVOL_NONE"))
        out["rvol"] = _p("rvol", sign * mag * MAX["rvol"], True, f"RVOL {rvol}")

    # 8) Relative strength vs SPY
    if chg is None or spy_chg is None:
        out["rel_strength"] = _p("rel_strength", None, False)
    else:
        diff = chg - spy_chg
        v = _clamp(diff / _s("RS_FULL_PCT")) * MAX["rel_strength"]
        out["rel_strength"] = _p("rel_strength", v, True, f"{diff:+.2f}% vs SPY")
    return out


# --------------------------------------------------------------------------- #
#  Group C -- options tape (today)
# --------------------------------------------------------------------------- #
def _options(symbol: str, spot: Optional[float], ref: Optional[float] = None) -> dict:
    import unusualwhales_service as uw
    out = {}
    ov_rows = []
    try:
        ov_rows = uw._rows(uw.options_volume(symbol))
    except Exception:  # noqa: BLE001
        ov_rows = []
    ov = ov_rows[0] if ov_rows else None
    # Freshness: only use the volume row if it is TODAY's ET session, else no data.
    if ov and str(ov.get("date"))[:10] != _today_et().isoformat():
        ov = None

    # 9) Options flow -- net call vs put premium, normalised to the 20-day avg.
    if not ov:
        out["flow"] = _p("flow", None, False)
    else:
        net_c = _f(ov.get("net_call_premium"))
        net_p = _f(ov.get("net_put_premium"))
        norm = _flow_normalizer(symbol)
        if net_c is None or net_p is None or not norm:
            out["flow"] = _p("flow", None, False)
        else:
            net = net_c - net_p
            out["flow"] = _p("flow", _clamp(net / norm) * MAX["flow"], True,
                             f"net ${net/1e6:.1f}M")

    # 11) Put/Call volume ratio
    if not ov:
        out["pc_ratio"] = _p("pc_ratio", None, False)
    else:
        cv, pv = _f(ov.get("call_volume")), _f(ov.get("put_volume"))
        if not cv or cv <= 0 or pv is None:
            out["pc_ratio"] = _p("pc_ratio", None, False)
        else:
            pc = pv / cv
            if pc < _s("PC_BULL"):
                v = MAX["pc_ratio"]
            elif pc <= _s("PC_BEAR_SOFT"):
                v = 0.0
            elif pc >= _s("PC_BEAR"):
                v = -MAX["pc_ratio"]
            else:
                frac = (pc - _s("PC_BEAR_SOFT")) / (_s("PC_BEAR") - _s("PC_BEAR_SOFT"))
                v = -frac * MAX["pc_ratio"]
            out["pc_ratio"] = _p("pc_ratio", v, True, f"P/C {pc:.2f}")

    # 13) Disparity -- call vs put volume imbalance
    if not ov:
        out["disparity"] = _p("disparity", None, False)
    else:
        cv, pv = _f(ov.get("call_volume")) or 0, _f(ov.get("put_volume")) or 0
        tot = cv + pv
        if tot <= 0:
            out["disparity"] = _p("disparity", None, False)
        else:
            out["disparity"] = _p("disparity", ((cv - pv) / tot) * MAX["disparity"], True,
                                 f"calls {cv:,.0f} / puts {pv:,.0f}")

    # 10) Unusual activity -- near-dated (<=14 DTE) ask-side sweeps/blocks today
    out["unusual"] = _unusual(symbol)

    # 12) Key option levels -- max pain + near-term call/put walls
    out["levels"] = _levels(symbol, spot, ref)
    return out


def _flow_normalizer(symbol: str) -> Optional[float]:
    """True 20-day average of daily (call+put) premium. Changes once a day, so
    cache it for hours rather than refetching every scan."""
    import swr
    return swr.serve(f"im2:flownorm:{symbol}", lambda: _flow_normalizer_calc(symbol), 6 * 3600.0)


def _flow_normalizer_calc(symbol: str) -> Optional[float]:
    import unusualwhales_service as uw
    try:
        rows = uw._rows(uw.get(f"/api/stock/{symbol}/options-volume", {"limit": 25}))
    except Exception:  # noqa: BLE001
        return None
    return _flow_norm_from_rows(rows, _today_et())


def _flow_norm_from_rows(rows: list, today) -> Optional[float]:
    """20-day avg of daily (call+put) premium, excluding today's row BY DATE
    (not by position -- the series isn't guaranteed today-first)."""
    tstr = today.isoformat()
    prem = []
    for r in rows:
        if str(r.get("date"))[:10] == tstr:          # exclude today's partial row
            continue
        c, p = _f(r.get("call_premium")), _f(r.get("put_premium"))
        if c is not None and p is not None:
            prem.append(c + p)
        if len(prem) >= 20:
            break
    if not prem:
        return None
    avg = sum(prem) / len(prem)
    return avg if avg > 0 else None


def _unusual(symbol: str) -> dict:
    import unusualwhales_service as uw
    try:
        rows = uw._rows(uw.unusual_activity(symbol, min_premium=50000, limit=100))
    except Exception:  # noqa: BLE001
        return _p("unusual", None, False)
    if not rows:
        return _p("unusual", None, False)          # source gave nothing -> no data
    net = _unusual_eval(rows, _today_et(), int(_s("UNUSUAL_MAX_DTE")))
    return _p("unusual", _clamp(net / 1_000_000) * MAX["unusual"], True,
              f"net ${net/1e6:.2f}M <= {int(_s('UNUSUAL_MAX_DTE'))}DTE")


def _unusual_eval(rows: list, today, max_dte: int) -> float:
    """Net ask-side premium (calls +, puts -) for prints EXECUTED in today's ET
    session, near-dated (0..max_dte DTE from today ET)."""
    from datetime import date as _date
    net = 0.0
    for r in rows:
        ts = r.get("last_fill") or r.get("executed_at") or r.get("timestamp")
        if ts is not None and _et_date(ts) != today:   # only today's ET prints
            continue
        try:
            dte = (_date.fromisoformat(str(r.get("expiry"))[:10]) - today).days
        except Exception:  # noqa: BLE001
            continue
        if dte < 0 or dte > max_dte:
            continue
        ask = _f(r.get("ask_side_volume")) or 0
        bid = _f(r.get("bid_side_volume")) or 0
        if ask <= bid:                               # only ask-side (initiated)
            continue
        prem = _f(r.get("premium")) or 0
        net += prem if (r.get("option_type") or "").lower().startswith("c") else -prem
    return net


def _levels_data(symbol: str) -> dict:
    """Max pain + call/put OI-by-strike. Open interest and max pain update once a
    day, so cache for ~30 min; only the spot comparison is recomputed each scan."""
    import swr
    return swr.serve(f"im2:levels:{symbol}", lambda: _levels_data_calc(symbol), 1800.0)


def _levels_data_calc(symbol: str) -> dict:
    import unusualwhales_service as uw
    import contract_detail_service as cd
    from collections import defaultdict
    from datetime import date as _date
    try:
        mp_rows = uw._rows(uw.max_pain(symbol))
    except Exception:  # noqa: BLE001
        mp_rows = []
    max_pain = _f(mp_rows[0].get("max_pain")) if mp_rows else None
    today = _today_et()
    max_dte = int(_s("UNUSUAL_MAX_DTE"))
    coi, poi = defaultdict(float), defaultdict(float)
    try:
        for r in uw._rows(uw.option_contracts(symbol, limit=1500)):
            p = cd.parse_occ(r.get("option_symbol")) if r.get("option_symbol") else None
            if not (p and p.get("strike")):
                continue
            try:                                   # near-term expiries only (<=14 DTE)
                dte = (_date.fromisoformat(str(p.get("expiry"))[:10]) - today).days
            except Exception:  # noqa: BLE001
                continue
            if dte < 0 or dte > max_dte:
                continue
            oi = _f(r.get("open_interest")) or 0
            if oi:
                (coi if p["right"] == "C" else poi)[p["strike"]] += oi
    except Exception:  # noqa: BLE001
        pass
    return {"max_pain": max_pain, "coi": dict(coi), "poi": dict(poi)}


def _levels(symbol: str, spot: Optional[float], ref: Optional[float] = None) -> dict:
    data = _levels_data(symbol) or {}
    v, detail, has = _levels_eval(spot, ref, data.get("coi") or {},
                                  data.get("poi") or {}, data.get("max_pain"))
    return _p("levels", v, has, detail)


def _levels_eval(spot, ref, coi: dict, poi: dict, max_pain):
    """Signed level score. Walls = the near-term max-OI strikes (either side of
    spot). A break scores the full +/-5 only when price has CROSSED the wall this
    session (ref = session open / prior close is on the other side); bouncing off
    the put wall is a partial +. Pinned at max pain -> 0."""
    if spot is None or (not coi and not poi and max_pain is None):
        return 0.0, "", False
    # Keys may come back as strings after the JSON cache round-trip -> coerce.
    def _numeric(d):
        out = {}
        for k, v in (d or {}).items():
            try:
                out[float(k)] = _f(v) or 0.0
            except (TypeError, ValueError):
                continue
        return out
    coi, poi = _numeric(coi), _numeric(poi)
    call_wall = max(coi, key=coi.get) if coi else None   # overall max-OI, not >=spot
    put_wall = max(poi, key=poi.get) if poi else None
    v = 0.0
    detail = []
    if call_wall is not None:
        detail.append(f"CW {call_wall:g}")
        if spot > call_wall and (ref is None or ref <= call_wall):
            v += MAX["levels"]                     # crossed above the call wall today
    if put_wall is not None:
        detail.append(f"PW {put_wall:g}")
        if spot < put_wall and (ref is None or ref >= put_wall):
            v -= MAX["levels"]                     # crossed below the put wall today
        elif spot >= put_wall and (spot - put_wall) / spot < 0.005:
            v += MAX["levels"] * 0.5               # bouncing off the put wall
    if max_pain is not None:
        detail.append(f"MP {max_pain:g}")
        if abs(spot - max_pain) / spot < 0.003:
            v = 0.0                                 # pinned at max pain
    return v, " ".join(detail), True


# --------------------------------------------------------------------------- #
#  Group D -- catalyst
# --------------------------------------------------------------------------- #
def _gap(bars: list) -> dict:
    g = ind.gap_pct(bars)
    pmh = ind.premarket_high(bars)
    px = ind.last_price(bars)
    sess = ind.session_bars(bars)
    if g is None or px is None or not sess:
        return _p("gap", None, False)
    open_px = sess[0]["o"] or sess[0]["c"]
    if g > 0.3:
        holds = (pmh is None) or (px >= pmh)
        v = MAX["gap"] if holds else -MAX["gap"]
        det = f"gap {g:+.2f}% {'holds' if holds else 'fading'}"
    elif g < -0.3:
        v = -MAX["gap"] if (open_px and px < open_px) else MAX["gap"] * 0.5
        det = f"gap {g:+.2f}%"
    else:
        v = 0.0
        det = f"gap {g:+.2f}%"
    return _p("gap", v, True, det)


def _news(symbol: str) -> dict:
    import unusualwhales_service as uw
    try:
        rows = uw._rows(uw.headlines(symbol, limit=30))
    except Exception:  # noqa: BLE001
        return _p("news", None, False)         # source unavailable -> no data
    v, detail = _news_eval(rows, _today_et())
    return _p("news", v, True, detail)


def _news_eval(rows: list, today):
    """Sentiment of headlines whose created_at is in today's ET session."""
    score = 0
    n = 0
    for r in rows:
        if _et_date(r.get("created_at")) != today:   # compare in ET, not server time
            continue
        sent = (r.get("sentiment") or "").lower()
        if "pos" in sent or "bull" in sent:
            score += 1
            n += 1
        elif "neg" in sent or "bear" in sent:
            score -= 1
            n += 1
    v = _clamp(score / 2.0) * MAX["news"] if n else 0.0
    return v, (f"{n} headline(s) today" if n else "no news today")


# --------------------------------------------------------------------------- #
#  Gates (point-in-time) + assembly
# --------------------------------------------------------------------------- #
def _earnings_blocked(symbol: str) -> bool:
    """G6: earnings today or before the next session open."""
    try:
        import uw_earnings_calendar as cal
        pv = cal.preview(symbol) or {}
        nxt = str(pv.get("next_report") or "")[:10]
        if not nxt:
            return False
        from datetime import date
        d = date.fromisoformat(nxt)
        return 0 <= (d - _today_et()).days <= 1    # ET session date, not server day
    except Exception:  # noqa: BLE001
        return False


def daily_candles(symbol: str) -> list:
    """Daily OHLCV bars, cached ~6h (used for ATR and the 20-day liquidity check;
    both change once a day)."""
    import swr

    def _fetch():
        import unusualwhales_service as uw
        try:
            return uw._rows(uw.candles(symbol, "1d", limit=40)) or []
        except Exception:  # noqa: BLE001
            return []

    return swr.serve(f"im2:daily:{symbol.upper()}", _fetch, 6 * 3600.0) or []


def atr(symbol: str) -> Optional[float]:
    return ind.atr14_daily(daily_candles(symbol))


def _liquidity_ok(symbol: str, spot: Optional[float]) -> Optional[bool]:
    """G7: price >= $10, 20-day avg vol >= 1M, optionable. None = unknown."""
    return _liquidity_from_candles(daily_candles(symbol), spot, _today_et(),
                                   _s("LIQ_MIN_PRICE"), _s("LIQ_MIN_AVGVOL"))


def _liquidity_from_candles(candles, spot, today, min_price, min_avgvol) -> Optional[bool]:
    if spot is None or spot < min_price:
        return False if spot is not None else None
    tstr = today.isoformat()
    per_day = {}
    for b in candles:
        dd = str(b.get("date"))[:10]
        v = _f(b.get("volume"))
        if dd and dd != tstr and v is not None:       # exclude today's partial bar
            per_day[dd] = v
    vols = [per_day[d] for d in sorted(per_day)][-20:]
    if len(vols) < 10:
        return None
    avg = sum(vols) / len(vols)
    return avg >= min_avgvol


def score(symbol: str) -> dict:
    """The one shared v2 scorer. Pure function of (symbol, now ET); cached ~45s
    so both screens read the same numbers in the same minute."""
    import swr
    return swr.serve(f"im2:{symbol.upper()}", lambda: _compute(symbol.upper()), 45.0)


def _compute(symbol: str) -> dict:
    # Critical market data first, every scan: SPY and QQQ (retry up to 2x, reuse
    # last-good < 5 min on failure). Stock bars retry once.
    spy_bars, spy_stale = bars_meta("SPY", critical=True)
    bars_meta("QQQ", critical=True)       # warm QQQ (shared cache) up front
    bars, _stock_stale = bars_meta(symbol, critical=False)
    spot = ind.last_price(bars)
    spy_sess = ind.session_bars(spy_bars)
    spy_chg = ind.pct_change_today(spy_bars)
    spy_status = "ok" if (spy_bars and not spy_stale) else "stale" if spy_bars else "missing"
    market_stale = spy_status != "ok"
    bars_ok = bool(bars)

    params: dict = {}
    stock_sess = ind.session_bars(bars)
    session_open = (stock_sess[0]["o"] or stock_sess[0]["c"]) if stock_sess else None
    params["market"] = _market()
    params["sector"] = _sector(symbol)
    params.update(_price_trend(bars, spy_chg))
    params.update(_options(symbol, spot, session_open))
    params["gap"] = _gap(bars)
    params["news"] = _news(symbol)

    ordered = [params[n] for g in ("A", "B", "C", "D") for n in GROUP[g]]
    net = round(sum(p["value"] for p in ordered), 2)
    display = round(50 + net / 2, 1)
    covered = sum(p["max"] for p in ordered if p["has_data"])
    coverage_pct = round(covered / 100 * 100, 1)

    def _group_net(g):
        return sum(params[n]["value"] for n in GROUP[g])

    def _group_has(g):
        return any(params[n]["has_data"] for n in GROUP[g])

    # --- point-in-time gates ---
    spy_above = None
    px = spot
    vw = ind.vwap(ind.session_bars(bars))
    spy_px, spy_vw = ind.last_price(spy_bars), ind.vwap(spy_sess)
    if spy_px is not None and spy_vw is not None:
        spy_above = spy_px > spy_vw

    g1_coverage = covered >= _s("COVERAGE_MIN") and _group_has("B") and _group_has("C")
    earnings_blocked = _earnings_blocked(symbol)
    liq = _liquidity_ok(symbol, spot)

    # desired side from score
    side = "BUY" if display >= _s("BUY") else "SELL" if display <= _s("SELL") else None

    g3_market = None
    g4_confluence = None
    g5_group = None
    if side == "BUY":
        g3_market = bool(spy_above)
        g4_confluence = bool(px is not None and vw is not None and px > vw
                             and params["ema_trend"]["value"] > 0
                             and params["flow"]["has_data"] and params["flow"]["value"] > 0)
        g5_group = _group_net("A") >= 0 and _group_net("B") > 0 and _group_net("C") > 0
    elif side == "SELL":
        g3_market = spy_above is False
        g4_confluence = bool(px is not None and vw is not None and px < vw
                             and params["ema_trend"]["value"] < 0
                             and params["flow"]["has_data"] and params["flow"]["value"] < 0)
        g5_group = _group_net("A") <= 0 and _group_net("B") < 0 and _group_net("C") < 0

    gates = {
        "G1_coverage": g1_coverage,
        "G3_market": g3_market,
        "G4_confluence": g4_confluence,
        "G5_group_agreement": g5_group,
        "G6_earnings": not earnings_blocked,
        "G7_liquidity": (liq is True),
    }

    # Status / call (G2 time + G8 confirmation + hysteresis handled by the engine).
    call = "NO EDGE"
    status = "OK"
    if not g1_coverage:
        call, status = "WITHHELD", "WITHHELD"
    elif earnings_blocked or liq is False:
        call, status = "EXCLUDED", "EXCLUDED"
    elif side and gates["G3_market"] and gates["G4_confluence"] and gates["G5_group_agreement"] \
            and gates["G6_earnings"] and gates["G7_liquidity"]:
        if display >= _s("STRONG_BUY"):
            call = "STRONG BUY"
        elif display >= _s("BUY"):
            call = "BUY"
        elif display <= _s("STRONG_SELL"):
            call = "STRONG SELL"
        elif display <= _s("SELL"):
            call = "SELL"
    # else NO EDGE

    if market_stale and status == "OK":
        status = "MARKET_STALE"               # don't silently show NO EDGE

    return {
        "symbol": symbol, "model": MODEL,
        "net": net, "score": display, "spot": spot,
        "coverage_pct": coverage_pct, "covered": covered,
        "side": side, "call": call, "status": status,
        "gates": gates, "spy_above_vwap": spy_above,
        "vwap": round(vw, 2) if vw is not None else None,
        "atr": atr(symbol),
        "market_stale": market_stale, "spy_status": spy_status, "bars_ok": bars_ok,
        "params": ordered,
    }
