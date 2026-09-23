"""
13F filings, read on the day they are filed.

The SEC's bulk 13F datasets arrive weeks after the funds file, so the board's
institutional numbers were a quarter behind plus the wait for the dataset. The
filings themselves are public the moment they are accepted, and each one
carries the fund's whole equity book.

So: watch EDGAR's live feed for 13F-HR, pull the holdings table of anything
new, keep the positions in the watched universe, and compare them with what
that same fund reported last quarter. The result is "this fund bought 2m
shares of NVDA", on filing day rather than a month later.

Deliberately bounded. A filing season puts thousands of these through in a
week, and every one is a download and an XML parse, so each pass takes a
handful of filings and the rest wait for the next one. Nothing here blocks a
page: the events are read from memory.
"""

from __future__ import annotations

import os
import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

SOURCE = "SEC 13F"
FEED = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=13F-HR"
        "&company=&dateb=&owner=include&count=40&output=atom")
AGENT = "US-Stock Reader personal research (jeetubajaj@gmail.com)"

# Per pass, because each one is a fetch of the index plus a holdings table
# that can run to megabytes.
FILINGS_PER_PASS = 6
MAX_TABLE_BYTES = 12 * 1024 * 1024
TIMEOUT = 30.0
PAUSE = 0.6  # EDGAR asks for well under 10 requests a second; this is far under

POLL_BUSY = 300.0
POLL_QUIET = 1800.0
KEEP = 300

_NS = {"a": "http://www.w3.org/2005/Atom"}
_lock = threading.Lock()
_events: list[dict] = []
_seen: set[str] = set()
_cusip_to_symbol: dict[str, str] = {}
_last_poll: Optional[float] = None
_last_detail: Optional[str] = None


def _get(url: str) -> bytes:
    time.sleep(PAUSE)
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    return urllib.request.urlopen(request, timeout=TIMEOUT).read()


def _universe_cusips() -> dict[str, str]:
    """CUSIP -> ticker, for the symbols the board scores."""
    global _cusip_to_symbol

    if _cusip_to_symbol:
        return _cusip_to_symbol
    import ai_trade_service as board
    import institutional_service as inst
    from database import SessionLocal

    session = SessionLocal()
    try:
        mapping = {}
        for symbol in board.UNIVERSE:
            try:
                cusip = inst.resolve_cusip(session, symbol)
            except Exception:  # noqa: BLE001
                cusip = None
            if cusip:
                mapping[cusip.upper()] = symbol
        _cusip_to_symbol = mapping
        return mapping
    finally:
        session.close()


def _latest_feed() -> list[dict]:
    root = ET.fromstring(_get(FEED))
    out = []
    for entry in root.findall("a:entry", _NS):
        title = entry.findtext("a:title", default="", namespaces=_NS)
        link = entry.find("a:link", _NS)
        href = link.get("href") if link is not None else None
        form, _, rest = title.partition(" - ")
        # Amendments restate rather than report a change, so they are skipped.
        if not href or form.strip() != "13F-HR":
            continue
        out.append({"fund": rest.split("(")[0].strip(), "url": href,
                    "updated": entry.findtext("a:updated", default="",
                                              namespaces=_NS)})
    return out


def _holdings(index_url: str, wanted: dict[str, str]) -> dict[str, dict]:
    """Positions in the watched universe, from a filing's holdings table."""
    page = _get(index_url).decode("utf-8", "ignore")
    tables = [x for x in re.findall(r'href="([^"]+\.xml)"', page)
              if "primary_doc" not in x and "/xsl" not in x]
    for path in tables:
        body = _get("https://www.sec.gov" + path)
        if b"infoTable" not in body or len(body) > MAX_TABLE_BYTES:
            continue
        held: dict[str, dict] = {}
        for row in (el for el in ET.fromstring(body).iter()
                    if el.tag.endswith("infoTable")):
            fields = {c.tag.split("}")[-1]: (c.text or "").strip() for c in row}
            symbol = wanted.get((fields.get("cusip") or "").upper())
            if not symbol:
                continue
            amount = None
            for child in row:
                if child.tag.endswith("shrsOrPrnAmt") and len(child):
                    amount = (child[0].text or "").strip()
            try:
                shares = float(amount) if amount else None
                value = float(fields.get("value") or 0) or None
            except ValueError:
                continue
            if shares is None:
                continue
            # One fund can report a name across several rows (different
            # managers, discretion categories); they are one position.
            prior = held.get(symbol)
            held[symbol] = {
                "cusip": (fields.get("cusip") or "").upper(),
                "shares": shares + (prior["shares"] if prior else 0.0),
                "value": (value or 0) + (prior["value"] if prior else 0.0),
            }
        if held:
            return held
    return {}


