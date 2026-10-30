"""Smart Move shadow pipeline, offline: calendar verification -> eligibility -> deterministic selection -> governor -> capture -> frozen 40-point scorer (both variants) ->
append-only store, with an explicit NO TRADE record (and reason codes) for every event that is excluded. Nothing here makes a provider request on its own: the injected
`fetch` is a fake provider in tests. Existing production prediction models are not touched.
"""
import uuid
from datetime import date, datetime, timezone

from . import calendar_confirm as cc
from . import calendar_validation as cv
from . import cutoff as co
from . import runner as rn
from . import selection as sel

UTC = timezone.utc


def no_trade(symbol, event_date, reasons, status, stage, now):
    return {"symbol": symbol, "event_date": str(event_date) if event_date else None, "signal": "NO_TRADE", "reason_codes": sorted(set(reasons)), "calendar_status": status, "stage": stage,
            "at": now.isoformat(), "type": "NO_TRADE_" + stage}


class ShadowPipeline:
    def __init__(self, governor, store, fetch, pre, pre_sha, *, db=None, run_id=None, seed="shadow-v1", core_cap=220, full_cap=30):
        self.g, self.store, self.fetch, self.pre, self.pre_sha, self.db = governor, store, fetch, pre, pre_sha, db
        self.run_id = run_id or str(uuid.uuid4())
        self.seed, self.core_cap, self.full_cap = seed, core_cap, full_cap

    def run(self, meta: dict, log: cc.ObservationLog, now: datetime, today: date):
        """meta: {symbol: {sector_etf, liquid, prior_event_date, prediction_snapshot_at, prior_prediction_exists, company_evidence}}."""
        evals, excluded, cands, infos = {}, [], [], {}
        for sym in sorted(meta):
            m = meta[sym]
            ev = cc.evaluate(sym, log.for_symbol(sym, now), now, today, prior_event_date=m.get("prior_event_date"), prediction_snapshot_at=m.get("prediction_snapshot_at"),
                             prior_prediction_exists=m.get("prior_prediction_exists", False), company_evidence=m.get("company_evidence"))
            evals[sym] = ev
            if not ev["eligible_for_shadow"]:
                excluded.append(no_trade(sym, ev["event_date"], ev["no_trade_reasons"], ev["status"], "CALENDAR", now))
                continue
            plan = co.plan_event(ev["event_date"], ev["kind"])
            cands.append(sel.Candidate(sym, ev["event_date"], 1, ev["kind"], cv.CONFIRMED, cv.T_CONFIRMED, plan["window_start_utc"], plan["deadline_utc"], m.get("liquid"), plan["evidence_only"]))
            infos[(sym, str(ev["event_date"]))] = {"kind": ev["kind"], "timing_status": cv.T_CONFIRMED, "version": 1, "plan": plan, "sector_etf": m.get("sector_etf"),
                                                   "calendar": {"status": ev["status"], "calendar_observed_at": ev["calendar_observed_at"].isoformat(), "prediction_snapshot_at":
                                                                ev["prediction_snapshot_at"].isoformat() if ev["prediction_snapshot_at"] else None, "dependency": ev["dependency"],
                                                                "note": "vendor agreement, not direct company confirmation" if ev["status"] == cc.VENDOR_CONFIRMED else "direct company evidence"}}
        plan = sel.schedule(cands, seed=self.seed, core_cap=self.core_cap, full_cap=self.full_cap)
        for e in plan["ledger"]:
            excluded.append(no_trade(e["symbol"], e["event_date"], [e["type"]], evals[e["symbol"]]["status"], "SELECTION", now))
        if self.db:
            self.db.add_calendar_observations(log)
            for sym, ev in evals.items():
                self.db.add_calendar_evaluation(self.run_id, ev, now)
            for (sym, ed), info in infos.items():
                v = cv.EventVersion(sym, date.fromisoformat(ed), 1, cv.CONFIRMED, cv.T_CONFIRMED, info["kind"], [], now)
                self.db.add_event_version(v, info["plan"])
        runner = rn.ShadowRunner(self.g, self.store, self.fetch, self.pre, self.pre_sha, db=self.db, run_id=self.run_id)
        runner.run(plan["assignments"], infos)
        for a in runner.missed:
            why = [e.get("reason") or e["type"] for e in self.g.ledger if e["type"] == "MISSED_DEADLINE" and e.get("symbol") == a["symbol"]] or ["MISSED_DEADLINE"]
            excluded.append(no_trade(a["symbol"], a["event_date"], ["MISSED_DEADLINE"] + why, evals[a["symbol"]]["status"], "CAPTURE", now))
        for p in runner.predictions:
            if p["signal"] == "NO_TRADE":
                excluded.append(no_trade(p["symbol"], p["earnings_date"], p["reason_codes"], evals[p["symbol"]]["status"], "SCORER:" + p["variant"], now))
        ledger = excluded[:]
        if self.db:
            self.db.add_ledger(self.run_id, ledger)
        runner.finish()
        return {"run_id": self.run_id, "evaluations": evals, "predictions": runner.predictions, "no_trade": excluded, "plan": plan, "processed": runner.processed, "missed": runner.missed,
                "governor_ledger": self.g.ledger, "requests_used": dict(self.g.used_day)}
