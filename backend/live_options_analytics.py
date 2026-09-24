"""
Aggregations over the live option chain.

Every panel on the Options Flow screen resolves to a function here. All inputs
come from live_options_service; nothing is invented. Values
that cannot be computed are returned as None so the UI can show a dash rather
than a fabricated number.
"""

from __future__ import annotations

SOURCE_UW = "Unusual Whales"

import asyncio
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any, Optional


import live_options_service as svc

# How long the options page will wait for the realized-move comparison before
# rendering without it. Everything else on the page is already resolved by
# this point, so a long wait here buys one number at the cost of the screen.
REALIZED_MOVE_BUDGET = 5.0

# Ceilings for the provider fetches that share one rate limit. Paging
# through symbols queues these behind one another, and an unbounded wait turned
# a five-second page into fifty.
PROVIDER_BUDGET = 8.0
FLOW_BUDGET = 12.0
from live_market_service import cache, market_clock, num


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _tape_map(flow: Optional[dict]) -> dict[tuple, float]:
    """(expiry, strike, right) -> contracts actually printed on the tape."""
    if not flow:
        return {}
    return {
        (e["expiry"], e["strike"], e["right"]): e["contracts"]
        for e in flow.get("tape_volume", [])
    }


def _effective_volume(rows: list[dict], tape: dict[tuple, float]) -> tuple[str, dict]:
    """
    Pick the volume series to display.

    The feed reports zero session volume before the open and after the close. The
    tape still holds the previous session's real prints, so it is used instead
    and the basis is labelled so the UI can say which one it is showing.
    """
    live_total = sum(r.get("volume") or 0.0 for r in rows)
    if live_total > 0:
        return "SESSION", {
            (r["expiry"], r["strike"], r["right"]): (r.get("volume") or 0.0)
            for r in rows
        }
    if tape:
        return "LAST_SESSION_TAPE", tape
    return "NONE", {}


def _sum(rows: list[dict], field: str, right: Optional[str] = None) -> float:
    return sum(
        (r.get(field) or 0.0)
        for r in rows
        if right is None or r["right"] == right
    )


def _atm(rows: list[dict], spot: float, right: str) -> Optional[dict]:
    candidates = [r for r in rows if r["right"] == right and r.get("mid")]
    if not candidates:
        return None
    return min(candidates, key=lambda r: abs(r["strike"] - spot))


def _nearest_delta(rows: list[dict], right: str, target: float) -> Optional[dict]:
    candidates = [
        r for r in rows
        if r["right"] == right and r.get("delta") is not None and r.get("iv")
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda r: abs(abs(r["delta"]) - abs(target)))


def _pct(part: float, whole: float) -> Optional[float]:
    if not whole:
        return None
    return round(part / whole * 100.0, 1)


# ---------------------------------------------------------------------------
# headline tiles
# ---------------------------------------------------------------------------


def _chain_source(chain: Optional[dict]) -> str:
    """
    Which provider actually supplied the chain these numbers came from.

    Hardcoding a provider name was wrong the moment a second one appeared
    source: every panel claimed a broker feed it had not used, which is the
    one field an operator checks when a number looks wrong.
    """
    return str((chain or {}).get("source") or SOURCE_UW)


def get_tiles(
    chain: dict,
    volume_context: Optional[dict] = None,
    flow: Optional[dict] = None,
) -> dict:
    rows = chain.get("rows", []) + chain.get("other_expiry_rows", [])
    if not rows:
        return {"tiles": [], "status": chain.get("status", "NO_DATA")}

    basis, vol_map = _effective_volume(rows, _tape_map(flow))
    call_vol = sum(
        v for k, v in vol_map.items() if k[2] == "C"
    )
    put_vol = sum(v for k, v in vol_map.items() if k[2] == "P")
    total_vol = call_vol + put_vol
    call_oi = _sum(rows, "open_interest", "C")
    put_oi = _sum(rows, "open_interest", "P")
    total_oi = call_oi + put_oi
    if basis == "SESSION":
        notional = _sum(rows, "notional")
    else:
        mids = {
            (r["expiry"], r["strike"], r["right"]): (r.get("mid") or 0.0)
            for r in rows
        }
        notional = sum(v * mids.get(k, 0.0) * 100 for k, v in vol_map.items())

    pc_vol = round(put_vol / call_vol, 2) if call_vol else None
    pc_oi = round(put_oi / call_oi, 2) if call_oi else None

    ctx = volume_context or {}

    return {
        "call_volume": call_vol,
        "put_volume": put_vol,
        "total_volume": total_vol,
        "call_share": _pct(call_vol, total_vol),
        "put_share": _pct(put_vol, total_vol),
        "call_oi": call_oi,
        "put_oi": put_oi,
        "total_oi": total_oi,
        "notional": notional,
        "put_call_volume": pc_vol,
        "put_call_oi": pc_oi,
        "call_oi_vs_put_oi": round(call_oi / put_oi, 2) if put_oi else None,
        "call_volume_change": ctx.get("call_change"),
        "put_volume_change": ctx.get("put_change"),
        "total_volume_change": ctx.get("total_change"),
        "oi_change": ctx.get("oi_change"),
        "notional_change": ctx.get("notional_change"),
        "volume_basis": basis,
        "put_call_bias": (
            "Bullish" if pc_vol is not None and pc_vol < 0.9
            else "Bearish" if pc_vol is not None and pc_vol > 1.1
            else "Neutral" if pc_vol is not None else None
        ),
        "status": "OK",
    }


