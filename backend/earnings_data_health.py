"""Data Health for the Earnings Trade equity model (Phase 1).

For each of the 12 equity parameters this reports, honestly, whether real and
sufficiently fresh data actually reached the scoring engine -- NOT merely whether
a request returned HTTP 200. The equity `Param` keeps only the computed bias
(None = unavailable), so the status is derived from:

  * `param.available` (bias is not None) and the magnitude of the bias,
  * the known provider + data class behind each parameter (the map below),
  * the live provider state (Unusual Whales `provider_status()` / `budget()`,
    Finnhub / Finviz key presence),
  * the one or two real observation timestamps that DO survive (short-interest
    `as_of`, 13F quarter).

Status vocabulary (spec section 5):
  LIVE | DELAYED | STALE | MISSING | ERROR | UNSUPPORTED | NEUTRAL

Never reports a missing input as a genuine zero-point neutral signal: a factor
reads NEUTRAL only when valid data arrived and showed no directional edge.
Per-observation raw values are not retained for most factors today; where a real
timestamp/value exists it is shown, otherwise the computed reading is shown and
the limitation is stated rather than a precise timestamp being invented.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import freshness as fr

SOURCE = "Earnings Trade — Data Health (equity model)"

# Flat band: a valid bias this small is treated as NEUTRAL (no edge), not a lean.
FLAT = 0.08
# Critical directional inputs. If too little of this weight is covered, the panel
# flags INSUFFICIENT DATA so high-confidence signals can be blocked upstream.
CRITICAL = {"eps_estimates", "revenue_estimates", "price_action", "options_positioning"}
CRITICAL_MIN_COVERAGE = 60.0   # % of critical weight that must be present

# param name -> (provider label, endpoint/method, provider key, data class)
#   provider key: "uw" | "finnhub" | "uw+sec" | "uw+13f"   (drives MISSING vs ERROR/UNSUPPORTED)
#   data class  : "snapshot" | "tape" | "filed" | "quarterly"  (drives the freshness stamp)
PROVIDERS: dict[str, tuple[str, str, str, str]] = {
    "eps_estimates":       ("Unusual Whales", "/api/companies/{t}/earnings-estimates", "uw", "filed"),
    "revenue_estimates":   ("Unusual Whales", "/api/companies/{t}/earnings-estimates", "uw", "filed"),
    "guidance":            ("Unusual Whales", "/api/companies/{t}/earnings-estimates", "uw", "filed"),
    "historical_reaction": ("Unusual Whales", "/api/stock/{t}/earnings", "uw", "filed"),
    "price_action":        ("Unusual Whales", "/api/stock/{t}/ohlc/1d + option-contracts", "uw", "snapshot"),
    "sector_market":       ("Unusual Whales", "/api/stock/SPY/quote", "uw", "snapshot"),
    "options_positioning": ("Unusual Whales + SEC 13F", "options overview + 13F holdings", "uw+13f", "quarterly"),
    "unusual_options":     ("Unusual Whales", "/api/option-activity/unusual", "uw", "tape"),
    "disparity":           ("Unusual Whales", "option chain / disparity", "uw", "tape"),
    "dark_pool":           ("Unusual Whales", "/api/stock/{t}/stock-volume-price-levels", "uw", "snapshot"),
    "peer_readthrough":    ("Finnhub", "/stock/peers · /stock/earnings · /stock/recommendation", "finnhub", "filed"),
    "short_insider":       ("Unusual Whales + SEC EDGAR", "/api/shorts/{t}/interest-float/v2 + insider", "uw+sec", "filed"),
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
    if not as_of:
        return None
    try:
        ts = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0)
    except (TypeError, ValueError):
        return None


def _stamp(klass: str, session: str, *, provider: str,
           si_asof: Optional[str] = None) -> dict:
    """Freshness descriptor for a data class, from the real feed behaviour."""
    if klass == "snapshot":
        return fr.for_quote(provider, session)
    if klass == "tape":
        if session == "OPEN":
            return fr.for_tape(15.0)                         # OPRA licensing delay
        return fr.stamp(fr.SNAPSHOT, source=provider,
                        detail="Options are closed; last session's tape.")
    if klass == "quarterly":
        return fr.weakest(fr.for_tape(15.0), fr.for_quarterly(None)) or fr.for_quarterly(None)
    # "filed": the latest analyst estimate / filing is the current reading
    if si_asof:
        return fr.for_filing("Short interest", filed=str(si_asof)[:10])
    return fr.stamp(fr.FILED, source=provider,
                    detail="The latest published estimate/filing is the current reading.")


# freshness.kind -> our status vocabulary (present-and-directional case)
_KIND_STATUS = {fr.LIVE: "LIVE", fr.DELAYED: "DELAYED", fr.SNAPSHOT: "LIVE",
                fr.FILED: "LIVE", fr.QUARTERLY: "LIVE"}


def _unavailable_status(provider_key: str, uw_state: dict) -> str:
    """Why is this factor absent? MISSING unless the provider tells us more."""
    if provider_key.startswith("uw") or provider_key == "uw+sec" or provider_key == "uw+13f":
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


def _reading(p: dict) -> str:
    """The only 'raw' the Param retains: the computed directional bias."""
    if not p.get("available") or p.get("bias") is None:
        return "—"
    b = float(p["bias"])
    lean = p.get("leaning") or ("Bullish" if b > FLAT else "Bearish" if b < -FLAT else "Neutral")
    return f"{lean} ({b:+.2f})"


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

    # Live provider state, fetched once.
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
        bias = p.get("bias")

        if available and bias is not None:
            st = _stamp(klass, session, provider=fr.UW,
                        si_asof=si_asof if name == "short_insider" else None)
            # Use the engine's own leaning so the status matches the shown reading:
            # valid data with no directional lean is NEUTRAL, not a MISSING-style gap.
            if (p.get("leaning") or "Neutral") == "Neutral":
                status = "NEUTRAL"
            else:
                status = _KIND_STATUS.get(st.get("kind"), "LIVE")
                # Real timestamp older than its class threshold -> STALE.
                if name == "short_insider":
                    age = _age_days(si_asof)
                    if age is not None and age > 20:       # short interest ~twice a month
                        status = "STALE"
            fresh = st
            last_update = st.get("as_of")
        else:
            status = _unavailable_status(pkey, uw_state)
            fresh = None
            last_update = None

        counts[status] = counts.get(status, 0) + 1
        raw = None
        if name == "short_insider" and (si_meta.get("pct_float") is not None):
            raw = {"short_pct_float": si_meta.get("pct_float"),
                   "days_to_cover": si_meta.get("days_to_cover"), "as_of": si_asof}

        rows.append({
            "name": name, "label": p.get("label"), "weight": p.get("weight"),
            "provider": provider, "endpoint": endpoint, "data_class": klass,
            "critical": name in CRITICAL,
            "status": status,
            "freshness": fresh,
            "last_update": last_update,
            "reading": _reading(p),
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
    panel = fr.weakest(*[r["freshness"] for r in rows if r["freshness"]])

    return {
        "status": "OK", "symbol": symbol, "source": SOURCE, "as_of": _now(),
        "session": session,
        "coverage_pct": coverage,
        "critical_coverage_pct": critical_coverage,
        "insufficient_data": critical_coverage < CRITICAL_MIN_COVERAGE,
        "panel_freshness": panel,
        "counts": counts,
        "rows": rows,
        "providers": {
            "unusual_whales": {
                "configured": uw_state.get("configured"),
                "status": uw_state.get("status"),
                "blocked": uw_state.get("blocked"),
                "env_var": uw_state.get("env_var"),
                "daily_limit": uw_state.get("daily_limit"),
                "app_left": uw_state.get("app_left"),
            },
            "finnhub": {"configured": fh_conf, "env_var": "FINNHUB_API_KEY"},
            "finviz": {"configured": fv_conf, "env_var": "FINVIZ_API_TOKEN"},
            "sec_13f": {"note": "public; gated by quarterly 13F ingest"},
        },
        "note": ("Status reflects whether real, mapped, sufficiently-fresh data "
                 "reached the model — not HTTP 200. Per-observation raw values and "
                 "exact timestamps are retained only for short interest and 13F "
                 "today; other factors show the data class's freshness."),
    }
