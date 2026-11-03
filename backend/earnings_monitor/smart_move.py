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
            "source": source, "engine_version": row.get("model_version") or VERSION}


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
                for r in self.engine.classify(now):
                    out[r["symbol"]] = to_obs(r, now, "smart_move_engine")
                    self._rows[r["symbol"]] = r
                if out:
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
                for r in snap["rows"]:
                    sig = r["category"] if r["category"] in ("BUY", "SELL", "NO_TRADE") else "NO_VALID_PREDICTION"
                    cov = "%s/%s" % (r["coverage_points"], r["coverage_max"]) if r.get("coverage_points") is not None else None
                    ev = r.get("evidence_retrieved") or []
                    self._rows[r["symbol"]] = r
                    out[r["symbol"]] = {"signal": sig, "score": None, "coverage": cov, "tier": r.get("liquidity"), "as_of": ev[-1] if ev else snap["generated_at"], "evidence_as_of": ev[-1] if ev else None,
                                        "source": "smart_move_snapshot", "engine_version": snap.get("model_version")}
        return out

    DETAIL_KEYS = ("result", "category", "coverage_points", "coverage_max", "bull_points", "bear_points", "margin", "liquidity", "evidence_retrieved", "model_version", "reasons", "missing_evidence",
                   "blocked_by", "why", "components")

    def details(self, symbol: str, d: Optional[str] = None) -> Optional[dict]:
        """The engine's own row for one symbol, restricted to descriptive keys, as of the latest models() read. None when the engine has not scored it."""
        r = self._rows.get(symbol)
        return {k: r[k] for k in self.DETAIL_KEYS if k in r} if r else None

    def plan(self, events: list, now: datetime) -> Optional[dict]:
        return self.engine.plan(events, now) if self.engine is not None else None