# ---------------------------------------------------------------------------
# volume / OI / delta exposure by strike
# ---------------------------------------------------------------------------


def get_volume_by_strike(
    chain: dict, mode: str = "volume", flow: Optional[dict] = None
) -> dict:
    rows = chain.get("rows", [])
    mode = (mode or "volume").lower()
    if not rows:
        return {"mode": mode, "strikes": [], "status": chain.get("status", "NO_DATA")}

    mult = chain.get("multiplier", 100)
    basis, vol_map = _effective_volume(rows, _tape_map(flow))

    def value(r: dict) -> float:
        if mode == "open_interest":
            return r.get("open_interest") or 0.0
        if mode == "delta_exposure":
            d = r.get("delta")
            oi = r.get("open_interest") or 0.0
            return abs(d) * oi * mult * (chain.get("spot") or 0.0) if d else 0.0
        return vol_map.get((r["expiry"], r["strike"], r["right"]), 0.0)

    by_strike: dict[float, dict] = {}
    for r in rows:
        slot = by_strike.setdefault(
            r["strike"], {"strike": r["strike"], "calls": 0.0, "puts": 0.0}
        )
        if r["right"] == "C":
            slot["calls"] += value(r)
        else:
            slot["puts"] += value(r)

    strikes = sorted(by_strike.values(), key=lambda s: s["strike"])
    peak = max((max(s["calls"], s["puts"]) for s in strikes), default=0.0)

    return {
        "mode": mode,
        "basis": basis if mode == "volume" else "OPEN_INTEREST",
        "spot": chain.get("spot"),
        "expiry_label": chain.get("expiry_label"),
        "peak": peak,
        "strikes": [
            {
                "strike": s["strike"],
                "calls": round(s["calls"], 2),
                "puts": round(s["puts"], 2),
            }
            for s in strikes
        ],
        "status": "OK",
    }


# ---------------------------------------------------------------------------
# key options metrics table
# ---------------------------------------------------------------------------


def get_metrics(
    chain: dict,
    iv_history: Optional[dict] = None,
    realized: Optional[dict] = None,
    flow: Optional[dict] = None,
) -> dict:
    rows = chain.get("rows", [])
    spot = chain.get("spot")
    if not rows or not spot:
        return {"status": chain.get("status", "NO_DATA")}

    atm_call = _atm(rows, spot, "C")
    atm_put = _atm(rows, spot, "P")

    straddle = None
    if atm_call and atm_put and atm_call["mid"] and atm_put["mid"]:
        straddle = round(atm_call["mid"] + atm_put["mid"], 2)

    expected_pct = round(straddle / spot * 100, 2) if straddle else None

    # ATM implied vol: average the two ATM legs when both solved.
    ivh = iv_history or {}

    atm_ivs = [r["iv"] for r in (atm_call, atm_put) if r and r.get("iv")]
    atm_iv = round(sum(atm_ivs) / len(atm_ivs) * 100, 2) if atm_ivs else None

    call25 = _nearest_delta(rows, "C", 0.25)
    put25 = _nearest_delta(rows, "P", 0.25)
    skew = None
    skew_source = None
    if call25 and put25:
        candidate = round((call25["iv"] - put25["iv"]) * 100, 2)
        # Locally solved IV is unusable on near-dated chains: a 0-DTE wing has
        # almost no extrinsic value, so Black-Scholes inverts to hundreds of
        # vol points. Left unchecked that clamps the sentiment skew component
        # to a confident 100 -- a fabricated signal, not a strong one. Anything
        # outside a plausible equity band is discarded rather than clamped.
        if -50.0 <= candidate <= 50.0:
            skew = candidate
            skew_source = "chain"

    # A constant-maturity 30-day skew from the provider beats whatever the
    # front expiry happens to imply, so it wins when present.
    provider_skew = ivh.get("skew_25d_30d") if isinstance(ivh, dict) else None
    if provider_skew is not None:
        skew = round(float(provider_skew) * 100, 2)
        # Named from the payload that carried it, not from a constant. This
        # read "OptionData 25d/30d" long after OptionData was gone -- and it
        # is a tooltip the operator sees, so it was telling them the figure
        # came from a provider this app no longer calls.
        skew_source = f"{ivh.get('source') or SOURCE_UW} 25d/30d"

    _, vol_map = _effective_volume(rows, _tape_map(flow))
    call_vol = sum(v for k, v in vol_map.items() if k[2] == "C")
    put_vol = sum(v for k, v in vol_map.items() if k[2] == "P")
    call_oi = _sum(rows, "open_interest", "C")
    put_oi = _sum(rows, "open_interest", "P")

    return {
        "symbol": chain.get("symbol"),
        "spot": spot,
        "expiry_label": chain.get("expiry_label"),
        "dte": chain.get("dte"),
        "implied_volatility": atm_iv if atm_iv is not None else ivh.get("iv"),
        "iv_percentile": ivh.get("iv_percentile"),
        "iv_rank": ivh.get("iv_rank"),
        "historical_volatility": ivh.get("hv"),
        "atm_call_price": atm_call["mid"] if atm_call else None,
        "atm_put_price": atm_put["mid"] if atm_put else None,
        "atm_call_strike": atm_call["strike"] if atm_call else None,
        "atm_put_strike": atm_put["strike"] if atm_put else None,
        "atm_straddle": straddle,
        "expected_move_dollars": straddle,
        "expected_move_percent": expected_pct,
        "expected_range": {
            "lower": round(spot - straddle, 2) if straddle else None,
            "upper": round(spot + straddle, 2) if straddle else None,
        },
        "realized_move_percent": (realized or {}).get("average"),
        "realized_windows": (realized or {}).get("windows"),
        "skew_25d": skew,
        "skew_source": skew_source,
        "iv_rank_source": ivh.get("source"),
        "iv_as_of": ivh.get("as_of"),
        "rv20": ivh.get("rv20"),
        "rv30": ivh.get("rv30"),
        "put_call_volume": round(put_vol / call_vol, 2) if call_vol else None,
        "put_call_oi": round(put_oi / call_oi, 2) if call_oi else None,
        "call_oi_vs_put_oi": round(call_oi / put_oi, 2) if put_oi else None,
        "status": "OK",
        "source": _chain_source(chain),
    }


