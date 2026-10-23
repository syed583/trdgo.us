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


def effective_eq(symbol: str, eq: Optional[dict]) -> tuple[Optional[dict], Optional[float], str]:
    """The equity analysis to score from, with the age of its SIGNALS and where they came from.

    The earnings snapshot carries the signals as of when it was built. If the live directional
    cache holds a fresher, similarly complete reading, use that instead (no extra API calls)."""
    snap_age = age_s(f"earn:equity:{symbol.upper()}")
    live = live_signals(symbol)
    base_n = len((eq or {}).get("signals") or {})
    if live and (snap_age is None or live[1] < snap_age) and len(live[0]) >= max(1, int(base_n * 0.8)):
        return dict(eq or {}, signals=live[0]), live[1], "live directional"
    return eq, snap_age, "earnings snapshot"


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


def evaluate(row: dict, *, phase: Optional[str], signals_age_s: Optional[float],
             price_age: Optional[float], model_status: Optional[str] = None,
             inputs_label: str = "signal inputs") -> dict:
    """{'ok': bool, 'reasons': [...], 'missing': [...]} -- pure, no I/O."""
    pol = policy()
    reasons: list[str] = []
    mx = max_signal_age(phase)
    if signals_age_s is None:
        reasons.append(f"{inputs_label} have no build time (never refreshed)")
    elif signals_age_s > mx:
        reasons.append(f"{inputs_label} are {_fmt(signals_age_s)} old (max {_fmt(mx)} in this session phase)")
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


def gate(row: dict, *, phase: Optional[str], signals_age_s: Optional[float],
         price_age: Optional[float], model_status: Optional[str] = None,
         inputs_label: str = "signal inputs", inputs_source: str = "") -> dict:
    """Apply the gate to a scored row in place and return it."""
    ev = evaluate(row, phase=phase, signals_age_s=signals_age_s, price_age=price_age,
                  model_status=model_status, inputs_label=inputs_label)
    row["data"] = {"signals_age_s": None if signals_age_s is None else round(signals_age_s),
                   "price_age_s": None if price_age is None else round(price_age),
                   "inputs": inputs_source or None, "phase": phase, "ok": ev["ok"]}
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
            "data_policy": {"max_signal_age_s": max_signal_age(phase),
                            "max_price_age_s": policy()["max_price_age_s"],
                            "min_coverage_ratio": policy()["min_coverage_ratio"]}}


def tradable(row: dict) -> bool:
    """May a NEW entry be opened from this row? Used by the paper-trade tracker."""
    return bool(row and not row.get("wait") and row.get("signal") in ("BUY", "SELL")
                and (row.get("data") or {}).get("ok", True))
