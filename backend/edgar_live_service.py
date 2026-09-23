"""
EDGAR, watched live.

A company's own filings are the first public record of a management change, a
merger or a results release, and EDGAR publishes each one within seconds of
accepting it. Reading a company's filing history every few hours -- which is
what the events panel does -- therefore shows real events late.

So one request a minute to EDGAR's "latest filings" feed, which lists what the
whole market has just filed, and anything from a company being watched is kept
and its cached history dropped so the next reader sees the new filing.

One feed for the whole universe rather than one request per symbol: 38
companies polled individually would be 38 requests a minute at EDGAR, which is
both rude and slower.
"""

from __future__ import annotations

import os
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

SOURCE = "SEC EDGAR"
FEED = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={type}"
        "&company=&dateb=&owner=include&count=100&output=atom")

# EDGAR asks for a real contact in the agent string, and rate-limits hard
# without one.
AGENT = "US-Stock Reader personal research (jeetubajaj@gmail.com)"

# Forms worth waking up for. 8-K is the event report; the rest is merger and
# tender paperwork.
TYPES = ("8-K", "425")

POLL_OPEN = 60.0
POLL_CLOSED = 600.0
TIMEOUT = 20.0
KEEP = 200

_NS = {"a": "http://www.w3.org/2005/Atom"}

_lock = threading.Lock()
_seen: set[str] = set()
_events: list[dict] = []
_cik_to_symbol: dict[str, str] = {}
_last_poll: Optional[float] = None
_last_error: Optional[str] = None


def _watched() -> dict[str, str]:
    """CIK (no leading zeros) -> symbol, for every symbol on the board."""
    global _cik_to_symbol

    import ai_trade_service as board
    import sec_filings_service as sec

    if len(_cik_to_symbol) >= len(board.UNIVERSE):
        return _cik_to_symbol
    mapping = dict(_cik_to_symbol)
    for symbol in board.UNIVERSE:
        try:
            cik = sec._cik(symbol)
        except Exception:  # noqa: BLE001
            cik = None
        if cik:
            mapping[str(int(cik))] = symbol
    _cik_to_symbol = mapping
    return mapping


def _fetch(form_type: str) -> list[dict]:
    request = urllib.request.Request(FEED.format(type=form_type),
                                     headers={"User-Agent": AGENT})
    body = urllib.request.urlopen(request, timeout=TIMEOUT).read()
    root = ET.fromstring(body)
    rows = []
    for entry in root.findall("a:entry", _NS):
        title = entry.findtext("a:title", default="", namespaces=_NS)
        updated = entry.findtext("a:updated", default="", namespaces=_NS)
        link = entry.find("a:link", _NS)
        href = link.get("href") if link is not None else None
        # "8-K - NN INC (0000918541) (Filer)"
        form, _, rest = title.partition(" - ")
        cik = None
        if "(" in rest:
            candidate = rest.rsplit("(", 2)[-2].strip(") ")
            cik = candidate if candidate.isdigit() else None
        rows.append({"form": form.strip(), "company": rest.split("(")[0].strip(),
                     "cik": cik, "updated": updated, "url": href})
    return rows


def poll_once() -> int:
    """One pass. Returns how many new filings from watched companies landed."""
    global _last_poll, _last_error

    watched = _watched()
    found = 0
    for form_type in TYPES:
        try:
            rows = _fetch(form_type)
        except Exception as exc:  # noqa: BLE001 - a missed poll is not an error
            _last_error = f"{type(exc).__name__}: {exc}"
            continue
        for row in rows:
            cik = row.get("cik")
            symbol = watched.get(str(int(cik))) if cik and cik.isdigit() else None
            if not symbol or not row.get("url"):
                continue
            key = row["url"]
            with _lock:
                if key in _seen:
                    continue
                _seen.add(key)
            event = {
                "symbol": symbol, "company": row["company"], "form": row["form"],
                "filed_at": row["updated"], "url": row["url"], "source": SOURCE,
                "seen_at": datetime.now(timezone.utc).isoformat(),
            }
            with _lock:
                _events.insert(0, event)
                del _events[KEEP:]
            found += 1
            _invalidate(symbol)
    _last_poll = time.time()
    _last_error = None if found or not _last_error else _last_error
    return found


def _invalidate(symbol: str) -> None:
    """Drop the cached filing history so the panel shows the new filing."""
    try:
        import swr
        from live_market_service import cache

        cache.purge(f"corpevents:{symbol}")
        swr.clear_key(f"corpevents:{symbol}")
    except Exception:  # noqa: BLE001
        pass


def recent(limit: int = 40, symbol: Optional[str] = None) -> dict:
    with _lock:
        rows = [e for e in _events
                if not symbol or e["symbol"] == (symbol or "").upper()]
    return {
        "status": "OK",
        "events": rows[:limit],
        "count": len(rows),
        "watching": len(_cik_to_symbol),
        "last_poll": (datetime.fromtimestamp(_last_poll, timezone.utc).isoformat()
                      if _last_poll else None),
        "seconds_since_poll": round(time.time() - _last_poll) if _last_poll else None,
        "detail": _last_error or (
            "EDGAR's live feed, checked every minute while the market is open. "
            "A filing appears here within about a minute of the company "
            "submitting it."
        ),
        "source": SOURCE,
    }


def start() -> None:
    """Poll in the background for the life of the server."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return

    def loop() -> None:
        from live_market_service import market_clock

        while True:
            try:
                poll_once()
            except Exception:  # noqa: BLE001 - the watcher must never die
                pass
            session = (market_clock() or {}).get("session")
            time.sleep(POLL_OPEN if session in ("OPEN", "PRE_MARKET", "AFTER_HOURS")
                       else POLL_CLOSED)

    threading.Thread(target=loop, daemon=True, name="edgar-live").start()