# ---------------------------------------------------------------------------
# risk zones: call wall, put wall, max pain
# ---------------------------------------------------------------------------


def _max_pain_curve(positioning: Optional[dict], spot: Optional[float],
                    span: float = 0.25, points: int = 60) -> list[dict]:
    """
    Trim the provider's payout curve to strikes worth drawing.

    It arrives covering every listed strike from 5 to well past spot, which
    renders as a near-straight line with the interesting part invisible. Only
    the band around spot says anything about where pain actually turns.
    """
    curve = (positioning or {}).get("max_pain_curve")
    if not curve or not spot:
        return []

    low, high = spot * (1 - span), spot * (1 + span)
    inside = [
        {"strike": float(p["strike"]), "payout": float(p["payout"])}
        for p in curve
        if p.get("strike") is not None and p.get("payout") is not None
        and low <= float(p["strike"]) <= high
    ]
    if not inside:
        return []

    # Keep the shape but cap the point count for the chart.
    if len(inside) > points:
        step = len(inside) / points
        inside = [inside[int(i * step)] for i in range(points)]

    floor = min(p["payout"] for p in inside)
    for p in inside:
        # Payouts are tens of billions; the curve only matters relatively.
        p["relative"] = round(p["payout"] - floor)
    return inside


def get_risk_zones(chain: dict, positioning: Optional[dict] = None) -> dict:
    rows = chain.get("rows", [])
    spot = chain.get("spot")
    if not rows or not spot:
        return {"status": chain.get("status", "NO_DATA")}

    calls = [r for r in rows if r["right"] == "C" and r.get("open_interest")]
    puts = [r for r in rows if r["right"] == "P" and r.get("open_interest")]

    call_wall = max(calls, key=lambda r: r["open_interest"]) if calls else None
    put_wall = max(puts, key=lambda r: r["open_interest"]) if puts else None

    # Max pain: the strike at which the aggregate intrinsic value owed to
    # option holders at expiry is smallest.
    strikes = sorted({r["strike"] for r in rows})
    max_pain = None
    if strikes:
        pain = {}
        for k in strikes:
            total = 0.0
            for r in calls:
                if k > r["strike"]:
                    total += (k - r["strike"]) * r["open_interest"]
            for r in puts:
                if k < r["strike"]:
                    total += (r["strike"] - k) * r["open_interest"]
            pain[k] = total
        max_pain = min(pain, key=pain.get)

    return {
        "symbol": chain.get("symbol"),
        "spot": spot,
        "expiry_label": chain.get("expiry_label"),
        "call_wall": call_wall["strike"] if call_wall else None,
        "call_wall_oi": call_wall["open_interest"] if call_wall else None,
        "put_wall": put_wall["strike"] if put_wall else None,
        "put_wall_oi": put_wall["open_interest"] if put_wall else None,
        "max_pain": max_pain,
        "max_pain_curve": _max_pain_curve(positioning, spot),
        "status": "OK",
        "source": _chain_source(chain),
    }


# ---------------------------------------------------------------------------
# order flow breakdown (donut)
# ---------------------------------------------------------------------------


def get_breakdown(flow: dict, chain: dict) -> dict:
    trades = flow.get("trades", []) if flow else []

    if not trades:
        # Fall back to resting open interest so the panel still says something
        # true when the tape is empty (pre-market / no prints yet).
        rows = chain.get("rows", []) + chain.get("other_expiry_rows", [])
        call_oi = _sum(rows, "open_interest", "C")
        put_oi = _sum(rows, "open_interest", "P")
        total = call_oi + put_oi
        if not total:
            return {"segments": [], "total": 0, "status": "NO_DATA"}
        return {
            "basis": "OPEN_INTEREST",
            "total": total,
            "total_label": "Open Interest",
            "segments": [
                {"label": "Call OI", "value": call_oi, "percent": _pct(call_oi, total)},
                {"label": "Put OI", "value": put_oi, "percent": _pct(put_oi, total)},
            ],
            "status": "OK",
            "note": "No prints on the tape yet; showing resting open interest.",
        }

    buckets = {
        "Call Buys": 0.0,
        "Call Sells": 0.0,
        "Put Buys": 0.0,
        "Put Sells": 0.0,
        "Mid / Unclassified": 0.0,
    }
    for t in trades:
        size = t["contracts"]
        is_call = t["right"] == "C"
        side = t["side"].upper()
        if side == "BUY":
            buckets["Call Buys" if is_call else "Put Buys"] += size
        elif side == "SELL":
            buckets["Call Sells" if is_call else "Put Sells"] += size
        else:
            buckets["Mid / Unclassified"] += size

    total = sum(buckets.values())
    return {
        "basis": "TAPE",
        "total": total,
        "total_label": "Contracts",
        "segments": [
            {"label": k, "value": v, "percent": _pct(v, total)}
            for k, v in buckets.items()
            if v > 0
        ],
        "status": "OK" if total else "NO_DATA",
    }


