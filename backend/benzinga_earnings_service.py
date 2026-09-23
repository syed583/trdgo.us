"""
Benzinga earnings provider: calendar, actual results and history.

Contract with the rest of the app:

* Never called on a page view. Every read goes to the PostgreSQL cache, and a
  refetch only happens once the TTL in provider_fetch_log has elapsed (or the
  caller explicitly forces one).
* With no BENZINGA_API_KEY set, every function returns PROVIDER_NOT_CONFIGURED
  and touches no network. That is a configuration state, not a failure.
* Field names are read defensively - Benzinga has shipped more than one
  spelling of these keys - and anything unparseable becomes None rather than a
  guessed number.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import provider_config as cfg
from database import SessionLocal
from models_earnings import EarningsCalendarEntry, ProviderFetchLog

PROVIDER = "BENZINGA"
CALENDAR_TTL = 6 * 3600.0        # calendar shifts slowly
HISTORY_TTL = 24 * 3600.0

ENDPOINT = f"{cfg.BENZINGA.base_url}/v2.1/calendar/earnings"


# ---------------------------------------------------------------------------
# parsing helpers
# ---------------------------------------------------------------------------


def _pick(row: dict, *names: str) -> Any:
    for name in names:
        if name in row and row[name] not in ("", None):
            return row[name]
    return None


def _num(value: Any) -> Optional[Decimal]:
    if value in (None, "", "-"):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _date(value: Any) -> Optional[date]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(value)[:19], fmt).date()
        except ValueError:
            continue
    return None


def _timing(value: Any) -> str:
    """Benzinga encodes timing as BMO/AMC/DMT or a clock time."""
    raw = str(value or "").strip().upper()
    if raw in ("BMO", "AMC", "DMT"):
        return "BMO" if raw == "BMO" else "AMC" if raw == "AMC" else "DMT"
    if ":" in raw:
        try:
            hour = int(raw.split(":")[0])
            return "BMO" if hour < 12 else "AMC"
        except ValueError:
            return "UNKNOWN"
    return "UNKNOWN"


def _clock(value: Any) -> Optional[str]:
    """The provider's clock time, kept only when it really is one."""
    raw = str(value or "").strip()
    if ":" not in raw:
        return None
    parts = raw.split(":")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _importance(value: Any) -> Optional[int]:
    """Benzinga ranks 0-5. Anything outside that is not a ranking."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= 5 else None


def _as_percent(value: Any) -> Optional[Decimal]:
    """
    Normalise a provider surprise field to percentage points.

    Benzinga sends a fraction (0.0688 = +6.88%). A genuine surprise larger
    than +/-100% is possible but rare, so a magnitude below 1 is treated as a
    fraction and scaled; anything larger is already in percent.
    """
    raw = _num(value)
    if raw is None:
        return None
    return raw * 100 if abs(raw) < 1 else raw


def _surprise(actual: Optional[Decimal], estimate: Optional[Decimal]) -> Optional[Decimal]:
    if actual is None or estimate in (None, 0):
        return None
    try:
        return (actual - estimate) / abs(estimate) * 100
    except (InvalidOperation, ZeroDivisionError):
        return None


def _lifecycle(eps_actual, revenue_actual, earnings_date: Optional[date]) -> str:
    """
    Where this event sits in the reporting lifecycle.

    The distinction that matters: a report with EPS but no revenue is
    PARTIAL_RESULT, not a finished report. Scoring must not treat it as
    complete.
    """
    today = datetime.now(timezone.utc).date()

    if eps_actual is None and revenue_actual is None:
        if earnings_date and earnings_date <= today:
            return "RESULT_DETECTED"       # date passed, numbers not in yet
        if earnings_date and earnings_date <= today + timedelta(days=1):
            return "PRE_READY"
        return "SCHEDULED"

    if eps_actual is not None and revenue_actual is not None:
        return "OFFICIAL_VERIFIED"

    return "PARTIAL_RESULT"


# ---------------------------------------------------------------------------
# fetch log
# ---------------------------------------------------------------------------


# provider_fetch_log.scope is VARCHAR(64); a long ticker list overflows it.
SCOPE_MAX = 64


def _scope_key(scope: str) -> str:
    """Readable when it fits, a stable digest when it does not."""
    if len(scope) <= SCOPE_MAX:
        return scope
    return "h:" + hashlib.sha1(scope.encode("utf-8")).hexdigest()[:40]


def _log_entry(db, endpoint: str, scope: str) -> Optional[ProviderFetchLog]:
    scope = _scope_key(scope)
    return (
        db.query(ProviderFetchLog)
        .filter(ProviderFetchLog.provider == PROVIDER)
        .filter(ProviderFetchLog.endpoint == endpoint)
        .filter(ProviderFetchLog.scope == scope)
        .first()
    )


def _record_fetch(db, endpoint: str, scope: str, status: str,
                  detail: str = "", rows: int = 0) -> None:
    entry = _log_entry(db, endpoint, scope)
    scope = _scope_key(scope)
    detail = cfg.redact(detail)[:400]
    now = datetime.now(timezone.utc)
    if entry:
        entry.status = status
        entry.detail = detail
        entry.rows = rows
        entry.fetched_at = now
    else:
        db.add(ProviderFetchLog(
            provider=PROVIDER, endpoint=endpoint, scope=scope,
            status=status, detail=detail, rows=rows, fetched_at=now,
        ))
    db.commit()


def _is_fresh(db, endpoint: str, scope: str, ttl: float) -> bool:
    entry = _log_entry(db, endpoint, scope)
    if not entry or not entry.fetched_at:
        return False
    age = (datetime.now(timezone.utc) - entry.fetched_at).total_seconds()
    return age < ttl and entry.status == cfg.OK


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def provider_status() -> dict:
    """Configuration + last-fetch state, for the Settings matrix."""
    base = cfg.BENZINGA.status()
    db = SessionLocal()
    try:
        cached = db.query(EarningsCalendarEntry).filter(
            EarningsCalendarEntry.source == PROVIDER).count()
        cached_symbols = db.query(EarningsCalendarEntry.symbol).filter(
            EarningsCalendarEntry.source == PROVIDER).distinct().count()
        last = (
            db.query(ProviderFetchLog)
            .filter(ProviderFetchLog.provider == PROVIDER)
            .order_by(ProviderFetchLog.fetched_at.desc())
            .first()
        )
    finally:
        db.close()

    base["cached_rows"] = cached
    base["cached_symbols"] = cached_symbols
    if last:
        base["last_fetch"] = last.fetched_at.isoformat() if last.fetched_at else None
        base["last_status"] = last.status
        base["last_detail"] = last.detail
    return cfg.apply_last_fetch(base, last.status if last else None)


def sync_calendar(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    symbols: Optional[list[str]] = None,
    force: bool = False,
) -> dict:
    """
    Pull a window of the earnings calendar into the cache.

    Returns a status dict describing what happened; it never raises for a
    provider problem, because "the provider is down" is information the UI has
    to render rather than an exception to swallow.
    """
    if not cfg.BENZINGA.configured:
        return {
            **cfg.BENZINGA.status(),
            "synced": 0,
            "detail": cfg.BENZINGA.status()["detail"],
        }

    today = datetime.now(timezone.utc).date()
    date_from = date_from or today - timedelta(days=120)
    date_to = date_to or today + timedelta(days=60)
    scope = f"{date_from}:{date_to}:{','.join(symbols or [])}"

    db = SessionLocal()
    try:
        if not force and _is_fresh(db, "calendar", scope, CALENDAR_TTL):
            entry = _log_entry(db, "calendar", scope)
            return {
                "provider": "Benzinga", "status": cfg.OK, "cached": True,
                "synced": entry.rows or 0,
                "detail": "Served from cache; provider not called.",
                "fetched_at": entry.fetched_at.isoformat(),
            }

        params = {
            "token": cfg.BENZINGA.api_key,
            "parameters[date_from]": date_from.isoformat(),
            "parameters[date_to]": date_to.isoformat(),
            "pagesize": 1000,
        }
        if symbols:
            params["parameters[tickers]"] = ",".join(s.upper() for s in symbols)

        try:
            payload = cfg.fetch_json(ENDPOINT, params)
        except cfg.ProviderError as exc:
            _record_fetch(db, "calendar", scope, exc.status, exc.detail, 0)
            return {"provider": "Benzinga", "status": exc.status,
                    "detail": exc.detail, "synced": 0}

        if isinstance(payload, dict):
            rows = payload.get("earnings")
        elif isinstance(payload, list):
            # An empty window comes back as a bare list.
            rows = payload
        else:
            rows = None

        if rows is None:
            _record_fetch(db, "calendar", scope, cfg.DATA_UNAVAILABLE,
                          "Unexpected response shape", 0)
            return {"provider": "Benzinga", "status": cfg.DATA_UNAVAILABLE,
                    "detail": "Provider returned an unexpected response shape",
                    "synced": 0}

        if not rows:
            _record_fetch(db, "calendar", scope, cfg.OK,
                          f"no events {date_from}..{date_to}", 0)
            return {"provider": "Benzinga", "status": cfg.OK, "synced": 0,
                    "detail": (f"Provider has no earnings events between "
                               f"{date_from} and {date_to}."),
                    "cached": False}

        saved = _upsert(db, rows)
        _record_fetch(db, "calendar", scope, cfg.OK,
                      f"{saved} rows {date_from}..{date_to}", saved)
        return {"provider": "Benzinga", "status": cfg.OK, "synced": saved,
                "detail": f"Cached {saved} events for {date_from} to {date_to}.",
                "cached": False}
    finally:
        db.close()


def _upsert(db, rows: list[dict]) -> int:
    saved = 0
    for row in rows:
        symbol = (_pick(row, "ticker", "symbol") or "").upper()
        when = _date(_pick(row, "date", "date_confirmed", "earnings_date"))
        if not symbol or not when:
            continue

        eps_prior = _num(_pick(row, "eps_prior"))
        eps_actual = _num(_pick(row, "eps", "eps_actual"))
        eps_estimate = _num(_pick(row, "eps_est", "eps_estimate", "eps_consensus"))
        rev_actual = _num(_pick(row, "revenue", "revenue_actual"))
        rev_estimate = _num(_pick(row, "revenue_est", "revenue_estimate"))

        # Compute the surprise from the estimate and the actual rather than
        # trusting the provider's field: Benzinga returns eps_surprise_percent
        # as a fraction (0.0688 for +6.88%), and mixing the two conventions
        # silently understates every surprise by 100x.
        eps_surprise = _surprise(eps_actual, eps_estimate)
        if eps_surprise is None:
            eps_surprise = _as_percent(_pick(row, "eps_surprise_percent"))

        rev_surprise = _surprise(rev_actual, rev_estimate)
        if rev_surprise is None:
            rev_surprise = _as_percent(_pick(row, "revenue_surprise_percent"))

        entry = (
            db.query(EarningsCalendarEntry)
            .filter(EarningsCalendarEntry.symbol == symbol)
            .filter(EarningsCalendarEntry.earnings_date == when)
            .first()
        )
        if not entry:
            entry = EarningsCalendarEntry(symbol=symbol, earnings_date=when)
            db.add(entry)

        entry.company_name = _pick(row, "name", "company_name") or entry.company_name
        entry.reporting_time = _timing(_pick(row, "time", "reporting_time"))
        entry.report_time = _clock(_pick(row, "time", "reporting_time"))
        entry.exchange = _pick(row, "exchange") or entry.exchange
        entry.importance = _importance(_pick(row, "importance"))
        entry.fiscal_period = _pick(row, "period")
        try:
            entry.fiscal_year = int(_pick(row, "period_year") or 0) or None
        except (TypeError, ValueError):
            entry.fiscal_year = None

        entry.eps_estimate = eps_estimate
        entry.eps_prior = eps_prior
        entry.eps_actual = eps_actual
        entry.eps_surprise_percent = eps_surprise
        entry.revenue_estimate = rev_estimate
        entry.revenue_prior = _num(_pick(row, "revenue_prior"))
        entry.revenue_actual = rev_actual
        entry.revenue_surprise_percent = rev_surprise
        entry.lifecycle = _lifecycle(eps_actual, rev_actual, when)
        confirmed = str(_pick(row, "date_confirmed") or "0") in ("1", "true", "True")
        entry.data_status = cfg.OK if confirmed else "ESTIMATED_DATE"
        entry.source = PROVIDER
        entry.fetched_at = datetime.now(timezone.utc)
        entry.raw = json.dumps(row)[:8000]
        saved += 1

    db.commit()
    return saved


def get_history(symbol: str, quarters: int = 8) -> dict:
    """Reported quarters for one symbol, newest first, straight from cache."""
    symbol = symbol.upper()
    if not cfg.BENZINGA.configured:
        return {
            "symbol": symbol, "quarters": [],
            **cfg.BENZINGA.status(),
        }

    db = SessionLocal()
    try:
        rows = (
            db.query(EarningsCalendarEntry)
            .filter(EarningsCalendarEntry.symbol == symbol)
            .filter(EarningsCalendarEntry.eps_actual.isnot(None))
            .order_by(EarningsCalendarEntry.earnings_date.desc())
            .limit(quarters)
            .all()
        )
        out = [_row_dict(r) for r in rows]
    finally:
        db.close()

    return {
        "symbol": symbol,
        "quarters": out,
        "count": len(out),
        "status": cfg.OK if out else cfg.DATA_UNAVAILABLE,
        "detail": None if out else (
            f"No reported quarters cached for {symbol}. "
            "Run a Benzinga sync to populate history."
        ),
        "source": PROVIDER,
    }


def _time_label(clock: Optional[str], timing: Optional[str]) -> Optional[str]:
    """"06:00" as "6:00 AM"; falls back to BMO/AMC when there is no clock."""
    if not clock:
        return {"BMO": "Before Open", "AMC": "After Close",
                "DMT": "During Market"}.get(timing or "")
    hour, minute = (int(p) for p in clock.split(":"))
    suffix = "AM" if hour < 12 else "PM"
    display = hour % 12 or 12
    return f"{display}:{minute:02d} {suffix}"


# Benzinga's 0-5 ranking, grouped the way a person would read it. 4 and 5 are
# the reports that move an index; 0 and 1 are names most desks never see.
def _importance_label(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    if value >= 4:
        return "High"
    if value >= 2:
        return "Medium"
    return "Low"


def _row_dict(r: EarningsCalendarEntry) -> dict:
    def f(v):
        return float(v) if v is not None else None

    return {
        "symbol": r.symbol,
        "company": r.company_name,
        "date": r.earnings_date.isoformat() if r.earnings_date else None,
        "date_label": r.earnings_date.strftime("%b %d, %Y") if r.earnings_date else None,
        "reporting_time": r.reporting_time,
        "report_time": r.report_time,
        "time_label": _time_label(r.report_time, r.reporting_time),
        "exchange": r.exchange,
        "importance": r.importance,
        "importance_label": _importance_label(r.importance),
        "fiscal_period": r.fiscal_period,
        "fiscal_year": r.fiscal_year,
        "quarter_label": (
            f"{r.fiscal_period} {r.fiscal_year}"
            if r.fiscal_period and r.fiscal_year else
            (r.earnings_date.strftime("%b %Y") if r.earnings_date else None)
        ),
        "eps_estimate": f(r.eps_estimate),
        "eps_prior": f(r.eps_prior),
        "revenue_prior": f(r.revenue_prior),
        "eps_actual": f(r.eps_actual),
        "eps_surprise_percent": f(r.eps_surprise_percent),
        "revenue_estimate": f(r.revenue_estimate),
        "revenue_actual": f(r.revenue_actual),
        "revenue_surprise_percent": f(r.revenue_surprise_percent),
        "post_earnings_move_percent": f(r.post_earnings_move_percent),
        "lifecycle": r.lifecycle,
        "date_confirmed": r.data_status != "ESTIMATED_DATE",
        "beat": (
            bool(r.eps_actual is not None and r.eps_estimate is not None
                 and r.eps_actual > r.eps_estimate)
        ),
        "source": r.source,
        "data_status": r.data_status,
        "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
    }


def get_upcoming(
    date_from: date, date_to: Optional[date], symbols: Optional[list[str]] = None,
) -> dict:
    """Cached calendar rows in a window. Never triggers a provider call."""
    db = SessionLocal()
    try:
        q = db.query(EarningsCalendarEntry).filter(
            EarningsCalendarEntry.earnings_date >= date_from)
        if date_to:
            q = q.filter(EarningsCalendarEntry.earnings_date <= date_to)
        if symbols:
            q = q.filter(EarningsCalendarEntry.symbol.in_(
                [s.upper() for s in symbols]))
        rows = q.order_by(EarningsCalendarEntry.earnings_date.asc()).all()
        out = [_row_dict(r) for r in rows]
    finally:
        db.close()

    return {"rows": out, "count": len(out), "source": PROVIDER,
            "status": cfg.OK if out else cfg.DATA_UNAVAILABLE}
