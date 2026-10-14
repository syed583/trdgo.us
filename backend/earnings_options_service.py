"""
Engine 2 -- the Options Earnings Analyzer (100-point evidence score).

Strategy: a long straddle / strangle (buy a call and a put). The evidence here
scores one question -- will the realised move beat what the options cost? -- so
every parameter leans toward "the move will pay" (+) or "the premium is too rich
/ IV will crush" (-). Weights are exactly the structure document's:

    Implied Volatility .................... 10
    IV Crush Risk ........................ 15
    Expected Move vs Combined Premium .... 15
    Historical Earnings Movement ......... 10
    Combined Call + Put Premium .......... 10
    Theta Decay ........................... 5
    Delta, Gamma & Vega ................... 5
    Strike Selection & Expiry ............. 5
    Options Liquidity & Bid/Ask Spread .... 5
    Unusual Options Activity .............. 5
    Options Disparity .................... 5
    Options Flow & OI Changes ............. 5
    Dark Pool Confirmation ............... 5

Direction-agnostic parameters (unusual activity, flow, OI, disparity, dark pool)
contribute by their magnitude -- a big move brewing helps a straddle whichever
way it breaks.
"""

from __future__ import annotations

from typing import Optional

import earnings_engine as eng
from earnings_equity_service import _directional_signals, _f, _avg

SOURCE = "Trdgo earnings model"


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _atm_straddle(symbol: str, spot: Optional[float],
                  after: Optional[str] = None) -> Optional[dict]:
    """
    Build the real ATM straddle from UW's option chain -- the first expiry on/after
    the earnings date (or, lacking it, a few days out so a 0-DTE weekly does not
    distort theta) with an at-the-money call and put -- and read the actual greeks
    and NBBO. Powers the theta, greeks, strike/expiry and liquidity parameters.
    """
    try:
        import unusualwhales_service as uw
        import contract_detail_service as cd
        from datetime import date, timedelta
        rows = uw._rows(uw.option_contracts(symbol, limit=1500))
        if not rows or spot is None:
            return None
        # First expiry we'll accept: the earnings date if known, else +3 days to
        # skip 0-DTE/near weeklies whose theta is not the earnings trade's.
        floor = after or (date.today() + timedelta(days=3)).isoformat()

        parsed = []
        for r in rows:
            occ = r.get("option_symbol")
            p = cd.parse_occ(occ) if occ else None
            if not p or not p.get("expiry") or p["expiry"] < floor:
                continue
            parsed.append((p, r))
        if not parsed:
            return None

        # Nearest qualifying expiry that has both a call and a put.
        expiries = sorted({p["expiry"] for p, _ in parsed})
        target = None
        for e in expiries:
            rights = {p["right"] for p, _ in parsed if p["expiry"] == e}
            if "C" in rights and "P" in rights:
                target = e
                break
        if not target:
            return None

        def nearest(right: str):
            cands = [(p, r) for p, r in parsed
                     if p["expiry"] == target and p["right"] == right
                     and p.get("strike")]
            if not cands:
                return None
            return min(cands, key=lambda pr: abs(pr[0]["strike"] - spot))

        call, put = nearest("C"), nearest("P")
        if not call or not put:
            return None

        def mid(r):
            b, a = _f(r.get("nbbo_bid")), _f(r.get("nbbo_ask"))
            if b is not None and a is not None:
                return (b + a) / 2, max(0.0, a - b)
            lp = _f(r.get("last_price"))
            return lp, None

        c_mid, c_spr = mid(call[1])
        p_mid, p_spr = mid(put[1])
        premium = (c_mid or 0) + (p_mid or 0)
        # Open interest at the ATM strike -- total across both legs, so a single
        # thin leg (common on a post-earnings weekly before it builds up) doesn't
        # misrepresent the straddle's liquidity.
        c_oi, p_oi = _f(call[1].get("open_interest")), _f(put[1].get("open_interest"))
        oi = None if (c_oi is None and p_oi is None) else (c_oi or 0) + (p_oi or 0)
        theta = (_f(call[1].get("theta")) or 0) + (_f(put[1].get("theta")) or 0)
        vega = (_f(call[1].get("vega")) or 0) + (_f(put[1].get("vega")) or 0)
        gamma = (_f(call[1].get("gamma")) or 0) + (_f(put[1].get("gamma")) or 0)
        spread = ((c_spr or 0) + (p_spr or 0)) if (c_spr is not None or p_spr is not None) else None
        # Per-leg spread as a % of that leg's mid -- the binding (wider) leg, for
        # the short-straddle liquidity gate ("spread > 10% on EITHER leg").
        c_lsp = (c_spr / c_mid * 100) if (c_spr is not None and c_mid) else None
        p_lsp = (p_spr / p_mid * 100) if (p_spr is not None and p_mid) else None
        leg_spread_pct = max([x for x in (c_lsp, p_lsp) if x is not None], default=None)
        # Front-month ATM implied vol (avg of the two legs), as a %. This is the
        # IV that actually spikes into earnings -- unlike a 30-day IV reading.
        c_iv, p_iv = _f(call[1].get("implied_volatility")), _f(put[1].get("implied_volatility"))
        ivs = [x for x in (c_iv, p_iv) if x is not None]
        atm_iv = (sum(ivs) / len(ivs)) if ivs else None
        if atm_iv is not None:
            atm_iv = round(atm_iv * 100, 1) if atm_iv <= 5 else round(atm_iv, 1)

        return {
            "expiry": target,
            "call_strike": call[0]["strike"], "put_strike": put[0]["strike"],
            "premium": round(premium, 2) if premium else None,
            "theta": theta, "vega": vega, "gamma": gamma,
            "atm_iv": atm_iv,
            "spread": spread, "oi": (int(oi) if oi is not None else None),
            "spread_pct": (spread / premium) if (spread is not None and premium) else None,
            "leg_spread_pct": (round(leg_spread_pct, 2) if leg_spread_pct is not None else None),
        }
    except Exception:  # noqa: BLE001
        return None