def _previous(cik: Optional[str], fund: str, cusips: list) -> dict[str, float]:
    """What this fund reported for these CUSIPs in the last stored quarter."""
    from database import SessionLocal
    from models_institutional import InstitutionalFund, InstitutionalHolding

    session = SessionLocal()
    try:
        # EDGAR writes a CIK unpadded in a URL and zero-padded elsewhere, and
        # the datasets store the padded form -- so a filing matched nothing and
        # every position read as a brand-new one.
        forms = []
        if cik:
            forms = [str(int(cik)), str(int(cik)).zfill(10)]
        row = None
        if forms:
            row = (session.query(InstitutionalFund)
                   .filter(InstitutionalFund.cik.in_(forms)).first())
        if row is None:
            row = (session.query(InstitutionalFund)
                   .filter(InstitutionalFund.fund_name.ilike(fund)).first())
        if row is None:
            return {}
        holdings = (session.query(InstitutionalHolding)
                    .filter(InstitutionalHolding.fund_id == row.id,
                            InstitutionalHolding.cusip.in_(cusips))
                    .order_by(InstitutionalHolding.report_quarter.desc())
                    .all())
        latest_quarter = holdings[0].report_quarter if holdings else None
        out: dict[str, float] = {}
        for holding in holdings:
            if holding.report_quarter != latest_quarter:
                continue
            key = (holding.cusip or "").upper()
            out[key] = out.get(key, 0.0) + (holding.shares or 0.0)
        return out
    except Exception:  # noqa: BLE001 - no history is not an error
        return {}
    finally:
        session.close()


def _cik_from(url: str) -> Optional[str]:
    found = re.search(r"/data/(\d+)/", url or "")
    return found.group(1) if found else None


def poll_once(limit: int = FILINGS_PER_PASS) -> int:
    """Read the newest filings not seen before. Returns events produced."""
    global _last_poll, _last_detail

    wanted = _universe_cusips()
    if not wanted:
        _last_detail = "No CUSIP mapping yet; 13F datasets may not be loaded."
        return 0

    try:
        feed = _latest_feed()
    except Exception as exc:  # noqa: BLE001
        _last_detail = f"EDGAR feed unavailable: {type(exc).__name__}"
        return 0

    produced = 0
    for filing in feed:
        if produced >= limit:
            break
        with _lock:
            if filing["url"] in _seen:
                continue
            _seen.add(filing["url"])
        try:
            held = _holdings(filing["url"], wanted)
        except Exception:  # noqa: BLE001 - one bad filing is not a failure
            continue
        if not held:
            continue

        cik = _cik_from(filing["url"])
        before = _previous(cik, filing["fund"],
                           [p["cusip"] for p in held.values() if p["cusip"]])
        rows = []
        for symbol, position in sorted(held.items()):
            prior = before.get(position["cusip"])
            change = None if prior is None else position["shares"] - prior
            rows.append({
                "symbol": symbol,
                "shares": position["shares"],
                "value": position["value"],
                "previous_shares": prior,
                "share_change": change,
                "action": ("NEW POSITION" if prior is None else
                           "ADDED" if change and change > 0 else
                           "TRIMMED" if change and change < 0 else "HELD"),
            })
        with _lock:
            _events.insert(0, {
                "fund": filing["fund"], "cik": cik, "form": "13F-HR",
                "filed_at": filing["updated"], "url": filing["url"],
                "positions": rows, "source": SOURCE,
                "seen_at": datetime.now(timezone.utc).isoformat(),
            })
            del _events[KEEP:]
        produced += 1

    _last_poll = time.time()
    if produced:
        _last_detail = None
    return produced


def recent(limit: int = 30, symbol: Optional[str] = None) -> dict:
    """Fund filings just read, newest first; filtered to one symbol if asked."""
    symbol = (symbol or "").upper() or None
    with _lock:
        rows = []
        for event in _events:
            positions = [p for p in event["positions"]
                         if not symbol or p["symbol"] == symbol]
            if positions:
                rows.append({**event, "positions": positions})
    return {
        "status": "OK",
        "filings": rows[:limit],
        "count": len(rows),
        "watching_symbols": len(_cusip_to_symbol),
        "last_poll": (datetime.fromtimestamp(_last_poll, timezone.utc).isoformat()
                      if _last_poll else None),
        "detail": _last_detail or (
            "13F filings read from EDGAR on the day they are filed, compared "
            "with the same fund's last reported quarter. Funds file within 45 "
            "days of quarter end, so the busy weeks are mid-February, May, "
            "August and November."
        ),
        "source": SOURCE,
    }


def start() -> None:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return

    def loop() -> None:
        time.sleep(60)  # let the server finish starting
        while True:
            try:
                produced = poll_once()
            except Exception:  # noqa: BLE001
                produced = 0
            time.sleep(POLL_BUSY if produced else POLL_QUIET)

    threading.Thread(target=loop, daemon=True, name="funds-live").start()
