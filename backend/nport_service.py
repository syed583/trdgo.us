"""
Form N-PORT: what every mutual fund and ETF held, month by month.

13F answers "who owns this" once a quarter, forty-five days late, and only for
institutions over $100m. N-PORT answers it every month, for every registered
fund -- which is where most retail money actually sits. Three readings a
quarter instead of one is the difference between "funds were net buyers last
quarter" and "funds added in May, added again in June, and stopped in July".

The catch is the lag: the SEC publishes these as quarterly archives about two
months after the quarter ends, so this is depth rather than speed. The live
13F watcher is the fast lane; this is the wide one.

Only the securities the app already tracks are kept. The archive is 910MB of
holdings across every fund in the country and almost all of it is bonds,
swaps and names nobody here searches.
"""

from __future__ import annotations

import csv
import io
import os
import re
import urllib.request
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Optional

from database import SessionLocal, engine
from models_nport import NportHolding, NportIngest, create_all

SOURCE = "SEC N-PORT"
INDEX_URL = "https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets"
AGENT = "US-Stock Reader personal research (jeetubajaj@gmail.com)"
DOWNLOADS = Path(__file__).resolve().parent / ".cache" / "nport"

# Equity positions held outright. The rest of the archive -- debt, swaps,
# futures, repo -- is a different question from "who owns the shares".
SHARE_UNITS = {"NS"}
BATCH = 5000


def _fetch(url: str, timeout: int = 900) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def list_datasets() -> list[str]:
    """Quarterly archive links, oldest first."""
    try:
        body = _fetch(INDEX_URL, timeout=90).decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return []
    links = re.findall(r'href="([^"]*form-n-port-data-sets/[^"]*\.zip)"', body)
    unique = sorted(set(links), key=_period_key)
    return [link if link.startswith("http") else f"https://www.sec.gov{link}"
            for link in unique]


def _period_key(link: str) -> tuple:
    found = re.search(r"(\d{4})q(\d)", link)
    return (int(found.group(1)), int(found.group(2))) if found else (0, 0)


def download(link: str) -> Optional[Path]:
    """Fetch an archive once; a file already on disk is reused."""
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    target = DOWNLOADS / link.rsplit("/", 1)[-1]
    if target.exists() and target.stat().st_size > 1_000_000:
        return target
    try:
        data = _fetch(link)
    except Exception:  # noqa: BLE001
        return None
    temporary = target.with_suffix(".part")
    temporary.write_bytes(data)
    os.replace(temporary, target)
    return target


def _day(value: str) -> Optional[date]:
    """N-PORT writes dates as 28-FEB-2026."""
    try:
        return datetime.strptime((value or "").strip(), "%d-%b-%Y").date()
    except ValueError:
        return None


def _rows(archive: zipfile.ZipFile, name: str) -> Iterator[dict]:
    with archive.open(name) as raw:
        stream = io.TextIOWrapper(raw, encoding="utf-8", errors="ignore",
                                  newline="")
        for row in csv.DictReader(stream, delimiter="\t"):
            yield row


def _tracked_cusips() -> dict[str, Optional[str]]:
    """CUSIP -> ticker for every security the app already holds detail on."""
    from models_institutional import InstitutionalSecurity

    session = SessionLocal()
    try:
        return {row.cusip.upper(): row.ticker
                for row in session.query(InstitutionalSecurity).all()
                if row.cusip}
    finally:
        session.close()


def _num(value: str) -> Optional[float]:
    try:
        return float(value) if value not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        return None