def _build_occ(symbol: str, expiry: str, strike: float, right: str) -> str:
    """OCC option symbol, e.g. TSM + 261016 + C + 00445000 for TSM 445 call 16 Oct."""
    from datetime import datetime
    yy = datetime.fromisoformat(str(expiry)[:10]).strftime("%y%m%d")
    ks = f"{int(round(strike * 1000)):08d}"
    return f"{symbol.upper()}{yy}{right}{ks}"


def iv_crush_quote(symbol: str, spot: Optional[float], reaction_date: Optional[str],
                   date_10d: Optional[str], spot_10d: Optional[float]) -> Optional[dict]:
    """IV Crush Check option data: the ATM call and put TODAY and 10 trading
    sessions ago (from the contract-history endpoint), for the result expiry.
    Rate = NBBO mid; the 10-sessions-ago rate is that day's closing mid (or last
    price). IV is each contract's implied vol at the same moment."""
    try:
        import unusualwhales_service as uw
        import contract_detail_service as cd
        from collections import defaultdict
        if not reaction_date:        # spot is optional -- the ATM is found via parity
            return None
        rd = str(reaction_date)[:10]
        rows = uw._rows(uw.option_contracts(symbol, limit=1500))
        parsed = []
        for r in rows:
            p = cd.parse_occ(r.get("option_symbol")) if r.get("option_symbol") else None
            if p and p.get("expiry") and p.get("strike") and p.get("right"):
                parsed.append((p, r))
        if not parsed:
            return None
        by_exp: dict = defaultdict(set)
        for p, _ in parsed:
            by_exp[p["expiry"]].add(p["right"])
        valid = sorted(e for e, rt in by_exp.items() if "C" in rt and "P" in rt)
        expiry = next((e for e in valid if e >= rd), None)
        if not expiry:
            return None
        strikes = sorted({p["strike"] for p, _ in parsed if p["expiry"] == expiry})
        if not strikes:
            return None

        def nearest(grid, px):
            return min(grid, key=lambda k: (abs(k - px), k)) if px is not None else None

        def mid_of(r):
            b, a = _f(r.get("nbbo_bid")), _f(r.get("nbbo_ask"))
            if b is not None and a is not None:
                return (b + a) / 2
            return _f(r.get("last_price"))

        def iv_pct(v):
            iv = _f(v)
            if iv is None:
                return None
            return round(iv * 100, 1) if iv <= 5 else round(iv, 1)

        def now_leg(strike, right):
            """Today's rate + IV for a given strike, from the live chain."""
            row = next((r for p, r in parsed if p["expiry"] == expiry
                        and p["right"] == right and p["strike"] == strike), None)
            if not row:
                return {"rate": None, "iv": None}
            m = mid_of(row)
            return {"rate": (round(m, 2) if m is not None else None),
                    "iv": iv_pct(row.get("implied_volatility"))}

        def then_leg(strike, right):
            """That strike's closing rate + IV on the 10-sessions-ago date."""
            if strike is None or not date_10d:
                return None
            occ = _build_occ(symbol, expiry, strike, right)
            try:
                hist = uw._rows(uw.get(f"/api/option-contract/{occ}/historic", {"limit": 25}))
            except Exception:  # noqa: BLE001
                return None
            row = next((r for r in hist if str(r.get("date"))[:10] == str(date_10d)[:10]), None)
            if not row:
                return None
            m = mid_of(row)
            if m is None:
                m = _f(row.get("avg_price"))
            return {"rate": (round(m, 2) if m is not None else None),
                    "iv": iv_pct(row.get("implied_volatility"))}

        # ONE locked strike: the ATM 10 sessions ago (nearest to the spot that
        # day). Show that same strike then vs now -- a clean like-for-like.
        k_locked = nearest(strikes, spot_10d)
        if k_locked is None:
            k_locked = nearest(strikes, spot)
        return {
            "expiry": expiry, "strike": k_locked,
            "call_then": then_leg(k_locked, "C"), "call_now": now_leg(k_locked, "C"),
            "put_then": then_leg(k_locked, "P"), "put_now": now_leg(k_locked, "P"),
        }
    except Exception:  # noqa: BLE001
        return None


