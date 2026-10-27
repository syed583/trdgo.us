"""Trade-PERMISSION rules for the six intraday paper-trading strategies (workflow v2).

This module never computes, changes or reads a strategy's formula, weight or threshold. A strategy produces its own score and its
own BUY (>= 55) / SELL (<= 45) call exactly as before. Everything here decides whether that call may become a PAPER TRADE:

    classify_parameters   what each parameter of the strategy actually is right now: available, missing, neutral, stale,
                          not implemented, not applicable to this session, or unavailable in the pre-market
    coverage / agreement  judged on the parameters ELIGIBLE for this strategy in this session, not on a fixed 100
    validate_quote        a fresh, two-sided, sane, executable price (stricter in the pre-market)
    validate_liquidity    price and average-volume floors (the live engine's own floors)
    persistence           the same side seen on distinct pieces of evidence, never on one cached snapshot read twice
    confirm_candles       closed 5-minute / 15-minute candles (regular session only; the forming candle never counts)
    check_risk            exposure, position-count, duplicate and daily-loss limits

Every function is pure (arguments in, verdict out) so each rule can be tested on its own. Nothing here calls a provider or
the database; intraday_workflow.py supplies the inputs and records the outcome.

Reject codes (the text is the contract with the audit log and the blotter):
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta
from typing import Any, Optional

import tab_settings as cfg

WAIT_STALE = "WAIT - STALE DATA"
WAIT_INSUFFICIENT = "WAIT - INSUFFICIENT DATA"
WAIT_UNCONFIRMED = "WAIT - UNCONFIRMED SIGNAL"
WAIT_PRICE = "WAIT - INVALID PRICE"
WAIT_LIQUIDITY = "WAIT - LOW LIQUIDITY"
WAIT_LIMIT = "WAIT - POSITION LIMIT"
WAIT_DUPLICATE = "WAIT - DUPLICATE ENTRY"
WAIT_SESSION = "WAIT - SESSION CLOSED"
WAIT_LOCKED = "WAIT - ENTRIES LOCKED"
WAIT_INSTRUMENT = "WAIT - UNSUPPORTED INSTRUMENT"
WAIT_LOSS = "WAIT - DAILY LOSS LIMIT"
WAIT_OPENING = "WAIT - OPENING WINDOW"
ALL_CODES = (WAIT_STALE, WAIT_INSUFFICIENT, WAIT_UNCONFIRMED, WAIT_PRICE, WAIT_LIQUIDITY, WAIT_LIMIT, WAIT_DUPLICATE,
             WAIT_SESSION, WAIT_LOCKED, WAIT_INSTRUMENT, WAIT_LOSS, WAIT_OPENING)

STRATEGIES = ("chatgpt", "grok", "meta", "kimi", "gemini", "claude_pro")
PREMARKET, REGULAR = "PREMARKET", "REGULAR"

# ----------------------------------------------------------------------------------------------------------------------
# What every parameter really is.  Parameter names and members are copied from the strategies' own source (see the audit
# TRDGO_INTRADAY_STRATEGIES_AUDIT.md, Report B); tests/test_intraday_execution.py fails if a strategy's parameter set drifts
# from this table, so it cannot silently go out of date.
# ----------------------------------------------------------------------------------------------------------------------
UNIMPLEMENTED = "UNIMPLEMENTED"          # the strategy declares a weight but no data source exists in the code
NOT_APPLICABLE = "NOT_APPLICABLE"        # no source on this data path (e.g. an engine-only parameter before the open)

_UNIMPL_CHATGPT = ("market_structure", "liquidity_sweeps", "absorption", "volume_profile", "supply_demand",
                   "breakout_quality", "volatility")
_GROK_EQ = {
    "options_flow": ["options_flow"], "trend_price": ["ema_trend", "price_action"], "unusual_activity": ["unusual_activity"],
    "volume_pcr": ["volume_pcr"], "key_levels": ["key_levels"], "oi_change_expiry": ["daily_oi_change", "flow_by_expiry"],
    "event_radar": ["event_radar"], "rsi_momentum": ["rsi"], "disparity": ["disparity"],
    "volatility_em": ["implied_volatility", "expected_move"],
}
REGISTRY: dict = {
    "grok": {"equity": _GROK_EQ},
    "kimi": {"equity": _GROK_EQ},
    "meta": {"equity": {
        "options_flow": ["options_flow"], "ema_trend_price": ["ema_trend", "price_action"],
        "unusual_activity": ["unusual_activity"], "key_levels": ["key_levels"], "volume_pcr": ["volume_pcr"],
        "daily_oi_change": ["daily_oi_change"], "event_radar": ["event_radar"], "rsi_momentum": ["rsi"]}},
    "gemini": {"equity": {
        "options_flow": ["options_flow"], "unusual_activity": ["unusual_activity"], "disparity": ["disparity"],
        "volume_pcr": ["volume_pcr"], "key_levels": ["key_levels"],
        "oi_change": ["daily_oi_change", "flow_by_expiry", "oi_positioning"], "ema_trend": ["ema_trend"],
        "rsi_price": ["rsi", "price_action"], "insider_ownership": ["insider_activity", "short_insider"],
        "earnings_catalyst": ["earnings_results", "merger_activity", "funding_activity"],
        "dividends_event": ["dividend_trend", "event_radar"]}},
    "chatgpt": {
        "equity": {
            "options_flow": ["options_flow"], "order_flow": ["disparity"], "vwap_ema": ["ema_trend"], "rvol": ["volume_pcr"],
            "gamma": ["key_levels"], "unusual": ["unusual_activity"], "momentum": ["rsi"], "catalyst": ["event_radar"],
            "oi_change": ["daily_oi_change"], "breadth_mtf": ["@breadth"],
            "rel_strength": NOT_APPLICABLE, "orb": NOT_APPLICABLE, "intermarket": NOT_APPLICABLE,
            **{k: UNIMPLEMENTED for k in _UNIMPL_CHATGPT}},
        "live": {
            "options_flow": ["@engine"], "order_flow": NOT_APPLICABLE,        # per-name net premium exists on the detail view only
            "vwap_ema": ["@engine"], "rvol": ["@engine"], "rel_strength": ["@engine"], "gamma": ["@engine"], "orb": ["@engine"],
            "unusual": ["@engine"], "momentum": ["@engine"], "catalyst": ["@engine"], "oi_change": ["daily_oi_change"],
            "intermarket": ["@engine"], "breadth_mtf": ["@breadth"], **{k: UNIMPLEMENTED for k in _UNIMPL_CHATGPT}}},
    "claude_pro": {
        "equity": {
            "options_flow": ["options_flow"], "price_structure": ["ema_trend"], "gamma_levels": ["key_levels"],
            "volume_pc": ["volume_pcr"], "market_context": ["@breadth"], "unusual_activity": ["unusual_activity"],
            "momentum": ["rsi"], "premarket_bias": NOT_APPLICABLE},
        "live": {
            "options_flow": ["@engine"], "price_structure": ["@engine"], "gamma_levels": ["@engine"], "volume_pc": ["@engine"],
            "market_context": ["@engine", "@breadth"], "unusual_activity": ["@engine"], "momentum": ["@engine"],
            "premarket_bias": ["@engine"]}},       # the engine's `gap` only: the second member (`coi`) does not exist
}

# data classes (from the existing SIGNAL_DATA_TYPE in tab_settings; listed options are NOT traded before 09:30 ET)
TAPE, REFERENCE, DAILY, SLOW, ENGINE = "TAPE", "REFERENCE", "DAILY", "SLOW", "ENGINE"
_CLASS_OF_TYPE = {"options_flow": TAPE, "options_positioning": REFERENCE, "daily_oi": REFERENCE,
                  "price_action_daily": DAILY, "earnings_fundamentals": SLOW, "ownership_insider": SLOW}
_EXTRA_CLASS = {"@breadth": TAPE, "@engine": ENGINE, "short_insider": SLOW}


def signal_class(member: str) -> str:
    if member in _EXTRA_CLASS:
        return _EXTRA_CLASS[member]
    return _CLASS_OF_TYPE.get(cfg.SIGNAL_DATA_TYPE.get(member, "options_positioning"), REFERENCE)


def data_path(session: str) -> str:
    """Which of a strategy's two data paths is live: the engine scores the regular session, equity signals the pre-market."""
    return "live" if session == REGULAR else "equity"


