"""Data-safety gate for the six AI intraday boards (Grok, Meta, Kimi, Gemini, ChatGPT, Claude Pro).

A BUY/SELL on these boards may open a paper trade, so it must rest on FRESH, COMPLETE data.
Before this gate each board read whatever was cached -- an earnings snapshot of any age, or a
live engine score of any age -- and scored missing inputs as neutral. Now, for every row:

  * the age of the signal inputs, and of the live price, is measured;
  * the share of the model's inputs that actually have data is checked;
  * if anything is stale, missing or the live engine is unhealthy, the row becomes
    WAIT / LOW DATA: no signal, no score (the raw one is kept internally for exit management
    only), the reasons and the missing inputs are listed, and no trade is opened from it.

The gate is symmetrical: it never looks at BUY vs SELL, only at the data behind them.
Scoring formulas, thresholds and exit rules are untouched.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import tab_settings as cfg

WAIT = "WAIT"


# ------------------------------------------------------------------ ages -----
def age_s(key: str) -> Optional[float]:
    """Seconds since the swr value for ``key`` was built; None if never built."""
    import swr
    at = swr.held_at(key)
    return None if at is None else max(0.0, time.time() - at)


def _fmt(sec: float) -> str:
    if sec < 90:
        return f"{sec:.0f}s"
    if sec < 5400:
        return f"{sec / 60:.0f} min"
    if sec < 172800:
        return f"{sec / 3600:.1f} h"
    return f"{sec / 86400:.1f} days"


def live_signals(symbol: str) -> Optional[tuple[dict, float]]:
    """Signals from the live directional-score cache, if it is warm. No API calls."""
    try:
        import live_market_service as market
        import directional_score_service as dss
        key = f"directional:{symbol.upper()}"
        age = market.cache.age(key)
        if age is None:
            return None
        ttl = market.session_ttl(dss.CACHE_TTL_OPEN, dss.CACHE_TTL_CLOSED)
        out = market.cache.get(key, ttl)
        if not out:
            return None
        sig = {s["name"]: s["bias"] for s in out.get("signals", [])
               if s.get("available") and s.get("bias") is not None}
        return (sig, age) if sig else None
    except Exception:  # noqa: BLE001
        return None


def effective_eq(symbol: str, eq: Optional[dict], phase: Optional[str] = None
                 ) -> tuple[Optional[dict], Optional[float], str]:
    """The equity analysis to score from: every signal judged by ITS OWN freshness limit.

    Fast signals (options flow, levels, daily-bar price action ...) come from the shared intraday feed
    or the live directional cache when fresher than the earnings snapshot; slow ones (earnings,
    ownership) from the snapshot against their own, much longer, limits. A signal past its limit is
    removed (the model sees it as missing -- never as bullish or bearish). Returns (analysis, the age
    of the freshest critical input, where the inputs came from); per-signal detail is in ["_meta"]."""
    import intraday_feed as feed
    if phase is None:
        try:
            import intraday_indicators as ind
            phase = ind.market_phase()
        except Exception:  # noqa: BLE001
            phase = None
    snap_age = age_s(f"earn:equity:{symbol.upper()}")
    inputs = feed.effective_inputs(symbol, eq, snap_age, phase, live=live_signals(symbol))
    eq2 = dict(eq or {}, signals=inputs["signals"], _meta=dict(inputs, phase=phase))
    crit_ages = [inputs["meta"][m]["age_s"] for members in feed.CRITICAL_GROUPS.values()
                 for m in members if m in inputs["signals"] and m in inputs["meta"]]
    sig_age = float(min(crit_ages)) if crit_ages else None
    srcs = sorted({m["source"] for m in inputs["meta"].values() if m["status"] != "STALE"})
    return eq2, sig_age, " + ".join(srcs) or "none"


def price_age_s(symbol: str) -> Optional[float]:
    return age_s(f"grokintra:px:{symbol.upper()}")


# -------------------------------------------------------------- evaluation ---
def policy() -> dict:
    return cfg.INTRADAY_SAFETY


def max_signal_age(phase: Optional[str]) -> float:
    m = policy()["max_signal_age_s"]
    return float(m.get(phase or "rth", m["rth"]))


def missing_inputs(row: dict) -> list[str]:
    out: list[str] = []
    for p in row.get("params") or []:
        missing = bool(p.get("no_data")) or (p.get("bias") is None and not p.get("has_data"))
        if missing:
            out.append(str(p.get("label") or p.get("name")))
    return out


def _group_detail(meta: dict, members: tuple) -> str:
    known = [(m, meta["meta"][m]) for m in members if m in meta.get("meta", {})]
    if not known:
        return "no reading"
    return ", ".join(f"{m} {_fmt(i['age_s'])} old (max {_fmt(i['max_s'])})" for m, i in known)


def evaluate(row: dict, *, phase: Optional[str], signals_age_s: Optional[float],
             price_age: Optional[float], model_status: Optional[str] = None,
             inputs_label: str = "signal inputs", meta: Optional[dict] = None) -> dict:
    """{'ok': bool, 'reasons': [...], 'missing': [...]} -- pure, no I/O.

    With ``meta`` (the equity-signal boards) freshness is judged per KIND of data: a critical group
    (options flow; daily-bar price action) with no fresh member blocks the name, while slow data
    (earnings, ownership) is held to its own long limits. Without it (the live 5-minute engine) the
    engine score's age is judged against the session-phase limit."""
    pol = policy()
    reasons: list[str] = []
    if meta is not None:
        import intraday_feed as feed
        for g in meta.get("missing_critical", []):
            reasons.append(f"critical input stale or missing: {g} "
                           f"({_group_detail(meta, feed.CRITICAL_GROUPS[g])})")
    else:
        mx = float(cfg.INTRADAY_FRESHNESS["engine_score"][1])
        if signals_age_s is None:
            reasons.append(f"{inputs_label} have no build time (never refreshed)")
        elif signals_age_s > mx:
            reasons.append(f"{inputs_label} are {_fmt(signals_age_s)} old (max {_fmt(mx)})")
    if row.get("now") is None:
        reasons.append("no live price")
    elif price_age is None:
        reasons.append("live price has no timestamp")
    elif price_age > pol["max_price_age_s"]:
        reasons.append(f"live price is {_fmt(price_age)} old (max {_fmt(pol['max_price_age_s'])})")
    cov = row.get("coverage")
    missing = missing_inputs(row)
    attainable = float(row.get("coverage_attainable") or 100.0)
    need = max(pol["min_coverage_floor_pct"], pol["min_coverage_ratio"] * attainable)
    if cov is not None and cov < need:
        reasons.append(f"only {cov:.0f}% of the model's inputs have data "
                       f"(needs {need:.0f}% of the {attainable:.0f}% this model can have)")
    if model_status not in (None, "OK"):
        reasons.append(f"live 5-minute engine status is {model_status}")
    return {"ok": not reasons, "reasons": reasons, "missing": missing}


