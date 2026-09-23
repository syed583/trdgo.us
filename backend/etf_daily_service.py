"""
ETF holdings, read every day.

Every other ownership feed in this app is weeks or months behind, because that
is what the law requires: 13F quarterly, N-PORT monthly. ETFs are the
exception -- they publish their entire book daily so market makers can price
creations, and State Street's sector SPDRs publish the share count, not just
the weight.

That makes this the fastest ownership signal that exists publicly. When an
ETF's share count for a stock rises, the fund genuinely bought those shares
yesterday: money came into the fund and the basket was created. It is not a
manager's opinion -- an index fund buys what the index says -- but it is real
demand, and it is one day old rather than forty-five.

Weight is deliberately not treated as buying. A stock's weight rises when its
price rises, with nobody buying anything.
"""

from __future__ import annotations

import io
import os
import threading
import time
import urllib.request
from datetime import date, datetime
from typing import Optional

from database import SessionLocal, engine
from models_etf import EtfHolding, create_all

SOURCE = "SPDR daily holdings"
URL = ("https://www.ssga.com/us/en/intermediary/library-content/products/"
       "fund-data/etfs/us/holdings-daily-us-en-{ticker}.xlsx")
AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")
TIMEOUT = 60.0

# The eleven sector SPDRs between them hold every S&P 500 company, and each
# file carries Shares Held. SPY itself is not here: its file gives weight
# only, which cannot be read as buying.
ETFS = ("XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU",
        "XLRE", "XLC")

REFRESH_SECONDS = 6 * 3600


def _sheet(ticker: str):
    import openpyxl

    request = urllib.request.Request(URL.format(ticker=ticker.lower()),
                                     headers={"User-Agent": AGENT})
    body = urllib.request.urlopen(request, timeout=TIMEOUT).read()
    book = openpyxl.load_workbook(io.BytesIO(body), read_only=True)
    return book.active


def _as_of(rows: list) -> Optional[date]:
    """The file states its own date: "Holdings: As of 21-Sep-2026"."""
    for row in rows[:6]:
        for cell in row:
            text = str(cell or "")
            if "As of" in text:
                try:
                    return datetime.strptime(text.split("As of")[-1].strip(),
                                             "%d-%b-%Y").date()
                except ValueError:
                    return None
    return None


def fetch(ticker: str) -> dict:
    """One ETF's holdings for the day it publishes."""
    try:
        sheet = _sheet(ticker)
    except Exception as exc:  # noqa: BLE001
        return {"etf": ticker, "status": "PROVIDER_OFFLINE",
                "detail": f"{type(exc).__name__}", "holdings": []}

    rows = [[c.value for c in r] for r in sheet.iter_rows()]
    as_of = _as_of(rows)
    header_at = next((i for i, r in enumerate(rows)
                      if r and str(r[0] or "").strip() == "Name"), None)
    if header_at is None or as_of is None:
        return {"etf": ticker, "status": "NO_DATA",
                "detail": "The published file did not contain a holdings table.",
                "holdings": []}

    header = [str(c or "").strip() for c in rows[header_at]]
    index = {name: position for position, name in enumerate(header)}
    if "Shares Held" not in index:
        return {"etf": ticker, "status": "NO_SHARES",
                "detail": f"{ticker} publishes weights only, not share counts.",
                "holdings": []}

    holdings = []
    for row in rows[header_at + 1:]:
        symbol = str(row[index["Ticker"]] or "").strip().upper()
        if not symbol or symbol in ("-", "CASH"):
            continue
        try:
            shares = float(row[index["Shares Held"]] or 0) or None
            weight = float(row[index["Weight"]] or 0) or None
        except (TypeError, ValueError):
            continue
        holdings.append({"ticker": symbol,
                         "name": str(row[index["Name"]] or "").strip(),
                         "shares": shares, "weight_pct": weight})

    return {"etf": ticker, "status": "OK", "as_of": as_of.isoformat(),
            "holdings": holdings, "count": len(holdings), "source": SOURCE}


