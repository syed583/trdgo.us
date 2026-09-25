"""
Turns provider readings into the model's sixteen parameters.

Every signal carries its own explanation
----------------------------------------
Each parameter returns four things beyond its score: the rule that was
applied, the raw numbers it was applied to, where those numbers came from,
and -- when it could not be computed -- why not. That is what lets the UI
answer "why is this a buy" with the actual arithmetic instead of a restated
label.

It also keeps the model honest. A rule that cannot be written down in one
sentence is usually a rule nobody has thought through, and a parameter whose
evidence looks thin on screen is one that should probably not carry weight.

Nothing here invents a reading. A provider that does not answer produces an
unavailable signal with a stated reason, which lowers confidence rather than
quietly scoring neutral.
"""

from __future__ import annotations

from typing import Any, Optional

from directional_model import INSIDER_SPLIT, Signal

# Thresholds, gathered so the rules are visible in one place rather than
# buried in sixteen functions.
PCR_BULL, PCR_BEAR = 0.85, 1.15          # put/call ratio
RSI_HOT, RSI_COLD = 70.0, 30.0
OI_MOVE_PCT = 3.0                        # open-interest change worth noting
UNUSUAL_RATIO = 3.0                      # volume vs its own baseline
BEAT_RATE_STRONG = 0.75
IV_RANK_RICH, IV_RANK_CHEAP = 70.0, 30.0


