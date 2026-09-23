"""
Sector and market cap, sourced from SEC EDGAR.

No configured market-data plan returns either field: Twelve Data's /profile and
/statistics are 403 on this key, and Alpha Vantage's OVERVIEW is premium-gated.
EDGAR supplies both for free, from filings, with a date attached to every
figure -- which is better provenance than a vendor field anyway.

**Sector** is the SIC code the company files under, grouped into its official
SIC division. It is labelled as SIC throughout rather than dressed up as GICS:
the two do not agree, and a sector label that silently means something other
than what the reader assumes is worse than a slightly unfamiliar one.

**Market cap** is shares outstanding times the live price, and the share count
carries the date it was reported. A quarterly share count against a live price
is how every vendor computes this; the difference here is that the staleness is
visible instead of hidden. A share count older than ``STALE_AFTER_DAYS`` is
refused rather than multiplied out -- a market cap off by a buyback programme
is not worth showing.
"""

from __future__ import annotations

import gzip
import json
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Optional

import sec_service as sec

CONCEPT = ("https://data.sec.gov/api/xbrl/companyconcept/"
           "CIK{cik}/{tax}/{concept}.json")
FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# EDGAR asks for no more than 10 requests a second. One at a time with a gap is
# well inside that and keeps the backfill polite.
_THROTTLE = 0.25
_lock = threading.Lock()
_last = 0.0

# A share count this old cannot be multiplied by today's price and called a
# market cap. Two years covers a missed annual filing without licensing a
# figure from before a buyback or a split.
STALE_AFTER_DAYS = 730

# Concepts that carry a share count, best first. dei is the cover-page figure
# and is the most current when present; the diluted weighted average is the
# fallback, and for some filers it is the *only* recent one -- Nike's dei value
# is from 2015, so recency decides between them rather than this order alone.
SHARE_CONCEPTS = (
    ("dei", "EntityCommonStockSharesOutstanding"),
    ("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding"),
)

# The SIC divisions, as the SEC defines them.
SIC_DIVISIONS = (
    (100, 999, "Agriculture"),
    (1000, 1499, "Mining & Energy"),
    (1500, 1799, "Construction"),
    (2000, 3999, "Manufacturing"),
    (4000, 4999, "Transport & Utilities"),
    (5000, 5199, "Wholesale Trade"),
    (5200, 5999, "Retail Trade"),
    (6000, 6799, "Finance & Real Estate"),
    (7000, 8999, "Services"),
    (9100, 9999, "Public Administration"),
)


def _throttle() -> None:
    global _last
    with _lock:
        gap = time.time() - _last
        if gap < _THROTTLE:
            time.sleep(_THROTTLE - gap)
        _last = time.time()


def _fetch(url: str, timeout: float = 30.0) -> Optional[dict]:
    """EDGAR JSON, transparently gunzipped."""
    headers = {k: v for k, v in sec.SEC_HEADERS.items() if k.lower() != "host"}
    _throttle()
    try:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except Exception:  # noqa: BLE001 - a missing concept is a 404, not a fault
        return None
    # The Accept-Encoding header asks for gzip, and urllib does not unwrap it.
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except Exception:  # noqa: BLE001
            return None
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


def sic_division(sic: Any) -> Optional[str]:
    """The SIC division a code falls in, or None for an unrecognised code."""
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return None
    for low, high, name in SIC_DIVISIONS:
        if low <= code <= high:
            return name
    return None


def _newest(entries: list[dict]) -> Optional[dict]:
    """
    The most recently *reported* figure.

    Ordered by the period end first and the filing date second: two filings can
    report the same period, and the later filing supersedes the earlier one.
    """
    best = None
    for entry in entries:
        key = (str(entry.get("end") or ""), str(entry.get("filed") or ""))
        if best is None or key > best[0]:
            best = (key, entry)
    return best[1] if best else None


def _shares_from_units(units: dict) -> Optional[dict]:
    rows: list[dict] = []
    for entries in (units or {}).values():
        rows.extend(entries)
    return _newest(rows)


def _share_count(cik: str) -> dict:
    """
    Shares outstanding, with the concept and date it came from.

    Tries the small per-concept endpoint first. It is sometimes empty even
    where the data exists -- Coca-Cola returns nothing for the cover-page
    concept but carries it in companyfacts -- so a miss falls back to the full
    facts document rather than concluding the filer never reported it.
    """
    candidates: list[dict] = []

    for tax, concept in SHARE_CONCEPTS:
        payload = _fetch(CONCEPT.format(cik=cik, tax=tax, concept=concept))
        hit = _shares_from_units((payload or {}).get("units"))
        if hit:
            candidates.append({**hit, "concept": f"{tax}:{concept}"})

    if not candidates:
        facts = _fetch(FACTS.format(cik=cik), timeout=60.0)
        for tax, concept in SHARE_CONCEPTS:
            node = ((facts or {}).get("facts", {})
                    .get(tax, {}).get(concept))
            hit = _shares_from_units((node or {}).get("units"))
            if hit:
                candidates.append({**hit, "concept": f"{tax}:{concept}"})

    if not candidates:
        return {"status": "NO_DATA",
                "detail": "No share count reported in this filer's XBRL data."}

    # Recency decides, not the concept order: a stale cover-page figure must
    # not outrank a current weighted average.
    best = max(candidates, key=lambda c: str(c.get("end") or ""))
    try:
        shares = float(best.get("val"))
    except (TypeError, ValueError):
        return {"status": "NO_DATA", "detail": "Share count was not numeric."}
    if shares <= 0:
        return {"status": "NO_DATA", "detail": "Share count was not positive."}

    as_of = str(best.get("end") or "")
    try:
        age = (date.today() - datetime.strptime(as_of, "%Y-%m-%d").date()).days
    except ValueError:
        age = None

    if age is not None and age > STALE_AFTER_DAYS:
        return {
            "status": "STALE_DATA",
            "shares": shares,
            "as_of": as_of,
            "concept": best.get("concept"),
            "detail": (f"The most recent share count on file is from {as_of}, "
                       f"{age} days ago. A market cap from it would be wrong "
                       "by every buyback since."),
        }

    return {
        "status": "OK",
        "shares": shares,
        "as_of": as_of,
        "age_days": age,
        "concept": best.get("concept"),
        "filed": best.get("filed"),
    }