# ---------------------------------------------------------------------------
# flow by expiration
# ---------------------------------------------------------------------------


def get_expiration_flow(chain: dict, flow: Optional[dict] = None) -> dict:
    rows = chain.get("rows", []) + chain.get("other_expiry_rows", [])
    if not rows:
        return {"expirations": [], "status": chain.get("status", "NO_DATA")}

    basis, vol_map = _effective_volume(rows, _tape_map(flow))

    def build(getter) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for r in rows:
            slot = out.setdefault(
                r["expiry"],
                {
                    "expiry": r["expiry"],
                    "label": r["expiry_label"][:5],
                    "full_label": r["expiry_label"],
                    "dte": r["dte"],
                    "calls": 0.0,
                    "puts": 0.0,
                },
            )
            v = getter(r)
            if r["right"] == "C":
                slot["calls"] += v
            else:
                slot["puts"] += v
        return out

    by_exp = build(lambda r: vol_map.get((r["expiry"], r["strike"], r["right"]), 0.0))

    # The tape only samples the busiest contracts, which nearly always sit on
    # the front expiry. A single-bar "by expiration" chart says nothing, so
    # when traded volume covers fewer than two expiries fall back to resting
    # open interest, which is populated across every expiry we quoted.
    covered = sum(1 for e in by_exp.values() if e["calls"] or e["puts"])
    if covered < 2:
        oi = build(lambda r: r.get("open_interest") or 0.0)
        if sum(1 for e in oi.values() if e["calls"] or e["puts"]) >= 2:
            by_exp = oi
            basis = "OPEN_INTEREST"

    out = sorted(by_exp.values(), key=lambda e: e["expiry"])
    return {
        "expirations": out,
        "basis": basis,
        "peak": max((max(e["calls"], e["puts"]) for e in out), default=0.0),
        "status": "OK",
        "source": _chain_source(chain),
    }


# ---------------------------------------------------------------------------
# scatter points for the real-time flow chart
# ---------------------------------------------------------------------------


def get_scatter(flow: dict, chain: dict) -> dict:
    trades = flow.get("trades", []) if flow else []
    points = []
    for t in trades:
        points.append(
            {
                "time": t["time"],
                "timestamp": t["timestamp"],
                "epoch": t["epoch"],
                "strike": t["strike"],
                "right": t["right"],
                "type": t["type"],
                "contracts": t["contracts"],
                "price": t["price"],
                "notional": t["notional"],
                "kind": t["kind"],
                "side": t["side"],
                "sentiment": t["sentiment"],
                "expiry_label": t["expiry_label"],
                "is_large": t["notional"] >= 250_000 or t["kind"] in ("SWEEP", "BLOCK"),
            }
        )
    points.sort(key=lambda p: p["epoch"])

    strikes = [p["strike"] for p in points]
    return {
        "points": points,
        "spot": chain.get("spot"),
        "strike_min": min(strikes) if strikes else None,
        "strike_max": max(strikes) if strikes else None,
        "status": "OK" if points else flow.get("status", "NO_DATA"),
        "note": flow.get("note"),
        "source": _chain_source(chain),
    }


# ---------------------------------------------------------------------------
# sentiment gauge
# ---------------------------------------------------------------------------


def get_sentiment(chain: dict, flow: dict, metrics: dict,
                  positioning: Optional[dict] = None) -> dict:
    rows = chain.get("rows", []) + chain.get("other_expiry_rows", [])
    if not rows:
        return {"score": None, "label": "NO DATA", "status": "NO_DATA"}

    signals: list[tuple[str, float, float]] = []  # (name, score 0-100, weight)

    _, vol_map = _effective_volume(rows, _tape_map(flow))
    call_vol = sum(v for k, v in vol_map.items() if k[2] == "C")
    put_vol = sum(v for k, v in vol_map.items() if k[2] == "P")
    if call_vol + put_vol > 0:
        signals.append(
            ("Call share of volume", call_vol / (call_vol + put_vol) * 100.0, 0.30)
        )

    call_oi = _sum(rows, "open_interest", "C")
    put_oi = _sum(rows, "open_interest", "P")
    if call_oi + put_oi > 0:
        signals.append(
            ("Call share of open interest", call_oi / (call_oi + put_oi) * 100.0, 0.20)
        )

    trades = flow.get("trades", []) if flow else []
    if trades:
        bull = sum(t["notional"] for t in trades if t["sentiment"] == "Bullish")
        total = sum(t["notional"] for t in trades)
        if total:
            signals.append(("Bullish premium on the tape", bull / total * 100.0, 0.35))

    skew = metrics.get("skew_25d")
    if skew is not None:
        # Equity skew is normally negative; less negative = less downside demand.
        signals.append(("25-delta skew", max(0.0, min(100.0, 50.0 + skew * 4)), 0.15))

    # Premium is a better read on conviction than contract counts: a thousand
    # cheap lottery calls and one large in-the-money call both move the volume
    # ratio, but only the second moves premium. Used when the per-print tape is
    # unavailable, which is the normal case for a thin name.
    pos = positioning or {}
    if pos.get("status") == "OK" and pos.get("call_premium_share") is not None:
        signals.append(
            ("Call share of premium", pos["call_premium_share"], 0.30))

    # Dealer gamma is a volatility read, not a direction read, so it is
    # reported alongside the score rather than folded into it.
    if not signals:
        return {"score": None, "label": "NO DATA", "status": "NO_DATA"}

    weight = sum(s[2] for s in signals)
    score = sum(s[1] * s[2] for s in signals) / weight

    label = (
        "BULLISH" if score >= 60
        else "BEARISH" if score <= 40
        else "NEUTRAL"
    )

    return {
        "score": round(score),
        "label": label,
        "components": [
            {"name": n, "score": round(v, 1), "weight": round(w / weight * 100)}
            for n, v, w in signals
        ],
        "gamma_regime": pos.get("gamma_regime"),
        "net_gex": pos.get("net_gex"),
        "status": "OK",
        "source": _chain_source(chain),
    }


