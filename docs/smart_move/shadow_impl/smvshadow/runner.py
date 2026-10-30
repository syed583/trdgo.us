"""Shadow runner: executes a schedule against the governor, captures evidence, scores both variants and persists everything append-only (components 2-7 together).

The runner is the only code that decides WHEN a captured event is processed: it never starts an event it cannot afford (hourly shared cap, daily authorization, core
reservation), waits for the next hour when only the hourly cap blocks, and writes MISSED_DEADLINE to the ledger when the window closes first. It never captures after the
cutoff. It is driven by an injected clock and fetch function, so it runs in tests with a fake provider and a fake clock only.
"""
import json
import uuid
from datetime import date, datetime, timedelta, timezone

from . import capture as cap
from . import scorer as sc

UTC = timezone.utc


class ShadowRunner:
    def __init__(self, governor, store, fetch, pre, pre_sha, db=None, run_id=None):
        self.g, self.store, self.pre, self.pre_sha, self.db = governor, store, pre, pre_sha, db
        self.run_id = run_id or str(uuid.uuid4())
        self.cap = cap.Capturer(governor, store, fetch, db, self.run_id)
        self.predictions, self.snapshots, self.processed, self.missed = [], [], [], []

    def need(self, tier, shared_missing):
        return len(cap.CORE_ENDPOINTS) + shared_missing + (len(cap.FULL_EXTRA_ENDPOINTS) if tier == "FULL" else 0)

    def _shared_missing(self, sector_etf, ts):
        day = ts.astimezone(self.g.clock.now().tzinfo).date() if False else None
        n = 0
        for sym in ("SPY", sector_etf):
            if sym and not any(k[0] == "ohlc" and k[1] == sym and k[2] == ts.astimezone(cap.co.ET).date() for k in self.cap.shared):
                n += 1
        return n

    def run(self, assignments, infos):
        """assignments: scheduler output; infos: {(symbol, event_date): {kind, timing_status, version, plan, sector_etf}}."""
        clock = self.g.clock
        order = sorted(assignments, key=lambda a: (a["deadline_utc"], 0 if a["tier"] == "FULL" else 1, a["symbol"]))
        core_requests = sum(len(cap.CORE_ENDPOINTS) for a in order)
        self.g.reserve_core(core_requests)
        for a in order:
            info = infos[(a["symbol"], a["event_date"])]
            planned = datetime.fromisoformat(a["planned_capture_utc"])
            deadline = datetime.fromisoformat(a["deadline_utc"])
            if clock.now() < planned:
                clock.sleep((planned - clock.now()).total_seconds())
            tier = a["tier"]
            while True:
                now = clock.now()
                if now >= deadline:
                    self.g.missed_deadline(a["symbol"], a["event_date"], deadline, "WINDOW_CLOSED_BEFORE_CAPTURE")
                    self.missed.append(a)
                    break
                need = self.need(tier, self._shared_missing(info["sector_etf"], now))
                ok, why = self.g.can_afford(need, "core")
                if ok and tier == "FULL":
                    ok, why = self.g.can_afford(need, "core")
                if ok:
                    self._process(a, info, now, tier)
                    break
                if why == "HOURLY_SHARED_CAP":
                    self.g.log("WAIT_NEXT_HOUR", symbol=a["symbol"], event_date=a["event_date"], needed=need)
                    clock.sleep(self.g.seconds_to_next_hour())
                    continue
                self.g.missed_deadline(a["symbol"], a["event_date"], deadline, why)
                self.missed.append(a)
                break
            self.g.reserve_core(max(0, self.g.core_reserved - len(cap.CORE_ENDPOINTS)))
        return self.predictions

    def _process(self, a, info, now, tier):
        plan = info["plan"]
        ev, extras = self.cap.capture_event(a, plan, info["kind"], now, info.get("sector_etf"))
        edate = date.fromisoformat(a["event_date"])
        preds = [sc.score(self.pre, self.pre_sha, ev, symbol=a["symbol"], event_date=edate, event_version=info["version"], kind=info["kind"],
                          timing_status=info["timing_status"], plan=plan, capture_ts=now, variant=v) for v in ("continuation", "contrarian")]
        f5 = ev.get(5)
        for p in preds:
            p["adv20"] = f5.extracted.get("adv20") if f5 and f5.usable else None
        for p in preds:
            if info.get("calendar"):
                p.setdefault("explanation", {})["calendar"] = info["calendar"]
        self.predictions += preds
        self.snapshots.append({"symbol": a["symbol"], "event_date": a["event_date"], "evidence": list(ev.values()) + extras})
        self.processed.append(a)
        if self.db:
            self._persist(a, info, ev, extras, preds)

    def _persist(self, a, info, ev, extras, preds):
        db = self.db
        edate = date.fromisoformat(a["event_date"])
        for m in self.cap.payload_meta:
            db.add_payload(m["sha"], m["rel"], "UW", m["endpoint"], m["params_hash"], 200, m["raw_len"], (self.store.root / m["rel"]).stat().st_size, m["captured_at"])
        self.cap.payload_meta.clear()
        sids = []
        for e in list(ev.values()) + extras:
            sids.append((e, db.add_snapshot(e, a["symbol"], edate, info["version"], self.run_id)))
        ok_ids = [sid for e, sid in sids if e.status == "OK" and e.factor in (3, 5, 7, 8)]
        for p in preds:
            pid = db.add_prediction(p)
            db.link_inputs(pid, ok_ids)

    def finish(self):
        if self.db:
            self.db.add_ledger(self.run_id, self.g.ledger)
        return {"processed": len(self.processed), "missed": len(self.missed), "ledger": len(self.g.ledger)}