def fetch_profile(symbol: str) -> dict:
    """Sector and share count for one symbol, straight from EDGAR."""
    import sec_filings_service as filings

    symbol = symbol.upper()
    cik = filings._cik(symbol)
    if not cik:
        return {"symbol": symbol, "status": "SYMBOL_NOT_FOUND",
                "detail": f"{symbol} has no CIK in the EDGAR ticker index."}

    submissions = filings._submissions(symbol)
    if not submissions:
        return {"symbol": symbol, "status": "PROVIDER_OFFLINE",
                "detail": "EDGAR submissions feed did not answer."}

    sic = submissions.get("sic")
    shares = _share_count(cik)

    return {
        "symbol": symbol,
        "cik": cik,
        "company_name": submissions.get("name"),
        "sic": str(sic) if sic else None,
        "industry": submissions.get("sicDescription"),
        "sector": sic_division(sic),
        "shares_outstanding": shares.get("shares"),
        "shares_as_of": shares.get("as_of"),
        "shares_concept": shares.get("concept"),
        "shares_status": shares.get("status"),
        "shares_detail": shares.get("detail"),
        "status": "OK",
        "source": "SEC_EDGAR",
    }


def market_cap(shares: Optional[float], price: Optional[float]) -> Optional[float]:
    """Shares times price, or None when either side is missing."""
    if not shares or not price or shares <= 0 or price <= 0:
        return None
    return round(shares * price, 2)


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

# Sector never moves and a share count moves quarterly, so a month between
# refreshes is generous. A failed lookup is retried far sooner: a symbol that
# 404s today may simply have been a network blip.
PROFILE_TTL_DAYS = 30
FAILURE_TTL_DAYS = 1


def _stale(profile, now: datetime) -> bool:
    fetched = profile.fetched_at
    if fetched is None:
        return True
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    age = now - fetched
    ttl = (PROFILE_TTL_DAYS if profile.status == "OK" else FAILURE_TTL_DAYS)
    return age > timedelta(days=ttl)


def _to_dict(profile) -> dict:
    return {
        "symbol": profile.symbol,
        "cik": profile.cik,
        "company_name": profile.company_name,
        "sic": profile.sic,
        "industry": profile.industry,
        "sector": profile.sector,
        "shares_outstanding": (float(profile.shares_outstanding)
                               if profile.shares_outstanding is not None else None),
        "shares_as_of": profile.shares_as_of,
        "shares_concept": profile.shares_concept,
        "shares_status": profile.shares_status,
        "status": profile.status,
        "detail": profile.detail,
    }


def get_profiles(symbols: list[str], refresh: bool = False) -> dict[str, dict]:
    """
    Cached profiles for many symbols, fetching only what is missing or stale.

    Reads never block on EDGAR for a symbol already on file, so the calendar
    pays the fetch once per company per month rather than once per page.
    """
    from datetime import timezone as _tz

    from database import SessionLocal, engine
    from models_profiles import CompanyProfile, create_all

    wanted = sorted({s.upper() for s in symbols if s})
    if not wanted:
        return {}

    create_all(engine)
    now = datetime.now(_tz.utc)

    db = SessionLocal()
    try:
        rows = (db.query(CompanyProfile)
                .filter(CompanyProfile.symbol.in_(wanted)).all())
        stored = {r.symbol: r for r in rows}
        out = {s: _to_dict(r) for s, r in stored.items()}

        missing = [s for s in wanted
                   if s not in stored or refresh or _stale(stored[s], now)]

        for symbol in missing:
            fresh = fetch_profile(symbol)
            entry = stored.get(symbol)
            if entry is None:
                entry = CompanyProfile(symbol=symbol)
                db.add(entry)

            entry.cik = fresh.get("cik")
            entry.company_name = fresh.get("company_name")
            entry.sic = fresh.get("sic")
            entry.industry = fresh.get("industry")
            entry.sector = fresh.get("sector")
            entry.shares_outstanding = fresh.get("shares_outstanding")
            entry.shares_as_of = fresh.get("shares_as_of")
            entry.shares_concept = fresh.get("shares_concept")
            entry.shares_status = fresh.get("shares_status")
            entry.status = fresh.get("status") or "OK"
            entry.detail = fresh.get("detail") or fresh.get("shares_detail")
            entry.fetched_at = now
            out[symbol] = _to_dict(entry)

        if missing:
            db.commit()
        return out
    finally:
        db.close()
