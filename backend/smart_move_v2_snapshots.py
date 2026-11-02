"""Point-in-time analyst-consensus snapshot history for V2 (the revision half of the analyst factor).

Why it exists: the Unusual Whales earnings-estimates endpoint returns only the CURRENT consensus; its own revision fields (`eps_estimate_revision_up_last_week` / `..._down_...`) were null for all
2,913 quarter rows saved on 1 Nov. A revision can therefore only be measured by comparing two snapshots of ours, taken days apart. This module keeps those snapshots honestly:
  * append-only ledger (`v2_estimate_snapshots.jsonl`) rebuilt idempotently from the evidence records (each with its retrieval time and body hash); nothing is edited or back-filled;
  * the period is chosen EXACTLY: the `fiscal quarter` row whose `date` equals the earnings event's `ending_fiscal_quarter` (from the saved earnings payload). The earlier "nearest period on/after the day it
    was saved" mapping picked a different period for 92 of 92 stocks (the next quarter or the fiscal year), so it is replaced; if the exact row is missing no snapshot is made (no guessing);
  * `revision()` needs a prior snapshot 7-60 days older for the SAME period; otherwise it returns None (a trend is never manufactured from one observation);
  * `readiness()` says, per stock, when the second snapshot becomes useful.
It sends no request and is not a scheduler: a future collection (separately authorized) adds the snapshots.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Iterable, Optional

LEDGER = "v2_estimate_snapshots"
MIN_GAP_DAYS, MAX_AGE_DAYS = 7, 60          # same bounds as the scorer's CONFIG["analyst"]


def ending_quarter(earnings_rows, event_date: str) -> Optional[str]:
    """The fiscal quarter the upcoming report covers, from the UW earnings payload row of the event date."""
    for r in earnings_rows or []:
        if str(r.get("report_date"))[:10] == event_date and r.get("ending_fiscal_quarter"):
            return str(r["ending_fiscal_quarter"])[:10]
    return None


def quarter_row(estimates, quarter: Optional[str]) -> Optional[dict]:
    if not quarter:
        return None
    for x in estimates or []:
        if x.get("horizon") == "fiscal quarter" and str(x.get("date"))[:10] == quarter:
            return x
    return None


def _num(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def snapshot_row(symbol: str, quarter: str, est_row: dict, retrieved_at: str, sha: str) -> dict:
    return {"symbol": symbol, "period": quarter, "horizon": "fiscal quarter", "retrieved_at": retrieved_at, "raw_sha256": sha, "eps": _num(est_row.get("eps_estimate_average")), "revenue": _num(est_row.get("revenue_estimate_average")),
            "eps_analysts": _num(est_row.get("eps_estimate_analyst_count")), "revenue_analysts": _num(est_row.get("revenue_estimate_analyst_count")),
            "provider_eps_revision_up_last_week": est_row.get("eps_estimate_revision_up_last_week"), "provider_eps_revision_down_last_week": est_row.get("eps_estimate_revision_down_last_week")}


def rebuild(store, quarters: dict) -> dict:
    """Append a ledger row for every saved estimates record that has an exact-quarter row and is not in the ledger yet. quarters: {symbol: ending_fiscal_quarter}. Idempotent."""
    have = {(r["symbol"], r["period"], r["raw_sha256"]) for r in store.read(LEDGER)}
    added = skipped_no_quarter = skipped_no_row = 0
    for rec in store.read("v2_evidence"):
        if rec["kind"] != "uw_estimates" or rec["status"] != "OK":
            continue
        q = quarters.get(rec["symbol"])
        if not q:
            skipped_no_quarter += 1
            continue
        body = json.loads(store.get(rec["raw_sha256"])).get("data") or {}
        row = quarter_row(body.get("estimates") if isinstance(body, dict) else body, q)
        if row is None:
            skipped_no_row += 1
            continue
        key = (rec["symbol"], q, rec["raw_sha256"])
        if key in have:
            continue
        store.append(LEDGER, snapshot_row(rec["symbol"], q, row, rec["retrieved_at"], rec["raw_sha256"]))
        have.add(key)
        added += 1
    return {"added": added, "skipped_no_quarter": skipped_no_quarter, "skipped_no_exact_row": skipped_no_row}


def history(store, symbol: str, period: str, as_of: datetime) -> list:
    rows = [r for r in store.read(LEDGER) if r["symbol"] == symbol and r["period"] == period and datetime.fromisoformat(r["retrieved_at"]) <= as_of]     # nothing saved after as_of is visible
    return sorted(rows, key=lambda r: r["retrieved_at"])


def revision(store, symbol: str, period: str, as_of: datetime) -> Optional[dict]:
    h = history(store, symbol, period, as_of)
    if len(h) < 2:
        return None
    latest = h[-1]
    prior = next((s for s in h if MIN_GAP_DAYS <= (datetime.fromisoformat(latest["retrieved_at"]) - datetime.fromisoformat(s["retrieved_at"])).days <= MAX_AGE_DAYS), None)
    if prior is None:
        return None

    def pct(k):
        a, b = latest.get(k), prior.get(k)
        return round((a - b) / abs(b) * 100, 3) if a is not None and b else None
    return {"symbol": symbol, "period": period, "from": prior["retrieved_at"], "to": latest["retrieved_at"], "eps_revision_pct": pct("eps"), "revenue_revision_pct": pct("revenue"),
            "analysts_then": prior.get("eps_analysts"), "analysts_now": latest.get("eps_analysts")}


def readiness(store, symbols: Iterable[str], quarters: dict, now: datetime) -> dict:
    out, ready, waiting, none = {}, 0, 0, 0
    for s in symbols:
        q = quarters.get(s)
        h = history(store, s, q, now) if q else []
        if not h:
            out[s] = {"snapshots": 0, "state": "NO_SNAPSHOT", "next_useful": "collect a first snapshot"}
            none += 1
            continue
        first, last = datetime.fromisoformat(h[0]["retrieved_at"]), datetime.fromisoformat(h[-1]["retrieved_at"])
        if revision(store, s, q, now):
            out[s] = {"snapshots": len(h), "state": "REVISION_MEASURABLE"}
            ready += 1
        else:
            out[s] = {"snapshots": len(h), "state": "WAITING_FOR_SECOND_SNAPSHOT", "second_snapshot_useful_from": (last + timedelta(days=MIN_GAP_DAYS)).isoformat() if len(h) == 1 else (first + timedelta(days=MIN_GAP_DAYS)).isoformat()}
            waiting += 1
    return {"as_of": now.isoformat(), "measurable": ready, "waiting": waiting, "no_snapshot": none, "by_symbol": out,
            "note": "plan only; no recurring collection is active. A second snapshot at least 7 days after the first makes the revision measurable."}
