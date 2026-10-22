"""Data Health for the Earnings Trade equity model (Phase 1, strict).

Verifies, per parameter, whether real and provably-fresh data reached the scoring
engine -- NOT whether a request returned HTTP 200. Two strict rules:

  1. A factor is LIVE only when a VERIFIED observation timestamp exists AND it is
     within that data class's freshness window. No timestamp -> never LIVE.
  2. Freshness and direction are separate axes. `status` is a data-health state
     only: LIVE | DELAYED | STALE | UNVERIFIED | MISSING | UNSUPPORTED | ERROR.
     Direction (Bullish / Bearish / Neutral) is reported separately -- NEUTRAL is
     "no directional edge", never a freshness status.

Today the pipeline retains an observation timestamp only for short interest
(`as_of`) and the 13F quarter; quotes and estimates carry no `as_of` (the live
quote path itself sets as_of=None), so those read UNVERIFIED rather than LIVE.
Nothing is gap-filled: a missing input is MISSING, not a zero, and never upgraded.

High-confidence BUY/SELL is blocked when any essential (critical) directional
input is MISSING / ERROR / UNSUPPORTED.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

SOURCE = "Earnings Trade — Data Health (equity model, strict)"

# Critical directional inputs. If too little of this weight is present, the panel
# flags INSUFFICIENT DATA; if any one is MISSING, high-confidence calls are blocked.
CRITICAL = {"eps_estimates", "revenue_estimates", "price_action", "options_positioning"}
CRITICAL_MIN_COVERAGE = 60.0        # % of critical weight that must be present
NOT_PRESENT = {"MISSING", "ERROR", "UNSUPPORTED"}

# Freshness windows for a VERIFIED LIVE reading.
QUOTE_LIVE_MIN = 5.0                 # a quote timestamp within 5 min, during RTH
SHORTINT_FRESH_DAYS = 18.0          # short interest settles ~twice a month

# param name -> (provider label, endpoint/method, provider key, data class)
#   provider key: "uw" | "finnhub" | "uw+sec" | "uw+13f"
#   data class  : "quote" | "tape" | "flow13f" | "filed" | "shortint"
PROVIDERS: dict[str, tuple[str, str, str, str]] = {
    "eps_estimates":       ("Unusual Whales", "/api/companies/{t}/earnings-estimates", "uw", "filed"),
    "revenue_estimates":   ("Unusual Whales", "/api/companies/{t}/earnings-estimates", "uw", "filed"),
    "guidance":            ("Unusual Whales", "/api/companies/{t}/earnings-estimates", "uw", "filed"),
    "historical_reaction": ("Unusual Whales", "/api/stock/{t}/earnings", "uw", "filed"),
    "price_action":        ("Unusual Whales", "/api/stock/{t}/ohlc/1d + option-contracts", "uw", "quote"),
    "sector_market":       ("Unusual Whales", "/api/stock/SPY/quote", "uw", "quote"),
    "options_positioning": ("Unusual Whales + SEC 13F", "options overview + 13F holdings", "uw+13f", "flow13f"),
    "unusual_options":     ("Unusual Whales", "/api/option-activity/unusual", "uw", "tape"),
    "disparity":           ("Unusual Whales", "option chain / disparity", "uw", "tape"),
    "dark_pool":           ("Unusual Whales", "/api/stock/{t}/stock-volume-price-levels", "uw", "quote"),
    "peer_readthrough":    ("Finnhub", "/stock/peers · /stock/earnings · /stock/recommendation", "finnhub", "filed"),
    "short_insider":       ("Unusual Whales + SEC EDGAR", "/api/shorts/{t}/interest-float/v2 + insider", "uw+sec", "shortint"),
}

# How each class's freshness reads when the data is present (for the display
# detail -- NOT the status). The status is computed strictly from a real timestamp.
_FRESH_DETAIL = {
    "quote":    "Quote/last price — no observation timestamp is retained, so freshness is UNVERIFIED.",
    "tape":     "OPRA options tape — delivered on a 15-minute licensing delay.",
    "flow13f":  "Delayed options flow (15-min) combined with quarterly 13F holdings.",
    "filed":    "Analyst estimate / filing — no observation timestamp is retained, so freshness is UNVERIFIED.",
    "shortint": "Short interest — settles about twice a month; judged by its as-of date.",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session() -> str:
    try:
        import live_market_service as mkt
        return (mkt.market_clock() or {}).get("session") or "CLOSED"
    except Exception:  # noqa: BLE001
        return "CLOSED"


def _age_days(as_of: Optional[str]) -> Optional[float]:
    s = _age_seconds(as_of)
    return None if s is None else s / 86400.0


def _age_minutes(as_of: Optional[str]) -> Optional[float]:
    s = _age_seconds(as_of)
    return None if s is None else s / 60.0


def _age_seconds(as_of: Optional[str]) -> Optional[float]:
    if not as_of:
        return None
    try:
        ts = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())
    except (TypeError, ValueError):
        return None


def _unavailable_status(provider_key: str, uw_state: dict) -> str:
    """Why is this factor absent? MISSING unless the provider tells us more."""
    if provider_key.startswith("uw"):
        st = (uw_state or {}).get("status")
        if st == "ENTITLEMENT_REQUIRED":
            return "UNSUPPORTED"
        if st in ("RATE_LIMITED", "BUDGET_EXHAUSTED") or (uw_state or {}).get("blocked"):
            return "ERROR"
        if st == "PROVIDER_NOT_CONFIGURED":
            return "ERROR"
        return "MISSING"
    if provider_key == "finnhub":
        try:
            import finnhub_service as fh
            return "MISSING" if fh.configured() else "UNSUPPORTED"
        except Exception:  # noqa: BLE001
            return "UNSUPPORTED"
    return "MISSING"


def _present_status(klass: str, verified_asof: Optional[str], session: str) -> tuple[str, Optional[str]]:
    """Data-health status for a PRESENT factor, strict about LIVE.

    Returns (status, verified_asof_used). LIVE only with a real timestamp inside
    the window; otherwise DELAYED (known bounded delay), STALE (timestamp but
    old), or UNVERIFIED (present, no timestamp to confirm freshness)."""
    if klass == "tape":
        return "DELAYED", None                      # OPRA 15-min licensing delay
    if klass == "flow13f":
        return "DELAYED", None                      # delayed flow + quarterly 13F
    if klass == "shortint":
        age = _age_days(verified_asof)
        if age is None:
            return "UNVERIFIED", None
        return ("DELAYED" if age <= SHORTINT_FRESH_DAYS else "STALE"), verified_asof
    # "quote" / "filed": LIVE needs a verified, recent timestamp.
    age_min = _age_minutes(verified_asof)
    if age_min is not None and klass == "quote" and session == "OPEN" and age_min <= QUOTE_LIVE_MIN:
        return "LIVE", verified_asof
    if verified_asof:
        return "STALE", verified_asof               # have a timestamp, but it's old
    return "UNVERIFIED", None                        # present, no observation timestamp


def _direction(p: dict) -> Optional[str]:
    if not p.get("available") or p.get("bias") is None:
        return None
    lean = p.get("leaning")
    if lean in ("Bullish", "Bearish", "Neutral"):
        return lean
    b = float(p["bias"])
    return "Bullish" if b > 0.15 else "Bearish" if b < -0.15 else "Neutral"


def health(symbol: str, profile: str = "default") -> dict:
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"status": "INVALID_SYMBOL", "symbol": symbol, "source": SOURCE}

    import earnings_equity_service as eq
    analysis = eq.get_analysis(symbol, profile=profile)
    params = analysis.get("params") or []
    si_meta = analysis.get("short_interest") or {}
    si_asof = si_meta.get("as_of")
    session = _session()

    try:
        import unusualwhales_service as uw
        uw_state = uw.provider_status()
    except Exception:  # noqa: BLE001
        uw_state = {}
    try:
        import finnhub_service as fh
        fh_conf = fh.configured()
    except Exception:  # noqa: BLE001
        fh_conf = False
    try:
        import finviz_service as fv
        fv_conf = fv.configured()
    except Exception:  # noqa: BLE001
        fv_conf = False

    rows: list[dict] = []
    counts: dict[str, int] = {}
    for p in params:
        name = p.get("name")
        prov = PROVIDERS.get(name)
        if not prov:
            continue
        provider, endpoint, pkey, klass = prov
        available = bool(p.get("available"))

        if available:
            verified = si_asof if name == "short_insider" else None
            status, used_asof = _present_status(klass, verified, session)
        else:
            status = _unavailable_status(pkey, uw_state)
            used_asof = None

        counts[status] = counts.get(status, 0) + 1
        raw = None
        if name == "short_insider" and si_meta.get("pct_float") is not None:
            raw = {"short_pct_float": si_meta.get("pct_float"),
                   "days_to_cover": si_meta.get("days_to_cover"), "as_of": si_asof}

        rows.append({
            "name": name, "label": p.get("label"), "weight": p.get("weight"),
            "provider": provider, "endpoint": endpoint, "data_class": klass,
            "critical": name in CRITICAL,
            "status": status,
            "verified_timestamp": used_asof,          # the only honest "last update"
            "freshness_detail": _FRESH_DETAIL.get(klass, ""),
            "direction": _direction(p),               # Bullish/Bearish/Neutral or None
            "bias": p.get("bias"),
            "raw_value": raw,
            "contribution": p.get("points"),
            "contribution_label": p.get("points_label"),
            "available": available,
            "unavailable_reason": p.get("unavailable_reason") or "",
        })

    def _cov(subset: Optional[set]) -> float:
        tot = sum(r["weight"] for r in rows if subset is None or r["name"] in subset) or 0
        cov = sum(r["weight"] for r in rows
                  if (subset is None or r["name"] in subset) and r["available"])
        return round(cov / tot * 100, 1) if tot else 0.0

    coverage = _cov(None)
    critical_coverage = _cov(CRITICAL)
    missing_critical = [r["label"] for r in rows if r["critical"] and r["status"] in NOT_PRESENT]
    block_high_confidence = bool(missing_critical)

    return {
        "status": "OK", "symbol": symbol, "source": SOURCE, "as_of": _now(),
        "session": session,
        "coverage_pct": coverage,
        "critical_coverage_pct": critical_coverage,
        "insufficient_data": critical_coverage < CRITICAL_MIN_COVERAGE,
        "block_high_confidence": block_high_confidence,
        "blocked_inputs": missing_critical,
        "counts": counts,
        "rows": rows,
        "providers": {
            "unusual_whales": {
                "configured": uw_state.get("configured"), "status": uw_state.get("status"),
                "blocked": uw_state.get("blocked"), "env_var": uw_state.get("env_var"),
                "daily_limit": uw_state.get("daily_limit"), "app_left": uw_state.get("app_left"),
            },
            "finnhub": {"configured": fh_conf, "env_var": "FINNHUB_API_KEY"},
            "finviz": {"configured": fv_conf, "env_var": "FINVIZ_API_TOKEN"},
            "sec_13f": {"note": "public; gated by quarterly 13F ingest"},
        },
        "note": ("Status is a data-health state only (LIVE needs a verified, fresh "
                 "observation timestamp); direction is separate. The pipeline retains "
                 "an observation timestamp only for short interest and 13F today, so "
                 "quotes and estimates read UNVERIFIED rather than LIVE. Nothing is "
                 "gap-filled; a missing input stays MISSING."),
    }
