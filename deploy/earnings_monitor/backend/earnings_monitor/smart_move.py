"""Smart Move score40-v1 as a source for the monitor.

The monitor never scores anything itself. Two ways a Smart Move call can reach it, both genuine:
  1. ENGINE: when the frozen engine and a private evidence store are installed on the machine (SMART_MOVE_ENGINE_ROOT + SMART_MOVE_EVIDENCE_STORE), `LocalEngine` runs the frozen scorer on that stored
     evidence (read only, no provider) and returns its own classification. Fresh evidence is what the engine itself requires; stale or missing evidence comes back as NO VALID PREDICTION / PENDING.
  2. SNAPSHOT: an immutable, hash-verified snapshot written after a separately authorised collection run (smart_move_engine20_snapshot).
Collecting the evidence is a provider activity with its own attestation and cap; the monitor only PLANS it (what is missing, how many requests) and records a request for the owner. It cannot run it."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

UTC = timezone.utc
VERSION = "score40-v1"
SIGNAL_OF = {"BUY": "BUY", "SELL": "SELL", "NO TRADE (genuine)": "NO_TRADE", "NO VALID PREDICTION": "NO_VALID_PREDICTION", "PENDING FINAL REFRESH": "PENDING"}


def to_obs(row: dict, now: datetime, source: str) -> dict:
    """One classified engine row -> the monitor's observation. as_of is the time of the newest evidence behind the call, so a prediction ages with its evidence."""
    cov = "%s/%s" % (row["coverage_points"], row["coverage_max"]) if row.get("coverage_points") is not None and row.get("coverage_max") else None
    ev = row.get("evidence_retrieved") or []
    as_of = (ev[-1] if ev else None) or now.isoformat()
    return {"signal": SIGNAL_OF.get(row["result"], "NO_VALID_PREDICTION"), "score": None, "coverage": cov, "tier": row.get("liquidity"), "as_of": as_of, "evidence_as_of": ev[-1] if ev else None,
            "source": source, "engine_version": row.get("model_version") or VERSION, "event_date": str(row.get("event_date") or "")[:10] or None}


class LocalEngine:
    """The frozen engine run offline on a private evidence store. Needs the Smart Move modules on the path; absent on the production server unless deliberately installed."""

    def __init__(self, repo_root, store_dir, clock_cls=None):
        self.repo_root, self.store_dir = Path(repo_root), Path(store_dir)
        for p in (self.repo_root / "backend", self.repo_root / "docs" / "smart_move" / "shadow_impl"):
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
        import smart_move_engine20 as E20
        import smart_move_live as L
        from smvshadow import governor as gv
        self.E20, self.L, self.gv = E20, L, gv

    def _svc(self, now: datetime):
        cfg = self.L.LiveConfig(mode="SIMULATION", store_dir=self.store_dir, refresh_enabled=False, authorized_daily=0, spacing_s=0.0)
        return self.L.RefreshService(cfg, self.L.Fetcher(), self.gv.FakeClock(now))

    def classify(self, now: datetime, symbols: Optional[set] = None) -> list:
        svc = self._svc(now)
        rows = self.L.LiveScorer(svc).score_all(now)
        return [self.E20.classify(r, now) for r in rows if symbols is None or r["symbol"] in symbols]

    def plan(self, events: list, now: datetime, budget: int = 50) -> dict:
        svc = self._svc(now)
        rows = self.L.LiveScorer(svc).score_all(now)
        caps = {}
        for o in svc.store.read("cal_obs"):
            try:
                caps[o["symbol"]] = float(o["marketcap"])
            except (KeyError, TypeError, ValueError):
                pass
        wanted = [e["symbol"] for e in events]
        selected = self.E20.select_top(rows, caps, now, n=max(1, len(wanted)), only=wanted)
        plan = self.E20.plan_tasks(svc, selected, now, budget=budget)
        return {"symbols": [s["symbol"] for s in selected], "requests_needed": plan["attempts_planned"], "funded": plan["stocks_fully_funded"], "unfunded": [(t["kind"], t["symbol"]) for t in plan["unfunded"]],
                "already_fresh": plan["already_fresh_stocks"], "budget": budget, "not_in_window": sorted(set(wanted) - {s["symbol"] for s in selected}),
                "note": "A request for the separately authorised Smart Move collector (profile engine20). The monitor does not run it."}


