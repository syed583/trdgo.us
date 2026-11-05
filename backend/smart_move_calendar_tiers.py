"""Tiered calendar refresh for a rolling horizon of up to 30 days (P1, development only; nothing here sends a request by itself).

WHY: a refresh of every trading day for 30 days is about 44 Unusual Whales requests (two per day), which would swallow a whole 50-request run. Only the calendar slots whose prediction window is open (or about to open)
need a fresh calendar to confirm an event inside the frozen D-4 rule; the far slots only need to be DISCOVERED and TRACKED. So each (date, side) slot is refreshed on its own clock:

  T0_OPEN  prediction window open now                  refresh when its own last refresh is older than 5 h  (the frozen rule needs a calendar younger than 6 h)
  T0_NEXT  window opens within the next 12 h           same 5 h rule (it must be confirmed before it opens)
  T1       later days up to the horizon                refresh when older than 24 h; only with budget left over after T0 and after the evidence the run needs; at most T1_RUN_MAX slots per run
  CLOSED   window already closed                       never refreshed (a closed window cannot produce a prediction)

The age is kept PER SLOT in <state>/calendar_slots.json (new file, append/replace only inside the state folder). The old rule "newest observation anywhere" would let a refresh of far days make the near days look fresh.
Nothing here changes the frozen scorer, thresholds, windows, ledger or any stored record. A slot that could not be refreshed in time is reported (never silently treated as fresh)."""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "docs" / "smart_move" / "shadow_impl"))
from smvshadow import cutoff as co  # noqa: E402

UTC = timezone.utc
HORIZON_MAX = 30
T0_MAX_AGE_H = 5.0            # same margin as smart_move_calendar_guard.MAX_AGE_H (the frozen limit is 6 h)
T0_LEAD_H = 12.0              # a window that opens within this many hours is refreshed like an open one
T1_MAX_AGE_H = 24.0
T1_RUN_MAX = 6                # far-day slots refreshed in one run at most
CALENDAR_SHARE = 0.30         # a run never plans more than this share of its ceiling for calendar requests
CONFIRM_RESERVE = 2           # Finviz + Finnhub confirmations that accompany a refresh (see RefreshService.refresh_calendar)
STATE_FILE = "calendar_slots.json"
SIDE_KIND = {"premarket": "BMO", "afterhours": "AMC"}


def _dt(x) -> datetime:
    t = x if isinstance(x, datetime) else datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def key(d, side: str) -> str:
    return "%s|%s" % (str(d)[:10], side)


def unkey(k: str):
    d, side = k.split("|")
    return date.fromisoformat(d), side


# ------------------------------------------------------------------ slots and tiers
def horizon_slots(now, days: int) -> list:
    """(date, side) for both sessions of every trading day from today (New York) through today + days (calendar days), `days` clamped to 1..HORIZON_MAX."""
    now = _dt(now)
    n = max(1, min(int(days), HORIZON_MAX))
    mc = co._mc()
    today = now.astimezone(co.ET).date()
    out = []
    for k in range(0, n + 1):
        d = today + timedelta(days=k)
        if mc.is_trading_day(d):
            out += [(d, "premarket"), (d, "afterhours")]
    return out


def classify(slot, now) -> dict:
    """Tier of one (date, side) slot at `now`, with its window times (the frozen cutoff rules: window = release-48 h .. release-24 h)."""
    now = _dt(now)
    d, side = slot
    p = co.plan_event(d, SIDE_KIND[side])
    start, cutoff = p["window_start_utc"], p["cutoff_utc"]
    if now > cutoff:
        tier = "CLOSED"
    elif start <= now:
        tier = "T0_OPEN"
    elif (start - now) <= timedelta(hours=T0_LEAD_H):
        tier = "T0_NEXT"
    else:
        tier = "T1"
    return {"date": str(d), "side": side, "key": key(d, side), "tier": tier, "window_start_utc": start.isoformat(), "cutoff_utc": cutoff.isoformat(),
            "hours_to_open": round((start - now).total_seconds() / 3600, 2), "hours_to_cutoff": round((cutoff - now).total_seconds() / 3600, 2)}


def required_age_h(tier: str) -> Optional[float]:
    return T0_MAX_AGE_H if tier in ("T0_OPEN", "T0_NEXT") else T1_MAX_AGE_H if tier == "T1" else None


# ------------------------------------------------------------------ persistent per-slot state
def load_state(state_dir) -> dict:
    f = Path(state_dir) / STATE_FILE
    if f.exists():
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(s, dict) and isinstance(s.get("slots"), dict):
                return s
        except (ValueError, OSError):
            pass
    return {"version": 1, "slots": {}, "symbol_index": {}, "refreshes": 0}