def ingest_dataset(link: str) -> dict:
    """Load one quarterly archive. Rows outside the tracked list are skipped."""
    create_all(engine)
    name = link.rsplit("/", 1)[-1]
    session = SessionLocal()
    try:
        done = session.query(NportIngest).filter_by(dataset=name).one_or_none()
        if done and done.status == "OK":
            return {"status": "ALREADY_INGESTED", "dataset": name,
                    "holdings": done.holdings, "months": done.months}
    finally:
        session.close()

    path = download(link)
    if path is None:
        return {"status": "DOWNLOAD_FAILED", "dataset": name}

    wanted = _tracked_cusips()
    if not wanted:
        return {"status": "NO_SECURITIES", "dataset": name,
                "detail": "No tracked securities; load the 13F datasets first."}

    archive = zipfile.ZipFile(path)

    # Both lookups are small -- one row per filing, not per holding.
    periods: dict[str, date] = {}
    for row in _rows(archive, "SUBMISSION.tsv"):
        when = _day(row.get("REPORT_DATE") or "")
        if when:
            periods[row["ACCESSION_NUMBER"]] = when

    funds: dict[str, tuple] = {}
    for row in _rows(archive, "REGISTRANT.tsv"):
        funds[row["ACCESSION_NUMBER"]] = (row.get("CIK"),
                                          row.get("REGISTRANT_NAME"))

    session = SessionLocal()
    kept = 0
    months: set = set()
    batch: list[dict] = []
    seen: set = set()
    try:
        for row in _rows(archive, "FUND_REPORTED_HOLDING.tsv"):
            cusip = (row.get("ISSUER_CUSIP") or "").upper()
            if cusip not in wanted or (row.get("UNIT") or "") not in SHARE_UNITS:
                continue
            accession = row.get("ACCESSION_NUMBER") or ""
            when = periods.get(accession)
            if not when:
                continue
            key = (accession, cusip)
            if key in seen:
                continue
            seen.add(key)
            cik, fund_name = funds.get(accession, (None, None))
            batch.append({
                "accession": accession, "fund_cik": cik, "fund_name": fund_name,
                "cusip": cusip, "ticker": wanted.get(cusip),
                "issuer_name": row.get("ISSUER_NAME"), "report_date": when,
                "shares": _num(row.get("BALANCE")),
                "value": _num(row.get("CURRENCY_VALUE")),
                "percent_of_fund": _num(row.get("PERCENTAGE")),
            })
            months.add(when.isoformat())
            if len(batch) >= BATCH:
                kept += _write(session, batch)
                batch = []
        if batch:
            kept += _write(session, batch)

        # The month list is a summary, not a record: an archive carries
        # dozens of distinct month ends and the whole list does not fit the
        # column, which failed the commit after the holdings were already in.
        ordered = sorted(months)
        summary = (f"{ordered[0]}..{ordered[-1]} ({len(ordered)} month ends)"
                   if ordered else "")
        session.merge(NportIngest(
            dataset=name, status="OK", holdings=kept, months=summary[:120],
            detail=f"{len(wanted)} securities tracked"))
        session.commit()
    finally:
        session.close()

    return {"status": "OK", "dataset": name, "holdings": kept,
            "months": sorted(months)}


def _write(session, batch: list[dict]) -> int:
    """Insert a batch, ignoring rows a previous run already stored."""
    from sqlalchemy.dialects.postgresql import insert

    if not batch:
        return 0
    statement = insert(NportHolding.__table__).values(batch)
    statement = statement.on_conflict_do_nothing(
        constraint="uq_nport_accession_cusip")
    result = session.execute(statement)
    session.commit()
    return result.rowcount if result.rowcount and result.rowcount > 0 else len(batch)


def ingest_latest(count: int = 1) -> dict:
    """Load the newest ``count`` archives."""
    datasets = list_datasets()
    if not datasets:
        return {"status": "NO_DATASETS"}
    return {"status": "OK",
            "ingested": [ingest_dataset(link) for link in datasets[-count:]]}