class SmartMoveSource:
    def __init__(self, engine=None, snapshot_dir: Optional[str] = None, clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.engine, self.snapshot_dir, self.clock = engine, snapshot_dir, clock
        self._rows: dict = {}                                    # symbol -> the engine's / snapshot's own classified row from the latest read
        self._meta: Optional[dict] = None                        # what the latest read covered: source, when it was made, which event dates and how many events

    @classmethod
    def from_env(cls):
        root, store = os.getenv("SMART_MOVE_ENGINE_ROOT"), os.getenv("SMART_MOVE_EVIDENCE_STORE")
        engine = None
        if root and store:
            try:
                engine = LocalEngine(root, store)
            except Exception:  # noqa: BLE001 - the engine is optional; absent means "not installed", never a guess
                engine = None
        return cls(engine, os.getenv("SMART_MOVE_ENGINE20_DIR"))

    def status(self) -> dict:
        return {"engine_installed": self.engine is not None, "snapshot_dir_configured": bool(self.snapshot_dir), "model_version": VERSION,
                "how_predictions_arise": "frozen engine on a private evidence store" if self.engine else ("verified snapshot only" if self.snapshot_dir else "none: not installed")}

    def models(self, now: Optional[datetime] = None) -> dict:
        now = now or self.clock()
        out = {}
        if self.engine is not None:
            try:
                rows = self.engine.classify(now)
                for r in rows:
                    out[r["symbol"]] = to_obs(r, now, "smart_move_engine")
                    self._rows[r["symbol"]] = r
                if out:
                    self._meta = {"source": "LIVE_ENGINE", "generated_at": now.isoformat(), "model_version": VERSION, "rows": len(rows), "event_dates": sorted({str(r.get("event_date"))[:10] for r in rows if r.get("event_date")})}
                    return out
            except Exception:  # noqa: BLE001 - a broken engine must not turn into invented calls
                out = {}
        if self.snapshot_dir:
            try:
                import smart_move_engine20_snapshot as S
                snap = S.load_latest(self.snapshot_dir)                  # verifies the snapshot's SHA-256 on every read
            except Exception:  # noqa: BLE001
                snap = None
            if snap and "error" not in snap:
                self._meta = {"source": "HISTORICAL_SNAPSHOT", "generated_at": snap.get("generated_at"), "model_version": snap.get("model_version") or VERSION, "rows": len(snap["rows"]),
                              "event_dates": sorted({str(r.get("event_date"))[:10] for r in snap["rows"] if r.get("event_date")})}
                for r in snap["rows"]:
                    sig = r["category"] if r["category"] in ("BUY", "SELL", "NO_TRADE") else "NO_VALID_PREDICTION"
                    cov = "%s/%s" % (r["coverage_points"], r["coverage_max"]) if r.get("coverage_points") is not None else None
                    ev = r.get("evidence_retrieved") or []
                    self._rows[r["symbol"]] = r
                    out[r["symbol"]] = {"signal": sig, "score": None, "coverage": cov, "tier": r.get("liquidity"), "as_of": ev[-1] if ev else snap["generated_at"], "evidence_as_of": ev[-1] if ev else None,
                                        "source": "smart_move_snapshot", "engine_version": snap.get("model_version"), "event_date": str(r.get("event_date") or "")[:10] or None}
        return out

    def window_report(self) -> Optional[dict]:
        """The engine's OWN window verdict per event from its latest read: how many events sit inside the prediction window now, how many of those still have no usable result, and when the first window closes.
        None unless the live engine produced rows (a stored snapshot carries only the funded events, which says nothing about windows). The frozen rule is never re-implemented here."""
        if not self._meta or self._meta.get("source") != "LIVE_ENGINE":
            return None
        open_rows = [r for r in self._rows.values() if r.get("window") == "OPEN"]
        missing = [r for r in open_rows if r.get("result") not in ("BUY", "SELL", "NO TRADE (genuine)")]
        cutoffs = sorted(str(r["window_cutoff_utc"]) for r in missing if r.get("window_cutoff_utc"))
        by_date: dict = {}
        for r in missing:
            by_date[str(r.get("event_date"))[:10]] = by_date.get(str(r.get("event_date"))[:10], 0) + 1
        pending = [{"symbol": r["symbol"] if "symbol" in r else sym, "date": str(r.get("event_date"))[:10], "cutoff": str(r.get("window_cutoff_utc") or ""), "result": r.get("result")} for sym, r in self._rows.items()
                   if r.get("window") == "OPEN" and r.get("result") not in ("BUY", "SELL", "NO TRADE (genuine)")]
        return {"as_of": self._meta.get("generated_at"), "open": len(open_rows), "open_without_result": len(missing), "first_cutoff_utc": cutoffs[0] if cutoffs else None, "missing_by_date": by_date,
                "closed": sum(1 for r in self._rows.values() if r.get("window") == "CLOSED"), "open_missing_events": pending[:5000]}

    def coverage(self) -> Optional[dict]:
        """What the latest read covered (None when neither the engine nor a snapshot produced anything)."""
        return dict(self._meta) if self._meta else None

    DETAIL_KEYS = ("result", "category", "reason", "reason_codes", "missing_factors", "event_date", "window", "coverage_points", "coverage_max", "bull_points", "bear_points", "margin", "liquidity", "evidence_retrieved", "model_version", "reasons", "missing_evidence",
                   "blocked_by", "why", "components")

    def details(self, symbol: str, d: Optional[str] = None) -> Optional[dict]:
        """The engine's own row for one symbol, restricted to descriptive keys, as of the latest models() read. None when the engine has not scored it."""
        r = self._rows.get(symbol)
        return {k: r[k] for k in self.DETAIL_KEYS if k in r} if r else None

    def plan(self, events: list, now: datetime) -> Optional[dict]:
        return self.engine.plan(events, now) if self.engine is not None else None