def registry_for(strategy: str, session: str) -> dict:
    """The parameter registry of the data path in force. A strategy with no live engine (Grok, Kimi, Meta, Gemini) scores the
    regular session from the same equity-signal path as the pre-market."""
    paths = REGISTRY.get(strategy) or {}
    return paths.get(data_path(session)) or paths.get("equity") or {}


# category names of a parameter in one snapshot
AVAILABLE, NEUTRAL, MISSING, STALE = "AVAILABLE", "NEUTRAL", "MISSING", "STALE"
UNAVAILABLE_PREMARKET, ZERO_WEIGHT = "UNAVAILABLE_PREMARKET", "ZERO_WEIGHT"
ELIGIBLE_CATEGORIES = (AVAILABLE, NEUTRAL, MISSING, STALE)


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def member_fresh(member: str, meta: Optional[dict], session: str, now: datetime, bounds: Optional[dict],
                 prev_close: Optional[datetime]) -> tuple[bool, str]:
    """Is the observation behind one signal valid for THIS session? ``meta`` = {"age_s", "status", "source"} from the feed.

    * TAPE (options flow, unusual activity, volume/put-call, expiry flow, market breadth): options trade 09:30-16:00 ET only, so
      the observation must be from today's regular session; nothing before the open qualifies.
    * REFERENCE / DAILY (positioning, open interest, daily-bar indicators): must have been built AFTER the last completed close
      and be within the existing per-type limit for the session phase.
    * SLOW (events, fundamentals, filings): the existing (long) per-type limit.
    The engine's own 5-minute data is judged by the engine score's age (done by the caller)."""
    if member == "@engine":
        return True, "engine"
    if not meta:
        return False, "no observation time"
    age = _num(meta.get("age_s"))
    if age is None:
        return False, "no observation time"
    built = now - timedelta(seconds=age)
    cls = signal_class(member)
    typ = cfg.SIGNAL_DATA_TYPE.get(member, "options_positioning")
    phase = "rth" if session == REGULAR else "pre"
    import intraday_feed as feed
    _live, mx = feed.limits_for_type(typ, phase)
    if age > mx:
        return False, f"{age:.0f}s old (max {mx:.0f}s)"
    if cls == TAPE:
        if session != REGULAR or bounds is None or built < bounds["regular_open"]:
            return False, "not an observation of today's regular session"
        return True, "ok"
    if cls in (REFERENCE, DAILY):
        if prev_close is not None and built < prev_close:
            return False, "built before the last completed close"
        return True, "ok"
    return True, "ok"