def monthly_flows(symbol: str, months: int = 6) -> dict:
    """
    Fund ownership of one stock, month by month.

    Each month is the total shares held across every fund that reported it,
    with the change on the month before. Fund counts come along because the
    two say different things: shares held is the weight of money, the number
    of funds is how broadly it is held.
    """
    from sqlalchemy import func

    symbol = (symbol or "").upper().strip()
    create_all(engine)
    session = SessionLocal()
    try:
        # Grouped by calendar month, not by the exact report date: funds use
        # 27 February and 28 February for the same month end, and two rows
        # for one month reads as a month where everybody sold and rebought.
        #
        # Per fund, not just per month, because funds report on their own
        # fiscal calendars: a month where two hundred funds reported and the
        # next where twenty did are not comparable totals, and subtracting
        # one from the other produced -99% "selling" that was really just a
        # different set of filers. The change below is computed only across
        # funds that reported in both months.
        period = func.date_trunc("month", NportHolding.report_date).label("period")
        rows = (session.query(
            period,
            NportHolding.fund_cik.label("fund"),
            func.sum(NportHolding.shares).label("shares"),
            func.sum(NportHolding.value).label("value"))
            .filter(NportHolding.ticker == symbol)
            .group_by(period, NportHolding.fund_cik)
            .all())
    finally:
        session.close()

    if not rows:
        return {"symbol": symbol, "status": "NO_DATA", "months": [],
                "detail": ("No N-PORT months loaded for this symbol. The "
                           "quarterly archive may not be ingested yet."),
                "source": SOURCE}

    # Each fund's own reports, in order. A fund files on its own fiscal
    # calendar, so the set reporting in March is not the set reporting in
    # April -- comparing month totals compared different funds and produced
    # "-99% selling" out of nothing but a change of filers. Every change
    # below is one fund against its own previous report.
    per_fund: dict[str, list] = {}
    for row in rows:
        per_fund.setdefault(row.fund or "?", []).append(
            (row.period.date(), row.shares or 0.0, row.value or 0.0))

    buckets: dict[str, dict] = {}
    for reports in per_fund.values():
        reports.sort()
        for index, (when, shares, value) in enumerate(reports):
            key = when.strftime("%Y-%m")
            bucket = buckets.setdefault(key, {
                "month": key, "shares": 0.0, "value": 0.0, "funds": 0,
                "funds_compared": 0, "funds_added": 0, "funds_trimmed": 0,
                "share_change": 0.0,
            })
            bucket["shares"] += shares
            bucket["value"] += value
            bucket["funds"] += 1
            if index:
                before = reports[index - 1][1]
                change = shares - before
                bucket["funds_compared"] += 1
                bucket["share_change"] += change
                if change > 0:
                    bucket["funds_added"] += 1
                elif change < 0:
                    bucket["funds_trimmed"] += 1

    out = []
    for key in sorted(buckets):
        bucket = buckets[key]
        compared = bucket["funds_compared"]
        net = bucket["share_change"] if compared else None
        added, trimmed = bucket["funds_added"], bucket["funds_trimmed"]
        out.append({
            **bucket,
            "share_change": net,
            # The share of reporting funds that grew their position. Breadth
            # rather than size: one giant fund rebalancing should not read as
            # the whole market moving.
            "added_share_pct": (round(added / compared * 100, 1)
                                if compared else None),
            "direction": ("added" if compared and added > trimmed
                          else "trimmed" if compared and trimmed > added
                          else "held" if compared else None),
        })

    scored = [m for m in out if m["funds_compared"]]
    rising = sum(1 for m in scored if m["funds_added"] > m["funds_trimmed"])
    falling = sum(1 for m in scored if m["funds_trimmed"] > m["funds_added"])
    trend = ("accumulating" if rising > falling else
             "distributing" if falling > rising else
             "steady" if scored else "not enough history")

    return {
        "symbol": symbol,
        "status": "OK",
        "months": out[-months:],
        "latest_month": out[-1]["month"] if out else None,
        "months_rising": rising,
        "months_falling": falling,
        "trend": trend,
        "detail": (
            "Every registered fund -- mutual funds and ETFs -- reports its "
            "holdings on Form N-PORT, and the months below are when those "
            "reports fall due. Each fund is compared with its own previous "
            "report, never with the other funds that happened to report the "
            "same month. Published about two months after the quarter ends, "
            "so this is breadth rather than speed."
        ),
        "source": SOURCE,
    }


def status() -> dict:
    """Which archives are loaded, for the settings screen."""
    create_all(engine)
    session = SessionLocal()
    try:
        rows = (session.query(NportIngest)
                .order_by(NportIngest.ingested_at.desc()).all())
        return {"status": "OK", "datasets": [
            {"dataset": r.dataset, "holdings": r.holdings, "months": r.months,
             "state": r.status,
             "ingested_at": r.ingested_at.isoformat() if r.ingested_at else None}
            for r in rows], "source": SOURCE}
    finally:
        session.close()