def _num(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _scale(value: float, low: float, high: float) -> float:
    """Map a value between two bounds onto -1..+1, clamped."""
    if high == low:
        return 0.0
    return max(-1.0, min(1.0, (value - low) / (high - low) * 2 - 1))


# ---------------------------------------------------------------------------
# options parameters
# ---------------------------------------------------------------------------


def unusual_activity(flow: dict) -> Signal:
    """Contracts trading far above their own recent average."""
    rows = (flow or {}).get("unusual") or []
    breadth = (flow or {}).get("unusual_breadth") or {}
    if not rows and breadth.get("status") != "OK":
        return Signal(
            "unusual_activity", None, label="Unusual Activity",
            source="Unusual Whales",
            unavailable_reason="No contract exceeded its baseline, or the "
                               "provider tape is unavailable.")

    # Side is taken from every contract above baseline, not from the listed
    # rows. That list is the top few by ratio and, on a capped plan, is
    # whatever the provider returned -- a side summed from it would be
    # measuring the ranking. The rows below are still what gets shown.
    if breadth.get("status") == "OK":
        call_vol = breadth.get("call_volume") or 0.0
        put_vol = breadth.get("put_volume") or 0.0
        flagged = breadth.get("contracts") or 0
        basis = "every contract above baseline"
    else:
        call_vol = sum(r.get("volume") or 0 for r in rows
                       if r.get("right") == "C")
        put_vol = sum(r.get("volume") or 0 for r in rows
                      if r.get("right") == "P")
        flagged = len(rows)
        basis = f"the {len(rows)} most unusual contracts"

    total = call_vol + put_vol
    if not total:
        return Signal(
            "unusual_activity", None, label="Unusual Activity",
            source="Unusual Whales",
            unavailable_reason="Contracts cleared the baseline but carried "
                               "no volume to attribute.")

    bias = (call_vol - put_vol) / total
    top = max(rows, key=lambda r: r.get("ratio") or 0) if rows else None

    evidence = {
        "contracts_flagged": flagged,
        "call_volume": call_vol,
        "put_volume": put_vol,
        "side_measured_over": basis,
    }
    if top:
        evidence["most_unusual"] = (
            f"{top.get('right')} {top.get('strike')} "
            f"{top.get('expiry_label')} at {top.get('ratio')}x its average")
    if (flow or {}).get("unusual_capped"):
        evidence["listed_rows"] = (
            f"{(flow or {}).get('unusual_listed', len(rows))} shown; the "
            f"provider plan caps the list, not the side above")

    return Signal(
        "unusual_activity", bias, label="Unusual Activity",
        detail=(f"{flagged} contracts above baseline; "
                f"{'calls' if call_vol > put_vol else 'puts'} lead "
                f"({max(call_vol, put_vol):,.0f} vs {min(call_vol, put_vol):,.0f})"),
        rule="Bias is the call share of unusual volume minus the put share, "
             "measured across every contract above its baseline rather than "
             "the few listed below. A contract's baseline is its average over "
             "the days it actually traded, so a strike listed this week is "
             "not compared against zeroes it was never quoted on. Contracts "
             "with no meaningful baseline are excluded entirely.",
        evidence=evidence,
        source="Unusual Whales")


def options_flow(flow: dict) -> Signal:
    """Sweeps, blocks and which side the premium is crossing on."""
    pressure = (flow or {}).get("pressure") or {}
    if pressure.get("status") != "OK":
        return Signal(
            "options_flow", None, label="Options Flow", source="Unusual Whales",
            unavailable_reason=(flow or {}).get("note")
            or "No per-print tape available for this symbol.")

    share = _num(pressure.get("bullish_share"))
    if share is None:
        return Signal("options_flow", None, label="Options Flow",
                      source="Unusual Whales",
                      unavailable_reason="No prints crossed the bid or ask, "
                                         "so no side could be attributed.")

    bias = _scale(share, 35.0, 65.0)
    return Signal(
        "options_flow", bias, label="Options Flow",
        detail=(f"{share}% of directional premium was bullish across "
                f"{pressure.get('prints', 0):,.0f} prints"),
        rule="Buying calls and selling puts are both bullish pressure; the "
             "mirror is bearish. Prints between the bid and ask have no "
             "aggressor and are left out of both sides rather than assigned "
             "a direction they do not have. 35% maps to fully bearish, 65% "
             "to fully bullish.",
        evidence={
            "bullish_premium": pressure.get("bullish_premium"),
            "bearish_premium": pressure.get("bearish_premium"),
            "prints_scanned": pressure.get("prints"),
            "sweeps": (flow or {}).get("sweeps"),
            "blocks": (flow or {}).get("blocks"),
            "delayed_minutes": (flow or {}).get("delay_minutes"),
        },
        source="Unusual Whales")


def volume_pcr(tiles: dict) -> Signal:
    """Put/call ratio on session volume."""
    pcr = _num((tiles or {}).get("put_call_volume"))
    if pcr is None:
        return Signal("volume_pcr", None, label="Volume & Put/Call Ratio",
                      source="Option chain",
                      unavailable_reason="No session volume on the chain.")

    # Inverted: a low put/call ratio is bullish.
    bias = -_scale(pcr, PCR_BULL, PCR_BEAR)
    return Signal(
        "volume_pcr", bias, label="Volume & Put/Call Ratio",
        detail=(f"Put/call {pcr:.2f} on "
                f"{(tiles or {}).get('total_volume', 0):,.0f} contracts"),
        rule=f"Below {PCR_BULL} is bullish, above {PCR_BEAR} bearish, and the "
             f"range between maps proportionally. The ratio is inverted "
             f"because more puts means more downside demand.",
        evidence={
            "put_call_volume": pcr,
            "call_volume": (tiles or {}).get("call_volume"),
            "put_volume": (tiles or {}).get("put_volume"),
            "basis": (tiles or {}).get("volume_basis"),
        },
        source="Option chain")


def oi_positioning(tiles: dict) -> Signal:
    """Where open interest already sits, calls against puts."""
    call_oi = _num((tiles or {}).get("call_oi"))
    put_oi = _num((tiles or {}).get("put_oi"))
    if not call_oi or not put_oi:
        return Signal("oi_positioning", None, label="OI Positioning",
                      source="Option chain",
                      unavailable_reason="Open interest not quoted.")

    total = call_oi + put_oi
    bias = _scale(call_oi / total * 100, 40.0, 60.0)
    return Signal(
        "oi_positioning", bias, label="OI Positioning",
        detail=f"{call_oi / total * 100:.1f}% of open interest is calls",
        rule="The standing book, not today's trading. Weighted lightly "
             "because open interest accumulates over weeks and says more "
             "about positions already held than about new conviction.",
        evidence={"call_oi": call_oi, "put_oi": put_oi,
                  "call_share_pct": round(call_oi / total * 100, 1)},
        source="Option chain")


def daily_oi_change(oi: dict) -> Signal:
    """Whether positions are being opened or closed."""
    if (oi or {}).get("status") != "OK":
        return Signal("daily_oi_change", None, label="Daily OI Change",
                      source="Unusual Whales",
                      unavailable_reason="Fewer than two sessions of "
                                         "open-interest history available.")

    change = _num(oi.get("change_pct")) or 0.0
    building = oi.get("building")

    # Rising open interest is only bullish if it is being built on calls.
    magnitude = min(1.0, abs(change) / (OI_MOVE_PCT * 3))
    if building == "CALLS":
        bias = magnitude if change > 0 else -magnitude * 0.5
    elif building == "PUTS":
        bias = -magnitude if change > 0 else magnitude * 0.5
    else:
        bias = 0.0

    return Signal(
        "daily_oi_change", bias, label="Daily OI Change",
        detail=(f"Open interest {oi.get('trend', '').lower()} "
                f"{change:+.1f}% over {oi.get('sessions')} sessions, "
                f"building on {str(building).lower()}"),
        rule="Rising open interest means positions are being opened rather "
             "than closed. Which side it builds on decides the direction: "
             "growth on calls and growth on puts are opposite messages.",
        evidence={
            "total_oi": oi.get("total_oi"),
            "previous_oi": oi.get("previous_oi"),
            "change_pct": change,
            "call_oi_change": oi.get("call_oi_change"),
            "put_oi_change": oi.get("put_oi_change"),
            "sessions": oi.get("sessions"),
        },
        source="Unusual Whales")


def flow_by_expiry(expiration: dict) -> Signal:
    """Which expiries are attracting the activity."""
    rows = (expiration or {}).get("expirations") or {}
    if not rows:
        return Signal("flow_by_expiry", None, label="Flow by Expiry",
                      source="Option chain",
                      unavailable_reason="No activity by expiry on the chain.")

    # The chain returns a list of per-expiry rows, not a mapping.
    entries = rows if isinstance(rows, list) else list(rows.values())
    calls = sum((v or {}).get("calls") or 0 for v in entries)
    puts = sum((v or {}).get("puts") or 0 for v in entries)
    total = calls + puts
    if not total:
        return Signal("flow_by_expiry", None, label="Flow by Expiry",
                      source="Option chain",
                      unavailable_reason="Every expiry showed zero volume.")

    bias = _scale(calls / total * 100, 40.0, 60.0)
    return Signal(
        "flow_by_expiry", bias, label="Flow by Expiry",
        detail=(f"{calls / total * 100:.0f}% of dated activity is calls "
                f"across {len(entries)} expiries"),
        rule="Calls against puts across every expiry that traded. The peak "
             "expiry is reported alongside because near-dated crowding and "
             "long-dated accumulation mean different things.",
        evidence={"call_volume": calls, "put_volume": puts,
                  "expiries": len(entries),
                  "peak": (expiration or {}).get("peak")},
        source="Option chain")


def key_levels(zones: dict, spot: Optional[float]) -> Signal:
    """Position between the call wall, the put wall and max pain."""
    if (zones or {}).get("status") != "OK" or not spot:
        return Signal("key_levels", None, label="Key Option Levels",
                      source="Option chain",
                      unavailable_reason="Walls need quoted open interest.")

    call_wall = _num(zones.get("call_wall"))
    put_wall = _num(zones.get("put_wall"))
    if not call_wall or not put_wall or call_wall <= put_wall:
        return Signal("key_levels", None, label="Key Option Levels",
                      source="Option chain",
                      unavailable_reason="Walls did not bracket the price.")

    # Where price sits between the two walls: near the put wall is support,
    # near the call wall is resistance overhead.
    position = (spot - put_wall) / (call_wall - put_wall)
    bias = _scale(position, 0.15, 0.85) * 0.8

    return Signal(
        "key_levels", bias, label="Key Option Levels",
        detail=(f"Price {spot:.2f} sits between the put wall {put_wall:.0f} "
                f"and the call wall {call_wall:.0f}; max pain {zones.get('max_pain')}"),
        rule="Position between the two largest open-interest strikes. Near "
             "the put wall there is support beneath; near the call wall "
             "there is resistance overhead. These are reference levels from "
             "positioning, not a dealer-hedging model.",
        evidence={
            "call_wall": call_wall, "put_wall": put_wall,
            "max_pain": zones.get("max_pain"), "spot": spot,
            "position_in_range": round(position, 3),
        },
        source="Option chain")


# ---------------------------------------------------------------------------
# volatility parameters -- weight without a directional vote
# ---------------------------------------------------------------------------


def implied_volatility(metrics: dict) -> Signal:
    """IV rank and percentile: how expensive options are, not which way."""
    rank = _num((metrics or {}).get("iv_rank"))
    if rank is None:
        return Signal(
            "implied_volatility", None, label="Implied Volatility",
            source=(metrics or {}).get("iv_rank_source") or "Provider",
            unavailable_reason="No one-year implied-volatility history.")

    bias = _scale(rank, 0.0, 100.0)
    state = ("expensive" if rank >= IV_RANK_RICH
             else "cheap" if rank <= IV_RANK_CHEAP else "mid-range")
    return Signal(
        "implied_volatility", bias, label="Implied Volatility",
        detail=(f"IV rank {rank:.0f}, percentile "
                f"{(metrics or {}).get('iv_percentile')} -- options are {state}"),
        rule="Carries weight but casts no directional vote. High implied "
             "volatility means the market expects a large move, not an "
             "upward one, so it belongs in sizing and entry timing rather "
             "than in the buy-or-sell decision.",
        evidence={
            "iv_rank": rank,
            "iv_percentile": (metrics or {}).get("iv_percentile"),
            "implied_volatility": (metrics or {}).get("implied_volatility"),
            "historical_volatility": (metrics or {}).get("historical_volatility"),
        },
        source=(metrics or {}).get("iv_rank_source") or "Provider")


def expected_move(metrics: dict) -> Signal:
    """The straddle's implied range."""
    pct = _num((metrics or {}).get("expected_move_percent"))
    if pct is None:
        return Signal("expected_move", None, label="Expected Move",
                      source="Option chain",
                      unavailable_reason="No at-the-money straddle quoted.")

    bias = min(1.0, pct / 10.0)
    return Signal(
        "expected_move", bias, label="Expected Move",
        detail=f"Options price a move of +/-{pct:.2f}% by {(metrics or {}).get('expiry_label')}",
        rule="Sets the size of the move being priced, not its direction. "
             "Used to sanity-check targets and stops: a target inside the "
             "expected move is likely, one far outside it is not.",
        evidence={
            "expected_move_percent": pct,
            "atm_straddle": (metrics or {}).get("atm_straddle"),
            "expected_range": (metrics or {}).get("expected_range"),
            "realized_move_percent": (metrics or {}).get("realized_move_percent"),
        },
        source="Option chain")


def gamma_exposure(positioning: dict) -> Signal:
    """Dealer gamma: whether moves get dampened or amplified."""
    if (positioning or {}).get("status") != "OK":
        return Signal("gamma_exposure", None, label="Gamma Exposure",
                      source="Unusual Whales",
                      unavailable_reason="No dealer positioning published "
                                         "for this symbol.")

    net = _num(positioning.get("net_gex"))
    if net is None:
        return Signal("gamma_exposure", None, label="Gamma Exposure",
                      source="Unusual Whales",
                      unavailable_reason="Gamma exposure not reported.")

    regime = positioning.get("gamma_regime")
    bias = 1.0 if regime == "LONG_GAMMA" else -1.0
    return Signal(
        "gamma_exposure", bias, label="Gamma Exposure",
        detail=(f"Net gamma {net / 1e9:+.2f}B -- dealers are "
                f"{'long gamma, which dampens moves' if regime == 'LONG_GAMMA' else 'short gamma, which amplifies moves'}"),
        rule="A volatility regime, not a direction. Long dealer gamma means "
             "hedging leans against the move and tends to compress ranges; "
             "short gamma means hedging chases the move and expands them. "
             "Neither is bullish by itself.",
        evidence={
            "net_gex": net,
            "call_gex": positioning.get("call_gex"),
            "put_gex": positioning.get("put_gex"),
            "regime": regime,
        },
        source="Unusual Whales")


# ---------------------------------------------------------------------------
# price parameters
# ---------------------------------------------------------------------------


def ema_trend(technicals: dict) -> Signal:
    """Price against its moving averages, and their stacking."""
    close = _num((technicals or {}).get("close"))
    e20 = _num((technicals or {}).get("ema_20"))
    e50 = _num((technicals or {}).get("ema_50"))
    e200 = _num((technicals or {}).get("ema_200"))
    if not close or not e20 or not e50:
        return Signal("ema_trend", None, label="EMA / Trend",
                      source="Daily bars",
                      unavailable_reason="Not enough history for the "
                                         "moving averages.")

    checks = [close > e20, close > e50]
    if e200:
        checks.append(close > e200)
        checks.append(e20 > e50 > e200)
    bias = (sum(1 for c in checks if c) / len(checks)) * 2 - 1

    return Signal(
        "ema_trend", bias, label="EMA / Trend",
        detail=(f"Close {close:.2f} against EMA20 {e20:.2f}, EMA50 {e50:.2f}"
                + (f", EMA200 {e200:.2f}" if e200 else "")),
        rule="Counts how many of these hold: price above each moving "
             "average, and the averages stacked 20 > 50 > 200. All true is "
             "fully bullish, none true fully bearish.",
        evidence={"close": close, "ema_20": e20, "ema_50": e50,
                  "ema_200": e200,
                  "stacked": bool(e200 and e20 > e50 > e200),
                  "trend": (technicals or {}).get("trend")},
        source="Daily bars")


def rsi(technicals: dict) -> Signal:
    """Momentum, with the extremes treated as exhaustion."""
    value = _num((technicals or {}).get("rsi_14"))
    if value is None:
        return Signal("rsi", None, label="RSI (14)", source="Daily bars",
                      unavailable_reason="Not enough history for RSI.")

    # 50 is neutral. Past the extremes the reading is faded rather than
    # extended: an overbought market is stretched, not doubly bullish.
    if value >= RSI_HOT:
        bias = max(0.0, 1.0 - (value - RSI_HOT) / 20.0)
        note = "overbought, momentum stretched"
    elif value <= RSI_COLD:
        bias = min(0.0, -1.0 + (RSI_COLD - value) / 20.0)
        note = "oversold, selling stretched"
    else:
        bias = _scale(value, 40.0, 60.0)
        note = "in its normal band"

    return Signal(
        "rsi", bias, label="RSI (14)",
        detail=f"RSI {value:.1f} -- {note}",
        rule=f"50 is neutral and the 40-60 band maps proportionally. Beyond "
             f"{RSI_HOT} or {RSI_COLD} the reading is faded rather than "
             f"extended, because an overbought market is stretched rather "
             f"than twice as bullish.",
        evidence={"rsi_14": value, "overbought_above": RSI_HOT,
                  "oversold_below": RSI_COLD},
        source="Daily bars")


def price_action(scored: dict) -> Signal:
    """Structure, breakout, volume confirmation and relative strength."""
    if not scored or scored.get("bias") == "NO_DATA":
        return Signal("price_action", None, label="Price Action",
                      source="Daily bars",
                      unavailable_reason="Not enough bars for swing "
                                         "structure.")

    raw = _num(scored.get("score")) or 0.0
    bias = max(-1.0, min(1.0, raw / (scored.get("max_score") or 10)))
    return Signal(
        "price_action", bias, label="Price Action",
        detail="; ".join((scored.get("reasons") or [])[:2]),
        rule="Higher highs and lower lows, breakouts graded by whether "
             "volume confirmed them, and strength against the benchmark. "
             "Volatility measures are deliberately excluded here and sent "
             "to confidence instead.",
        evidence={"components": scored.get("components"),
                  "reasons": scored.get("reasons"),
                  "score": raw},
        source="Daily bars")


# ---------------------------------------------------------------------------
# filings and events
# ---------------------------------------------------------------------------


def insider_activity(form4: dict, ownership: dict,
                     institutional: dict) -> Signal:
    """
    Form 4, 13D/13G and 13F, weighted by how fresh each one is.

    A Form 4 lands two days after the trade; a 13F is a quarter old before
    anyone can read it. Blending them evenly would let the stalest input
    outvote the freshest, so the split inside this parameter is 6/2/1.
    """
    parts: list[tuple[str, float, int]] = []
    evidence: dict[str, Any] = {}

    if (form4 or {}).get("status") == "OK":
        buys = form4.get("buy_count") or 0
        sells = form4.get("sell_count") or 0
        traded = buys + sells
        cluster = (form4.get("cluster") or {}).get("detected")
        if traded:
            score = (buys - sells) / traded
            if cluster:
                score = min(1.0, score + 0.35)
            parts.append(("form4", score, INSIDER_SPLIT["form4"]))
        evidence["form4"] = {
            "discretionary_buys": buys, "discretionary_sells": sells,
            "pre_scheduled_10b5_1": form4.get("planned_count"),
            "grants_and_exercises": form4.get("mechanical_count"),
            "cluster_detected": cluster,
        }

    if (ownership or {}).get("status") == "OK":
        activist = ownership.get("activist_count") or 0
        parts.append(("ownership_13dg", min(1.0, activist / 2.0),
                      INSIDER_SPLIT["ownership_13dg"]))
        evidence["ownership_13dg"] = {
            "activist_13d": activist,
            "passive_13g": ownership.get("passive_count"),
        }

    # 13F is no longer scored here -- fund_flows is its own parameter now, at
    # five points rather than one. It stays in the evidence so this signal's
    # panel still shows the ownership backdrop it always did.
    if (institutional or {}).get("status") == "OK":
        evidence["institutional_13f"] = {
            "signal": institutional.get("signal"),
            "funds_increasing": institutional.get("funds_increasing"),
            "funds_decreasing": institutional.get("funds_decreasing"),
            "net_share_change_pct": institutional.get("net_share_change_pct"),
            "quarter": institutional.get("latest_quarter"),
        }

    if not parts:
        return Signal("insider_activity", None, label="Insider & Ownership",
                      source=form4.get("source") or "SEC EDGAR",
                      unavailable_reason="No filings in the lookback window.")

    weight = sum(w for _, _, w in parts)
    bias = sum(s * w for _, s, w in parts) / weight

    f4 = evidence.get("form4") or {}
    return Signal(
        "insider_activity", bias, label="Insider & Ownership",
        detail=(f"{f4.get('discretionary_buys', 0)} insider buys against "
                f"{f4.get('discretionary_sells', 0)} sells"
                + (", cluster detected" if f4.get("cluster_detected") else "")),
        rule="Form 4 counts for 5 of the 6 points and 13D/13G for 1, weighted "
             "by age, since a Form 4 is two days old and a 13D/13G five. "
             "Institutional holdings moved out to their own parameter. "
             "Grants, option exercises and pre-arranged "
             "10b5-1 sales are excluded: they run on a schedule and are not "
             "anyone deciding anything.",
        evidence=evidence, source=form4.get("source") or "SEC EDGAR")


def earnings_results(history: dict) -> Signal:
    """Past beats and misses against consensus."""
    rows = (history or {}).get("rows") or (history or {}).get("quarters") or []
    # Whichever provider answered -- Finviz's calendar or Benzinga's history.
    origin = (history or {}).get("source") or "Benzinga"
    if not rows:
        return Signal("earnings_results", None, label="Earnings Results",
                      source=origin,
                      unavailable_reason="No reported results on file.")

    surprises = [_num(r.get("eps_surprise_percent") or r.get("surprise_percent"))
                 for r in rows]
    surprises = [s for s in surprises if s is not None]
    if not surprises:
        return Signal("earnings_results", None, label="Earnings Results",
                      source=origin,
                      unavailable_reason="Results carried no surprise figure.")

    beats = sum(1 for s in surprises if s > 0)
    rate = beats / len(surprises)
    average = sum(surprises) / len(surprises)

    bias = _scale(rate, 0.35, 0.85) * 0.7 + max(-0.3, min(0.3, average / 30))
    return Signal(
        "earnings_results", max(-1.0, min(1.0, bias)),
        label="Earnings Results",
        detail=(f"Beat {beats} of {len(surprises)} quarters "
                f"({rate * 100:.0f}%), average surprise {average:+.1f}%"),
        rule="Beat rate and average surprise across reported quarters. "
             "Backward-looking by nature, so it confirms a pattern rather "
             "than anticipating the next report.",
        evidence={"quarters": len(surprises), "beats": beats,
                  "beat_rate_pct": round(rate * 100, 1),
                  "average_surprise_pct": round(average, 2),
                  "recent": surprises[:4]},
        source=origin)


def event_radar(events: Optional[dict] = None) -> Signal:
    """
    Analyst actions and news tone, with earnings proximity as context.

    Earnings timing is carried in the evidence but never scored. A report in
    three days does not make a stock bullish; it makes the next move larger
    and less predictable, which changes position size rather than direction.
    """
    if not events or events.get("status") != "OK":
        return Signal(
            "event_radar", None, label="Event Radar",
            source="Benzinga + Marketaux",
            unavailable_reason=(events or {}).get("detail")
            or "No analyst actions and no scored headlines for this symbol.")

    bias = events.get("bias")
    earnings = events.get("earnings") or {}
    detail = events.get("detail") or ""
    if events.get("timing_warning"):
        detail = f"{detail}. {events['timing_warning']}"

    return Signal(
        "event_radar", bias, label="Event Radar",
        detail=detail,
        rule="Analyst upgrades, downgrades and price-target revisions carry "
             "most of this, because each is a dated decision by a named "
             "firm. Headline tone counts for the rest and is scaled down on "
             "a thin sample -- three articles agreeing is an accident of "
             "what was published, not a signal. Earnings proximity is "
             "reported but never scored: a report next week changes how much "
             "to risk, not which way to lean.",
        evidence={
            "analyst_actions": events.get("analyst_actions"),
            "news": events.get("news"),
            "earnings_date": earnings.get("date"),
            "earnings_days_away": earnings.get("days_away"),
            "earnings_proximity": earnings.get("proximity"),
            "components": events.get("components"),
        },
        source="Benzinga + Marketaux")


# ---------------------------------------------------------------------------
# what the company filed, and who owns it
# ---------------------------------------------------------------------------

# An event nine months old is in the price. Only the last quarter counts, and
# the last month counts double.
EVENT_WINDOW_DAYS = 90
RECENT_DAYS = 30

# Filings whose direction is not in doubt. A results release is not here: it
# says results exist, not which way they went, and earnings_results already
# scores the actual numbers.
DEAL_PULL = {
    "Takeover bid for this company": 0.9,
    "Response to a takeover bid": 0.6,
    "The company is buying back stock by tender": 0.6,
    "Merger registration filed": 0.35,
    "Merger proxy sent to shareholders": 0.35,
    "Merger proxy (preliminary)": 0.3,
    "Merger communication": 0.25,
    "Acquisition or disposal completed": 0.2,
    "Material agreement signed": 0.15,
    "Material agreement ended": -0.3,
    "Change of control": 0.4,
}


def _recent(events: Optional[dict], categories: tuple) -> list:
    rows = (events or {}).get("events") or []
    out = []
    for row in rows:
        age = row.get("days_ago")
        if age is None or age > EVENT_WINDOW_DAYS:
            continue
        if row.get("category") in categories:
            out.append(row)
    return out


def merger_activity(events: Optional[dict] = None) -> Signal:
    """
    Takeover bids, merger agreements and asset sales, from the company's own
    filings.

    A bid for the company is the strongest single thing an 8-K can say about
    direction, which is why this is scored apart from the rest: blended into a
    general "events" number it would be averaged away by routine paperwork.
    """
    if not (events or {}).get("events") and (events or {}).get("status") != "OK":
        return Signal("merger_activity", None, label="Mergers & Deals",
                      unavailable_reason="No filings read for this company.",
                      source="SEC EDGAR")

    rows = _recent(events, ("deal",))
    if not rows:
        return Signal("merger_activity", 0.0, label="Mergers & Deals",
                      detail="No deal filings in the last 90 days",
                      source="SEC EDGAR")

    weighted = 0.0
    used = 0.0
    notes = []
    for row in rows:
        pull = DEAL_PULL.get(row.get("headline"))
        if pull is None:
            continue
        recency = 1.0 if (row.get("days_ago") or 0) <= RECENT_DAYS else 0.5
        weighted += pull * recency
        used += recency
        notes.append(f"{row['headline'].lower()} {row['days_ago']}d ago")

    if not used:
        return Signal("merger_activity", 0.0, label="Mergers & Deals",
                      detail=f"{len(rows)} deal filings, none of them directional",
                      source="SEC EDGAR")

    return Signal("merger_activity", _clamp(weighted / used, -1.0, 1.0),
                  label="Mergers & Deals", detail="; ".join(notes[:3]),
                  evidence={"filings": len(rows)}, source="SEC EDGAR")


def management_change(events: Optional[dict] = None) -> Signal:
    """
    Directors and officers coming and going. Reported, never scored.

    The 8-K item says a senior person changed; it does not say who, or whether
    they jumped or were pushed, and those read opposite ways. So this carries
    no direction -- it sits beside the score as context, and the filing is one
    click away for anyone who wants the particulars.
    """
    if not (events or {}).get("events") and (events or {}).get("status") != "OK":
        return Signal("management_change", None, label="Management Changes",
                      unavailable_reason="No filings read for this company.",
                      source="SEC EDGAR")

    rows = _recent(events, ("leadership",))
    if not rows:
        return Signal("management_change", 0.0, label="Management Changes",
                      detail="No management changes filed in 90 days",
                      source="SEC EDGAR")

    newest = min(rows, key=lambda r: r.get("days_ago") or 999)
    return Signal(
        "management_change", 0.0, label="Management Changes",
        detail=(f"{len(rows)} filed in 90 days; most recent "
                f"{newest['days_ago']}d ago -- read the filing for who"),
        evidence={"filings": len(rows), "newest_days_ago": newest.get("days_ago")},
        source="SEC EDGAR")


def fund_flows(institutional: Optional[dict] = None,
               live: Optional[dict] = None) -> Signal:
    """
    Whether institutions are net buying or net selling the stock.

    Two readings, both of them 13F. The quarterly comparison is the weight of
    money: net shares held across every fund that reports. The live filings
    are the same signal arriving early -- funds that have filed this quarter
    but whose numbers are not yet in the SEC's bulk dataset -- and they only
    nudge, because a handful of filers is not the market.
    """
    data = institutional or {}
    if data.get("status") != "OK":
        return Signal("fund_flows", None, label="Fund Buying & Selling",
                      unavailable_reason=(data.get("detail")
                                          or "No 13F holdings on record."),
                      source="SEC 13F")

    net_pct = _num(data.get("net_share_change_pct"))
    up = data.get("funds_increasing") or 0
    down = data.get("funds_decreasing") or 0
    breadth = ((up - down) / (up + down)) if (up + down) else 0.0

    # Net shares say how much moved; breadth says how many moved it. A 3% net
    # change driven by one giant fund is a different fact from the same change
    # across two thousand of them, so both are in the number.
    size = _scale(net_pct if net_pct is not None else 0.0, -4.0, 4.0)
    bias = _clamp(size * 0.6 + breadth * 0.4, -1.0, 1.0)

    filings = (live or {}).get("filings") or []
    early = [p for f in filings for p in f.get("positions") or []
             if p.get("share_change")]
    if early:
        added = sum(1 for p in early if p["share_change"] > 0)
        trimmed = len(early) - added
        nudge = (added - trimmed) / len(early) * 0.15
        bias = _clamp(bias + nudge, -1.0, 1.0)

    detail = (f"{up} funds added, {down} trimmed; net shares held "
              f"{net_pct:+.1f}%" if net_pct is not None
              else f"{up} funds added, {down} trimmed")
    if early:
        detail += f"; {len(early)} positions in filings since the dataset"

    return Signal("fund_flows", bias, label="Fund Buying & Selling",
                  detail=detail,
                  evidence={"quarter": data.get("latest_quarter"),
                            "net_share_change_pct": net_pct,
                            "live_positions": len(early)},
                  source="SEC 13F")


def dividend_trend(dividends: Optional[dict] = None) -> Signal:
    """
    Whether the payout is rising, held or cut.

    A cut is the sharper signal of the two and is scored harder: boards cut
    dividends when they have to, and raise them when they can. The yield
    itself is deliberately not scored -- a high yield is as often a falling
    price as a generous board.
    """
    data = dividends or {}
    if data.get("status") not in ("OK", "NO_DATA"):
        return Signal("dividend_trend", None, label="Dividend Trend",
                      unavailable_reason=(data.get("detail")
                                          or "No dividend record."),
                      source="Nasdaq")
    if data.get("status") == "NO_DATA" or not data.get("pays_dividend"):
        return Signal("dividend_trend", 0.0, label="Dividend Trend",
                      detail="Pays no dividend", source="Nasdaq")

    trend = data.get("trend")
    growth = _num(data.get("growth_pct"))
    bias = {"rising": 0.5, "flat": 0.0, "cut": -0.9}.get(trend)
    if bias is None:
        return Signal("dividend_trend", 0.0, label="Dividend Trend",
                      detail="Too few payments to judge the trend",
                      source="Nasdaq")

    amount = data.get("latest", {}).get("amount")
    detail = (f"{trend} payout"
              + (f" ({growth:+.1f}% on last year)" if growth is not None else "")
              + (f", last ${amount}" if amount else ""))
    return Signal("dividend_trend", bias, label="Dividend Trend", detail=detail,
                  evidence={"trend": trend, "growth_pct": growth,
                            "yield_pct": data.get("dividend_yield_pct")},
                  source="Nasdaq")


def disparity(reading: Optional[dict] = None) -> Signal:
    """
    How far the options market is leaning, across thirteen readings.

    Tilt is which side the directional readings favour; stretch is how far
    from normal the options market sits regardless of side. The score is the
    tilt scaled by the stretch, because a lean nobody is trading on is not a
    signal -- a 0.6 tilt across a market sitting at its usual balance says
    much less than the same tilt with half the readings stretched.

    Readings that carry no direction -- volume against open interest, IV
    rank, gamma, concentration -- raise the stretch and never the tilt. They
    are loud, not bullish.
    """
    data = reading or {}
    if data.get("status") != "OK" or data.get("tilt") is None:
        return Signal("disparity", None, label="Options Disparity",
                      source="Unusual Whales",
                      unavailable_reason=(data.get("detail")
                                          or "No options readings available."))

    tilt = _num(data.get("tilt")) or 0.0
    stretch = _num(data.get("stretch")) or 0.0
    bias = _clamp(tilt * min(1.0, stretch / 40.0))

    stretched = data.get("readings_stretched") or 0
    available = data.get("readings_available") or 0
    loudest = sorted(
        [r for r in data.get("readings") or [] if r.get("available")],
        key=lambda r: -(r.get("stretch") or 0))[:2]

    return Signal(
        "disparity", bias, label="Options Disparity",
        detail=(f"{stretched} of {available} readings stretched, leaning "
                f"{data.get('leaning')}"
                + (f" -- {loudest[0]['label'].lower()} {loudest[0]['detail']}"
                   if loudest else "")),
        rule="Thirteen readings of the options market against its own "
             "balance: call versus put volume, open interest and premium, "
             "volume against open interest, IV against its own year, skew, "
             "delta, gamma, dealer positioning, strike and expiry "
             "concentration, sweeps, and the tape against the day's price "
             "move. The ones with a direction set the tilt; the rest only "
             "raise the stretch. Score is tilt scaled by stretch.",
        evidence={
            "stretch": stretch, "tilt": tilt,
            "leaning": data.get("leaning"),
            "readings_stretched": stretched,
            "readings_available": available,
            "coverage_pct": data.get("coverage_pct"),
            "readings": {r["name"]: r["value"]
                         for r in data.get("readings") or [] if r.get("available")},
        },
        source="Unusual Whales + option chain")


# How the company is paying for itself, and what that costs the holder. Almost
# all of it reads one way: a company raising equity, having debt called early
# or writing assets down is not doing it from strength. Buybacks are the
# bullish mirror and live in merger_activity, which is where the tender offer
# is filed.
FUNDING_PULL = {
    "Shares sold privately": -0.6,
    "Share offering": -0.5,
    "New debt or obligation": -0.2,
    "Debt acceleration": -0.8,
    "Bankruptcy or receivership": -1.0,
    "Accounts cannot be relied on": -0.9,
    "Listing rule notice": -0.7,
    "Restructuring costs": -0.3,
    "Asset write-down": -0.4,
}

FUNDING_CATEGORIES = ("funding", "distress", "restructuring", "listing")


def funding_activity(events: Optional[dict] = None) -> Signal:
    """
    Equity raises, new debt, covenant trouble and write-downs.

    Scored apart from mergers because they are different questions asked of
    the same filing cabinet: a takeover bid is what someone will pay for the
    company, a share offering is the company asking holders to pay for it.
    Averaged together they would cancel, which is how a dilutive raise in a
    quarter with deal talk ends up reading as neutral.
    """
    if not (events or {}).get("events") and (events or {}).get("status") != "OK":
        return Signal("funding_activity", None, label="Funding & Debt",
                      unavailable_reason="No filings read for this company.",
                      source="SEC EDGAR")

    rows = _recent(events, FUNDING_CATEGORIES)
    if not rows:
        return Signal("funding_activity", 0.0, label="Funding & Debt",
                      detail="No funding or balance-sheet filings in 90 days",
                      source="SEC EDGAR")

    weighted = 0.0
    used = 0.0
    notes = []
    for row in rows:
        pull = FUNDING_PULL.get(row.get("headline"))
        if pull is None:
            continue
        recency = 1.0 if (row.get("days_ago") or 0) <= RECENT_DAYS else 0.5
        weighted += pull * recency
        used += recency
        notes.append(f"{row['headline'].lower()} {row['days_ago']}d ago")

    if not used:
        return Signal("funding_activity", 0.0, label="Funding & Debt",
                      detail=f"{len(rows)} filings, none of them directional",
                      source="SEC EDGAR")

    return Signal("funding_activity", _clamp(weighted / used, -1.0, 1.0),
                  label="Funding & Debt", detail="; ".join(notes[:3]),
                  rule="Equity sold privately, new or accelerated debt, "
                       "write-downs, restructuring charges and listing "
                       "notices, faded by age over ninety days. Buybacks are "
                       "not here -- a tender offer is filed as a deal and "
                       "scored there.",
                  evidence={"filings": len(rows)}, source="SEC EDGAR")