def classify_parameters(strategy: str, session: str, params: list, signal_meta: Optional[dict], *, now: datetime,
                        bounds: Optional[dict], prev_close: Optional[datetime], engine_ok: bool = True) -> list:
    """One entry per parameter of the snapshot: {name, weight, bias, points, category, members, reason}.

    ``params`` are the strategy's own rows ({"name", "max", "bias", "points"}); nothing about them is changed."""
    reg = registry_for(strategy, session)
    meta = signal_meta or {}
    out = []
    for p in params or []:
        name = p.get("name")
        weight = _num(p.get("max")) or 0.0
        bias = _num(p.get("bias"))
        spec = reg.get(name)
        row = {"name": name, "label": p.get("label"), "weight": weight, "bias": bias, "points": _num(p.get("points")),
               "members": spec if isinstance(spec, list) else [], "reason": ""}
        if spec is None:
            row.update(category=MISSING, reason="parameter not in the workflow registry")     # fail visible, never silent
        elif spec == UNIMPLEMENTED:
            row.update(category=UNIMPLEMENTED, reason="declared with a weight but no data source exists in the code")
        elif spec == NOT_APPLICABLE:
            row.update(category=NOT_APPLICABLE, reason="no data source on this path / session")
        elif weight == 0:
            row.update(category=ZERO_WEIGHT, reason="weight 0: contributes nothing to the score")
        elif session == PREMARKET and any(signal_class(m) == TAPE for m in spec):
            row.update(category=UNAVAILABLE_PREMARKET, reason="options-tape data: the options market is closed before 09:30 ET")
        else:
            judged = []                                         # (member, ok, why) for the members the feed reported on
            for m in spec:
                if m == "@engine":
                    judged.append((m, engine_ok, "engine score fresh" if engine_ok else "engine score stale"))
                elif m == "@breadth":
                    judged.append((m, True, "shared 5-minute cache"))
                elif m in meta:
                    ok, why = member_fresh(m, meta.get(m), session, now, bounds, prev_close)
                    judged.append((m, ok, why))
            bad = [f"{m}: {why}" for m, ok, why in judged if not ok]
            good = [m for m, ok, _w in judged if ok]
            if bias is None:
                row.update(category=STALE if bad and not good else MISSING,
                           reason="; ".join(bad) if bad and not good else "no reading")
            elif judged and not good:
                row.update(category=STALE, reason="; ".join(bad))
            else:
                row.update(category=NEUTRAL if bias == 0 else AVAILABLE, reason=("partly stale: " + "; ".join(bad)) if bad else "")
        out.append(row)
    return out


