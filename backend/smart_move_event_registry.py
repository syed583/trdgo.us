"""Versioned earnings-event registry for Smart Move.

Append-only history of (symbol, earnings_date, BMO/AMC) observations from a calendar snapshot. A snapshot
supersedes the symbol's earlier view: a changed date/timing becomes a new version (the old one is kept as
RESCHEDULED), a symbol missing from a fresh full snapshot becomes REMOVED (kept, never deleted), and only
CURRENT events count in the live universe. Predictions attach to the event version they were captured for,
so a valid prediction for a since-removed or rescheduled event stays visible in history, never in the live count.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

CURRENT, RESCHEDULED, REMOVED = "CURRENT", "RESCHEDULED", "REMOVED"


def _iso(x) -> str:
    return (x if isinstance(x, datetime) else datetime.fromisoformat(str(x))).astimezone(timezone.utc).isoformat()


class EventRegistry:
    def __init__(self, path):
        self.path = Path(path) if path else None             # None = in-memory (derived views never write)
        self.versions: list = []                      # every version ever recorded, in order
        if self.path and self.path.exists():
            self.versions = [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def _append(self, v: dict) -> dict:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(v, sort_keys=True) + "\n")
        self.versions.append(v)
        return v

    def _latest(self, symbol: str) -> Optional[dict]:
        for v in reversed(self.versions):
            if v["symbol"] == symbol:
                return v
        return None

    def apply_snapshot(self, events: Iterable[dict], observed, *, complete: bool = True) -> dict:
        """events: dicts with symbol, earnings_date (YYYY-MM-DD), kind (BMO/AMC), confirmed (bool), fiscal_period (opt).
        complete=True means the snapshot lists the whole window, so absent symbols are REMOVED."""
        ts = _iso(observed)
        seen, added, rescheduled, removed, dupes = set(), [], [], [], []
        for e in events:
            sym = str(e["symbol"]).upper().strip()
            if sym in seen:                            # duplicate symbol in one snapshot: first row wins, the rest are reported
                dupes.append(sym)
                continue
            seen.add(sym)
            prev = self._latest(sym)
            key = (e["earnings_date"], e.get("kind"), bool(e.get("confirmed", False)))
            if prev and prev["status"] == CURRENT and (prev["earnings_date"], prev["kind"], prev["confirmed"]) == key:
                continue                               # unchanged: no new version
            if prev and prev["status"] == CURRENT:
                prev_update = dict(prev, status=RESCHEDULED, superseded_ts=ts)
                self._append(prev_update)
                rescheduled.append(sym)
            else:
                added.append(sym)
            self._append({"symbol": sym, "earnings_date": e["earnings_date"], "kind": e.get("kind"), "confirmed": bool(e.get("confirmed", False)),
                          "fiscal_period": e.get("fiscal_period"), "status": CURRENT, "version": 1 + max([v["version"] for v in self.versions if v["symbol"] == sym] or [0]),
                          "observed_ts": ts, "original_date": (prev or {}).get("original_date") or (prev or {}).get("earnings_date") or e["earnings_date"]})
        if complete:
            for sym in sorted({v["symbol"] for v in self.versions} - seen):
                cur = self._latest(sym)
                if cur and cur["status"] == CURRENT:
                    self._append(dict(cur, status=REMOVED, superseded_ts=ts))
                    removed.append(sym)
        return {"added": added, "rescheduled": rescheduled, "removed": removed, "duplicates": dupes}

    def current(self) -> dict:
        """The live universe: symbol -> its CURRENT version (latest record per symbol, only when CURRENT)."""
        out = {}
        for v in self.versions:
            out[v["symbol"]] = v
        return {s: v for s, v in out.items() if v["status"] == CURRENT}

    def history(self, symbol: str) -> list:
        return [v for v in self.versions if v["symbol"] == symbol.upper()]

    def reconcile(self, predictions: Iterable[dict]) -> dict:
        """Attach each prediction (symbol, earnings_date) to an event version. Nothing is dropped:
        live = matches the CURRENT event; historical = matches an older/removed version; orphan = no event ever recorded."""
        cur = self.current()
        live, historical, orphan = [], [], []
        for p in predictions:
            sym = str(p["symbol"]).upper()
            if sym in cur and cur[sym]["earnings_date"] == p["earnings_date"]:
                live.append(p)
            elif any(v["earnings_date"] == p["earnings_date"] for v in self.history(sym)):
                historical.append(p)
            else:
                orphan.append(p)
        have = {p["symbol"].upper() for p in live}
        return {"live": live, "historical": historical, "orphan": orphan, "universe": len(cur),
                "current_without_prediction": sorted(set(cur) - have)}
