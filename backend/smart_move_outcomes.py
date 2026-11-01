"""Smart Move post-earnings outcome pipeline. Local, deterministic code over daily candles that are PASSED IN: it sends no request.
A live fetch, when separately authorized, only supplies `rows`; everything below is tested with saved data and mocks.

Rules (each covered by a test):
  * the reaction session (BMO: the event day; AMC: the next trading day) must be closed AND SAFETY_DELAY_MIN past its close, else PENDING (premature outcomes are refused);
  * the move is reaction-session close / base-session close - 1 (the frozen grader convention); both bars must exist as regular-session rows, else MISSING_BARS;
  * the outcome row keeps actual open/close prices and is append-only; a different value for the same event+prediction chain is stored as a correction VERSION, never an overwrite;
  * SIMULATION and LIVE outcomes are kept apart by `mode`.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "shadow_impl"))
from smvshadow import adapters as ad  # noqa: E402
from smvshadow import cutoff as co  # noqa: E402

UTC = timezone.utc
SAFETY_DELAY_MIN = 30
FILE = "outcome_details.jsonl"


def due(event_date: date, kind: str, now: datetime) -> dict:
    """Is the outcome collectable yet? Returns the sessions and the earliest fetch time."""
    base, react = co.base_session(event_date, kind), co.reaction_session(event_date, kind)
    earliest = co._mc().session_close_utc(react) + timedelta(minutes=SAFETY_DELAY_MIN)
    return {"base_session": base, "reaction_session": react, "earliest_fetch_utc": earliest, "due": now >= earliest}


def compute(symbol: str, event_date, kind: str, rows, now: datetime, *, mode: str = "LIVE", source: str = "unspecified") -> dict:
    ed = event_date if isinstance(event_date, date) else date.fromisoformat(str(event_date)[:10])
    d = due(ed, kind, now)
    out = {"symbol": symbol.upper(), "earnings_date": ed.isoformat(), "kind": kind, "mode": mode, "source": source, "computed_at": now.isoformat(),
           "base_session": d["base_session"].isoformat(), "reaction_session": d["reaction_session"].isoformat(), "earliest_fetch_utc": d["earliest_fetch_utc"].isoformat()}
    if not d["due"]:
        return dict(out, status="PENDING_SESSION_NOT_CLOSED", move_pct=None)
    ser = ad.regular_series(rows)
    b, r = ser.get(d["base_session"]), ser.get(d["reaction_session"])
    if not b or not r or not b[3]:
        return dict(out, status="MISSING_BARS", move_pct=None, missing=[s for s, x in (("base", b), ("reaction", r)) if not x])
    move = (r[3] / b[3] - 1) * 100
    if abs(move) > 100:
        return dict(out, status="IMPLAUSIBLE", move_pct=None)
    return dict(out, status="GRADED", move_pct=round(move, 6), base_close=b[3], reaction_open=r[0], reaction_close=r[3], gap_pct=round((r[0] / b[3] - 1) * 100, 6))


def record(track_dir, outcome: dict) -> dict:
    """Append-only. Same value again -> duplicate. Different value -> a new correction version; nothing is overwritten."""
    p = Path(track_dir) / FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    prior = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []
    chain = [o for o in prior if (o["symbol"], o["earnings_date"], o["mode"]) == (outcome["symbol"], outcome["earnings_date"], outcome["mode"])]
    if outcome["status"] != "GRADED":
        return {"result": "not_graded", "status": outcome["status"]}
    if chain and abs(chain[-1]["move_pct"] - outcome["move_pct"]) < 1e-9:
        return {"result": "duplicate_same", "version": chain[-1]["version"]}
    row = dict(outcome, version=len(chain) + 1, supersedes=chain[-1]["version"] if chain else None)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    return {"result": "correction" if chain else "recorded", "version": row["version"]}


def latest(track_dir) -> list:
    p = Path(track_dir) / FILE
    if not p.exists():
        return []
    cur = {}
    for l in p.read_text(encoding="utf-8").splitlines():
        if l.strip():
            o = json.loads(l)
            cur[(o["symbol"], o["earnings_date"], o["mode"])] = o
    return list(cur.values())


def plan_fetches(events: Iterable[dict], track_dir, now: datetime, *, max_requests: int) -> dict:
    """Which events need a candle fetch now: due, no outcome yet. Capped; never fetches. events: dicts with symbol, earnings_date, kind."""
    have = {(o["symbol"], o["earnings_date"]) for o in latest(track_dir)}
    todo, waiting = [], []
    for e in events:
        if e.get("kind") not in ("BMO", "AMC"):
            continue
        ed = date.fromisoformat(str(e["earnings_date"])[:10])
        if (e["symbol"].upper(), ed.isoformat()) in have:
            continue
        (todo if due(ed, e["kind"], now)["due"] else waiting).append(e["symbol"])
    return {"fetch": todo[:max_requests], "deferred_by_budget": todo[max_requests:], "not_due_yet": waiting, "max_requests": max_requests}