def save_state(state_dir, st: dict) -> None:
    d = Path(state_dir)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (STATE_FILE + ".tmp")
    tmp.write_text(json.dumps(st, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, d / STATE_FILE)


def slot_age_h(st: dict, k: str, now) -> Optional[float]:
    s = st["slots"].get(k)
    if not s or not s.get("observed_at"):
        return None
    return round((_dt(now) - _dt(s["observed_at"])).total_seconds() / 3600, 3)


def slot_is_fresh(st: dict, c: dict, now) -> bool:
    """A slot counts as fresh only if it was refreshed COMPLETELY (every page, nothing cut short) within the age its tier requires."""
    limit = required_age_h(c["tier"])
    if limit is None:
        return True
    s = st["slots"].get(c["key"])
    age = slot_age_h(st, c["key"], now)
    return bool(s and s.get("complete") and age is not None and age <= limit)


def est_requests(st: dict, k: str) -> int:
    """One request per page; a slot that filled its pages last time is assumed to need as many again (UW returns 100 rows per page)."""
    return max(1, int((st["slots"].get(k) or {}).get("pages_fetched") or 1))


# ------------------------------------------------------------------ the plan
def plan_refresh(now, st: dict, *, horizon_days: int, run_ceiling: int, budget_left: int, evidence_demand: int = 0, t1_run_max: int = T1_RUN_MAX, share: float = CALENDAR_SHARE) -> dict:
    """Which slots to refresh in this run, in this order: T0_OPEN by earliest cutoff, T0_NEXT by earliest opening, then (only from the budget left after the evidence this run needs) T1 by oldest age. Pure; sends nothing.

    cap = min(share * run_ceiling, budget_left) minus the two confirmation requests. A stale T0 slot that does not fit is DEFERRED and reported (completeness = False); it is never dropped silently.
    Returns {"refresh": [...funded slots...], "deferred": [...], "est_requests": n, "cap": n, "needed": bool, "t0_stale": n, "t1_stale": n}."""
    now = _dt(now)
    cs = [classify(s, now) for s in horizon_slots(now, horizon_days)]
    stale = [c for c in cs if c["tier"] != "CLOSED" and not slot_is_fresh(st, c, now)]
    for c in stale:
        c["age_h"] = slot_age_h(st, c["key"], now)
        c["est_requests"] = est_requests(st, c["key"])
        prev = st["slots"].get(c["key"]) or {}
        c["why"] = "never refreshed" if not prev else ("last refresh incomplete (%s)" % prev.get("reason")) if not prev.get("complete") else "older than %.0f h" % required_age_h(c["tier"])
    t0 = [c for c in stale if c["tier"] in ("T0_OPEN", "T0_NEXT")]
    t1 = [c for c in stale if c["tier"] == "T1"]
    t0.sort(key=lambda c: (0 if c["tier"] == "T0_OPEN" else 1, c["cutoff_utc"] if c["tier"] == "T0_OPEN" else c["window_start_utc"], c["key"]))
    t1.sort(key=lambda c: (-(c["age_h"] if c["age_h"] is not None else 1e9), c["window_start_utc"], c["key"]))
    cap = max(0, min(int(share * run_ceiling), budget_left) - (CONFIRM_RESERVE if t0 or t1 else 0))
    refresh, deferred, used = [], [], 0
    for c in t0:
        if used + c["est_requests"] <= cap:
            refresh.append(c)
            used += c["est_requests"]
        else:
            deferred.append(dict(c, deferred_because="CALENDAR_CAP (%d requests planned of %d)" % (used, cap)))
    t0_used = used
    t1_room = max(0, min(cap - t0_used, budget_left - evidence_demand - CONFIRM_RESERVE - t0_used))          # far days only get what is left after T0 AND the evidence this run needs
    t1_used, taken = 0, 0
    for c in t1:
        if taken < t1_run_max and t1_used + c["est_requests"] <= t1_room:
            refresh.append(c)
            t1_used += c["est_requests"]
            taken += 1
        else:
            deferred.append(dict(c, deferred_because="T1_DEFERRED (budget left after T0 and evidence, or per-run T1 limit)"))
    used = t0_used + t1_used
    return {"refresh": refresh, "deferred": deferred, "est_requests": used + (CONFIRM_RESERVE if refresh else 0), "cap": cap, "needed": bool(refresh), "t0_stale": len(t0), "t1_stale": len(t1), "as_of": now.isoformat()}


# ------------------------------------------------------------------ recording what a refresh did
def record_refresh(st: dict, slot_log: list, now) -> dict:
    """slot_log entries come from RefreshService.refresh_calendar (`last_slot_log`): {date, side, pages_fetched, symbols, complete, reason}. Only slots that were actually fetched are updated; a slot the budget never reached keeps
    its old state (and therefore stays stale). Returns the updated state."""
    now = _dt(now)
    for e in slot_log:
        k = key(e["date"], e["side"])
        st["slots"][k] = {"observed_at": now.isoformat(), "pages_fetched": e["pages_fetched"], "complete": bool(e["complete"]), "reason": e.get("reason"), "symbols": sorted(set(e.get("symbols") or []))}
    st["refreshes"] = int(st.get("refreshes", 0)) + 1
    st["last_refresh_at"] = now.isoformat()
    return st


def completeness(st: dict, now, horizon_days: int) -> dict:
    """What a reader must know about the calendar right now: which slots are stale, by tier. `t0_complete` False means an event in an open (or about to open) window may rest on a calendar older than the frozen limit."""
    now = _dt(now)
    out = {"as_of": now.isoformat(), "horizon_days": max(1, min(int(horizon_days), HORIZON_MAX)), "t0_open_stale": [], "t0_next_stale": [], "t1_stale": [], "slots_total": 0, "slots_fresh": 0}
    for s in horizon_slots(now, horizon_days):
        c = classify(s, now)
        if c["tier"] == "CLOSED":
            continue
        out["slots_total"] += 1
        if slot_is_fresh(st, c, now):
            out["slots_fresh"] += 1
        else:
            out[{"T0_OPEN": "t0_open_stale", "T0_NEXT": "t0_next_stale", "T1": "t1_stale"}[c["tier"]]].append(c["key"])
    out["t0_complete"] = not out["t0_open_stale"] and not out["t0_next_stale"]
    return out