# ---------------------------------------------------------------------------
# one call that powers the whole Options Flow screen
# ---------------------------------------------------------------------------


def _gamma_flip(symbol: str) -> Optional[float]:
    """
    The strike where cumulative dealer gamma crosses zero.

    Above it dealers are long gamma and dampen moves; below it they are short
    and chase. It is the one level on this panel that says what dealers will
    do to a move rather than how big their book is, and the disparity screen
    reported it as "not published" -- which was true of the aggregate
    endpoint, but the per-strike exposure it can be summed from is published.

    Returns None rather than a guess when the running total never changes
    sign: a book that is long gamma at every strike has no flip, and naming
    the nearest strike anyway would invent a level.
    """
    import unusualwhales_service as uw

    rows = uw._rows(uw.get(f"/api/stock/{symbol}/greek-exposure/strike"))
    if not rows:
        return None

    # One date per response in practice, but the endpoint has sent more than
    # one before; the current book is the latest of them.
    latest = max((r.get("date") for r in rows if r.get("date")), default=None)
    if latest:
        rows = [r for r in rows if r.get("date") == latest]

    priced = []
    for row in rows:
        strike = _num(row.get("strike"))
        if strike is None:
            continue
        net = (_num(row.get("call_gex")) or 0.0) + (_num(row.get("put_gex")) or 0.0)
        priced.append((strike, net))
    if len(priced) < 2:
        return None

    priced.sort()
    running = 0.0
    previous = None
    flip = None
    for strike, net in priced:
        running += net
        if previous is not None and (previous < 0) != (running < 0):
            flip = strike
        previous = running
    return flip


def get_dealer_positioning(symbol: str) -> dict:
    """
    Gamma exposure and aggregate premium flow, as published.

    These were previously reported as permanently unobtainable, which was true
    of a raw tick stream: price, size and venue only, with no dealer
    inventory and no participant tagging. The feed publishes both, so the
    figures are the provider's, not ours, and are labelled as such.
    """
    import unusualwhales_service as uw

    if not uw.configured():
        return {"status": "NOT_CONFIGURED", "source": SOURCE_UW}

    # Their greek exposure comes back oldest first and ignores a limit, so
    # the current day is the last row. Reading it as newest-first showed a
    # gamma regime measured a year ago, dated as though it were today.
    exposure = uw._rows(uw.get(f"/api/stock/{symbol}/greek-exposure",
                               {"timeframe": "1m"}))[-1:]
    volume = uw._rows(uw.get(f"/api/stock/{symbol}/options-volume",
                             {"limit": 1}))
    if not exposure and not volume:
        return {"status": "NO_DATA", "source": SOURCE_UW}

    today = exposure[0] if exposure else {}
    day_volume = volume[0] if volume else {}

    gex = {"call_gex": today.get("call_gamma"), "put_gex": today.get("put_gamma"),
           "spot": None}
    day = {"call_oi": day_volume.get("call_open_interest"),
           "put_oi": day_volume.get("put_open_interest"),
           "spot": None, "max_pain_curve": None}
    flow = {"call_premium": day_volume.get("call_premium"),
            "put_premium": day_volume.get("put_premium"),
            "bullish_dex": today.get("call_delta"),
            "bearish_dex": today.get("put_delta"),
            "trade_count": None,
            "traded_contract_count": ((uw._f(day_volume.get("call_volume")) or 0)
                                      + (uw._f(day_volume.get("put_volume")) or 0))}
    meta = {"intraday_gex_as_of": today.get("date"),
            "flow_as_of": day_volume.get("date")}

    call_gex = _num(gex.get("call_gex"))
    put_gex = _num(gex.get("put_gex"))
    net_gex = (call_gex + put_gex) if (call_gex is not None and put_gex is not None) else None
    flip = _gamma_flip(symbol)

    call_prem = _num(flow.get("call_premium"))
    put_prem = _num(flow.get("put_premium"))
    total_prem = (call_prem + put_prem) if (call_prem is not None and put_prem is not None) else None

    return {
        "symbol": symbol,
        "spot": _num(gex.get("spot")) or _num(day.get("spot")),
        "call_gex": call_gex,
        "put_gex": put_gex,
        "net_gex": net_gex,
        # Positive net gamma means dealers are long gamma and tend to dampen
        # moves; negative means they amplify them.
        "gamma_regime": (
            None if net_gex is None
            else ("LONG_GAMMA" if net_gex > 0 else "SHORT_GAMMA")),
        # The strike where dealer gamma changes sign. This is ours -- the
        # per-strike exposure is published, the crossing is arithmetic over
        # it -- so it is labelled as derived rather than as the provider's.
        "gamma_flip": flip,
        "gamma_flip_basis": (
            "Strike where cumulative dealer gamma crosses zero, summed over "
            "the provider's per-strike call and put exposure."
            if flip is not None else None),
        "call_premium": call_prem,
        "put_premium": put_prem,
        "total_premium": total_prem,
        "call_premium_share": (
            round(call_prem / total_prem * 100, 1)
            if total_prem else None),
        "premium_bias": (
            None if not total_prem
            else ("Bullish" if call_prem > put_prem else "Bearish")),
        "bullish_dex": _num(flow.get("bullish_dex")),
        "bearish_dex": _num(flow.get("bearish_dex")),
        "trade_count": _num(flow.get("trade_count")),
        "traded_contracts": _num(flow.get("traded_contract_count")),
        "day_call_oi": _num(day.get("call_oi")),
        "day_put_oi": _num(day.get("put_oi")),
        "max_pain_curve": day.get("max_pain_curve"),
        "as_of": meta.get("intraday_gex_as_of") or meta.get("flow_as_of"),
        "status": "OK",
        "source": SOURCE_UW,
    }


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _flow_provider_configured() -> bool:
    """Whether the provider tape is available at all."""
    try:
        import uw_flow_service as odflow
    except ImportError:
        return False
    return odflow.configured()


