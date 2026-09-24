"""
Options disparity: how far the options market is leaning, and which way.

A stock's options market is usually balanced -- roughly as much call as put
interest, volume in line with open interest, implied volatility near its own
recent range. Disparity measures the distance from that balance across
thirteen readings, because any one of them alone is noise: a single 5x
volume/OI print is a trader, five of these stretched at once is positioning.

Two numbers come out, and they are deliberately separate:

  * **stretch** (0-100) -- how unusual the options market looks right now,
    ignoring direction. High stretch on its own is a reason to look, not a
    reason to buy.
  * **tilt** (-1..+1) -- which side the imbalance favours, from the readings
    that genuinely carry a direction. Volume/OI and IV rank do not: heavy
    trading and expensive options are loud, not bullish.

Sources: Unusual Whales for the unusual tape across every optionable name (one
five minutes, settles end of day), and the app's own option-chain overview
for gamma and dealer positioning. Anything a provider does not answer is
reported missing rather than scored as balanced -- a reading nobody took is
not evidence of calm.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

_LOG = logging.getLogger(__name__)

SOURCE = "Unusual Whales + option chain"

# A reading has to clear this before it counts as stretched at all. Below it
# the options market is doing what it does every day.
FLAT = 0.15


def _f(value: Any) -> Optional[float]:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _imbalance(left: Optional[float], right: Optional[float]) -> Optional[float]:
    """(left - right) / (left + right), the share difference between two sides."""
    if left is None or right is None:
        return None
    total = left + right
    return None if total <= 0 else (left - right) / total


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _reading(name: str, label: str, value: Optional[float],
             detail: str = "", directional: bool = True,
             missing: str = "") -> dict:
    """One component. ``value`` is -1..+1 for directional, 0..1 for magnitude."""
    return {
        "name": name, "label": label,
        "value": None if value is None else round(value, 3),
        "directional": directional,
        "stretch": None if value is None else round(abs(value), 3),
        "available": value is not None,
        "detail": detail or missing,
        "missing_reason": missing or None,
    }


# ---------------------------------------------------------------------------
# the thirteen readings
# ---------------------------------------------------------------------------


def _from_rows(rows: list) -> list[dict]:
    """The readings that come out of the unusual tape itself."""
    calls = [r for r in rows if r.get("right") == "C"]
    puts = [r for r in rows if r.get("right") == "P"]

    def total(items, field):
        values = [_f(r.get(field)) for r in items]
        return sum(v for v in values if v is not None) or 0.0

    call_volume, put_volume = total(calls, "volume"), total(puts, "volume")
    call_oi, put_oi = total(calls, "open_interest"), total(puts, "open_interest")
    call_prem, put_prem = total(calls, "premium"), total(puts, "premium")

    out = []

    volume_side = _imbalance(call_volume, put_volume)
    out.append(_reading(
        "call_put_volume", "Call vs Put Volume", volume_side,
        f"{call_volume:,.0f} call contracts against {put_volume:,.0f} put"
        if volume_side is not None else "",
        missing="" if volume_side is not None else "No unusual volume today."))

    oi_side = _imbalance(call_oi, put_oi)
    out.append(_reading(
        "call_put_oi", "Call vs Put Open Interest", oi_side,
        f"{call_oi:,.0f} calls open against {put_oi:,.0f} puts"
        if oi_side is not None else "",
        missing="" if oi_side is not None else "No open interest reported."))

    premium_side = _imbalance(call_prem, put_prem)
    out.append(_reading(
        "call_put_premium", "Call vs Put Premium", premium_side,
        f"${call_prem:,.0f} call premium against ${put_prem:,.0f} put"
        if premium_side is not None else "",
        missing="" if premium_side is not None else "No premium estimated."))

    # Volume against open interest: new positioning rather than old. Loud,
    # never bullish or bearish on its own.
    ratios = [_f(r.get("ratio")) for r in rows]
    ratios = [r for r in ratios if r is not None]
    if ratios:
        peak = max(ratios)
        average = sum(ratios) / len(ratios)
        out.append(_reading(
            "volume_vs_oi", "Volume vs Open Interest",
            _clamp(average / 10.0, 0.0, 1.0), directional=False,
            detail=(f"{len(ratios)} contracts averaging {average:.1f}x their "
                    f"open interest, top {peak:.1f}x")))
    else:
        out.append(_reading("volume_vs_oi", "Volume vs Open Interest", None,
                            directional=False,
                            missing="No contract cleared the unusual filter."))

    # Skew: what the two sides are paying in implied volatility. Puts bid over
    # calls is the market paying up for protection.
    call_iv = [_f(r.get("iv")) for r in calls]
    put_iv = [_f(r.get("iv")) for r in puts]
    call_iv = [v for v in call_iv if v is not None]
    put_iv = [v for v in put_iv if v is not None]
    if call_iv and put_iv:
        mean_call = sum(call_iv) / len(call_iv)
        mean_put = sum(put_iv) / len(put_iv)
        skew = _clamp((mean_call - mean_put) / max(mean_put, 1.0), -1.0, 1.0)
        out.append(_reading(
            "iv_skew", "IV Skew", skew,
            f"calls priced at {mean_call:.0f}% implied against puts at "
            f"{mean_put:.0f}%"))
    else:
        out.append(_reading("iv_skew", "IV Skew", None,
                            missing="Implied volatility missing on one side."))

    # Delta imbalance: contracts weighted by how much stock they represent.
    def delta_exposure(items):
        exposure = 0.0
        for row in items:
            delta, volume = _f(row.get("delta")), _f(row.get("volume"))
            if delta is not None and volume is not None:
                exposure += delta * volume * 100
        return exposure

    call_delta, put_delta = delta_exposure(calls), delta_exposure(puts)
    net_delta = call_delta + put_delta  # put deltas are already negative
    gross = abs(call_delta) + abs(put_delta)
    out.append(_reading(
        "delta_imbalance", "Delta Imbalance",
        _clamp(net_delta / gross, -1.0, 1.0) if gross else None,
        f"net {net_delta:,.0f} shares of delta across the unusual tape"
        if gross else "",
        missing="" if gross else "No deltas published for these contracts."))

    # Concentration: is the money spread across the board or piled on one
    # strike and one expiry? Piled is the more meaningful signal.
    def concentration(field):
        weights: dict = {}
        for row in rows:
            key = row.get(field)
            premium = _f(row.get("premium")) or 0.0
            if key is not None and premium:
                weights[key] = weights.get(key, 0.0) + premium
        if not weights:
            return None, None, None
        total_premium = sum(weights.values())
        top_key = max(weights, key=weights.get)
        return weights[top_key] / total_premium, top_key, len(weights)

    share, strike, count = concentration("strike")
    out.append(_reading(
        "strike_concentration", "Strike Concentration", share,
        directional=False,
        detail=(f"{share * 100:.0f}% of premium on the {strike} strike, "
                f"across {count} strikes") if share is not None else "",
        missing="" if share is not None else "No premium to attribute."))

    share, expiry, count = concentration("expiry")
    out.append(_reading(
        "expiry_concentration", "Expiry Concentration", share,
        directional=False,
        detail=(f"{share * 100:.0f}% of premium expiring {expiry}, "
                f"across {count} expiries") if share is not None else "",
        missing="" if share is not None else "No premium to attribute."))

    # Sweeps: orders worked across exchanges to fill now, which is urgency.
    sweeps = [r for r in rows if r.get("sweep_like")]
    if rows:
        sweep_share = len(sweeps) / len(rows)
        sweep_calls = sum(1 for r in sweeps if r.get("right") == "C")
        sweep_puts = len(sweeps) - sweep_calls
        side = _imbalance(float(sweep_calls), float(sweep_puts))
        out.append(_reading(
            "sweeps", "Sweeps & Blocks", side,
            f"{len(sweeps)} of {len(rows)} contracts filled like sweeps "
            f"({sweep_calls} call, {sweep_puts} put), "
            f"{sweep_share * 100:.0f}% of the tape"))
    else:
        out.append(_reading("sweeps", "Sweeps & Blocks", None,
                            missing="No unusual contracts today."))
    return out


def _from_overview(overview: dict, symbol: str = "") -> list[dict]:
    """
    Gamma, dealer positioning and IV rank.

    Gamma and the flip level come from the provider's dealer-exposure
    endpoints; IV rank is published against a year of its own history.
    """
    positioning = (overview or {}).get("positioning") or {}
    metrics = (overview or {}).get("metrics") or {}

    def _provider_iv_rank() -> Optional[float]:
        """
        Where implied volatility sits against its own year, 0 to 100.

        Their rows come back oldest first, so the current reading is the last
        one -- taking the first would have compared today's options against a
        rank struck a week ago and called it today's.
        """
        try:
            import unusualwhales_service as uw

            rows = uw._rows(uw.iv_rank(symbol))
            if not rows:
                return None
            rank = _f(rows[-1].get("iv_rank_1y"))
            if rank is None:
                return None
            # Theirs is a 0-1 fraction; the app talks in 0-100.
            return round(rank * 100, 1) if abs(rank) <= 1 else round(rank, 1)
        except Exception:  # noqa: BLE001
            return None

    out = []

    # ``net_gex`` is what the positioning payload calls it. This looked for
    # "net_gamma" and "gamma_exposure", found neither, and reported the
    # reading as missing while the number sat right there -- two of thirteen
    # readings dark on every symbol.
    gex = _f(positioning.get("net_gex"))
    if gex is not None:
        # Sign matters more than size: positive net gamma means dealers sell
        # rallies and buy dips, which pins price; negative means they chase.
        out.append(_reading(
            "gamma_exposure", "Gamma Exposure", _clamp(gex / 1e9, -1.0, 1.0),
            directional=False,
            detail=(f"net dealer gamma {gex:,.0f}; "
                    + ("dealers dampen moves" if gex > 0 else "dealers amplify moves"))))
    else:
        out.append(_reading("gamma_exposure", "Gamma Exposure", None,
                            directional=False,
                            missing="No gamma reading on the chain."))

    flip = positioning.get("gamma_flip")
    spot = _f((overview or {}).get("spot"))
    flip_value = _f(flip)
    if flip_value and spot:
        distance = (spot - flip_value) / spot
        out.append(_reading(
            "dealer_positioning", "Dealer Positioning",
            _clamp(distance * 10, -1.0, 1.0),
            f"price {spot:.2f} sits {distance * 100:+.1f}% from the gamma flip "
            f"at {flip_value:.2f}"))
    else:
        out.append(_reading("dealer_positioning", "Dealer Positioning", None,
                            missing="Dealer gamma does not change sign across "
                                    "the listed strikes, so there is no flip "
                                    "level to measure against."))

    iv_rank = _f(metrics.get("iv_rank"))
    if iv_rank is None:
        iv_rank = _provider_iv_rank()
    if iv_rank is not None:
        # Distance from the middle of its own year, either way.
        out.append(_reading(
            "iv_vs_history", "IV vs Its Own History",
            _clamp(abs(iv_rank - 50) / 50, 0.0, 1.0), directional=False,
            detail=(f"IV rank {iv_rank:.0f} of 100 -- options are "
                    + ("expensive" if iv_rank > 60 else "cheap" if iv_rank < 40
                       else "middling") + " against their own year")))
    else:
        out.append(_reading("iv_vs_history", "IV vs Its Own History", None,
                            directional=False,
                            missing="No IV rank available."))
    return out


def _flow_against_price(rows: list, day_change_pct: Optional[float]) -> dict:
    """
    The options tape against what the stock actually did.

    Divergence is the point: heavy call buying on a day the stock fell is a
    different statement from heavy call buying on a day it rose. Agreement
    scores nothing here -- it is already in every other reading.
    """
    calls = sum(_f(r.get("premium")) or 0 for r in rows if r.get("right") == "C")
    puts = sum(_f(r.get("premium")) or 0 for r in rows if r.get("right") == "P")
    side = _imbalance(calls, puts)
    if side is None or day_change_pct is None:
        return _reading("flow_vs_price", "Flow vs Price", None,
                        missing="No price move or no premium to compare.")

    if side > FLAT and day_change_pct < -0.5:
        value, detail = side, (f"calls bought into a {day_change_pct:+.2f}% "
                               "fall -- the tape disagrees with the price")
    elif side < -FLAT and day_change_pct > 0.5:
        value, detail = side, (f"puts bought into a {day_change_pct:+.2f}% "
                               "rise -- the tape disagrees with the price")
    else:
        value, detail = 0.0, (f"tape and price agree ({day_change_pct:+.2f}% "
                              "on the day)")
    return _reading("flow_vs_price", "Flow vs Price", value, detail)


# ---------------------------------------------------------------------------
# the composite
# ---------------------------------------------------------------------------


def get_disparity(symbol: str, day_change_pct: Optional[float] = None) -> dict:
    """Every reading, plus the two numbers that summarise them."""
    import unusualwhales_service as uw

    symbol = (symbol or "").upper().strip()
    flow = uw.tape_rows(symbol, limit=60)
    rows = flow.get("rows") or []

    overview = {}
    try:
        import live_options_analytics as options

        overview = options.get_overview(symbol) or {}
    except Exception as exc:  # noqa: BLE001 - the chain is optional here
        # Named, not swallowed. This imported live_options_service, which has
        # no get_overview -- the AttributeError landed here every time and
        # left the overview empty, so Gamma Exposure and Dealer Positioning
        # reported "no reading" on every symbol while the figures were
        # sitting in the payload one module over.
        overview = {}
        _LOG.warning("disparity: overview unavailable for %s: %s", symbol, exc)

    if day_change_pct is None:
        try:
            import live_market_service as market

            day_change_pct = _f((market.get_quote(symbol) or {}).get("change_percent"))
        except Exception:  # noqa: BLE001
            day_change_pct = None

    readings = _from_rows(rows) + _from_overview(overview, symbol)
    readings.append(_flow_against_price(rows, day_change_pct))

    available = [r for r in readings if r["available"]]
    if not available:
        return {
            "symbol": symbol, "status": flow.get("status", "NO_DATA"),
            "readings": readings, "stretch": None, "tilt": None,
            "detail": (flow.get("detail")
                       or "No options reading could be taken for this symbol."),
            "source": SOURCE,
        }

    stretched = [r for r in available if r["stretch"] >= FLAT]
    stretch = round(sum(r["stretch"] for r in available) / len(available) * 100, 1)

    directional = [r for r in available if r["directional"]]
    tilt = (round(sum(r["value"] for r in directional) / len(directional), 3)
            if directional else None)

    leaning = ("calls" if tilt is not None and tilt > FLAT
               else "puts" if tilt is not None and tilt < -FLAT else "neither side")

    return {
        "symbol": symbol,
        "status": "OK",
        "stretch": stretch,
        "tilt": tilt,
        "leaning": leaning,
        "readings": readings,
        "readings_available": len(available),
        "readings_total": len(readings),
        "readings_stretched": len(stretched),
        "coverage_pct": round(len(available) / len(readings) * 100, 1),
        "headline": (
            f"{len(stretched)} of {len(available)} readings stretched, "
            f"leaning {leaning}"),
        "detail": (
            "Stretch is how far the options market sits from its usual "
            "balance, ignoring direction; tilt is which side the readings "
            "that carry a direction favour. Volume against open interest, IV "
            "rank, gamma and concentration count towards stretch only -- "
            "they are loud, not bullish."
        ),
        "day_change_pct": day_change_pct,
        "source": SOURCE,
    }
