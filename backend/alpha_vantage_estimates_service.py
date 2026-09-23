"""
Alpha Vantage provider for analyst estimates and estimate revisions.

Honest scope note, which drives the whole design here:

Alpha Vantage's EARNINGS_ESTIMATES endpoint returns the current consensus
(mean/high/low, analyst count) *and*, on plans that include them, the same
figures as they stood 7/30/60/90 days ago (eps_estimate_average_30_days_ago
and siblings). Each window the payload supplies is stored as its own row, so a
revision trend is available from the first sync rather than after ~90 days of
accumulated snapshots.

Any window the plan omits stays absent: missing horizons are never
interpolated or back-filled with the current value, which would manufacture a
revision trend out of a single observation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import provider_config as cfg
from database import SessionLocal
from models_earnings import EstimateRevision, ProviderFetchLog

PROVIDER = "ALPHA_VANTAGE"
SNAPSHOT_TTL = 12 * 3600.0
HORIZONS = [0, 7, 30, 60, 90]

# How far from the exact horizon a snapshot may sit and still represent it.
HORIZON_TOLERANCE = {0: 2, 7: 4, 30: 8, 60: 12, 90: 15}


def _num(value: Any) -> Optional[Decimal]:
    if value in (None, "", "None", "-"):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _f(value) -> Optional[float]:
    return float(value) if value is not None else None


def provider_status() -> dict:
    base = cfg.ALPHA_VANTAGE.status()
    db = SessionLocal()
    try:
        snapshots = db.query(EstimateRevision).filter(
            EstimateRevision.source == PROVIDER).count()
        symbols = db.query(EstimateRevision.symbol).filter(
            EstimateRevision.source == PROVIDER).distinct().count()
        last = (
            db.query(ProviderFetchLog)
            .filter(ProviderFetchLog.provider == PROVIDER)
            .order_by(ProviderFetchLog.fetched_at.desc())
            .first()
        )
    finally:
        db.close()

    base["cached_rows"] = snapshots
    base["cached_symbols"] = symbols
    if last:
        base["last_fetch"] = last.fetched_at.isoformat() if last.fetched_at else None
        base["last_status"] = last.status
        base["last_detail"] = last.detail
    if base["status"] == cfg.OK:
        base["note"] = (
            "Revision windows (7/30/60/90d) come from the horizons Alpha "
            "Vantage returns in the payload. Windows this plan does not "
            "supply are reported absent rather than estimated."
        )
    return cfg.apply_last_fetch(base, last.status if last else None)


def _horizon_field(row: dict, base: str, horizon: int) -> Any:
    """
    Read `base` for horizon 0, or its `_<n>_days_ago` sibling otherwise.

    Returned via .get so a plan that omits a window yields None instead of
    raising -- the caller turns that into an absent row.
    """
    if horizon == 0:
        return row.get(base)
    return row.get(f"{base}_{horizon}_days_ago")


def _upsert_horizon(db, symbol: str, horizon: int, period: Optional[str]):
    """
    Fetch-or-create the row for (symbol, horizon, fiscal_period).

    estimate_revisions has a unique constraint on that triple, so a re-sync has
    to update in place; inserting blindly raised IntegrityError on the second
    run for a symbol.
    """
    record = (
        db.query(EstimateRevision)
        .filter(EstimateRevision.symbol == symbol)
        .filter(EstimateRevision.horizon_days == horizon)
        .filter(EstimateRevision.fiscal_period == period)
        .one_or_none()
    )
    if record is None:
        record = EstimateRevision(
            symbol=symbol, horizon_days=horizon, fiscal_period=period)
        db.add(record)
    return record


def _record_fetch(db, scope: str, status: str, detail: str, rows: int) -> None:
    entry = (
        db.query(ProviderFetchLog)
        .filter(ProviderFetchLog.provider == PROVIDER)
        .filter(ProviderFetchLog.endpoint == "estimates")
        .filter(ProviderFetchLog.scope == scope)
        .first()
    )
    now = datetime.now(timezone.utc)
    if entry:
        entry.status, entry.detail, entry.rows, entry.fetched_at = (
            status, detail, rows, now)
    else:
        db.add(ProviderFetchLog(
            provider=PROVIDER, endpoint="estimates", scope=scope,
            status=status, detail=detail, rows=rows, fetched_at=now))
    db.commit()


def snapshot_symbol(symbol: str, force: bool = False) -> dict:
    """
    Fetch current consensus for one symbol and record it as a dated snapshot.

    Snapshots are what later become the 7/30/60/90-day windows, so this is the
    only function that calls the provider.
    """
    symbol = symbol.upper()
    if not cfg.ALPHA_VANTAGE.configured:
        return {**cfg.ALPHA_VANTAGE.status(), "symbol": symbol, "saved": 0}

    db = SessionLocal()
    try:
        if not force:
            recent = (
                db.query(EstimateRevision)
                .filter(EstimateRevision.symbol == symbol)
                .filter(EstimateRevision.source == PROVIDER)
                .order_by(EstimateRevision.fetched_at.desc())
                .first()
            )
            if recent and recent.fetched_at:
                age = (datetime.now(timezone.utc) - recent.fetched_at).total_seconds()
                if age < SNAPSHOT_TTL:
                    return {"symbol": symbol, "status": cfg.OK, "cached": True,
                            "saved": 0,
                            "detail": "Recent snapshot exists; provider not called."}

        if cfg.in_cooldown(PROVIDER):
            return {"symbol": symbol, "status": cfg.RATE_LIMITED, "saved": 0,
                    "detail": "Alpha Vantage quota exhausted; retrying later."}

        try:
            payload = cfg.fetch_json(cfg.ALPHA_VANTAGE.base_url, {
                "function": "EARNINGS_ESTIMATES",
                "symbol": symbol,
                "apikey": cfg.ALPHA_VANTAGE.api_key,
            })
        except cfg.ProviderError as exc:
            _record_fetch(db, symbol, exc.status, exc.detail, 0)
            return {"symbol": symbol, "status": exc.status,
                    "detail": exc.detail, "saved": 0}

        # Alpha Vantage answers rate limits and bad keys with a 200 and a note.
        if isinstance(payload, dict):
            note = payload.get("Note") or payload.get("Information")
            if note:
                # Alpha Vantage echoes the key inside this message.
                note = cfg.redact(note)
                status = (cfg.RATE_LIMITED if "call frequency" in str(note).lower()
                          or "rate limit" in str(note).lower()
                          else cfg.ENTITLEMENT_REQUIRED)
                if status == cfg.RATE_LIMITED:
                    cfg.start_cooldown(PROVIDER)
                _record_fetch(db, symbol, status, str(note)[:400], 0)
                return {"symbol": symbol, "status": status,
                        "detail": str(note)[:400], "saved": 0}
            if payload.get("Error Message"):
                message = cfg.redact(payload["Error Message"])[:400]
                _record_fetch(db, symbol, cfg.DATA_UNAVAILABLE, message, 0)
                return {"symbol": symbol, "status": cfg.DATA_UNAVAILABLE,
                        "detail": message, "saved": 0}

        rows = (payload or {}).get("estimates") or []
        if not rows:
            _record_fetch(db, symbol, cfg.DATA_UNAVAILABLE,
                          "No estimates array returned", 0)
            return {"symbol": symbol, "status": cfg.DATA_UNAVAILABLE,
                    "detail": "Provider returned no estimates for this symbol.",
                    "saved": 0}

        current = rows[0]
        period = current.get("horizon") or current.get("fiscal_period")
        now = datetime.now(timezone.utc)

        # Alpha Vantage ships the consensus as it stood 7/30/60/90 days ago in
        # the same payload (eps_estimate_average_30_days_ago and friends).
        # Storing only horizon 0 threw that away and left the revision trend
        # waiting ~90 days to rebuild history the provider had already sent.
        saved = 0
        for horizon in HORIZONS:
            eps = _num(_horizon_field(current, "eps_estimate_average", horizon))
            revenue = _num(
                _horizon_field(current, "revenue_estimate_average", horizon))
            if eps is None and revenue is None:
                # This provider/plan did not supply the window. A gap stays a
                # gap rather than being back-filled with the current value.
                continue

            record = _upsert_horizon(db, symbol, horizon, period)
            record.eps_estimate = eps
            record.revenue_estimate = revenue
            record.eps_mean = eps
            record.source = PROVIDER
            record.data_status = cfg.OK
            # Backdated so the row's age matches the consensus it holds.
            record.fetched_at = now - timedelta(days=horizon)

            if horizon == 0:
                record.analyst_count = int(
                    _num(current.get("eps_estimate_analyst_count")) or 0) or None
                record.eps_high = _num(current.get("eps_estimate_high"))
                record.eps_low = _num(current.get("eps_estimate_low"))
            saved += 1

        if not saved:
            _record_fetch(db, symbol, cfg.DATA_UNAVAILABLE,
                          "No usable estimate values in payload", 0)
            return {"symbol": symbol, "status": cfg.DATA_UNAVAILABLE,
                    "detail": "Provider returned no usable estimate values.",
                    "saved": 0}

        db.commit()
        _record_fetch(db, symbol, cfg.OK, f"{saved} horizon rows stored", saved)

        return {"symbol": symbol, "status": cfg.OK, "saved": saved,
                "detail": f"Stored {saved} revision window(s)."}
    finally:
        db.close()


def get_revisions(symbol: str) -> dict:
    """
    Build the revision table from stored snapshots.

    A horizon appears only if a snapshot actually exists near that date. Gaps
    stay gaps.
    """
    symbol = symbol.upper()
    if not cfg.ALPHA_VANTAGE.configured:
        return {
            "symbol": symbol, "rows": [], "horizons_available": [],
            **cfg.ALPHA_VANTAGE.status(),
        }

    db = SessionLocal()
    try:
        snapshots = (
            db.query(EstimateRevision)
            .filter(EstimateRevision.symbol == symbol)
            .filter(EstimateRevision.source == PROVIDER)
            .order_by(EstimateRevision.fetched_at.desc())
            .limit(400)
            .all()
        )
    finally:
        db.close()

    if not snapshots:
        return {
            "symbol": symbol, "rows": [], "horizons_available": [],
            "status": cfg.DATA_UNAVAILABLE,
            "detail": (
                f"No estimate snapshots stored for {symbol} yet. "
                "Snapshots accumulate on each sync; the 7/30/60/90-day windows "
                "appear once history exists."
            ),
            "source": PROVIDER,
        }

    now = datetime.now(timezone.utc)
    latest = snapshots[0]

    rows = []
    available = []
    for horizon in HORIZONS:
        target = now - timedelta(days=horizon)
        tolerance = timedelta(days=HORIZON_TOLERANCE[horizon])

        # Rows carry the horizon they represent, so match on that first: it is
        # exact, and it is what the provider actually stated. The timestamp
        # search below stays as the fallback for snapshots stored before
        # horizons were recorded.
        match = next(
            (s for s in snapshots
             if s.horizon_days == horizon and s.fiscal_period == latest.fiscal_period),
            None,
        )
        if match is None and horizon == 0:
            match = latest
        elif match is None:
            candidates = [
                s for s in snapshots
                if s.fetched_at and abs(s.fetched_at - target) <= tolerance
            ]
            if candidates:
                match = min(candidates, key=lambda s: abs(s.fetched_at - target))

        label = "Current" if horizon == 0 else f"{horizon} Days Ago"
        if match:
            available.append(horizon)
            rows.append({
                "label": label,
                "days_ago": horizon,
                "eps_estimate": _f(match.eps_estimate),
                "revenue_estimate": _f(match.revenue_estimate),
                "revenue_estimate_b": (
                    round(float(match.revenue_estimate) / 1e9, 2)
                    if match.revenue_estimate else None
                ),
                "analyst_count": match.analyst_count,
                "eps_high": _f(match.eps_high),
                "eps_low": _f(match.eps_low),
                "eps_mean": _f(match.eps_mean),
                "snapshot_time": match.fetched_at.isoformat(),
                "available": True,
            })
        else:
            rows.append({
                "label": label, "days_ago": horizon,
                "eps_estimate": None, "revenue_estimate": None,
                "revenue_estimate_b": None, "analyst_count": None,
                "eps_high": None, "eps_low": None, "eps_mean": None,
                "snapshot_time": None, "available": False,
            })

    complete = len(available) == len(HORIZONS)
    first = rows[0]
    oldest_available = next(
        (r for r in reversed(rows) if r["available"] and r["days_ago"] > 0), None)

    change = None
    if oldest_available and first["eps_estimate"] and oldest_available["eps_estimate"]:
        change = round(
            (first["eps_estimate"] - oldest_available["eps_estimate"])
            / abs(oldest_available["eps_estimate"]) * 100, 1)

    return {
        "symbol": symbol,
        "rows": rows,
        "horizons_available": available,
        "eps_change": change,
        "eps_change_window": oldest_available["days_ago"] if oldest_available else None,
        "analyst_count": first["analyst_count"],
        "eps_high": first["eps_high"],
        "eps_low": first["eps_low"],
        "eps_mean": first["eps_mean"],
        "status": cfg.OK if complete else cfg.PARTIAL_DATA,
        "detail": None if complete else (
            f"{len(available)} of {len(HORIZONS)} revision windows available. "
            "Windows Alpha Vantage supplies in the payload are stored on the "
            "first sync; any it omits on this plan stay absent rather than "
            "being back-filled with the current value."
        ),
        "source": PROVIDER,
    }


# ---------------------------------------------------------------------------
# adapter for the scorer
# ---------------------------------------------------------------------------

def revision_windows(symbol: str) -> dict:
    """
    Alpha Vantage revisions in the shape ``score_estimates`` consumes.

    The scorer was written against ``calculate_estimate_revisions``, which
    reads the legacy estimate_snapshots table and requires a companies row.
    Alpha Vantage writes to estimate_revisions instead, so the two never met
    and the 25-point component reported UNAVAILABLE even with data in hand.
    This translates one into the other rather than duplicating the scoring.
    """
    data = get_revisions(symbol)
    rows = {r["days_ago"]: r for r in data.get("rows", []) if r.get("available")}

    current = rows.get(0)
    if not current:
        return {
            "symbol": symbol.upper(),
            "status": "NO_DATA",
            "snapshot_count": 0,
            "revisions": {},
            "available_windows": 0,
        }

    revisions = {}
    available_windows = 0
    for horizon in (7, 30, 60, 90):
        old = rows.get(horizon)
        if not old:
            revisions[f"{horizon}d"] = {
                "days": horizon, "available": False,
                "reason": f"Provider supplied no {horizon}-day consensus",
            }
            continue

        revisions[f"{horizon}d"] = {
            "days": horizon,
            "available": True,
            "eps_old": old.get("eps_estimate"),
            "eps_current": current.get("eps_estimate"),
            "eps_revision_percent": _pct(
                old.get("eps_estimate"), current.get("eps_estimate")),
            "revenue_old": old.get("revenue_estimate"),
            "revenue_current": current.get("revenue_estimate"),
            "revenue_revision_percent": _pct(
                old.get("revenue_estimate"), current.get("revenue_estimate")),
            "historical_snapshot_time": old.get("snapshot_time"),
            "current_snapshot_time": current.get("snapshot_time"),
        }
        available_windows += 1

    if available_windows == 4:
        status = "GOOD"
    elif available_windows:
        status = "PARTIAL"
    else:
        status = "INSUFFICIENT_HISTORY"

    return {
        "symbol": symbol.upper(),
        "status": status,
        "snapshot_count": len(rows),
        "current_snapshot": {
            "snapshot_time": current.get("snapshot_time"),
            "eps_estimate": current.get("eps_estimate"),
            "revenue_estimate": current.get("revenue_estimate"),
            "analyst_count": current.get("analyst_count"),
            "source": PROVIDER,
        },
        "available_windows": available_windows,
        "required_windows": 4,
        "revisions": revisions,
    }


def _pct(old: Optional[float], new: Optional[float]) -> Optional[float]:
    if old is None or new is None or not old:
        return None
    return round((new - old) / abs(old) * 100, 4)


# ---------------------------------------------------------------------------
# reported earnings history
# ---------------------------------------------------------------------------

# Long, because the free tier allows twenty-five calls a day and reported
# quarters only change four times a year. Re-fetching this on every page view
# would exhaust the quota before lunch.
EARNINGS_HISTORY_TTL = 24 * 3600.0


def get_earnings_history(symbol: str, quarters: int = 8) -> dict:
    """
    Reported EPS against consensus, newest first.

    Exists because Benzinga's plan covers a restricted universe: a full year
    of its calendar returns 116 events for the entire market, and neither
    NVDA nor TSLA appears at all. Alpha Vantage carries the full history, so
    it backs up the calendar rather than replacing it -- Benzinga stays first
    because it is already cached in the database and costs no quota.
    """
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"symbol": symbol, "status": "INVALID_SYMBOL", "source": PROVIDER}
    if not cfg.ALPHA_VANTAGE.configured:
        return {"symbol": symbol, "quarters": [], **cfg.ALPHA_VANTAGE.status()}

    key = f"av:earnings:{symbol}"
    from live_market_service import cache

    cached = cache.get(key, EARNINGS_HISTORY_TTL)
    if cached:
        return cached

    try:
        payload = cfg.fetch_json(cfg.ALPHA_VANTAGE.base_url, {
            "function": "EARNINGS", "symbol": symbol,
            "apikey": cfg.ALPHA_VANTAGE.api_key,
        })
    except Exception as exc:  # noqa: BLE001
        return {"symbol": symbol, "quarters": [], "status": cfg.PROVIDER_OFFLINE,
                "detail": str(exc)[:200], "source": PROVIDER}

    rows = (payload or {}).get("quarterlyEarnings") or []
    out = []
    for row in rows[:quarters]:
        surprise = _f(row.get("surprisePercentage"))
        out.append({
            "fiscal_period_end": row.get("fiscalDateEnding"),
            "reported_date": row.get("reportedDate"),
            "eps_actual": _f(row.get("reportedEPS")),
            "eps_estimate": _f(row.get("estimatedEPS")),
            "eps_surprise_percent": surprise,
            "beat": None if surprise is None else surprise > 0,
        })

    result = {
        "symbol": symbol,
        "quarters": out,
        "count": len(out),
        "status": cfg.OK if out else cfg.DATA_UNAVAILABLE,
        "detail": None if out else (
            "Provider returned no reported quarters. The free tier allows "
            "25 calls a day, and an exhausted quota looks exactly like an "
            "empty answer."),
        "source": PROVIDER,
    }
    # Only successes are cached. A rate-limited call returns an empty payload
    # that is indistinguishable from "this company has never reported", and
    # storing that for a day would blank the parameter until tomorrow.
    if out:
        cache.put(key, result)
    return result
