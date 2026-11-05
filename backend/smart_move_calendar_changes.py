"""Calendar CHANGE tracking for the rolling horizon (P1, development only): new, rescheduled, missing and removed earnings events, from the per-slot refreshes.

Why this exists: with tiered refreshes only SOME slots are fetched in a run, so "absent from today's saved observations" can never mean "removed" (the old observe_snapshot had to be told a window was complete, and the runner never did).
A change is therefore decided only from slots that were refreshed COMPLETELY (every page, nothing cut short), and a removal needs two such refreshes in a row:

  NEW          a symbol appears in a completed slot and was in no slot before
  RESCHEDULED  a symbol that was in slot A is now in slot B (a new date and/or session); the old slot is not treated as 'missing' for it
  MISSING      a symbol that was in a slot is absent from that slot's next COMPLETE refresh and appears in no other slot refreshed in the same run (first time: only a warning, it may have moved to a slot not refreshed yet)
  REMOVED      MISSING again at the next complete refresh of that slot (two in a row)
  REAPPEARED   a MISSING or REMOVED symbol is seen again

Every change is APPENDED to <state>/event_changes.jsonl (never edited or deleted); the symbol index lives in calendar_slots.json next to the per-slot state. Nothing here reads or changes a prediction, score, window or ledger record."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc
CHANGES_FILE = "event_changes.jsonl"
REMOVE_AFTER = 2                    # consecutive complete refreshes of the same slot that no longer list the symbol


def _iso(now) -> str:
    t = now if isinstance(now, datetime) else datetime.fromisoformat(str(now))
    return (t if t.tzinfo else t.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


def track(st: dict, slot_log: list, now) -> list:
    """Update st['symbol_index'] from the slots refreshed in this run and return the list of changes (also the caller's to append with `append_changes`). Only COMPLETE slots are trusted."""
    idx = st.setdefault("symbol_index", {})
    at = _iso(now)
    complete = [e for e in slot_log if e.get("complete")]
    seen_now = {}
    for e in complete:
        k = "%s|%s" % (str(e["date"])[:10], e["side"])
        for s in e.get("symbols") or []:
            seen_now.setdefault(s, []).append(k)
    changes = []
    for s, ks in sorted(seen_now.items()):
        k = sorted(ks)[0]
        rec = idx.get(s)
        if rec is None:
            idx[s] = {"slot": k, "first_seen": at, "last_seen": at, "missing_count": 0, "state": "LISTED"}
            changes.append({"at": at, "symbol": s, "change": "NEW", "to_slot": k})
            continue
        if rec["state"] in ("MISSING", "REMOVED"):
            changes.append({"at": at, "symbol": s, "change": "REAPPEARED", "from_slot": rec["slot"], "to_slot": k, "was": rec["state"]})
        elif rec["slot"] != k:
            changes.append({"at": at, "symbol": s, "change": "RESCHEDULED", "from_slot": rec["slot"], "to_slot": k})
        rec.update(slot=k, last_seen=at, missing_count=0, state="LISTED")
    for e in complete:
        k = "%s|%s" % (str(e["date"])[:10], e["side"])
        listed_here = set(e.get("symbols") or [])
        for s, rec in sorted(idx.items()):
            if rec["slot"] != k or s in listed_here or s in seen_now or rec["state"] == "REMOVED":
                continue                                                          # not in this slot, still listed, moved to another slot refreshed in this run, or already removed
            rec["missing_count"] = int(rec.get("missing_count", 0)) + 1
            if rec["missing_count"] >= REMOVE_AFTER:
                if rec["state"] != "REMOVED":
                    rec["state"] = "REMOVED"
                    changes.append({"at": at, "symbol": s, "change": "REMOVED", "from_slot": k, "missing_refreshes": rec["missing_count"]})
            elif rec["state"] != "MISSING":
                rec["state"] = "MISSING"
                changes.append({"at": at, "symbol": s, "change": "MISSING", "from_slot": k, "missing_refreshes": rec["missing_count"]})
    return changes


def append_changes(state_dir, changes: list) -> int:
    if not changes:
        return 0
    d = Path(state_dir)
    d.mkdir(parents=True, exist_ok=True)
    with (d / CHANGES_FILE).open("a", encoding="utf-8") as fh:
        for c in changes:
            fh.write(json.dumps(c, sort_keys=True) + "\n")
    return len(changes)


def read_changes(state_dir) -> list:
    f = Path(state_dir) / CHANGES_FILE
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue                                                          # a half-written last line is not damage
    return out