def get_overview(symbol: str) -> dict:
    symbol = symbol.upper()

    # The option chain is the slowest fetch on this page by a wide margin, and
    # it used to gate everything behind it. Implied-volatility history, dealer
    # positioning and the print tape all come from a different provider and
    # need nothing from the chain, so they are fetched alongside it rather
    # than after. On a symbol nobody has opened yet this is the difference
    # between one slow wait and several stacked on top of each other.
    def provider_flow() -> dict:
        try:
            import uw_flow_service as odflow
        except ImportError:
            return {}
        return odflow.get_flow(symbol)

    # The chain is fetched on its own, before the rest fan out. It shares one
    # provider rate limit with the print scans, and when those five queries
    # start alongside it the chain loses the token and waits. Ordering it
    # first trades a little of the best case for the removal of that outlier.
    chain = svc.load_chain(symbol, extra_expiries=2)

    with ThreadPoolExecutor(max_workers=3,
                            thread_name_prefix="options") as pool:
        iv_job = pool.submit(svc.get_iv_history, symbol)
        positioning_job = pool.submit(get_dealer_positioning, symbol)
        od_flow_job = pool.submit(provider_flow)

        def settled(job, fallback, budget=None):
            try:
                return job.result(timeout=budget)
            except FuturesTimeout:
                # Still running: it will populate the cache and land on the
                # next load rather than holding this one open.
                return fallback
            except Exception:  # noqa: BLE001
                # One provider failing must not blank the whole screen.
                return fallback

        iv_history = settled(iv_job, {}, PROVIDER_BUDGET)
        positioning = settled(positioning_job,
                              {"status": "NO_DATA", "source": SOURCE_UW},
                              PROVIDER_BUDGET)
        # The print scans are the heaviest thing here and share one provider
        # rate limit, so opening several symbols in a row queues them behind
        # each other. Capped, because a delayed tape is a supporting panel and
        # not worth holding the whole screen for.
        od_flow = settled(od_flow_job, {}, FLOW_BUDGET)
        pool.shutdown(wait=False)

    if chain.get("status") != "OK":
        return {
            "symbol": symbol,
            "status": chain.get("status"),
            "error": chain.get("error"),
            "market": market_clock(),
            "source": _chain_source(chain),
        }

    # Realized move needs the chain's horizon, so it cannot join the fan-out
    # above. It reads daily bars from a rate-limited provider (8 requests a
    # minute), and when that bucket is empty the wait ran to tens of seconds --
    # the whole options page blocked on a single comparison figure. Bounded
    # here: if it cannot answer quickly it is reported as pending and every
    # other panel still renders. The value lands on the next load, by which
    # time the bars are cached and it is free.
    horizon = int(chain.get("dte") or 7) or 7
    with ThreadPoolExecutor(max_workers=1,
                            thread_name_prefix="realized") as pool:
        realized_job = pool.submit(svc.get_realized_move, symbol, horizon)
        try:
            realized = realized_job.result(timeout=REALIZED_MOVE_BUDGET)
        except FuturesTimeout:
            realized = {
                "symbol": symbol,
                "status": "PENDING",
                "detail": "Awaiting daily bars from a rate-limited provider.",
                "source": "PROVIDER",
            }
            # Let it finish in the background so the cache is warm next time.
            pool.shutdown(wait=False)

    # One flow source. The IBKR tape used to sit behind this and was asked
    # only when the provider had not answered; without an OPRA subscription
    # that scan walked historical ticks contract by contract, returned
    # nothing, and cost forty seconds on a liquid name -- the single largest
    # item on this page, spent to confirm an empty result we then discarded.
    flow = od_flow or {}
    if not flow:
        flow = {
            "symbol": symbol,
            "trades": [],
            "status": "PENDING",
            "source": SOURCE_UW,
            "note": "Print history still loading; it will appear shortly.",
        }
    unusual = flow.get("unusual") or []

    # Per-contract volume context is not shown. It needed daily bars on each
    # option contract, which no feed here serves, so the tiles show no change
    # figure rather than a fabricated one.
    volume_context: dict = {}

    metrics = get_metrics(chain, iv_history, realized, flow)

    return {
        "symbol": symbol,
        "spot": chain.get("spot"),
        "expiry": chain.get("expiry"),
        "expiry_label": chain.get("expiry_label"),
        "expirations": chain.get("expirations"),
        "expiration_labels": chain.get("expiration_labels"),
        "dte": chain.get("dte"),
        "market": chain.get("market"),
        "tiles": get_tiles(chain, volume_context, flow),
        "metrics": metrics,
        "volume_by_strike": get_volume_by_strike(chain, "volume", flow),
        "open_interest_by_strike": get_volume_by_strike(chain, "open_interest"),
        "delta_exposure_by_strike": get_volume_by_strike(chain, "delta_exposure"),
        "flow": flow,
        "scatter": get_scatter(flow, chain),
        "breakdown": get_breakdown(flow, chain),
        "expiration_flow": get_expiration_flow(chain, flow),
        "risk_zones": get_risk_zones(chain, positioning),
        "sentiment": get_sentiment(chain, flow, metrics, positioning),
        "data_basis": get_data_basis(chain, flow, metrics, positioning),
        "iv_history": iv_history,
        "realized_move": realized,
        "volume_context": volume_context,
        "positioning": positioning,
        "unusual": unusual,
        "status": "OK",
        "source": _chain_source(chain),
    }


