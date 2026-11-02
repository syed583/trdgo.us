"""Two additional collection job types for the governed collector (Phase 14): earnings payloads (exact fiscal-quarter evidence) and daily price bars (outcome validation).

They add NO new client, NO new request path and NO new limit:
  * they are ordinary tasks of the existing collector (`smart_move_v2_collect.run`), fetched through `svc._call` -> governor -> `InfraFetcher` (attestation check, background priority, no client retries);
  * they count against the same attempt ceiling, the same 2 s spacing and the same 3-slot partition, and stop on the same 401/403/429 / kill switch;
  * raw responses go to the append-only hashed payload store and a record is appended to `v2_evidence.jsonl` with the retrieval time; nothing is ever overwritten, and an earlier record is never relabelled;
  * a re-request is planned only when no OK record of that kind exists inside its minimum interval (safe de-duplication); a record is never backdated;
  * no Snapshot A is created here, and price bars that fail validation are quarantined (counted on the record, excluded from outcome use) rather than repaired.
This module only PLANS tasks; it sends nothing."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "shadow_impl"))
import smart_move_outcomes as O  # noqa: E402  (read only: due() gives the reaction session and its earliest fetch time)

UTC = timezone.utc
EARNINGS_MIN_DAYS = 7                  # an earnings payload lists the last 24 reports; re-requesting it within a week adds nothing
OHLC_LOOKBACK_DAYS = 10                # outcome bars are wanted for events whose reaction session closed within this many days


def _dt(x) -> datetime:
    d = x if isinstance(x, datetime) else datetime.fromisoformat(str(x))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _ok_recent(recs: Iterable[dict], kind: str, symbol: str, since: datetime) -> bool:
    return any(r["kind"] == kind and r["symbol"] == symbol and r.get("status") == "OK" and _dt(r["retrieved_at"]) >= since for r in recs)


def earnings_tasks_due(events: Iterable[dict], recs: list, now: datetime, cap: Optional[int] = None) -> list:
    """events: [{symbol, event_date, cutoff_utc}] (window events first, then tracking). One task per symbol, earliest cutoff first, skipped while an OK payload is younger than EARNINGS_MIN_DAYS."""
    now = _dt(now)
    out, seen = [], set()
    for e in sorted(events, key=lambda e: (_dt(e["cutoff_utc"]), e["symbol"])):
        if e["symbol"] in seen or _ok_recent(recs, "uw_earnings", e["symbol"], now - timedelta(days=EARNINGS_MIN_DAYS)):
            continue
        seen.add(e["symbol"])
        out.append({"kind": "uw_earnings", "symbol": e["symbol"], "stage": "E", "event_date": str(e["event_date"])[:10], "cutoff_utc": _dt(e["cutoff_utc"]).isoformat(),
                    "id": "uw_earnings-%s-%s" % (e["symbol"], now.strftime("%Y%m%d"))})
    return out[:cap] if cap is not None else out


def ohlc_tasks_due(outcome_events: Iterable[dict], recs: list, now: datetime, cap: Optional[int] = None) -> list:
    """outcome_events: [{symbol, event_date, session}] with session BMO or AMC from the EVENT RECORD (a vendor label alone is not enough). A task is due once the reaction session has closed plus the 30-minute
    safety delay (smart_move_outcomes.due) and is still inside OHLC_LOOKBACK_DAYS; it is satisfied only by an OK record retrieved AFTER that moment (earlier bars would be partial)."""
    now = _dt(now)
    out, seen = [], set()
    for e in sorted(outcome_events, key=lambda e: (str(e["event_date"]), e["symbol"])):
        if e.get("session") not in ("BMO", "AMC"):
            continue
        d = O.due(date.fromisoformat(str(e["event_date"])[:10]), e["session"], now)
        if not d["due"] or now - d["earliest_fetch_utc"] > timedelta(days=OHLC_LOOKBACK_DAYS):
            continue
        if _ok_recent(recs, "uw_ohlc", e["symbol"], d["earliest_fetch_utc"]) or e["symbol"] in seen:
            continue
        seen.add(e["symbol"])
        out.append({"kind": "uw_ohlc", "symbol": e["symbol"], "stage": "O", "event_date": str(e["event_date"])[:10], "cutoff_utc": d["earliest_fetch_utc"].isoformat(),
                    "id": "uw_ohlc-%s-%s" % (e["symbol"], str(e["event_date"])[:10])})
    return out[:cap] if cap is not None else out


def bar_quality(rows, retrieved_at) -> dict:
    """Validate the daily bars of one response (smart_move_price_bars rules). Returns counts and the dates quarantined; the raw response is untouched either way."""
    import smart_move_price_bars as PB
    v = PB.validate_rows([r for r in (rows or []) if isinstance(r, dict)], retrieved_at)
    return {"bars_accepted": len(v["accepted"]), "bars_quarantined": len(v["rejected"]), "quarantine_reasons": sorted({x["reason"] for x in v["rejected"]}),
            "last_bar_date": v["accepted"][-1]["date"] if v["accepted"] else None}
