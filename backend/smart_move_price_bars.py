"""Historical price-bar ingestion INTERFACE (Phase 3, offline). Takes daily bars that someone else obtained (a saved file, a future authorized fetch) and validates, hashes and stores them append-only. It never
fetches, never fills a gap, never adjusts a price and never invents a bar: a row that fails validation is rejected with a reason and kept in the batch record.

Accepted row: {date, open, high, low, close, volume?, market_time='r'} with finite positive prices, low <= open/close <= high, a trading day, not dated after the retrieval day, and (for the retrieval day itself)
only when the retrieval happened after that session's close + 30 minutes (otherwise it is a partial bar). Output of `bars_by_symbol()` has the shape the outcome audit reads."""
from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import market_calendar as mc  # noqa: E402

UTC = timezone.utc
SAFETY_MIN = 30
SCHEMA = "smart_move.price_bars.v1"


def _dt(x) -> datetime:
    d = x if isinstance(x, datetime) else datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def validate_rows(rows: Iterable[dict], retrieved_at) -> dict:
    """-> {accepted: [row], rejected: [{row, reason}]}. Regular-session rows only ('r'); extended-hours rows are rejected, not merged."""
    ret = _dt(retrieved_at)
    acc, rej, seen = [], [], {}
    for r in rows:
        def no(reason, r=r):
            rej.append({"row": r, "reason": reason})
        if not isinstance(r, dict):
            no("NOT_AN_OBJECT")
            continue
        if str(r.get("market_time", "r")).lower() != "r":
            no("NOT_REGULAR_SESSION")
            continue
        try:
            d = date.fromisoformat(str(r.get("date"))[:10])
        except ValueError:
            no("BAD_DATE")
            continue
        o, h, l, c = (_num(r.get(k)) for k in ("open", "high", "low", "close"))
        if None in (o, h, l, c) or min(o, h, l, c) <= 0:
            no("BAD_OR_NON_POSITIVE_PRICE")
            continue
        if not (l <= min(o, c) and max(o, c) <= h):
            no("OHLC_INCONSISTENT")
            continue
        vol = r.get("volume")
        if vol not in (None, "") and (_num(vol) is None or _num(vol) < 0):
            no("BAD_VOLUME")
            continue
        if not mc.is_trading_day(d):
            no("NOT_A_TRADING_DAY")
            continue
        if d > ret.astimezone(mc.NY).date():
            no("DATED_AFTER_RETRIEVAL")
            continue
        if mc.session_close_utc(d) + timedelta(minutes=SAFETY_MIN) > ret:
            no("PARTIAL_OR_UNCLOSED_SESSION")
            continue
        if d in seen:
            if seen[d] != (o, h, l, c):
                no("CONFLICTING_DUPLICATE_DATE")
            else:
                no("DUPLICATE_DATE")
            continue
        seen[d] = (o, h, l, c)
        acc.append({"date": d.isoformat(), "open": o, "high": h, "low": l, "close": c, "volume": vol, "market_time": "r"})
    acc.sort(key=lambda x: x["date"])
    return {"accepted": acc, "rejected": rej}


def missing_sessions(accepted: list) -> list:
    """Trading days between the first and last accepted bar that have no bar (reported, never filled)."""
    if len(accepted) < 2:
        return []
    have = {x["date"] for x in accepted}
    d, end, out = date.fromisoformat(accepted[0]["date"]), date.fromisoformat(accepted[-1]["date"]), []
    while d <= end:
        if mc.is_trading_day(d) and d.isoformat() not in have:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


class BarStore:
    """Append-only batches: one line per ingest with the accepted rows, the rejected rows with reasons, a content hash and the declared source. Nothing is edited or removed."""

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.batches = [json.loads(ln) for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()] if self.path and self.path.exists() else []

    def ingest(self, symbol: str, rows: Iterable[dict], *, source: str, retrieved_at) -> dict:
        if not str(source or "").strip():
            raise ValueError("a source description is required (where did these bars come from?)")
        v = validate_rows(list(rows), retrieved_at)
        body = {"schema": SCHEMA, "symbol": symbol.upper(), "source": source, "retrieved_at": _dt(retrieved_at).isoformat(), "accepted": v["accepted"], "rejected": v["rejected"],
                "missing_sessions": missing_sessions(v["accepted"])}
        body["raw_sha256"] = hashlib.sha256(json.dumps({k: body[k] for k in ("symbol", "accepted")}, sort_keys=True).encode("utf-8")).hexdigest()
        if any(b["raw_sha256"] == body["raw_sha256"] for b in self.batches):
            return dict(body, stored=False, note="IDENTICAL_BATCH_ALREADY_STORED")
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(body, sort_keys=True, default=str) + chr(10))
        self.batches.append(body)
        return dict(body, stored=True)

    def bars_by_symbol(self) -> dict:
        """Same shape as smart_move_outcome_audit.load_bars: {symbol: [{retrieved_at, raw_sha256, rows}]}."""
        out: dict = {}
        for b in self.batches:
            out.setdefault(b["symbol"], []).append({"retrieved_at": b["retrieved_at"], "raw_sha256": b["raw_sha256"], "rows": b["accepted"]})
        return out

    def coverage(self, symbol: str) -> dict:
        rows = {r["date"] for b in self.batches if b["symbol"] == symbol.upper() for r in b["accepted"]}
        return {"symbol": symbol.upper(), "bars": len(rows), "first": min(rows) if rows else None, "last": max(rows) if rows else None}