# ---------------------------------------------------------------------------
# option chain table (calls | strike | puts)
# ---------------------------------------------------------------------------


def get_chain_table(chain: dict) -> dict:
    """
    Reshape a loaded chain into the ladder the Option Chain screen renders.

    Every field here is quoted by the feed -- bid, ask, last, volume, open
    interest, implied volatility and the greeks all arrive on the contract.
    No value is filled in from a model of what it "should" be. The IV and
    greeks used to be solved here with Black-Scholes from the quoted mid,
    which was this app's arithmetic rather than anyone's measurement.
    """
    rows = chain.get("rows", [])
    spot = chain.get("spot")
    if not rows:
        return {"strikes": [], "status": chain.get("status", "NO_DATA"),
                "error": chain.get("error")}

    def leg(r: Optional[dict]) -> Optional[dict]:
        if not r:
            return None
        return {
            "con_id": r["con_id"],
            "bid": r["bid"], "ask": r["ask"], "last": r["last"],
            "mid": r["mid"], "volume": r["volume"],
            "open_interest": r["open_interest"],
            "iv": round(r["iv"] * 100, 2) if r.get("iv") else None,
            "delta": r.get("delta"), "gamma": r.get("gamma"),
            "vega": r.get("vega"), "theta": r.get("theta"),
            "itm": (
                (r["strike"] < spot) if r["right"] == "C" else (r["strike"] > spot)
            ) if spot else None,
        }

    by_strike: dict[float, dict] = {}
    for r in rows:
        slot = by_strike.setdefault(r["strike"], {"strike": r["strike"]})
        slot["call" if r["right"] == "C" else "put"] = r

    strikes = []
    for strike in sorted(by_strike):
        slot = by_strike[strike]
        strikes.append({
            "strike": strike,
            "call": leg(slot.get("call")),
            "put": leg(slot.get("put")),
            "atm": bool(spot and abs(strike - spot) ==
                        min(abs(s - spot) for s in by_strike)),
        })

    return {
        "symbol": chain.get("symbol"),
        "spot": spot,
        "expiry": chain.get("expiry"),
        "expiry_label": chain.get("expiry_label"),
        "dte": chain.get("dte"),
        "expirations": chain.get("expirations", []),
        "expiration_labels": chain.get("expiration_labels", []),
        "strikes": strikes,
        "count": len(strikes),
        "market": chain.get("market"),
        "status": "OK",
        "source": _chain_source(chain),
        # Carried through rather than re-derived: this table is a reshaping of
        # the chain, and a badge that disagrees with the chain it was built
        # from is worse than no badge.
        "freshness": chain.get("freshness"),
        "greeks_note": (
            "IV and greeks are published per contract by the provider. They "
            "used to be solved here from the quoted bid/ask with "
            "Black-Scholes, which was this app's arithmetic rather than a "
            "measurement."
        ),
    }


# ---------------------------------------------------------------------------
# data provenance
# ---------------------------------------------------------------------------