def coverage(classified: list) -> dict:
    """Total and ELIGIBLE coverage, plus the weight in every category. Neutral readings are available data."""
    total_w = sum(r["weight"] for r in classified)
    cats: dict = {}
    for r in classified:
        cats[r["category"]] = cats.get(r["category"], 0.0) + r["weight"]
    eligible_w = sum(cats.get(c, 0.0) for c in ELIGIBLE_CATEGORIES)
    have = cats.get(AVAILABLE, 0.0) + cats.get(NEUTRAL, 0.0)
    return {"total_weight": total_w, "eligible_weight": eligible_w, "available_weight": have,
            "total_pct": round(have / total_w * 100, 1) if total_w else 0.0,
            "eligible_pct": round(have / eligible_w * 100, 1) if eligible_w else 0.0,
            "by_category": {k: round(v, 2) for k, v in cats.items()}}


def agreement(classified: list, direction: str) -> Optional[float]:
    """Share of the valid covered weight whose reading points the signal's way (the existing definition, on valid data only)."""
    side = 1 if direction == "BUY" else -1
    covered = [r for r in classified if r["category"] in (AVAILABLE, NEUTRAL)]
    w = sum(r["weight"] for r in covered)
    if not w:
        return None
    agree = sum(r["weight"] for r in covered if r["category"] == AVAILABLE and (r["bias"] or 0) * side > 0.05)
    return round(agree / w, 3)


def evaluate_data(strategy: str, direction: str, session: str, row: dict, *, now: datetime, bounds: Optional[dict],
                  prev_close: Optional[datetime], engine_ok: bool = True) -> dict:
    """The data-validity verdict of one candidate signal. {'ok', 'code', 'reasons', 'coverage', 'agreement', 'classified'}."""
    ex = cfg.INTRADAY_EXEC
    classified = classify_parameters(strategy, session, row.get("params") or [], row.get("signal_meta"), now=now, bounds=bounds,
                                     prev_close=prev_close, engine_ok=engine_ok)
    cov = coverage(classified)
    agr = agreement(classified, direction)
    reasons: list[str] = []
    code: Optional[str] = None
    stale_names = [r["name"] for r in classified if r["category"] == STALE]
    if row.get("model_status") not in (None, "OK", "equity-fallback"):
        reasons.append(f"live engine status is {row.get('model_status')}")
        code = WAIT_STALE
    if not engine_ok and data_path(session) == "live":
        reasons.append("the live engine score is stale")
        code = WAIT_STALE
    if cov["eligible_weight"] <= 0:
        reasons.append("no parameter is eligible for this strategy in this session")
        code = code or WAIT_INSUFFICIENT
    else:
        if cov["eligible_pct"] < float(ex["min_eligible_coverage_pct"]):
            reasons.append(f"eligible coverage {cov['eligible_pct']:.0f}% < {float(ex['min_eligible_coverage_pct']):.0f}%"
                           + (f" (stale: {', '.join(stale_names)})" if stale_names else ""))
            code = code or (WAIT_STALE if stale_names and cov["by_category"].get(STALE, 0) >= cov["by_category"].get(MISSING, 0)
                            else WAIT_INSUFFICIENT)
        if agr is None or agr < float(ex["min_agreement"]):
            reasons.append(f"only {0 if agr is None else agr * 100:.0f}% of the valid covered weight agrees with the {direction}"
                           f" (needs {float(ex['min_agreement']) * 100:.0f}%)")
            code = code or WAIT_INSUFFICIENT
    return {"ok": not reasons, "code": code, "reasons": reasons, "coverage": cov, "agreement": agr, "classified": classified}


