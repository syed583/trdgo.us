"""Offline Options Flow analysis (Phase 4). A PURE module over tape rows in the shape `uw_flow_service._trade` produces: it neither imports nor changes the live Options Flow service and makes no request.

What it adds on top of the live shape (all deterministic, all documented):
  classify()        bullish / bearish / neutral from the side a contract crossed on, with a MISSING or unknown side reported as UNKNOWN_SIDE (the live `_sentiment` rounds it into bullish/bearish: see AUDIT_FINDINGS).
  summarize()       calls vs puts (count, volume, premium), sweeps, blocks, volume/open-interest and unusual rows, expiry buckets, bullish/bearish premium shares, freshness and missing-data indicators.
  ui_state()        the empty / error / delayed / last-session / live state a screen should show for an envelope, never a blank panel.
  audit_contract()  field and value-domain mismatches between the backend row shape and the frontend `FlowTrade` type.
The unusual-activity thresholds below are ANALYSIS DEFAULTS for this offline summary; they are not the production thresholds and nothing here feeds a score."""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

UTC = timezone.utc
BLOCK_PREMIUM = 250_000.0              # same figure as uw_flow_service.BLOCK_PREMIUM (copied, not imported)
UNUSUAL_VOL_OI = 1.0                   # volume above open interest
UNUSUAL_MIN_PREMIUM = 25_000.0
LIVE_WINDOW_MIN = 2.0
STALE_AFTER_MIN = 30.0
REQUIRED_FIELDS = ("right", "strike", "expiry", "volume", "premium", "side", "epoch")

AUDIT_FINDINGS = [
    {"id": "SENTIMENT_MISSING_SIDE", "where": "backend/uw_flow_service.py::_sentiment", "severity": "DEFECT",
     "what": "A row whose side is missing or unknown is classified from `(right == 'C') == (side == 'ask')`: a call with no side becomes BEARISH and a put with no side becomes BULLISH.",
     "evidence": "_sentiment({'side': None, 'right': 'C'}) -> BEARISH; _sentiment({'side': None, 'right': 'P'}) -> BULLISH (checked offline).",
     "status": "NOT FIXED (production service must not be modified): classify() here returns UNKNOWN_SIDE. Needs owner approval before the live function is changed."},
    {"id": "FRONTEND_TYPE_DRIFT", "where": "frontend/src/api/client.ts::FlowTrade", "severity": "INCONSISTENCY",
     "what": "FlowTrade lists type 'Call'|'Put', kind 'SWEEP'|'BLOCK'|'SPLIT'|'SINGLE' and sentiment 'Bullish'|'Bearish'; the backend row sends CALL|PUT, SWEEP|BLOCK|PRINT and BULLISH|BEARISH|NEUTRAL.",
     "status": "REPORTED by audit_contract(); the page shapes some rows itself (type fallback), so it is not a proven runtime failure."},
]


def _num(x) -> Optional[float]:
    try:
        return None if x in (None, "") else float(x)
    except (TypeError, ValueError):
        return None


def classify(row: dict) -> str:
    """BULLISH: call at the ask or put at the bid. BEARISH: call at the bid or put at the ask. NEUTRAL: traded at the mid. UNKNOWN_SIDE: side or right missing/unrecognised (never guessed)."""
    side, right = str(row.get("side") or "").lower(), row.get("right")
    if right not in ("C", "P") or side not in ("ask", "bid", "mid"):
        return "UNKNOWN_SIDE"
    if side == "mid":
        return "NEUTRAL"
    return "BULLISH" if (right == "C") == (side == "ask") else "BEARISH"


def dte(expiry, today: date) -> Optional[int]:
    try:
        return (date.fromisoformat(str(expiry)[:10]) - today).days
    except (TypeError, ValueError):
        return None


def bucket(d: Optional[int]) -> str:
    return "UNKNOWN" if d is None else "EXPIRED" if d < 0 else "0DTE" if d == 0 else "1-7D" if d <= 7 else "8-30D" if d <= 30 else "31-90D" if d <= 90 else ">90D"


def is_unusual(row: dict) -> bool:
    vol, oi, prem = _num(row.get("volume")), _num(row.get("open_interest")), _num(row.get("premium"))
    return bool(vol and oi and vol / oi > UNUSUAL_VOL_OI and (prem or 0) >= UNUSUAL_MIN_PREMIUM)


