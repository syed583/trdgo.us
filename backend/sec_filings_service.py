"""
Insider transactions (Form 4) and large-stake disclosures (13D / 13G).

Why these two and not 13F
-------------------------
Form 4 and 13D/13G are filed *by* or *about* the company, so one submissions
feed per ticker answers "who bought this stock". 13F is the other way round: a
fund files a list of everything it owns, and the company's own feed contains
none of them -- AAPL has zero. Answering "which funds bought AAPL" means
parsing thousands of unrelated fund filings into a reverse index, which is a
different and much larger job for the smallest weight in the model.

The distinction that decides whether any of this is useful
----------------------------------------------------------
Most Form 4 rows are not decisions. Codes A (award), M (option exercise) and F
(shares withheld for tax) are compensation mechanics that fire on a schedule
regardless of what the executive thinks. Only P (open-market purchase) and S
(open-market sale) are someone choosing to trade their own money, and only P
is hard to explain away -- insiders sell for tax bills, houses and
diversification, but they buy for one reason.

Counting grants as "insider buying" is the classic way to build an indicator
that looks busy and predicts nothing, so the split is enforced here rather
than left to the caller.
"""

from __future__ import annotations

import re
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Optional

import sec_service as sec

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{doc}"

# SEC asks for no more than ten requests a second and a contact in the agent.
_MIN_INTERVAL = 0.12
_last_call = 0.0
_LOCK = threading.Lock()

FILING_TTL = 3600.0

# Open-market decisions, as opposed to compensation mechanics.
DISCRETIONARY = {"P", "S"}
PURCHASE, SALE = "P", "S"

CODE_LABELS = {
    "P": "Open-market purchase",
    "S": "Open-market sale",
    "A": "Grant or award",
    "M": "Option exercise",
    "F": "Shares withheld for tax",
    "G": "Gift",
    "C": "Conversion",
    "D": "Disposition to issuer",
}

# A single executive buying is weak evidence; several buying inside a few weeks
# is the pattern worth surfacing, because it is hard to explain by one person's
# circumstances.
CLUSTER_WINDOW_DAYS = 45
CLUSTER_MIN_BUYERS = 3


def _throttle() -> None:
    global _last_call
    with _LOCK:
        wait = _MIN_INTERVAL - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()


def _get_text(url: str) -> Optional[str]:
    """Fetch an EDGAR document as text, reusing the SEC disk cache."""
    cached = sec._disk_read(url)
    if cached is not None:
        return cached if isinstance(cached, str) else None

    _throttle()
    headers = {k: v for k, v in sec.SEC_HEADERS.items() if k.lower() != "host"}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                import gzip
                data = gzip.decompress(data)
            body = data.decode("utf-8", "ignore")
    except (urllib.error.HTTPError, urllib.error.URLError,
            TimeoutError, OSError):
        return None

    sec._disk_write(url, body)
    return body


def _cik(symbol: str) -> Optional[str]:
    try:
        record = sec.get_company_cik(symbol)
    except Exception:  # noqa: BLE001
        return None
    if not record:
        return None
    return record.get("cik") if isinstance(record, dict) else None


def _submissions(symbol: str) -> Optional[dict]:
    cik = _cik(symbol)
    if not cik:
        return None
    _throttle()
    try:
        return sec._get_json(SUBMISSIONS.format(cik=cik))
    except Exception:  # noqa: BLE001
        return None