def dubai(ts: Optional[float]) -> Optional[str]:
    """Dubai time (GST, UTC+4) for display."""
    if ts is None:
        return None
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(ts, ZoneInfo("Asia/Dubai")).strftime("%d %b %H:%M:%S GST")


_RANK = {"LIVE": 0, "DELAYED": 1, "STALE": 2, "MISSING": 3}


def health_block(row: dict, *, ok: bool, phase: Optional[str], meta: Optional[dict],
                 signals_age_s: Optional[float], price_age: Optional[float],
                 model_status: Optional[str], now: Optional[float] = None) -> dict:
    """LIVE / DELAYED / STALE / MISSING from the age of the data itself -- not from an HTTP 200."""
    import uw_budget
    now = time.time() if now is None else now
    api = uw_budget.get().api_health()
    pol = policy()
    status = "LIVE"
    missing: list[str] = []
    freshest: list[float] = []
    if meta is not None:
        import intraday_feed as feed
        for g, members in feed.CRITICAL_GROUPS.items():
            known = [meta["meta"][m] for m in members if m in meta.get("meta", {})]
            usable = [k for k in known if k["status"] != "STALE"]
            if usable:
                best = min(usable, key=lambda k: k["age_s"])
                freshest.append(best["age_s"])
                if best["status"] == "DELAYED":
                    status = max(status, "DELAYED", key=_RANK.get)
            elif known:
                status = max(status, "STALE", key=_RANK.get)
                missing.append(g)
            else:
                status = max(status, "MISSING", key=_RANK.get)
                missing.append(g)
        timeframe = ("daily bars + today's live bar (EMA / RSI / price action); options flow and positioning "
                     "snapshots; no intraday candle analysis")
    else:
        lim_live, lim_max = float(cfg.INTRADAY_FRESHNESS["engine_score"][0]), float(cfg.INTRADAY_FRESHNESS["engine_score"][1])
        if signals_age_s is None:
            status = "MISSING"
            missing.append("live engine score")
        else:
            freshest.append(signals_age_s)
            status = "LIVE" if signals_age_s <= lim_live else "DELAYED" if signals_age_s <= lim_max else "STALE"
            if status == "STALE":
                missing.append("live engine score")
        timeframe = "5-minute bars (live engine); there is no 15-minute analysis"
    if row.get("now") is None or price_age is None:
        status = "MISSING"
        missing.append("live price")
    elif price_age > pol["max_price_age_s"]:
        status = max(status, "STALE", key=_RANK.get)
        missing.append("live price (stale)")
    elif price_age > cfg.INTRADAY_FRESHNESS["price_volume"][0]:
        status = max(status, "DELAYED", key=_RANK.get)
    if model_status not in (None, "OK"):
        status = max(status, "STALE", key=_RANK.get)
    if status == "LIVE" and api != "OK":
        status = "DELAYED"                              # data is recent but the API is not healthy
    newest = (now - min(freshest)) if freshest else None
    return {"status": status, "api_health": api, "timeframe": timeframe, "phase": phase,
            "signal_generated_at": now, "signal_generated_dubai": dubai(now),
            "market_data_at": newest, "market_data_dubai": dubai(newest),
            "data_age_s": None if not freshest else round(max(freshest)),
            "missing_critical": missing, "eligible_for_new_entries": bool(ok), "display_tz": "Asia/Dubai"}