def short_straddle_quote(symbol: str, spot: Optional[float], reaction_date: Optional[str],
                         atm_mode: str = "nearest", back_gap: int = 21,
                         back_max: int = 60) -> Optional[dict]:
    """Full short-straddle quote for the change spec: the EVENT expiry (first on/
    after the earnings reaction session) ATM call+put bid/mid/ask/IV, and the
    BACK expiry (>= back_gap days later, ex-event reference) ATM IV. Returns every
    leg detail so the tab can show prices and IV components separately."""
    try:
        import unusualwhales_service as uw
        import contract_detail_service as cd
        from collections import defaultdict
        from datetime import date, datetime
        if spot is None or not reaction_date:
            return None
        rd = str(reaction_date)[:10]
        rows = uw._rows(uw.option_contracts(symbol, limit=1500))
        parsed = []
        for r in rows:
            occ = r.get("option_symbol")
            p = cd.parse_occ(occ) if occ else None
            if p and p.get("expiry") and p.get("strike") and p.get("right"):
                parsed.append((p, r))
        if not parsed:
            return None

        by_exp: dict = defaultdict(set)
        for p, _ in parsed:
            by_exp[p["expiry"]].add(p["right"])
        valid = sorted(e for e, rt in by_exp.items() if "C" in rt and "P" in rt)
        event_exp = next((e for e in valid if e >= rd), None)
        if not event_exp:
            return None

        def _dt(x):
            return datetime.fromisoformat(x).date()
        ev_d = _dt(event_exp)
        back_cands = [e for e in valid if (_dt(e) - ev_d).days >= back_gap]
        back_far = False
        if back_cands:
            within = [e for e in back_cands if (_dt(e) - ev_d).days <= back_max]
            back_exp = within[0] if within else back_cands[-1]
            back_far = not within
        else:
            later = [e for e in valid if _dt(e) > ev_d]
            back_exp = later[-1] if later else None
            back_far = True

        def atm_leg(exp, right):
            cands = [(p, r) for p, r in parsed if p["expiry"] == exp and p["right"] == right]
            if not cands:
                return None
            if atm_mode == "delta50":
                def dk(pr):
                    dv = _f(pr[1].get("delta"))
                    return abs(abs(dv) - 0.5) if dv is not None else 9.0
                return min(cands, key=dk)
            return min(cands, key=lambda pr: (abs(pr[0]["strike"] - spot), pr[0]["strike"]))

        call = atm_leg(event_exp, "C")
        put = atm_leg(event_exp, "P")
        if not call or not put:
            return None
        atm_strike = call[0]["strike"]
        same_put = next((pr for pr in parsed if pr[0]["expiry"] == event_exp
                         and pr[0]["right"] == "P" and pr[0]["strike"] == atm_strike), None)
        if same_put:
            put = same_put

        def quote(r):
            b, a = _f(r.get("nbbo_bid")), _f(r.get("nbbo_ask"))
            mid = (b + a) / 2 if (b is not None and a is not None) else _f(r.get("last_price"))
            spr = ((a - b) / mid * 100) if (b is not None and a is not None and mid) else None
            iv = _f(r.get("implied_volatility"))
            iv = (round(iv * 100, 1) if (iv is not None and iv <= 5) else (round(iv, 1) if iv is not None else None))
            return {"bid": b, "ask": a, "mid": (round(mid, 2) if mid is not None else None),
                    "iv": iv, "spread_pct": (round(spr, 1) if spr is not None else None),
                    "delta": _f(r.get("delta")), "oi": _f(r.get("open_interest")),
                    "vol": _f(r.get("volume"))}

        cq, pq = quote(call[1]), quote(put[1])
        straddle = (cq["mid"] + pq["mid"]) if (cq["mid"] is not None and pq["mid"] is not None) else None
        ivs = [x for x in (cq["iv"], pq["iv"]) if x is not None]
        front_iv = round(sum(ivs) / len(ivs), 1) if ivs else None
        wide = ((cq["spread_pct"] or 0) > 15.0) or ((pq["spread_pct"] or 0) > 15.0)

        back_iv = None
        if back_exp:
            bivs = []
            for leg in (atm_leg(back_exp, "C"), atm_leg(back_exp, "P")):
                if leg:
                    iv = _f(leg[1].get("implied_volatility"))
                    if iv is not None:
                        bivs.append(iv * 100 if iv <= 5 else iv)
            if bivs:
                back_iv = round(sum(bivs) / len(bivs), 1)

        return {
            "event_expiry": event_exp, "event_dte": (ev_d - date.today()).days,
            "atm_strike": atm_strike, "call": cq, "put": pq,
            "straddle": (round(straddle, 2) if straddle is not None else None),
            "front_iv": front_iv, "wide_spread": wide,
            "back_expiry": back_exp, "back_iv": back_iv, "back_far": back_far,
        }
    except Exception:  # noqa: BLE001
        return None


