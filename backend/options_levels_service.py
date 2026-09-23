"""
The levels and structure a flow desk reads off a chain.

Everything here comes from one chain load: open-interest structure, dealer
gamma by strike and where it flips, the key levels with the distance to each,
the expected move, the Greek profile, and a directional score reported
separately from how much of it was actually measurable.

Confidence is kept apart from the score on purpose. A score of 64 built from
five signals and a score of 64 built from one are the same number and very
different claims, and folding the second fact into the first destroys it.
"""

from __future__ import annotations

from typing import Any, Optional

import live_market_service as market
import live_options_service as options

LEVELS_TTL_OPEN = 120.0
LEVELS_TTL_CLOSED = 900.0

# Contract multiplier. Gamma is published per share; exposure is per contract.
MULTIPLIER = 100

# A wall is the strike carrying the most open interest on its side. Below this
# share of the side's total it is not a wall, it is just the biggest of a flat
# distribution, and calling it a level would invent a level.
WALL_MIN_SHARE = 0.08


def _num(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _distance(level: Optional[float], spot: Optional[float]) -> Optional[float]:
    """Percent from spot to a level, signed: positive means the level is above."""
    if level is None or not spot:
        return None
    return round((level - spot) / spot * 100.0, 2)


def _oi_structure(rows: list[dict], spot: Optional[float]) -> dict:
    """Open interest by strike, the walls, and the day's change."""
    by_strike: dict[float, dict] = {}
    call_oi = put_oi = 0.0
    call_change = put_change = 0.0

    for row in rows:
        strike = _num(row.get("strike"))
        oi = _num(row.get("open_interest")) or 0.0
        change = _num(row.get("oi_change")) or 0.0
        if strike is None:
            continue
        entry = by_strike.setdefault(strike, {"strike": strike, "calls": 0.0,
                                              "puts": 0.0, "change": 0.0})
        if str(row.get("right") or "").upper().startswith("C"):
            entry["calls"] += oi
            call_oi += oi
            call_change += change
        else:
            entry["puts"] += oi
            put_oi += oi
            put_change += change
        entry["change"] += change

    strikes = sorted(by_strike.values(), key=lambda e: e["strike"])

    def wall(side: str, total: float) -> Optional[float]:
        if not strikes or total <= 0:
            return None
        best = max(strikes, key=lambda e: e[side])
        return best["strike"] if best[side] / total >= WALL_MIN_SHARE else None

    return {
        "strikes": strikes,
        "call_oi": round(call_oi),
        "put_oi": round(put_oi),
        "total_oi": round(call_oi + put_oi),
        "put_call_oi": (round(put_oi / call_oi, 2) if call_oi else None),
        "call_wall": wall("calls", call_oi),
        "put_wall": wall("puts", put_oi),
        "call_oi_change": round(call_change),
        "put_oi_change": round(put_change),
        "net_oi_change": round(call_change + put_change),
        "peak": max((e["calls"] + e["puts"] for e in strikes), default=0.0),
    }


def _gamma_profile(rows: list[dict], spot: Optional[float]) -> dict:
    """
    Dealer gamma by strike, and the price where the sign flips.

    Sign convention: dealers are assumed short the calls and long the puts that
    customers buy, so call gamma is positive exposure and put gamma negative.
    The flip is the strike where the running total crosses zero -- above it
    dealers hedge against the move and dampen it, below it they hedge with the
    move and amplify it.
    """
    per_strike: dict[float, float] = {}
    call_gex = put_gex = 0.0

    for row in rows:
        strike = _num(row.get("strike"))
        gamma = _num(row.get("gamma"))
        oi = _num(row.get("open_interest"))
        if strike is None or gamma is None or not oi or not spot:
            continue
        # Exposure in dollars per 1% move, the convention every GEX chart uses.
        exposure = gamma * oi * MULTIPLIER * spot * spot * 0.01
        if str(row.get("right") or "").upper().startswith("C"):
            per_strike[strike] = per_strike.get(strike, 0.0) + exposure
            call_gex += exposure
        else:
            per_strike[strike] = per_strike.get(strike, 0.0) - exposure
            put_gex -= exposure

    if not per_strike:
        return {"status": "NO_DATA",
                "detail": "The chain carries no gamma or open interest."}

    strikes = sorted(per_strike.items())
    running = 0.0
    flip = None
    previous_strike = None
    previous_running = 0.0
    curve = []
    for strike, exposure in strikes:
        running += exposure
        curve.append({"strike": strike, "gex": round(exposure),
                      "cumulative": round(running)})
        if (previous_strike is not None
                and (previous_running <= 0 < running
                     or previous_running >= 0 > running)):
            # Linear interpolation between the two strikes that straddle zero,
            # rather than naming whichever strike happens to sit nearest: the
            # flip is a price, and strikes are five dollars apart.
            span = running - previous_running
            if span:
                ratio = -previous_running / span
                flip = round(previous_strike
                             + (strike - previous_strike) * ratio, 2)
        previous_strike, previous_running = strike, running

    net_gex = call_gex + put_gex
    concentrations = sorted(
        ({"strike": s, "gex": round(e)} for s, e in strikes),
        key=lambda e: -abs(e["gex"]))[:5]

    return {
        "status": "OK",
        "call_gex": round(call_gex),
        "put_gex": round(put_gex),
        "net_gex": round(net_gex),
        "regime": "LONG_GAMMA" if net_gex > 0 else "SHORT_GAMMA",
        "regime_detail": (
            "Dealers are long gamma: hedging leans against moves, which tends "
            "to compress realised volatility."
            if net_gex > 0 else
            "Dealers are short gamma: hedging goes with moves, which tends to "
            "amplify them."),
        "gamma_flip": flip,
        "gamma_flip_distance": _distance(flip, spot),
        "curve": curve,
        "concentrations": concentrations,
    }


def _max_pain(rows: list[dict]) -> Optional[float]:
    """
    The strike where the most open contracts expire worthless.

    Computed by settling the whole chain at every strike and taking the
    cheapest outcome for option holders, which is the definition -- not the
    strike with the most open interest, which is a different thing that is
    often confused with it.
    """
    strikes = sorted({_num(r.get("strike")) for r in rows
                      if _num(r.get("strike")) is not None})
    if len(strikes) < 3:
        return None

    best_strike = None
    best_value = None
    for settle in strikes:
        total = 0.0
        for row in rows:
            strike = _num(row.get("strike"))
            oi = _num(row.get("open_interest")) or 0.0
            if strike is None or not oi:
                continue
            if str(row.get("right") or "").upper().startswith("C"):
                total += max(settle - strike, 0.0) * oi
            else:
                total += max(strike - settle, 0.0) * oi
        if best_value is None or total < best_value:
            best_value, best_strike = total, settle
    return best_strike


def _greeks(rows: list[dict]) -> dict:
    """Open-interest-weighted Greeks, so one illiquid strike cannot dominate."""
    fields = ("delta", "gamma", "theta", "vega")
    totals = {f: 0.0 for f in fields}
    weight = 0.0
    missing: set[str] = set()

    for row in rows:
        oi = _num(row.get("open_interest")) or 0.0
        if not oi:
            continue
        values = {f: _num(row.get(f)) for f in fields}
        if any(v is None for v in values.values()):
            missing.update(f for f, v in values.items() if v is None)
            continue
        sign = 1.0 if str(row.get("right") or "").upper().startswith("C") else -1.0
        totals["delta"] += values["delta"] * oi
        totals["gamma"] += values["gamma"] * oi
        totals["theta"] += values["theta"] * oi
        totals["vega"] += values["vega"] * oi
        weight += oi
        del sign

    if not weight:
        return {"status": "NO_DATA",
                "detail": ("The chain publishes no Greeks"
                           + (f" ({', '.join(sorted(missing))} absent)"
                              if missing else "") + ".")}

    return {
        "status": "OK",
        "weighted_by": "open interest",
        "contracts": round(weight),
        **{f: round(totals[f] / weight, 4) for f in fields},
    }


def _confidence(sentiment: dict, oi: dict, gamma: dict) -> dict:
    """
    How much of the evidence was actually measurable, kept out of the score.

    Two parts, deliberately separate from direction:

    * coverage -- the share of the intended signal weight that returned a
      value at all;
    * agreement -- how tightly those signals point the same way. Five signals
      averaging 60 because they all read 60 is a different claim from five
      averaging 60 because half read 95 and half read 25.
    """
    components = sentiment.get("components") or []
    if not components:
        return {"status": "NO_DATA", "confidence": None,
                "detail": "No directional signal was available."}

    coverage = min(sum(c.get("weight") or 0 for c in components), 100.0)

    scores = [c.get("score") for c in components if c.get("score") is not None]
    if len(scores) >= 2:
        mean = sum(scores) / len(scores)
        spread = (sum((s - mean) ** 2 for s in scores) / len(scores)) ** 0.5
        # A standard deviation of 25 points across signals is disagreement;
        # zero is unanimity.
        agreement = max(0.0, 100.0 - spread / 25.0 * 100.0)
    else:
        agreement = 50.0

    structure = 100.0 if (oi.get("total_oi") and gamma.get("status") == "OK") else 55.0
    confidence = coverage * 0.45 + agreement * 0.35 + structure * 0.20

    return {
        "status": "OK",
        "confidence": round(confidence),
        "coverage": round(coverage),
        "agreement": round(agreement),
        "signals": len(components),
        "detail": (f"{len(components)} signals covering {round(coverage)}% of "
                   f"the intended weight, agreeing to {round(agreement)}%."),
    }


def get_levels(symbol: str, expiry: Optional[str] = None) -> dict:
    """Open interest, gamma, key levels, expected move, Greeks and the score."""
    symbol = symbol.upper()
    key = f"levels:{symbol}:{expiry or 'front'}"
    cached = market.cache.get(key, market.session_ttl(LEVELS_TTL_OPEN,
                                                      LEVELS_TTL_CLOSED))
    if cached:
        return cached

    chain = options.load_chain(symbol, expiry)
    rows = (chain or {}).get("rows") or []
    if not rows:
        return {
            "symbol": symbol,
            "status": (chain or {}).get("status") or "NO_CHAIN",
            "detail": ((chain or {}).get("error")
                       or f"No option chain available for {symbol}."),
        }

    spot = _num(chain.get("spot"))
    oi = _oi_structure(rows, spot)
    gamma = _gamma_profile(rows, spot)
    max_pain = _max_pain(rows)

    import live_options_analytics as analytics

    metrics = analytics.get_metrics(chain, options.get_iv_history(symbol), None)
    # get_sentiment takes the metrics dict, not None: it reads 25-delta skew
    # out of it, and passing None made the whole panel fail on an attribute
    # error rather than degrade by one signal.
    sentiment = analytics.get_sentiment(
        chain, analytics.get_tiles(chain), metrics or {})
    expiration_flow = analytics.get_expiration_flow(chain)

    levels = [
        {"name": "Max pain", "value": max_pain,
         "distance": _distance(max_pain, spot)},
        {"name": "Call wall", "value": oi.get("call_wall"),
         "distance": _distance(oi.get("call_wall"), spot)},
        {"name": "Put wall", "value": oi.get("put_wall"),
         "distance": _distance(oi.get("put_wall"), spot)},
        {"name": "Gamma flip", "value": gamma.get("gamma_flip"),
         "distance": gamma.get("gamma_flip_distance")},
    ]

    # Issuer identity, from the EDGAR profiles already cached for the
    # calendar. Free, and the only source here that actually knows a company's
    # registered name rather than a ticker abbreviation.
    profile = {}
    try:
        import company_profile_service as profiles

        profile = profiles.get_profiles([symbol]).get(symbol) or {}
    except Exception:  # noqa: BLE001 - a name is decoration, not the panel
        profile = {}

    result = {
        "symbol": symbol,
        "status": "OK",
        "company": profile.get("company_name"),
        "sector": profile.get("sector"),
        "industry": profile.get("industry"),
        "spot": spot,
        "expiry": chain.get("expiry"),
        "expiry_label": chain.get("expiry_label"),
        "dte": chain.get("dte"),
        "expirations": chain.get("expirations") or [],
        "source": chain.get("source"),
        "open_interest": oi,
        "gamma": gamma,
        "levels": levels,
        "max_pain": max_pain,
        "expected_move": {
            "percent": metrics.get("expected_move_percent"),
            "dollars": metrics.get("expected_move_dollars"),
            "straddle": metrics.get("atm_straddle"),
            "range": metrics.get("expected_range"),
            "implied_volatility": metrics.get("implied_volatility"),
            "iv_rank": metrics.get("iv_rank"),
            "iv_percentile": metrics.get("iv_percentile"),
            "status": metrics.get("status"),
        },
        "greeks": _greeks(rows),
        "expiration_flow": expiration_flow,
        "score": {
            "value": sentiment.get("score"),
            "label": sentiment.get("label"),
            "components": sentiment.get("components"),
            "status": sentiment.get("status"),
        },
        "confidence": _confidence(sentiment, oi, gamma),
    }
    market.cache.put(key, result)
    return result
