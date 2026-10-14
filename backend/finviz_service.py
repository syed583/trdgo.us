"""Finviz Elite -- read-only data via the authenticated export API.

Right now this provides one thing the other feeds don't: the earnings release
date WITH a clock time (e.g. "11/2/2026 4:30:00 PM"), which Unusual Whales only
classifies as BMO/AMC. Used to show the real result time on the earnings tabs.

The token is FINVIZ_API_TOKEN in backend/.env (the Elite "auth" token). Never
committed, never logged.
"""
from __future__ import annotations

import csv
import io
import os
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional

BASE = "https://elite.finviz.com/export.ashx"
# Custom-view column indices: 1 = Ticker, 68 = Earnings Date.
_COLS = "1,68"
_CHUNK = 100                      # tickers per export request
_UA = "Mozilla/5.0"


def _token() -> Optional[str]:
    return (os.getenv("FINVIZ_API_TOKEN") or "").strip() or None


def configured() -> bool:
    return _token() is not None


def _get(params: dict) -> Optional[str]:
    tok = _token()
    if not tok:
        return None
    url = f"{BASE}?{urllib.parse.urlencode(params)}&auth={urllib.parse.quote(tok)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            if r.status != 200:
                return None
            return r.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None


def _parse_earnings(raw) -> Optional[dict]:
    """Finviz 'M/D/YYYY h:mm:ss AM/PM' (date may come without a time) ->
    {date: 'YYYY-MM-DD', time: '4:30 PM' | None}. None if unparseable/empty."""
    s = (raw or "").strip()
    if not s or s == "-":
        return None
    parts = s.split(" ", 1)
    date_s = parts[0]
    time_s = parts[1].strip() if len(parts) > 1 else ""
    try:
        d = datetime.strptime(date_s, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None
    clock = None
    if time_s:
        for fmt in ("%I:%M:%S %p", "%I:%M %p"):
            try:
                clock = datetime.strptime(time_s, fmt).strftime("%-I:%M %p")
                break
            except ValueError:
                try:                       # Windows has no %-I; fall back to %I
                    clock = datetime.strptime(time_s, fmt).strftime("%I:%M %p").lstrip("0")
                    break
                except ValueError:
                    continue
    return {"date": d, "time": clock}


def _fetch_chunk(symbols: list[str]) -> dict:
    out: dict = {}
    data = _get({"v": "152", "t": ",".join(symbols), "c": _COLS})
    if not data:
        return out
    rows = list(csv.reader(io.StringIO(data)))
    if not rows:
        return out
    hdr = rows[0]
    try:
        i_tkr = hdr.index("Ticker")
        i_earn = hdr.index("Earnings Date")
    except ValueError:
        return out
    for row in rows[1:]:
        if len(row) <= max(i_tkr, i_earn):
            continue
        sym = (row[i_tkr] or "").upper().strip()
        parsed = _parse_earnings(row[i_earn])
        if sym and parsed:
            out[sym] = parsed
    return out


def fetch_earnings(symbols: list[str]) -> dict:
    """{SYM: {date, time}} for the given tickers (batched). Missing/failed
    tickers are simply absent."""
    syms = [s.upper() for s in dict.fromkeys(symbols) if s]
    out: dict = {}
    for i in range(0, len(syms), _CHUNK):
        out.update(_fetch_chunk(syms[i:i + _CHUNK]))
    return out


def earnings_map(symbols: Optional[list[str]] = None) -> dict:
    """Cached {SYM: {date, time}} for the upcoming-earnings universe. Refreshed
    every 6h (release dates move slowly). Finviz being down just yields {}."""
    import swr

    def _build() -> dict:
        syms = symbols
        if syms is None:
            try:
                import uw_earnings_calendar as cal
                rows = (cal.calendar(days=20) or {}).get("rows") or []
                syms = [(r.get("symbol") or "").upper() for r in rows if r.get("symbol")]
            except Exception:  # noqa: BLE001
                syms = []
        return fetch_earnings(syms) if syms else {}

    if symbols is not None:                # ad-hoc list: don't pollute the shared key
        return _build()
    return swr.serve("fviz:earn", _build, 6 * 3600.0) or {}