def _vix_regime_bias() -> Optional[float]:
    """Market Regime / VIX: elevated VIX = bigger moves = favours a straddle.
    The indices feed rarely carries a spot VIX, so fall back to VIXY (the VIX
    short-term ETF the app already uses as a proxy)."""
    v: Optional[float] = None
    try:
        import live_market_service as market
        idx = market.get_indices() or {}
        vix = next((i for i in (idx.get("indices") or []) if i.get("label") == "VIX"), None)
        v = _f((vix or {}).get("value") or (vix or {}).get("price"))
    except Exception:  # noqa: BLE001
        v = None
    if v is None:
        try:
            import unusualwhales_service as uw
            rows = uw._rows(uw.candles("VIXY", "1d", limit=2))
            v = _f((rows[-1] or {}).get("close")) if rows else None
        except Exception:  # noqa: BLE001
            v = None
    return None if v is None else _clamp((v - 18.0) / 10.0)


def _move_uncertainty_bias(symbol: str) -> Optional[float]:
    """Move Uncertainty: wide analyst EPS spread (dispersion) = more uncertainty,
    which favours a long straddle (a bigger move is more likely)."""
    try:
        import unusualwhales_service as uw
        rows = uw._rows(uw.earnings_estimates(symbol))
        fq = sorted((r for r in rows if r.get("horizon") == "fiscal quarter" and r.get("date")),
                    key=lambda r: r.get("date"))
        if not fq:
            return None
        r = fq[0]
        hi, lo, avg = (_f(r.get("eps_estimate_high")), _f(r.get("eps_estimate_low")),
                       _f(r.get("eps_estimate_average")))
        if hi is None or lo is None or not avg:
            return None
        disp = abs(hi - lo) / abs(avg)
        return _clamp((disp - 0.15) / 0.35)
    except Exception:  # noqa: BLE001
        return None


# "Final Earnings Structure" profile (My Calls): the options engine built to the
# user's exact 13-line spec (totals 100). Built explicitly in get_analysis from
# the biases already computed there, so the default Earnings Trade weighting is
# untouched.
#   IV 10 · IV Crush 15 · EM vs Premium 15 · Historical 10 · Combined Premium 10 ·
#   Theta 5 · Greeks 5 · Strike 5 · Liquidity 5 · Unusual 5 · Disparity 5 ·
#   Flow+OI 5 · Dark Pool 5


