"""Capacity model for the rolling-horizon monitor (P1, development only): how many events can receive F3 + F5 evidence INSIDE their valid prediction windows under the approved budget (50 per run, 100 per New York day)?

Pure arithmetic on the frozen window rules (`smvshadow.cutoff.plan_event`); it sends nothing, reads no store and changes nothing. It answers "what is possible", given:
  * the events (symbol, event date, session, proxy tier),
  * the instants at which a supervised run is actually made,
  * the calendar share of each run (every run refreshes the stale open / about-to-open slots plus the two confirmations), the shared evidence (SPY + sector ETFs, once per completed session) and 2 requests per stock (F5 bars, F3 history),
  * the per-run and per-New-York-day limits.

An event is SERVED when both of its requests fall inside its own window [window_start, cutoff] at a run instant, before the day's budget is gone. Events that are not tradable under the frozen order-deadline rule are never funded (the scorer answers
NO VALID PREDICTION: NOT_TRADABLE for them whatever the evidence). The model assumes every F3/F5 request succeeds and every calendar slot is stale at each run (conservative)."""
from __future__ import annotations

import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "docs" / "smart_move" / "shadow_impl"))
import smart_move_calendar_tiers as CT  # noqa: E402
import smart_move_engine20 as E20  # noqa: E402
from smvshadow import cutoff as co  # noqa: E402

UTC = timezone.utc
PER_RUN, PER_DAY = 50, 100                 # the approved limits
ETFS_PER_SESSION = 11                      # worst case: every sector ETF is needed (the sector ETF is optional: 3 of 40 points)


def _dt(x) -> datetime:
    t = x if isinstance(x, datetime) else datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def schedule(start, end, hours_utc, days_utc: Optional[set] = None) -> list:
    """Run instants: every day from start to end at the given UTC hours (e.g. [13, 22])."""
    s, e = _dt(start), _dt(end)
    out, d = [], s.date()
    while d <= e.date():
        for h in hours_utc:
            t = datetime(d.year, d.month, d.day, h, tzinfo=UTC)
            if s <= t <= e:
                out.append(t)
        d += timedelta(days=1)
    return out


def events_from_counts(counts: dict, universe: str = "broad") -> list:
    """Synthetic events from a per-day calendar count table {date: {bmo, amc, us, price, options, optcap}}. `universe` = broad (all US-listed) | price (price floor) | options | optcap (options and cap >= 2B). Within each
    (date, session) the events are ordered by proxy tier: optcap = A, other options = B, other price-floor = C, the rest = D (D exists only in the broad universe)."""
    key = {"broad": "us", "price": "price", "options": "options", "optcap": "optcap"}[universe]
    out = []
    for d, c in sorted(counts.items()):
        us = max(1, c["us"])
        for sess, n in (("BMO", c["bmo"]), ("AMC", c["amc"])):
            share = n / max(1, c["bmo"] + c["amc"])
            tiers = [("A", c["optcap"]), ("B", c["options"] - c["optcap"]), ("C", c["price"] - c["options"]), ("D", c["us"] - c["price"])]
            quota = {t: round(v * share) for t, v in tiers}
            if universe != "broad":
                keep = {"price": ("A", "B", "C"), "options": ("A", "B"), "optcap": ("A",)}[universe]
                quota = {t: (v if t in keep else 0) for t, v in quota.items()}
            i = 0
            for t in ("A", "B", "C", "D"):
                for _ in range(max(0, quota[t])):
                    i += 1
                    out.append({"symbol": "%s%s%d" % (d[5:].replace("-", ""), sess[0], i), "event_date": date.fromisoformat(d), "session": sess, "tier": t, "cap": {"A": 5e9, "B": 5e9, "C": 1e9, "D": 1e8}[t] - i})
    return out


def _order(open_events, t, policy):
    if policy == "deadline":
        return sorted(open_events, key=lambda e: (e["_cutoff"], -e["cap"], e["symbol"]))
    urgent = lambda e: (e["_cutoff"] - t).total_seconds() <= E20.PRIORITY_URGENT_H * 3600  # noqa: E731
    return sorted(open_events, key=lambda e: (0, e["_cutoff"], e["tier"], e["symbol"]) if urgent(e) else (1, e["tier"], e["_cutoff"], -e["cap"], e["symbol"]))


def simulate(events: list, run_times: list, *, per_run: int = PER_RUN, per_day: int = PER_DAY, policy: str = "deadline_priority", etfs_per_session: int = ETFS_PER_SESSION) -> dict:
    evs = []
    for e in events:
        p = co.plan_event(e["event_date"], e["session"])
        evs.append(dict(e, _start=p["window_start_utc"], _cutoff=p["cutoff_utc"], _tradable=bool(p["tradable"]), _session=p["last_usable_session"], served_at=None))
    used_day, shared_done, runs = Counter(), set(), []
    for t in sorted(_dt(x) for x in run_times):
        et_day = t.astimezone(co.ET).date().isoformat()
        left = min(per_run, per_day - used_day[et_day])
        run = {"at": t.isoformat(), "et_day": et_day, "calendar": 0, "shared": 0, "evidence": 0, "served": 0}
        open_ev = [e for e in evs if e["served_at"] is None and e["_tradable"] and e["_start"] <= t <= e["_cutoff"]]
        if left <= 0 or not open_ev:
            runs.append(dict(run, skipped="no budget left today" if left <= 0 else "no open tradable event"))
            continue
        slots = [x for x in CT.horizon_slots(t, CT.HORIZON_MAX) if CT.classify(x, t)["tier"] in ("T0_OPEN", "T0_NEXT")]          # every open or about-to-open slot is assumed stale at each run (conservative), whether or not the data set has events in it
        cal = min(left, len(slots) + CT.CONFIRM_RESERVE)
        run["calendar"], left = cal, left - cal
        for s in sorted({e["_session"] for e in open_ev}):                      # shared SPY + ETFs, once per completed session, when at least one event needs them
            if s not in shared_done and left >= 1:
                cost = min(left, 1 + etfs_per_session)
                shared_done.add(s)
                run["shared"] += cost
                left -= cost
        for e in _order(open_ev, t, policy):
            if left < 2:
                break
            e["served_at"] = t.isoformat()
            run["evidence"] += 2
            run["served"] += 1
            left -= 2
        used_day[et_day] += run["calendar"] + run["shared"] + run["evidence"]
        runs.append(run)
    served = [e for e in evs if e["served_at"]]
    by = {}
    for e in evs:
        by.setdefault("%s %s" % (e["event_date"], e["session"]), Counter())["served" if e["served_at"] else "not_tradable" if not e["_tradable"] else "missed"] += 1
    return {"policy": policy, "per_run": per_run, "per_day": per_day, "runs": runs, "events": len(evs), "served": len(served), "not_tradable": sum(1 for e in evs if not e["_tradable"]),
            "missed": sum(1 for e in evs if not e["served_at"] and e["_tradable"]), "requests_by_et_day": dict(used_day), "served_by_tier": dict(Counter(e["tier"] for e in served)),
            "total_by_tier": dict(Counter(e["tier"] for e in evs if e["_tradable"])), "by_date_session": {k: dict(v) for k, v in sorted(by.items())}, "events_detail": evs}