def summarize(rows: Iterable[dict], now: datetime) -> dict:
    rows = [r for r in rows if isinstance(r, dict)]
    today = now.astimezone(UTC).date()
    n = len(rows)
    cls = Counter(classify(r) for r in rows)
    leg = {"C": [r for r in rows if r.get("right") == "C"], "P": [r for r in rows if r.get("right") == "P"]}
    prem = lambda rs: round(sum(_num(r.get("premium")) or 0 for r in rs), 2)          # noqa: E731
    vol = lambda rs: int(sum(_num(r.get("volume")) or 0 for r in rs))                  # noqa: E731
    bull, bear = prem([r for r in rows if classify(r) == "BULLISH"]), prem([r for r in rows if classify(r) == "BEARISH"])
    classified = bull + bear
    missing = {f: sum(1 for r in rows if r.get(f) in (None, "")) for f in REQUIRED_FIELDS}
    epochs = [e for e in (_num(r.get("epoch")) for r in rows) if e]
    newest = max(epochs) if epochs else None
    age = max(0.0, (now.timestamp() * 1000 - newest) / 60000) if newest else None
    return {
        "rows": n, "classification": {k: cls.get(k, 0) for k in ("BULLISH", "BEARISH", "NEUTRAL", "UNKNOWN_SIDE")},
        "calls": {"count": len(leg["C"]), "volume": vol(leg["C"]), "premium": prem(leg["C"])}, "puts": {"count": len(leg["P"]), "volume": vol(leg["P"]), "premium": prem(leg["P"])},
        "put_call_premium_ratio": round(prem(leg["P"]) / prem(leg["C"]), 3) if prem(leg["C"]) else None,
        "sweeps": sum(1 for r in rows if r.get("sweep") or r.get("kind") == "SWEEP"), "blocks": sum(1 for r in rows if r.get("kind") == "BLOCK" or (_num(r.get("premium")) or 0) >= BLOCK_PREMIUM),
        "unusual": sum(1 for r in rows if is_unusual(r)), "vol_oi_available": sum(1 for r in rows if _num(r.get("volume")) and _num(r.get("open_interest"))),
        "bullish_premium": bull, "bearish_premium": bear, "bullish_premium_share": round(bull / classified, 3) if classified else None,
        "flow_lean": None if not classified else "BULLISH" if bull / classified > 0.55 else "BEARISH" if bull / classified < 0.45 else "BALANCED",
        "expiry_buckets": dict(Counter(bucket(dte(r.get("expiry"), today)) for r in rows)),
        "freshness": {"newest_epoch": int(newest) if newest else None, "age_minutes": round(age, 1) if age is not None else None,
                      "state": "NO_TIMESTAMPS" if age is None else "LIVE" if age <= LIVE_WINDOW_MIN else "RECENT" if age <= STALE_AFTER_MIN else "STALE"},
        "missing_data": {"by_field": missing, "rows_missing_any_required": sum(1 for r in rows if any(r.get(f) in (None, "") for f in REQUIRED_FIELDS)),
                         "unclassifiable_share": round(cls.get("UNKNOWN_SIDE", 0) / n, 3) if n else None},
        "note": "Offline analysis of the rows given. Thresholds are analysis defaults, not production values. Synthetic rows must be labelled synthetic by the caller."}


def ui_state(envelope: Optional[dict], now: datetime) -> dict:
    """What a panel should show for an Options Flow envelope: state, headline, detail. Handles None (request failed), provider statuses, empty tapes, delayed and last-session tapes."""
    if envelope is None:
        return {"state": "ERROR", "headline": "Options flow unavailable", "detail": "The request failed or returned nothing. Retry; no data is shown rather than stale guesses."}
    status = str(envelope.get("status") or "").upper()
    trades = envelope.get("trades") or []
    if status == "INVALID_SYMBOL":
        return {"state": "INVALID_SYMBOL", "headline": "Unknown symbol", "detail": envelope.get("detail") or "Check the ticker."}
    if status in ("ERROR", "UNAUTHORIZED", "RATE_LIMITED") or (status not in ("", "OK", "NO_TRADES", "NO_DATA") and not trades):
        return {"state": "ERROR", "headline": "Options flow provider problem (%s)" % (status or "unknown"), "detail": envelope.get("detail") or envelope.get("note") or ""}
    if not trades:
        return {"state": "EMPTY", "headline": "No option prints for this selection", "detail": envelope.get("note") or "The provider returned no trades for this symbol and session."}
    delay = _num(envelope.get("delay_minutes"))
    note = str(envelope.get("note") or "")
    if delay and delay > LIVE_WINDOW_MIN:
        return {"state": "DELAYED", "headline": "Delayed %d min" % round(delay), "detail": note}
    if "last session" in note.lower() or "market is closed" in note.lower():
        return {"state": "LAST_SESSION", "headline": "Last session's tape", "detail": note}
    return {"state": "LIVE", "headline": "Live", "detail": note}


# ------------------------------------------------------------------ backend / frontend contract audit
def backend_row_keys(source: str) -> list:
    """Keys of the dict returned by `_trade` in uw_flow_service.py (read as text)."""
    m = re.search(r"def _trade\(.*?\n    return \{(.*?)\n    \}\n", source, re.S)
    return sorted(set(re.findall(r'^\s+"([a-z_]+)":', m.group(1), re.M))) if m else []


def frontend_type_fields(source: str, name: str = "FlowTrade") -> dict:
    """{field: type text} of an exported TypeScript interface."""
    m = re.search(r"export interface %s \{(.*?)\n\}" % re.escape(name), source, re.S)
    out = {}
    if m:
        for fld, typ in re.findall(r"^\s+([a-z_]+)\??:\s*([^;]+);", m.group(1), re.M):
            out[fld] = typ.strip()
    return out


def audit_contract(backend_source: str, frontend_source: str) -> dict:
    be, fe = backend_row_keys(backend_source), frontend_type_fields(frontend_source)
    domains = {}
    for f in ("type", "kind", "sentiment"):
        domains[f] = sorted(re.findall(r"'([^']+)'", fe.get(f, "")))
    expected = {"type": {"CALL", "PUT"}, "kind": {"SWEEP", "BLOCK", "PRINT"}, "sentiment": {"BULLISH", "BEARISH", "NEUTRAL"}}
    drift = {f: {"frontend_declares": domains[f], "backend_sends": sorted(expected[f]), "case_or_member_mismatch": sorted(set(domains[f]) ^ expected[f])} for f in domains
             if {d.upper() for d in domains[f]} != expected[f] or set(domains[f]) != expected[f]}
    return {"backend_keys": be, "frontend_fields": sorted(fe), "frontend_only_fields": sorted(set(fe) - set(be)), "backend_only_fields": sorted(set(be) - set(fe)), "value_domain_drift": drift,
            "consistent": not drift and not (set(fe) - set(be))}


def audit_file_pair(root) -> dict:
    root = Path(root)
    return audit_contract((root / "backend" / "uw_flow_service.py").read_text(encoding="utf-8"), (root / "frontend" / "src" / "api" / "client.ts").read_text(encoding="utf-8"))