def _recent_rows(payload: dict) -> list[dict]:
    """Flatten EDGAR's column-oriented filing index into rows."""
    recent = (payload.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    if not forms:
        return []

    def col(name: str) -> list:
        values = recent.get(name) or []
        return values + [None] * (len(forms) - len(values))

    # Padded once per column. Padding inside the loop copied every column for
    # every row -- quadratic in a list that runs to thousands of filings, and
    # enough to hold a CPU core flat out while the board scanned.
    filed, accession, document = (col("filingDate"), col("accessionNumber"),
                                  col("primaryDocument"))
    return [
        {
            "form": forms[i],
            "filed": filed[i],
            "accession": (accession[i] or "").replace("-", ""),
            "document": document[i] or "",
        }
        for i in range(len(forms))
    ]


def _first(body: str, tag: str) -> Optional[str]:
    """First value of a Form 4 tag, unwrapping EDGAR's <value> nesting."""
    match = re.search(rf"<{tag}>\s*(?:<value>)?\s*([^<]+)", body)
    return match.group(1).strip() if match else None


def _num(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _parse_form4(body: str, filed: str, url: str) -> list[dict]:
    """
    One row per non-derivative transaction in a Form 4.

    Derivative tables (option grants and exercises) are deliberately skipped:
    they move on vesting schedules, not conviction.
    """
    owner = _first(body, "rptOwnerName")
    title = _first(body, "officerTitle")
    is_director = (_first(body, "isDirector") or "").strip() in ("1", "true")
    is_officer = (_first(body, "isOfficer") or "").strip() in ("1", "true")
    is_ten_pct = (_first(body, "isTenPercentOwner") or "").strip() in ("1", "true")

    # Rule 10b5-1 plans are adopted months ahead and then execute on a
    # schedule. A weekly sale of an identical share count is the plan running,
    # not the executive forming a view -- treating it as a bearish decision is
    # how an insider indicator ends up screaming sell at every large issuer.
    planned = (_first(body, "aff10b5One") or "").strip().lower() in ("1", "true")

    section = body.split("<derivativeTable")[0]
    blocks = re.findall(
        r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>",
        section, re.S)

    rows = []
    for block in blocks:
        code = _first(block, "transactionCode")
        shares = _num(_first(block, "transactionShares"))
        price = _num(_first(block, "transactionPricePerShare"))
        acquired = _first(block, "transactionAcquiredDisposedCode")
        held = _num(_first(block, "sharesOwnedFollowingTransaction"))
        if not code or shares is None:
            continue

        role = ("10% owner" if is_ten_pct
                else title or ("Director" if is_director
                               else "Officer" if is_officer else "Insider"))

        rows.append({
            "filed": filed,
            "date": _first(block, "transactionDate") or filed,
            "owner": owner,
            "role": role,
            "is_director": is_director,
            "is_officer": is_officer,
            "is_ten_percent_owner": is_ten_pct,
            "code": code,
            "code_label": CODE_LABELS.get(code, code),
            # Discretionary means a decision: an open-market trade that is
            # not running off a pre-adopted plan.
            "discretionary": code in DISCRETIONARY and not planned,
            "planned_10b5_1": planned,
            "direction": ("Buy" if code == PURCHASE
                          else "Sell" if code == SALE else None),
            "shares": shares,
            "price": price,
            "value": round(shares * price, 2) if price else None,
            "shares_held_after": held,
            "acquired_disposed": acquired,
            "url": url,
        })
    return rows


def insider_transactions(symbol: str, days: int = 180,
                         max_filings: int = 60) -> dict:
    # days widens the scan window; cap it so a huge value cannot walk an
    # issuer's entire submission history. max_filings already bounds fetches.
    days = max(1, min(int(days) if str(days).lstrip("-").isdigit() else 180, 3650))
    """
    Form 4 activity for one symbol.

    ``max_filings`` bounds the work: a large issuer files hundreds a year and
    each is a separate document fetch. Filings are read newest first, so the
    cap trims the oldest rather than an arbitrary slice.
    """
    symbol = (symbol or "").upper().strip()

    # The provider returns the whole window in one request. Reading Form 4
    # documents one at a time meant a cap -- sixty filings -- and a large
    # issuer files hundreds a year, so the oldest trades in the window were
    # dropped without saying so.
    try:
        import uw_ownership_service as uwo

        if uwo.configured():
            provider = uwo.insider_transactions(symbol, days)
            if provider.get("status") == "OK":
                return provider
    except Exception:  # noqa: BLE001 - EDGAR is still the record
        pass

    key = f"sec:form4:{symbol}:{days}"
    cached = sec._CACHE.get(key)
    if cached and (time.time() - cached[0]) < FILING_TTL:
        return cached[1]

    payload = _submissions(symbol)
    if not payload:
        return {"symbol": symbol, "status": "NO_DATA", "source": "SEC"}

    cik = _cik(symbol)
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    filings = [
        r for r in _recent_rows(payload)
        if r["form"] == "4" and r["filed"] and r["filed"] >= cutoff
    ][:max_filings]

    transactions: list[dict] = []
    for row in filings:
        # The primaryDocument path points at EDGAR's styled render; the raw
        # XML sits beside it, one directory up.
        doc = (row["document"] or "").split("/")[-1]
        if not doc.endswith(".xml"):
            continue
        url = ARCHIVE.format(cik=int(cik), accession=row["accession"], doc=doc)
        body = _get_text(url)
        if body:
            transactions.extend(_parse_form4(body, row["filed"], url))

    result = {
        "symbol": symbol,
        "window_days": days,
        "filings_read": len(filings),
        "transactions": transactions,
        **summarise_insiders(transactions),
        "status": "OK" if transactions else "NO_FILINGS",
        "source": "SEC",
    }
    sec._CACHE[key] = (time.time(), result)
    return result


def summarise_insiders(transactions: list[dict]) -> dict:
    """
    Reduce raw rows to the few numbers a score can use.

    Grants and exercises are reported separately rather than dropped, so the
    UI can show that an insider "received" shares without that counting as a
    vote of confidence.
    """
    buys = [t for t in transactions
            if t["code"] == PURCHASE and not t["planned_10b5_1"]]
    sells = [t for t in transactions
             if t["code"] == SALE and not t["planned_10b5_1"]]
    planned = [t for t in transactions if t["planned_10b5_1"]]
    mechanical = [t for t in transactions
                  if t["code"] not in DISCRETIONARY]

    buy_value = sum(t["value"] or 0.0 for t in buys)
    sell_value = sum(t["value"] or 0.0 for t in sells)
    traded = buy_value + sell_value

    buyers = {t["owner"] for t in buys if t["owner"]}
    sellers = {t["owner"] for t in sells if t["owner"]}

    return {
        "buy_count": len(buys),
        "sell_count": len(sells),
        "mechanical_count": len(mechanical),
        # Reported, not discarded: the UI should be able to say "$114m sold,
        # all of it on pre-arranged plans", which is a very different sentence
        # from "$114m sold".
        "planned_count": len(planned),
        "planned_value": round(
            sum(t["value"] or 0.0 for t in planned), 2),
        "unique_buyers": len(buyers),
        "unique_sellers": len(sellers),
        "buy_value": round(buy_value, 2),
        "sell_value": round(sell_value, 2),
        # Share of discretionary money that was buying. Grants are excluded,
        # which is what keeps this from reading bullish every vesting date.
        "buy_share": round(buy_value / traded * 100, 1) if traded else None,
        "net_value": round(buy_value - sell_value, 2),
        "cluster": _cluster(buys),
        "latest": transactions[0] if transactions else None,
    }


def _cluster(buys: list[dict]) -> dict:
    """
    Several different insiders buying inside one window.

    One executive buying can be explained by that person's circumstances.
    Several, independently, in a few weeks, is harder to explain -- which is
    the whole reason to look at clusters rather than single trades.
    """
    if not buys:
        return {"detected": False, "buyers": 0, "window_days": CLUSTER_WINDOW_DAYS}

    by_day: dict[str, set] = defaultdict(set)
    for t in buys:
        if t.get("owner") and t.get("date"):
            by_day[t["date"][:10]].add(t["owner"])

    days = sorted(by_day)
    best: set = set()
    for i, start in enumerate(days):
        try:
            start_date = datetime.strptime(start, "%Y-%m-%d").date()
        except ValueError:
            continue
        window: set = set()
        for other in days[i:]:
            try:
                other_date = datetime.strptime(other, "%Y-%m-%d").date()
            except ValueError:
                continue
            if (other_date - start_date).days > CLUSTER_WINDOW_DAYS:
                break
            window |= by_day[other]
        if len(window) > len(best):
            best = window

    return {
        "detected": len(best) >= CLUSTER_MIN_BUYERS,
        "buyers": len(best),
        "names": sorted(best)[:8],
        "window_days": CLUSTER_WINDOW_DAYS,
        "threshold": CLUSTER_MIN_BUYERS,
    }


def ownership_filings(symbol: str, days: int = 1460) -> dict:
    """
    13D and 13G disclosures: someone crossing 5% of the company.

    The window is deliberately wide. Passive holders file 13G once a year in
    February, so an eighteen-month lookback can miss the entire most recent
    round and report "no filings" for a company that has plenty. A stake is
    also not news that expires -- who owns 5% of the company is still true a
    year later.

    The two are not the same signal. 13D means the holder intends to influence
    the company; 13G means they are passive, and is mostly index funds
    rebalancing. Merging them would turn routine Vanguard housekeeping into an
    activist alert, so the split is kept.
    """
    symbol = (symbol or "").upper().strip()
    payload = _submissions(symbol)
    if not payload:
        return {"symbol": symbol, "status": "NO_DATA", "source": "SEC"}

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows = [
        r for r in _recent_rows(payload)
        if r["form"] and r["form"].startswith("SC 13")
        and r["filed"] and r["filed"] >= cutoff
    ]

    activist = [r for r in rows if r["form"].startswith("SC 13D")]
    passive = [r for r in rows if r["form"].startswith("SC 13G")]

    cik = _cik(symbol)
    def shape(r: dict) -> dict:
        return {
            "form": r["form"],
            "filed": r["filed"],
            "amendment": r["form"].endswith("/A"),
            "kind": "ACTIVIST" if r["form"].startswith("SC 13D") else "PASSIVE",
            "url": (f"https://www.sec.gov/Archives/edgar/data/"
                    f"{int(cik)}/{r['accession']}/{r['document']}"
                    if cik and r["document"] else None),
        }

    return {
        "symbol": symbol,
        "window_days": days,
        "activist_count": len(activist),
        "passive_count": len(passive),
        "filings": [shape(r) for r in rows][:25],
        "latest_activist": shape(activist[0]) if activist else None,
        "status": "OK" if rows else "NO_FILINGS",
        "source": "SEC",
    }


def provider_status() -> dict:
    """Health probe: can we read a submissions feed at all."""
    payload = _submissions("AAPL")
    if not payload:
        return {"status": "PROVIDER_OFFLINE", "source": "SEC"}
    return {
        "status": "OK",
        "source": "SEC",
        "detail": "EDGAR submissions reachable",
    }