# ------------------------------------------------------------------------------------------------ price and liquidity ---
def validate_quote(q: Optional[dict], *, session: str, side: str, reference_price: Optional[float]) -> dict:
    """Is there a fresh, sane, executable price?  ``q`` = {"price", "bid", "ask", "age_s"}.

    Returns {'ok', 'code', 'reasons', 'fill_price', 'spread_pct', 'assumption'}. The paper entry fills at the ask (BUY) or the
    bid (SELL) when the quote is two-sided; a last-traded price alone is accepted only where the rules allow it (regular
    session) and is then labelled as such."""
    ex = cfg.INTRADAY_EXEC
    out = {"ok": False, "code": WAIT_PRICE, "reasons": [], "fill_price": None, "spread_pct": None, "assumption": None}
    if not q:
        out["reasons"].append("no quote")
        return out
    last, bid, ask, age = (_num(q.get(k)) for k in ("price", "bid", "ask", "age_s"))
    if last is None or last <= 0:
        out["reasons"].append("no valid last price")
    if age is None:
        out["reasons"].append("the quote carries no age")
    elif age > float(ex["quote_max_age_s"][session]):
        out["reasons"].append(f"quote is {age:.0f}s old (max {float(ex['quote_max_age_s'][session]):.0f}s in the {session.lower()})")
    two_sided = bid is not None and ask is not None and bid > 0 and ask > 0
    if (bid is not None and bid <= 0) or (ask is not None and ask <= 0):
        out["reasons"].append("zero or negative bid/ask")
    elif two_sided:
        if ask < bid:
            out["reasons"].append("crossed quote (ask below bid)")
        else:
            mid = (bid + ask) / 2.0
            spread = (ask - bid) / mid * 100.0
            out["spread_pct"] = round(spread, 4)
            lim = float(ex["max_spread_pct"][session])
            if spread > lim:
                out["reasons"].append(f"spread {spread:.3f}% > {lim:.3f}%")
            if last is not None and last > 0 and not (bid - (ask - bid) <= last <= ask + (ask - bid)):
                out["reasons"].append("last price is far outside the bid-ask range")
    elif ex["require_bid_ask"][session]:
        out["reasons"].append(f"a two-sided quote is required in the {session.lower()}")
    if reference_price and last and last > 0:
        disc = abs(last - reference_price) / reference_price * 100.0
        if disc > float(ex["max_price_discrepancy_pct"]):
            out["reasons"].append(f"fresh quote differs {disc:.2f}% from the price the signal was priced at")
    if out["reasons"]:
        return out
    if two_sided:
        out["fill_price"] = float(ask if side == "BUY" else bid)
        out["assumption"] = "entry at the ask (long) / bid (short) of a fresh two-sided quote"
    else:
        out["fill_price"] = float(last)
        out["assumption"] = "entry at the last trade (no two-sided quote available in the regular session)"
    out.update(ok=True, code=None)
    return out


def average_volume(candles: list, today_iso: str) -> Optional[float]:
    """20-day average daily volume, excluding today's partial bar. None when fewer than 10 complete days exist."""
    per_day: dict = {}
    for b in candles or []:
        d = str(b.get("date") or b.get("t") or "")[:10]
        v = _num(b.get("volume"))
        if d and d != today_iso and v is not None:
            per_day[d] = v
    vols = [per_day[d] for d in sorted(per_day)][-20:]
    return (sum(vols) / len(vols)) if len(vols) >= 10 else None


def validate_liquidity(price: Optional[float], avg_volume: Optional[float]) -> dict:
    ex = cfg.INTRADAY_EXEC
    reasons = []
    if price is None or price < float(ex["min_price"]):
        reasons.append(f"price {price} below the {float(ex['min_price']):.0f} floor")
    if avg_volume is None:
        reasons.append("average volume unknown (fewer than 10 complete days)")
    elif avg_volume < float(ex["min_avg_daily_volume"]):
        reasons.append(f"average volume {avg_volume:,.0f} below {float(ex['min_avg_daily_volume']):,.0f}")
    return {"ok": not reasons, "code": WAIT_LIQUIDITY if reasons else None, "reasons": reasons, "avg_volume": avg_volume}