def get_analysis(symbol: str, profile: str = "default") -> dict:
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"status": "INVALID_SYMBOL", "symbol": symbol, "source": SOURCE}

    # Volatility stats: iv, rv, variance-risk-premium, iv_rank, ranges.
    stats: dict = {}
    try:
        import uw_volatility_service as vol
        stats = (vol.get_volatility(symbol) or {}).get("stats") or {}
    except Exception:  # noqa: BLE001
        stats = {}

    levels: dict = {}
    try:
        import options_levels_service as ol
        levels = ol.get_levels(symbol)
    except Exception:  # noqa: BLE001
        levels = {}
    ok_levels = levels.get("status") == "OK"
    greeks = levels.get("greeks") if ok_levels else None
    em = (levels.get("expected_move") or {}) if ok_levels else {}

    iv = _f(stats.get("iv"))
    rv = _f(stats.get("rv"))
    iv_rank = _f(stats.get("iv_rank"))           # normalise to 0..1
    # Some UW vol endpoints return IV rank as a percentile (0..100), others as a
    # fraction (0..1). Treat anything above 1.5 as a 0..100 value. Without this
    # the IV lean was (0.5 - 66.5)*2 -> clamped to -1, pinning the IV parameter
    # fully bearish every time and dragging the options score to NO TRADE.
    if iv_rank is not None and iv_rank > 1.5:
        iv_rank = iv_rank / 100.0
    iv_low, iv_high = _f(stats.get("iv_low")), _f(stats.get("iv_high"))
    rv_low, rv_high = _f(stats.get("rv_low")), _f(stats.get("rv_high"))

    # --- derive each parameter's lean ---
    # IV level: cheap IV (low rank) is good for a buyer.
    b_iv = None if iv_rank is None else _clamp((0.5 - iv_rank) * 2)
    # IV crush: rich options (iv above realised) crush harder post-event.
    b_crush = None if (iv is None or rv is None or not iv) else _clamp(-((iv - rv) / iv) * 2)
    # Expected move vs premium: realised above implied means the straddle pays.
    b_emp = None if (iv is None or rv is None or not iv) else _clamp(((rv - iv) / iv) * 2)
    # Historical movement: where realised vol sits in its own range (big mover?).
    b_hist = None
    if rv is not None and rv_low is not None and rv_high is not None and rv_high > rv_low:
        b_hist = _clamp((rv - (rv_low + rv_high) / 2) / ((rv_high - rv_low) / 2))
    # Combined premium cheapness (correlated with IV; kept mild).
    b_prem = None if b_iv is None else _clamp(b_iv * 0.7)

    # Real ATM straddle from the chain powers theta / greeks / strike / liquidity.
    spot_px = _f(levels.get("spot")) if ok_levels else iv and None
    if spot_px is None:
        try:
            import live_market_service as market
            spot_px = _f((market.get_quote(symbol) or {}).get("price"))
        except Exception:  # noqa: BLE001
            spot_px = None
    # The report date, so the straddle lands on the first expiry after earnings.
    earnings_date = None
    try:
        import uw_earnings_calendar as uwcal
        earnings_date = (uwcal.preview(symbol) or {}).get("next_report")
    except Exception:  # noqa: BLE001
        earnings_date = None
    straddle = _atm_straddle(symbol, spot_px, after=earnings_date)

    b_theta = b_greeks = b_strike = b_liq = None
    if straddle and straddle.get("premium"):
        prem = straddle["premium"]
        # Theta: daily decay as a share of premium -- a standing cost (negative).
        if straddle.get("theta") is not None:
            b_theta = _clamp(-(abs(straddle["theta"]) / prem) / 0.05)
        # Greeks: vega per premium -- how much the straddle gains per vol point.
        if straddle.get("vega") is not None:
            b_greeks = _clamp((straddle["vega"] / prem) / 0.15)
        # Strike/expiry: ATM strikes exist at a usable expiry; tighter to spot is
        # better. Full marks when the strike sits within ~1.5% of spot.
        if spot_px:
            off = abs(straddle["call_strike"] - spot_px) / spot_px
            b_strike = _clamp(1.0 - off / 0.015)
        # Liquidity: combined bid/ask spread as a share of premium.
        if straddle.get("spread_pct") is not None:
            b_liq = _clamp((0.08 - straddle["spread_pct"]) / 0.08)

    sig = _directional_signals(symbol)

    def mag(*names) -> Optional[float]:
        """Magnitude of a directional signal -- a move brewing, any direction."""
        v = _avg(*(sig.get(n) for n in names))
        return None if v is None else _clamp(abs(v))

    # Signals distributed to match the named parameters (no double-counting):
    #   Unusual Options Activity   -> dated volume crowding
    #   Options Flow + OI Change    -> per-print flow + OI build/positioning
    #   Put/Call Disparity + Skew   -> options disparity + session put/call ratio
    b_unusual = mag("flow_by_expiry")
    b_flowoi = mag("options_flow", "daily_oi_change", "oi_positioning")
    b_disp = mag("disparity", "volume_pcr")

    # Greeks + Theta are one parameter in the default profile; the "final"
    # profile splits them back apart (see below).
    b_greeks_theta = _avg(b_greeks, b_theta)
    b_regime = _vix_regime_bias()
    b_moveunc = _move_uncertainty_bias(symbol)
    # Off-lit (dark pool) activity -- direction-agnostic magnitude: heavy
    # off-exchange prints signal a move brewing. Reuses the equity helper.
    b_dark = None
    try:
        import earnings_equity_service as _eq
        _dpb = _eq._darkpool_bias(symbol)
        b_dark = None if _dpb is None else _clamp(abs(_dpb) * 2)
    except Exception:  # noqa: BLE001
        b_dark = None

    params = [
        eng.Param("implied_vs_realized", "Implied Move vs Historical Realized Move", 25, b_emp,
                  detail="Implied move vs realised -- the straddle pays when realised wins."),
        eng.Param("historical_move", "Historical Earnings Option Performance", 18, b_hist,
                  detail="Where realised volatility sits in its range (recent earnings)."),
        eng.Param("iv_crush", "IV Crush Risk", 12, b_crush,
                  detail=f"IV {iv}% vs realised {rv}%; rich options crush harder."),
        eng.Param("iv_rank", "IV Rank / Percentile", 8, b_iv,
                  detail=(f"IV {iv}% (rank {round(iv_rank*100) if iv_rank is not None else '--'}%); "
                          "cheaper IV / lower rank favours the buyer.")),
        eng.Param("move_uncertainty", "Move Uncertainty (estimate dispersion + peer moves)", 8, b_moveunc,
                  detail="Analyst EPS spread (dispersion); a wider spread means a "
                         "bigger move is more likely.",
                  unavailable_reason="" if b_moveunc is not None else "No analyst EPS spread."),
        eng.Param("flow_oi", "Options Flow + OI Change", 7, b_flowoi,
                  detail="Per-print flow and overnight OI build / positioning (magnitude)."),
        eng.Param("greeks_theta", "Greeks + Theta", 5, b_greeks_theta,
                  detail=(f"ATM straddle vega {round(straddle['vega'], 3)}, theta "
                          f"{round(straddle['theta'], 2)}/day (UW)." if straddle
                          else "No chain greeks."),
                  unavailable_reason="" if b_greeks_theta is not None else "No ATM chain."),
        eng.Param("liquidity", "Liquidity + Bid/Ask Spread", 5, b_liq,
                  detail=(f"ATM spread {round(straddle['spread_pct']*100, 1)}% of premium (UW)."
                          if straddle and straddle.get("spread_pct") is not None
                          else "No NBBO on the ATM chain."),
                  unavailable_reason="" if b_liq is not None else "No ATM NBBO."),
        eng.Param("market_regime", "Market Regime / VIX", 5, b_regime,
                  detail="Broad volatility regime (VIX); elevated VIX favours a move.",
                  unavailable_reason="" if b_regime is not None else "No VIX reading."),
        eng.Param("skew", "Put/Call Skew", 4, b_disp,
                  detail="Options disparity / put-call skew (magnitude)."),
        eng.Param("unusual_options", "Unusual Options Activity", 3, b_unusual,
                  detail="Volume above open interest / near-term positioning."),
    ]

    # My Calls "Final Earnings Structure": the exact 13-line options spec,
    # Theta/Greeks/Strike/Combined-Premium/Dark-Pool each their own parameter.
    if profile == "final":
        params = [
            eng.Param("iv_rank", "Implied Volatility (IV) + IV Rank", 10, b_iv,
                      detail=(f"IV {iv}% (rank {round(iv_rank*100) if iv_rank is not None else '--'}%); "
                              "cheaper IV / lower rank favours the buyer.")),
            eng.Param("iv_crush", "IV Crush Risk", 15, b_crush,
                      detail=f"IV {iv}% vs realised {rv}%; rich options crush harder."),
            eng.Param("implied_vs_realized", "Expected Move vs Option Premium", 15, b_emp,
                      detail="Implied move vs realised -- the straddle pays when realised wins."),
            eng.Param("historical_move", "Historical Earnings Movement", 10, b_hist,
                      detail="Where realised volatility sits in its range (recent earnings)."),
            eng.Param("combined_premium", "Combined Premium", 10, b_prem,
                      detail="Total straddle premium richness (correlated with IV; kept mild).",
                      unavailable_reason="" if b_prem is not None else "No IV reading."),
            eng.Param("theta", "Theta Decay", 5, b_theta,
                      detail=(f"ATM straddle theta {round(straddle['theta'], 2)}/day (UW)."
                              if straddle and straddle.get("theta") is not None else "No ATM chain."),
                      unavailable_reason="" if b_theta is not None else "No ATM chain theta."),
            eng.Param("greeks", "Greeks (Vega / Delta)", 5, b_greeks,
                      detail=(f"ATM straddle vega {round(straddle['vega'], 3)} per premium (UW)."
                              if straddle and straddle.get("vega") is not None else "No ATM chain."),
                      unavailable_reason="" if b_greeks is not None else "No ATM chain greeks."),
            eng.Param("strike", "Strike Selection", 5, b_strike,
                      detail="ATM strike tightness to spot at a usable post-earnings expiry.",
                      unavailable_reason="" if b_strike is not None else "No ATM strike."),
            eng.Param("liquidity", "Liquidity + Bid/Ask Spread", 5, b_liq,
                      detail=(f"ATM spread {round(straddle['spread_pct']*100, 1)}% of premium (UW)."
                              if straddle and straddle.get("spread_pct") is not None
                              else "No NBBO on the ATM chain."),
                      unavailable_reason="" if b_liq is not None else "No ATM NBBO."),
            eng.Param("unusual_options", "Unusual Options Activity", 5, b_unusual,
                      detail="Volume above open interest / near-term positioning."),
            eng.Param("skew", "Options Disparity / Put-Call Skew", 5, b_disp,
                      detail="Options disparity / put-call skew (magnitude)."),
            eng.Param("flow_oi", "Options Flow + OI", 5, b_flowoi,
                      detail="Per-print flow and overnight OI build / positioning (magnitude)."),
            eng.Param("dark_pool", "Dark Pool Activity", 5, b_dark,
                      detail="Off-exchange print intensity (direction-agnostic).",
                      unavailable_reason="" if b_dark is not None else "No dark-pool prints."),
        ]

    result = eng.score(params, pos_label="STRADDLE", neg_label="NO TRADE",
                       buy_at=58.0, sell_at=-1.0)

    # Split the positive decision into STRADDLE (strong) vs STRANGLE (moderate).
    s = result.get("score")
    if result["decision"] == "STRADDLE" and s is not None:
        result["decision"] = "STRADDLE" if s >= 66 else "STRANGLE"

    result.update({
        "symbol": symbol,
        "engine": "OPTIONS",
        "profile": profile,
        "construction": {
            "spot": spot_px,
            "expected_move_percent": _f(em.get("percent")),
            "expected_range": em.get("range"),
            "implied_volatility": _f(em.get("implied_volatility")) or iv,
            "iv_rank": None if iv_rank is None else round(iv_rank * 100, 1),
            "realized_vol": rv,
            "variance_risk_premium": _f(stats.get("vrp")),
            "call_strike": straddle.get("call_strike") if straddle else None,
            "put_strike": straddle.get("put_strike") if straddle else None,
            "combined_premium": straddle.get("premium") if straddle else None,
            "expiry": (straddle.get("expiry") if straddle
                       else "First eligible Friday after earnings."),
            "max_premium_at_risk": ("The combined debit paid -- the entire "
                                    "premium is at risk if price finishes between "
                                    "the strikes."),
            "exit": "First regular US session after the release.",
        },
        "note": ("A straddle profits from a large move either way; it loses if "
                 "the realised move is smaller than the combined premium or IV "
                 "crushes. Weights are the structure's development settings."),
        "source": SOURCE,
        "status": "OK" if s is not None else "NO_DATA",
    })
    return result