def store(payload: dict) -> int:
    """Save one day's file. A day already stored is left alone."""
    from sqlalchemy.dialects.postgresql import insert

    if payload.get("status") != "OK" or not payload.get("holdings"):
        return 0
    create_all(engine)
    as_of = date.fromisoformat(payload["as_of"])
    rows = [{"etf": payload["etf"], "ticker": h["ticker"], "name": h["name"],
             "as_of": as_of, "shares": h["shares"], "weight_pct": h["weight_pct"]}
            for h in payload["holdings"]]

    session = SessionLocal()
    try:
        statement = insert(EtfHolding.__table__).values(rows)
        statement = statement.on_conflict_do_nothing(
            constraint="uq_etf_ticker_date")
        statement = statement.returning(EtfHolding.__table__.c.id)
        added = len(session.execute(statement).fetchall())
        session.commit()
        return added
    finally:
        session.close()


def refresh(etfs: tuple = ETFS) -> dict:
    """Pull today's file for each ETF and store what is new."""
    stored = {}
    for ticker in etfs:
        payload = fetch(ticker)
        stored[ticker] = {"status": payload.get("status"),
                          "as_of": payload.get("as_of"),
                          "rows_added": store(payload)}
    return {"status": "OK", "etfs": stored, "source": SOURCE}


def daily_flows(symbol: str, days: int = 10) -> dict:
    """
    Day-by-day change in the shares ETFs hold of one stock.

    Summed across the sector funds that hold it, because a reader wants "are
    the index funds accumulating this", not a per-fund breakdown of the same
    flow.
    """
    from sqlalchemy import func

    symbol = (symbol or "").upper().strip()
    create_all(engine)
    session = SessionLocal()
    try:
        rows = (session.query(
            EtfHolding.as_of,
            func.sum(EtfHolding.shares).label("shares"),
            func.count(EtfHolding.etf).label("funds"))
            .filter(EtfHolding.ticker == symbol)
            .group_by(EtfHolding.as_of)
            .order_by(EtfHolding.as_of.desc())
            .limit(days + 1).all())
    finally:
        session.close()

    if not rows:
        return {"symbol": symbol, "status": "NO_DATA", "days": [],
                "detail": ("No ETF file has been read for this stock yet. The "
                           "sector SPDRs cover the S&P 500 only."),
                "source": SOURCE}

    ordered = list(reversed(rows))
    out = []
    for position, row in enumerate(ordered):
        prior = ordered[position - 1] if position else None
        change = (row.shares or 0) - (prior.shares or 0) if prior else None
        out.append({
            "date": row.as_of.isoformat(),
            "shares": row.shares,
            "funds": row.funds,
            "share_change": change,
            "share_change_pct": (round(change / prior.shares * 100, 3)
                                 if prior and prior.shares else None),
        })

    scored = [d for d in out if d["share_change"] is not None]
    added = sum(1 for d in scored if d["share_change"] > 0)
    cut = sum(1 for d in scored if d["share_change"] < 0)
    total_change = sum(d["share_change"] for d in scored) if scored else None

    return {
        "symbol": symbol,
        "status": "OK",
        "days": out[-days:],
        "latest": out[-1],
        "days_added": added,
        "days_trimmed": cut,
        "net_shares": total_change,
        "trend": ("accumulating" if added > cut else
                  "distributing" if cut > added else "steady"),
        "detail": (
            "Shares held by the sector SPDRs, which publish their whole book "
            "daily. A rise means the fund created units and bought the stock "
            "yesterday -- money arriving, not a manager's opinion."
        ),
        "source": SOURCE,
    }


def start() -> None:
    """Refresh once a day in the background; files update overnight."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return

    def loop() -> None:
        time.sleep(120)
        while True:
            try:
                refresh()
            except Exception:  # noqa: BLE001 - never take down the server
                pass
            time.sleep(REFRESH_SECONDS)

    threading.Thread(target=loop, daemon=True, name="etf-daily").start()