# ------------------------------------------------------------------------------------------------------ persistence ---
def fingerprint(classified: list, signal_meta: Optional[dict], now: datetime) -> str:
    """Identifies a piece of EVIDENCE. Reading the same cached snapshot again (same readings, same build times) gives the
    same fingerprint and therefore never counts as a new observation. Build times are quantised to 30 s."""
    built = {}
    for k, m in sorted((signal_meta or {}).items()):
        age = _num((m or {}).get("age_s"))
        if age is not None:
            built[k] = int((now.timestamp() - age) // 30)
    payload = {"b": [(r["name"], r["bias"], r["category"]) for r in classified], "t": built}
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def persistence(observations: list, direction: str) -> dict:
    """``observations`` = [{"ts": epoch, "direction", "evidence"}] oldest first for ONE strategy/symbol/session.
    Passes when the latest ``min_observations`` DISTINCT pieces of evidence all point the same way and span >= ``min_span_s``."""
    ex = cfg.INTRADAY_EXEC
    need, span = int(ex["min_observations"]), float(ex["min_span_s"])
    seen: dict = {}
    for o in observations:
        seen.setdefault(o["evidence"], o)                    # first time each distinct evidence appeared
    distinct = sorted(seen.values(), key=lambda o: o["ts"])
    same = [o for o in distinct if o["direction"] == direction]
    reasons = []
    if len(distinct) >= 1 and distinct[-1]["direction"] != direction:
        reasons.append("the newest evidence points the other way")
    if len(same) < need:
        reasons.append(f"{len(same)} distinct observation(s) of {direction} (needs {need}); an unchanged snapshot read again is not new evidence")
    elif same[-1]["ts"] - same[-need]["ts"] < span:
        reasons.append(f"the {need} observations span {same[-1]['ts'] - same[-need]['ts']:.0f}s (needs {span:.0f}s)")
    return {"ok": not reasons, "code": WAIT_UNCONFIRMED if reasons else None, "reasons": reasons,
            "distinct": len(distinct), "same_side": len(same)}


# ------------------------------------------------------------------------------------------------ candle confirmation ---
def _ema(vals: list, span: int) -> Optional[float]:
    if not vals:
        return None
    k = 2.0 / (span + 1)
    e = vals[0]
    for x in vals[1:]:
        e = x * k + e * (1 - k)
    return e


def _rsi(closes: list, period: int) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag, al = sum(gains[:period]) / period, sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    return 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)


def closed_bars(bars: list, bar_s: int, now: datetime) -> list:
    """Closed, valid, regular-session bars (oldest first): {"dt","o","h","l","c","v"}. A bar whose interval has not ended is
    still forming and is dropped; malformed or duplicate bars are dropped, never repaired."""
    import intraday_indicators as ind
    out: dict = {}
    for b in bars or []:
        dt = ind._et(b.get("start_time") or b.get("t") or b.get("date") or b.get("dt"))
        o, h, l, c = (_num(b.get(k) if b.get(k) is not None else b.get(k[0])) for k in ("open", "high", "low", "close"))
        v = _num(b.get("volume") if b.get("volume") is not None else b.get("v"))
        if dt is None or None in (o, h, l, c) or min(o, h, l, c) <= 0 or h < max(o, c, l) - 1e-9 or l > min(o, c, h) + 1e-9:
            continue
        if dt.weekday() >= 5 or not ind._is_rth(dt) or dt in out:
            continue
        if dt + timedelta(seconds=bar_s) > now:
            continue
        out[dt] = {"dt": dt, "o": o, "h": h, "l": l, "c": c, "v": v or 0.0}
    return [out[k] for k in sorted(out)]