def gate(row: dict, *, phase: Optional[str], signals_age_s: Optional[float],
         price_age: Optional[float], model_status: Optional[str] = None,
         inputs_label: str = "signal inputs", inputs_source: str = "",
         meta: Optional[dict] = None) -> dict:
    """Apply the gate to a scored row in place and return it."""
    ev = evaluate(row, phase=phase, signals_age_s=signals_age_s, price_age=price_age,
                  model_status=model_status, inputs_label=inputs_label, meta=meta)
    row["data"] = {"signals_age_s": None if signals_age_s is None else round(signals_age_s),
                   "price_age_s": None if price_age is None else round(price_age),
                   "inputs": inputs_source or None, "phase": phase, "ok": ev["ok"],
                   "dropped_stale": (meta or {}).get("dropped") or []}
    row["health"] = health_block(row, ok=ev["ok"], phase=phase, meta=meta, signals_age_s=signals_age_s,
                                 price_age=price_age, model_status=model_status)
    if ev["ok"]:
        return row
    row["signal_raw"] = row.get("signal")            # kept for exit management / audit only
    row["score_raw"] = row.get("score")
    row["signal"] = WAIT
    row["tier"] = "LOW DATA"
    row["score"] = None                              # never show a confidence built on bad data
    row["wait"] = True
    row["wait_reasons"] = ev["reasons"]
    row["missing"] = ev["missing"]
    for k in ("entry", "tp", "sl"):                  # no entry levels for a name we are not trading
        row[k] = None
    return row


# ----------------------------------------------------------------- boards ----
def compose(rows: list[dict], source: str, universe: int, phase: Optional[str] = None) -> dict:
    """The standard board payload, plus a WAIT bucket. Symmetric for BUY and SELL."""
    buyers = sorted([r for r in rows if r.get("signal") == "BUY"], key=lambda r: r["score"], reverse=True)
    sellers = sorted([r for r in rows if r.get("signal") == "SELL"], key=lambda r: r["score"])
    neutral = [r for r in rows if r.get("signal") == "NEUTRAL"]
    wait = sorted([r for r in rows if r.get("signal") == WAIT], key=lambda r: r["symbol"])
    return {"status": "OK", "source": source, "phase": phase,
            "buyers": buyers, "sellers": sellers, "neutral": neutral, "wait": wait,
            "counts": {"buy": len(buyers), "sell": len(sellers), "neutral": len(neutral), "wait": len(wait)},
            "universe": universe, "scored": len(rows) - len(wait),
            "data_policy": {"freshness_s": {k: list(v) for k, v in cfg.INTRADAY_FRESHNESS.items()},
                            "max_price_age_s": policy()["max_price_age_s"],
                            "min_coverage_ratio": policy()["min_coverage_ratio"], "phase": phase,
                            "note": "each input is judged by its own freshness limit (live, maximum seconds); "
                                    "outside the regular session market-data limits are relaxed"}}


def tradable(row: dict) -> bool:
    """May a NEW entry be opened from this row? Used by the paper-trade tracker."""
    return bool(row and not row.get("wait") and row.get("signal") in ("BUY", "SELL")
                and (row.get("data") or {}).get("ok", True))
