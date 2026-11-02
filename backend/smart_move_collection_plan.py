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


# ---------------------------------------------------------------- analyst two-snapshot design (A about 10 days before the release, B inside the prediction window)
SNAP_A_FROM_DAYS, SNAP_A_TO_DAYS = 12, 9      # Snapshot A is due between release-12 d and release-9 d, so that B (at or after release-48 h) is always at least 7 days later
SNAP_A_LOOKBACK_DAYS = 58                     # an earlier estimate record of the same stock still counts as A while it is at most 58 days older than release-9 d (B must stay within 60 days)


def snapshot_stage(release, window_start, cutoff, now) -> Optional[str]:
    """A, B or None. B: from the moment the window opens until 1 h before the cutoff. A: the tracking window described above. Never after the cutoff."""
    rel, ws, co, now = _t(release), _t(window_start), _t(cutoff), _t(now)
    if ws <= now <= co - timedelta(hours=FINAL_PASS_END_H):
        return "B"
    if rel - timedelta(days=SNAP_A_FROM_DAYS) <= now <= rel - timedelta(days=SNAP_A_TO_DAYS):
        return "A"
    return None


def snapshot_stage_tasks_due(events: Iterable[dict], estimate_records: Iterable[dict], now) -> list:
    """events: [{symbol, event_date, release_utc, window_start_utc, cutoff_utc}]. Dedup is by (symbol, event date, stage); an OK estimates record that already satisfies the stage is not requested again.
    A rescheduled event (new event date) gets its own stage keys, but an earlier OK record that is still within the A lookback is reused, so nothing is requested twice for the same history."""
    now = _t(now)
    ok = [(r["symbol"], _t(r["retrieved_at"])) for r in estimate_records if r.get("status") == "OK"]
    out = []
    for e in events:
        stage = snapshot_stage(e["release_utc"], e["window_start_utc"], e["cutoff_utc"], now)
        if stage is None:
            continue
        rel, ws = _t(e["release_utc"]), _t(e["window_start_utc"])
        if stage == "B":
            satisfied = any(s == e["symbol"] and ws <= t <= now for s, t in ok)
        else:
            lo, hi = rel - timedelta(days=SNAP_A_TO_DAYS + SNAP_A_LOOKBACK_DAYS), rel - timedelta(days=SNAP_A_TO_DAYS)
            satisfied = any(s == e["symbol"] and lo <= t <= hi for s, t in ok)
        if satisfied:
            continue
        out.append({"kind": "uw_estimates", "symbol": e["symbol"], "stage": stage, "event_date": str(e["event_date"])[:10], "cutoff_utc": _t(e["cutoff_utc"]).isoformat(),
                    "id": "uw_estimates-%s-%s-%s" % (e["symbol"], str(e["event_date"])[:10], stage)})
    return sorted(out, key=lambda t: (t["cutoff_utc"], t["symbol"], t["stage"]))


def revision_state(snapshots: list, cutoff) -> dict:
    """snapshots: [{retrieved_at, period_rows: {fiscal_quarter_end: {eps, revenue}}}] of ONE stock, each holding every quarter row of its saved response. A revision needs a quarter that appears in both snapshots,
    retrieved 7 to 60 days apart, neither after the cutoff. Otherwise ANALYST is INSUFFICIENT_HISTORY with the reason. Provider values are used exactly as saved."""
    co = _t(cutoff)
    snaps = sorted(((_t(s["retrieved_at"]), s["period_rows"]) for s in snapshots if _t(s["retrieved_at"]) <= co), key=lambda x: x[0])
    if len(snaps) < 2:
        return {"state": "INSUFFICIENT_HISTORY", "reason": "FEWER_THAN_TWO_SNAPSHOTS_BEFORE_CUTOFF", "snapshots": len(snaps)}
    latest_t, latest_rows = snaps[-1]
    for t, rows in snaps[:-1]:
        gap = (latest_t - t).days
        if not (MIN_GAP_DAYS <= gap <= MAX_AGE_DAYS):
            continue
        for q in sorted(set(rows) & set(latest_rows), reverse=True):
            a, b = latest_rows[q], rows[q]
            return {"state": "REVISION_MEASURABLE", "quarter": q, "from": t.isoformat(), "to": latest_t.isoformat(), "gap_days": gap,
                    "eps_revision_pct": round((a["eps"] - b["eps"]) / abs(b["eps"]) * 100, 3) if a.get("eps") is not None and b.get("eps") else None,
                    "revenue_revision_pct": round((a["revenue"] - b["revenue"]) / abs(b["revenue"]) * 100, 3) if a.get("revenue") is not None and b.get("revenue") else None}
        return {"state": "INSUFFICIENT_HISTORY", "reason": "NO_COMMON_FISCAL_QUARTER_IN_THE_TWO_SNAPSHOTS", "snapshots": len(snaps)}
    return {"state": "INSUFFICIENT_HISTORY", "reason": "SNAPSHOTS_NOT_7_TO_60_DAYS_APART", "snapshots": len(snaps), "gap_days": (latest_t - snaps[0][0]).days}