def candle_reading(bars: list, bar_s: int, now: datetime, today) -> dict:
    """The three-vote reading (price vs today's session VWAP, EMA9 vs EMA21, RSI14) of CLOSED candles of one timeframe - the
    same votes and thresholds as the existing 15-minute confirmation (INTRADAY_15M), applied to any bar size. EMA and RSI use
    the recent sessions' closed bars for warm-up; VWAP uses today's session only (needs >= 2 closed candles today)."""
    p = cfg.INTRADAY_15M
    cb = closed_bars(bars, bar_s, now)
    sess = [b for b in cb if b["dt"].date() == today]
    base = {"timeframe": f"{bar_s // 60}m", "bars": len(cb), "session_bars": len(sess)}
    if len(sess) < 2:
        return dict(base, status="WARMUP", detail=f"{len(sess)} closed {bar_s // 60}-minute candle(s) today (needs 2)")
    need = max(int(p["min_bars"]), p["rsi_period"] + 1)                                    # the existing 15-minute rule's warm-up depth
    if len(cb) < need:
        return dict(base, status="INVALID", detail=f"only {len(cb)} closed candles (needs {need})")
    closes = [b["c"] for b in cb]
    num = sum((b["h"] + b["l"] + b["c"]) / 3.0 * b["v"] for b in sess)
    den = sum(b["v"] for b in sess)
    vwap = num / den if den > 0 else None
    ef, es, rsi = _ema(closes[-60:], p["ema_fast"]), _ema(closes[-100:], p["ema_slow"]), _rsi(closes[-100:], p["rsi_period"])
    if vwap is None or ef is None or es is None or rsi is None:
        return dict(base, status="INVALID", detail="VWAP / EMA / RSI could not be computed")
    px = cb[-1]["c"]
    votes = {"vwap": 1 if px > vwap else -1 if px < vwap else 0, "ema": 1 if ef > es else -1 if ef < es else 0,
             "rsi": 1 if rsi >= p["rsi_bull"] else -1 if rsi <= p["rsi_bear"] else 0}
    tot = sum(votes.values())
    bias = 1 if tot >= 2 else -1 if tot <= -2 else 0
    end = cb[-1]["dt"] + timedelta(seconds=bar_s)
    return dict(base, status="OK", bias=bias, votes=votes, close=px, last_bar_end=end.isoformat(), last_bar_end_epoch=end.timestamp(),
                detail=f"{bar_s // 60}m {'bullish' if bias > 0 else 'bearish' if bias < 0 else 'mixed'}: close {px:.2f}, VWAP {vwap:.2f}, "
                       f"EMA{p['ema_fast']}/{p['ema_slow']} {ef:.2f}/{es:.2f}, RSI {rsi:.0f}")


def opening_state(now: datetime, session: str) -> dict:
    """The state of the (configurable, default OFF) regular-session opening rule at ``now``.

    state: "OFF" (rule disabled or not the regular session), "NO_ENTRY" (inside [no_entry_start, no_entry_end)) or "BOTH_REQUIRED"
    (at or after no_entry_end, 5-minute AND 15-minute confirmation both mandatory)."""
    rule = cfg.INTRADAY_EXEC.get("opening_rule") or {}
    if not rule.get("enabled") or session != REGULAR:
        return {"state": "OFF", "enabled": bool(rule.get("enabled"))}
    def hm(t):
        h, m = str(t).split(":")
        return now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
    start, end = hm(rule.get("no_entry_start", "09:30")), hm(rule.get("no_entry_end", "10:00"))
    if start <= now < end:
        return {"state": "NO_ENTRY", "enabled": True, "from": start.strftime("%H:%M"), "until": end.strftime("%H:%M")}
    if now >= end and rule.get("require_both_after_end", True):
        return {"state": "BOTH_REQUIRED", "enabled": True, "from": end.strftime("%H:%M")}
    return {"state": "OFF", "enabled": True}


def confirm_candles(direction: str, session: str, r5: Optional[dict], r15: Optional[dict], now: datetime,
                    require_both: bool = False) -> dict:
    """Candle confirmation, regular session only (the pre-market has no regular-session candle to require).

    * 5-minute: a reading must exist and AGREE (closed candles only).
    * 15-minute: required to agree once it is SUPPORTED (>= 2 closed candles today); during its warm-up (first 30 minutes) the
      5-minute reading alone decides. A 15-minute reading that is stale, missing or invalid after warm-up refuses the entry."""
    if session != REGULAR:
        return {"ok": True, "code": None, "applies": False, "reasons": [], "detail": "no candle confirmation required before the open"}
    want = 1 if direction == "BUY" else -1
    reasons = []
    if not r5 or r5.get("status") != "OK":
        reasons.append("5-minute: " + ((r5 or {}).get("detail") or "no reading"))
    elif r5["bias"] != want:
        reasons.append("5-minute candles do not confirm: " + r5.get("detail", ""))
    if r15 and r15.get("status") == "OK":
        age = (now.timestamp() - float(r15.get("last_bar_end_epoch", 0)))
        if age > float(cfg.INTRADAY_15M["max_close_lag_s"]):
            reasons.append(f"15-minute reading is stale ({age / 60:.0f} min since the newest closed candle)")
        elif r15["bias"] != want:
            reasons.append("15-minute candles do not confirm: " + r15.get("detail", ""))
    elif r15 and r15.get("status") == "WARMUP" and not require_both:
        pass
    elif r15 and r15.get("status") == "WARMUP":
        reasons.append("15-minute: still warming up, but both timeframes are required at this time of day")
    else:
        reasons.append("15-minute: " + ((r15 or {}).get("detail") or "no reading"))
    return {"ok": not reasons, "code": WAIT_UNCONFIRMED if reasons else None, "applies": True, "reasons": reasons,
            "five": r5, "fifteen": r15}


