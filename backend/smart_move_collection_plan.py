"""PLANNING ONLY (no provider request, no scheduler): when and how much to collect for (1) news and (2) analyst-estimate snapshots, for events with a known prediction window.

Nothing here starts a job. The functions return tasks / budgets that a separately authorized collector would run through the existing governed path (RefreshService._call: cap, 2 s spacing,
hourly share, 401/403/429 stop, kill switch). They never read a provider and never change a score, threshold or stored prediction.

News: two passes per event inside its 48-to-24-hour window - INITIAL (as soon as the window opens) and FINAL (shortly before the cutoff) - page 0 first, further pages only while a full page still
reaches into the 7-day lookback, at most NEWS_MAX_PAGES pages per ticker per pass. Nothing is ever requested after the cutoff, and nothing retrieved after the cutoff is accepted as pre-cutoff evidence.
Analyst snapshots: one request per tracked stock per UTC day for the estimates endpoint; a revision needs two snapshots of the SAME fiscal quarter 7 to 60 days apart. History is never reconstructed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

UTC = timezone.utc
NEWS_MAX_PAGES = 3
NEWS_LOOKBACK_DAYS = 7
FINAL_PASS_START_H = 6           # final pass: cutoff - 6 h ...
FINAL_PASS_END_H = 1             # ... until cutoff - 1 h (never closer: the collector needs time to finish before the cutoff)
MIN_GAP_DAYS, MAX_AGE_DAYS = 7, 60


def _t(x) -> datetime:
    t = x if isinstance(x, datetime) else datetime.fromisoformat(str(x))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


# ---------------------------------------------------------------- news
def news_pass(window_start, cutoff, now) -> Optional[str]:
    """INITIAL from the moment the window opens until the final pass starts, FINAL until 1 h before the cutoff, None otherwise (not yet open, or too close to / past the cutoff)."""
    ws, co, now = _t(window_start), _t(cutoff), _t(now)
    if now < ws or now > co - timedelta(hours=FINAL_PASS_END_H):
        return None
    return "FINAL" if now >= co - timedelta(hours=FINAL_PASS_START_H) else "INITIAL"


def news_tasks_due(events: Iterable[dict], news_records: Iterable[dict], now) -> list:
    """events: [{symbol, window_start_utc, cutoff_utc}] (the existing eligibility rules decide who is in this list). news_records: saved uw_news evidence rows (status, symbol, retrieved_at, params.page).
    A pass is done for a stock when an OK page-0 record exists that was retrieved inside that pass's interval; failed records do not count, so a failed stock is retried at the next due time."""
    now = _t(now)
    done = {}
    for r in news_records:
        if r.get("status") == "OK" and (r.get("params") or {}).get("page", 0) == 0:
            done.setdefault(r["symbol"], []).append(_t(r["retrieved_at"]))
    out = []
    for e in events:
        p = news_pass(e["window_start_utc"], e["cutoff_utc"], now)
        if p is None:
            continue
        ws, co = _t(e["window_start_utc"]), _t(e["cutoff_utc"])
        lo = ws if p == "INITIAL" else co - timedelta(hours=FINAL_PASS_START_H)
        if any(lo <= t <= now for t in done.get(e["symbol"], [])):
            continue
        out.append({"kind": "uw_news", "symbol": e["symbol"], "pass": p, "id": "uw_news-%s-%s-%s" % (e["symbol"], co.strftime("%Y%m%d%H"), p.lower()), "cutoff_utc": co.isoformat()})
    return sorted(out, key=lambda t: (t["cutoff_utc"], t["symbol"]))


def news_budget(n_events: int, extra_pages_per_pass: float = 0.15, passes: int = 2) -> dict:
    """Requests for n eligible events: floor (page 0 only), expected (a share of full pages needs one more page) and ceiling (every pass at the page cap)."""
    return {"events": n_events, "passes": passes, "floor": n_events * passes, "expected": round(n_events * passes * (1 + extra_pages_per_pass)), "ceiling": n_events * passes * NEWS_MAX_PAGES,
            "minutes_at_2s_spacing": {"floor": round(n_events * passes * 2 / 60, 1), "ceiling": round(n_events * passes * NEWS_MAX_PAGES * 2 / 60, 1)}}