def get_data_basis(chain: dict, flow: Optional[dict] = None,
                   metrics: Optional[dict] = None,
                   positioning: Optional[dict] = None) -> dict:
    """
    Say exactly what produced each options metric, and what cannot be produced.

    The distinction this encodes: a figure computed from a value the feed
    actually quoted (open interest, bid/ask) is real; a figure that needs
    exchange-side order attribution is not obtainable here at all and is
    reported as REQUIRES_ADVANCED_OPTIONS_DATA rather than estimated.
    """
    rows = (chain.get("rows") or []) + (chain.get("other_expiry_rows") or [])
    has_oi = any(r.get("open_interest") for r in rows)
    has_px = any(r.get("mid") for r in rows)
    tape = bool((flow or {}).get("trades"))
    tape_source = (flow or {}).get("source") or SOURCE_UW
    delayed = (flow or {}).get("delay_minutes")

    def entry(available: bool, basis: str, note: str = "") -> dict:
        return {
            "available": available,
            "status": "OK" if available else "REQUIRES_ADVANCED_OPTIONS_DATA",
            "basis": basis,
            "note": note,
        }

    supported = {
        "open_interest": entry(
            has_oi, f"{SOURCE_UW}: open interest published per contract"),
        "volume": entry(
            True, f"{SOURCE_UW}: session volume per contract, with the "
                  "previous session's as the labelled fallback outside "
                  "market hours"),
        "bid_ask_last": entry(has_px, f"{SOURCE_UW} option quotes"),
        "implied_volatility": entry(
            has_px, f"{SOURCE_UW}: published per contract",
            "Published by the provider, not solved here from the mid."),
        "greeks": entry(
            has_px, f"{SOURCE_UW}: delta, gamma, theta, vega and rho "
                    "published per contract"),
        "expected_move": entry(
            has_px, "ATM straddle from live quotes"),
        "put_call_ratios": entry(has_oi or has_px, "Quoted volume / open interest"),
        # Reported from the value actually produced, not from the fact that a
        # code path exists. This entry used to be hardcoded available while
        # the source returned NO_DATA, so the panel promised a figure it
        # never had.
        "iv_rank_percentile": entry(
            (metrics or {}).get("iv_rank") is not None,
            ((metrics or {}).get("iv_rank_source") or SOURCE_UW)
            + " implied-volatility rank and percentile",
            "" if (metrics or {}).get("iv_rank") is not None
            else "The provider returned no rank for this symbol."),
        "call_wall": entry(
            has_oi, "Strike with the largest quoted call open interest"),
        "put_wall": entry(
            has_oi, "Strike with the largest quoted put open interest"),
        "max_pain": entry(
            has_oi,
            "Strike minimising aggregate intrinsic payout across quoted open "
            "interest"),
        "sweeps_blocks": entry(
            tape,
            f"{tape_source}: prints clustered by timestamp"
            + (f", delayed {int(delayed)} minutes" if delayed else ""),
            "Several legs stamped at one instant is a sweep; a single "
            "oversized print is a block. This is a classification "
            "of real prints, not an exchange-supplied flag."),
        # A stronger claim than it used to be. The side was previously
        # inferred here from where a print landed in the spread.
        "trade_side": entry(
            tape,
            f"{tape_source}: the side each print crossed on, counted by the "
            f"provider rather than inferred from the price"
            + (f", delayed {int(delayed)} minutes" if delayed else "")),
    }

    # A per-contract baseline needs historical option volume, which the
    # provider's print history supplies as 15 rolling days.
    if (flow or {}).get("unusual") is not None:
        supported["unusual_activity_baseline"] = entry(
            bool((flow or {}).get("unusual")),
            f"{tape_source}: session volume per contract against its own "
            f"trailing average",
            "Contracts without a meaningful baseline are excluded rather "
            "than shown with an inflated ratio.")

    # Dealer positioning and aggregate premium flow are published, so these
    # are not out of reach.
    pos_ok = (positioning or {}).get("status") == "OK"
    if pos_ok:
        supported["gamma_exposure"] = entry(
            (positioning or {}).get("net_gex") is not None,
            f"{SOURCE_UW}: intraday dealer gamma exposure",
            "Provider-published dealer positioning, not derived by us.")
        supported["premium_flow"] = entry(
            (positioning or {}).get("total_premium") is not None,
            f"{SOURCE_UW}: aggregate call/put premium and directional exposure",
            "Session totals, not individual prints.")

    # Off-exchange prints were listed here as out of reach because the IBKR
    # tick stream could not distinguish them. The feed publishes them, and
    # the app has a Dark Pool screen reading exactly that.
    supported["dark_pool_prints"] = entry(
        True, f"{SOURCE_UW}: off-exchange prints with venue and size",
        "Shown on the Dark Pool tab rather than on this ladder.")

    # Genuinely out of reach on this data source.
    unsupported = {}
    if not pos_ok:
        unsupported["gamma_exposure"] = {
            "available": False,
            "status": "REQUIRES_ADVANCED_OPTIONS_DATA",
            "label": "Gamma Exposure (GEX)",
            "reason": (
                "Requires dealer inventory / market-maker positioning, and "
                "the provider did not answer for this symbol."
            ),
            "needs": "An options analytics provider publishing dealer positioning.",
        }
    if "unusual_activity_baseline" in supported:
        pass
    unsupported.update({
        "institutional_aggressor_flow": {
            "available": False,
            "status": "REQUIRES_ADVANCED_OPTIONS_DATA",
            "label": "Per-print institutional aggressor flow",
            "reason": (
                "Requires exchange-side participant tagging on each print. "
                "The feed supplies the side each print crossed on and "
                "session-level directional exposure, but not who was on "
                "either end of it."
            ),
            "needs": "An OPRA-level feed with participant classification.",
        },
        "unusual_activity_baseline": {
            "available": False,
            "status": "REQUIRES_ADVANCED_OPTIONS_DATA",
            "label": "Unusual activity vs historical average",
            "reason": (
                "Requires historical per-contract option volume, which the "
                "provider did not return for this symbol."
            ),
            "needs": "A provider with historical option volume by contract.",
        },
    })
    for name in list(unsupported):
        if name in supported:
            unsupported.pop(name)

    return {
        "supported": supported,
        "unsupported": unsupported,
        "unsupported_count": len(unsupported),
        "source": _chain_source(chain),
    }