# ------------------------------------------------------------------------------------------------------------- risk ---
def check_risk(*, strategy: str, symbol: str, direction: str, price: float, open_positions: list, day_pnl: dict,
               entries_today: int = 0, session_entry_exists: bool = False) -> dict:
    """Position-count, exposure, duplicate, correlated-position and daily-loss limits.

    ``open_positions`` = [{"strategy","symbol","side","shares","price"}] (price = entry or last); ``day_pnl`` =
    {"total": $, "<strategy>": $} realised + unrealised today. Returns {'ok','code','reasons','readings','quantity'}; the
    quantity is the existing fixed size, reduced (never raised) to fit the exposure limits."""
    ex = cfg.INTRADAY_EXEC
    qty = int(ex["quantity"])
    reasons: list[str] = []
    code: Optional[str] = None
    readings: dict = {}

    def notional(p): return abs(float(p["shares"]) * float(p["price"]))
    mine = [p for p in open_positions if p["strategy"] == strategy]
    same_sym = [p for p in open_positions if p["symbol"] == symbol]
    if any(p["strategy"] == strategy and p["symbol"] == symbol for p in open_positions):
        reasons.append("this strategy already holds this symbol")
        code = WAIT_DUPLICATE
    if session_entry_exists:
        reasons.append("this strategy already opened a position in this symbol in this session")
        code = code or WAIT_DUPLICATE
    gross_total, gross_strat, gross_sym = (sum(notional(p) for p in x) for x in (open_positions, mine, same_sym))
    readings.update(open_total=len(open_positions), open_strategy=len(mine), gross_total=round(gross_total, 2),
                    gross_strategy=round(gross_strat, 2), gross_symbol=round(gross_sym, 2), price=price)
    if price and price > 0:
        room = min(float(ex["max_exposure_total_usd"]) - gross_total, float(ex["max_exposure_per_strategy_usd"]) - gross_strat,
                   float(ex["max_exposure_per_symbol_usd"]) - gross_sym, float(ex["paper_capital_usd"]) - gross_total)
        fit = int(max(0.0, room) // price)
        readings["quantity_that_fits"] = fit
        qty = min(qty, fit)
        if qty < 1:
            reasons.append(f"no exposure room left for one share at {price:.2f}")
            code = code or WAIT_LIMIT
    else:
        reasons.append("no price to size the position")
        code = code or WAIT_PRICE
    if len(open_positions) >= int(ex["max_open_total"]):
        reasons.append(f"{len(open_positions)} open positions (max {int(ex['max_open_total'])})")
        code = code or WAIT_LIMIT
    if len(mine) >= int(ex["max_open_per_strategy"]):
        reasons.append(f"{strategy} holds {len(mine)} positions (max {int(ex['max_open_per_strategy'])})")
        code = code or WAIT_LIMIT
    side = "LONG" if direction == "BUY" else "SHORT"
    same_side = [p for p in same_sym if p["side"] == side]
    readings["strategies_same_symbol_side"] = len(same_side)
    if len(same_side) >= int(ex["max_strategies_same_symbol_side"]):
        reasons.append(f"{len(same_side)} strategies already hold {symbol} {side} (max {int(ex['max_strategies_same_symbol_side'])})")
        code = code or WAIT_LIMIT
    sp, tp = day_pnl.get(strategy, 0.0), day_pnl.get("total", 0.0)
    readings.update(pnl_strategy=round(sp, 2), pnl_total=round(tp, 2))
    if sp <= -float(ex["daily_loss_limit_per_strategy_usd"]):
        reasons.append(f"{strategy} is down {-sp:,.0f} today (limit {float(ex['daily_loss_limit_per_strategy_usd']):,.0f})")
        code = code or WAIT_LOSS
    if tp <= -float(ex["daily_loss_limit_total_usd"]):
        reasons.append(f"all strategies are down {-tp:,.0f} today (limit {float(ex['daily_loss_limit_total_usd']):,.0f})")
        code = code or WAIT_LOSS
    return {"ok": not reasons, "code": code, "reasons": reasons, "readings": readings, "quantity": qty if not reasons else 0}