def accept_retrieval(retrieved_at, cutoff) -> dict:
    """A record retrieved after the cutoff is never pre-cutoff evidence (the stored body is kept, but flagged)."""
    ok = _t(retrieved_at) <= _t(cutoff)
    return {"accepted": ok, "reason": None if ok else "RETRIEVED_AFTER_CUTOFF"}


def pre_cutoff_headlines(rows: Iterable[dict], cutoff, lookback_days: int = NEWS_LOOKBACK_DAYS) -> dict:
    """Headlines published before the cutoff and inside the lookback, de-duplicated on (publication time, source, headline). Others are counted by reason, never silently dropped."""
    co = _t(cutoff)
    seen, keep, rej = set(), [], {"PUBLISHED_AFTER_CUTOFF": 0, "OUTSIDE_LOOKBACK": 0, "NO_TIMESTAMP": 0, "DUPLICATE": 0}
    for h in rows:
        try:
            t = _t(str(h.get("created_at")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            rej["NO_TIMESTAMP"] += 1
            continue
        k = (h.get("created_at"), h.get("source"), str(h.get("headline", "")).strip().lower())
        if k in seen:
            rej["DUPLICATE"] += 1
            continue
        seen.add(k)
        if t >= co:
            rej["PUBLISHED_AFTER_CUTOFF"] += 1
        elif co - t > timedelta(days=lookback_days):
            rej["OUTSIDE_LOOKBACK"] += 1
        else:
            keep.append(h)
    return {"kept": keep, "rejected": rej}


# ---------------------------------------------------------------- analyst-estimate snapshots
def snapshots_due(tracked: Iterable[dict], ledger_rows: Iterable[dict], now) -> list:
    """tracked: [{symbol, period}] (the fiscal quarter the upcoming report covers, mapped exactly). One request per stock and UTC day: skipped when the ledger already holds a snapshot of that
    (symbol, period) retrieved today. Never invents a period: a stock without a mapped quarter is not scheduled."""
    now = _t(now)
    today = now.astimezone(UTC).date()
    have = {(r["symbol"], r["period"]) for r in ledger_rows if _t(r["retrieved_at"]).astimezone(UTC).date() == today}
    return [{"kind": "uw_estimates", "symbol": s["symbol"], "period": s["period"], "id": "uw_estimates-%s-%s" % (s["symbol"], today.strftime("%Y%m%d"))}
            for s in tracked if s.get("period") and (s["symbol"], s["period"]) not in have]


def revision_eligibility(ledger_rows: Iterable[dict], symbol: str, period: str, capture_ts) -> dict:
    """Whether a 7-to-60-day comparison exists at capture_ts, and when it first can. Uses only snapshots retrieved at or before capture_ts."""
    cap = _t(capture_ts)
    h = sorted((_t(r["retrieved_at"]) for r in ledger_rows if r["symbol"] == symbol and r["period"] == period and _t(r["retrieved_at"]) <= cap))
    if not h:
        return {"state": "NO_SNAPSHOT", "eligible_from": None}
    first, last = h[0], h[-1]
    elig_from = first + timedelta(days=MIN_GAP_DAYS)
    ok = any(MIN_GAP_DAYS <= (last - s).days <= MAX_AGE_DAYS for s in h)
    return {"state": "REVISION_MEASURABLE" if ok else "WAITING", "eligible_from": elig_from.isoformat(), "snapshots": len(h)}


def snapshot_budget(tracked_stocks: int, days: int = 1) -> dict:
    return {"tracked_stocks": tracked_stocks, "requests_per_day": tracked_stocks, "requests": tracked_stocks * days, "minutes_per_day_at_2s_spacing": round(tracked_stocks * 2 / 60, 1)}
