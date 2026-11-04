"""The monitor itself: calendar observation (new / rescheduled / canceled events, never duplicated, vendor-verified), model-prediction recording (append-only, only real calls, with their source), change detection,
alerts, results and verified outcomes.

Everything reads through an Adapters object (cache reads) and reaches a provider only through ProviderAccess.call(), which the gate controls. Nothing here scores an event, grades with a rule of its own for
Smart Move, trades, or substitutes one model's call for another's."""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from . import alerts as A
from . import calendar_us as CAL
from . import config as C
from . import evidence as EV
from . import logic as L
from .gate import GateState, ProviderAccess, ProviderBlocked, day_key
from .store import Store, iso, parse

UTC = timezone.utc
ET = ZoneInfo("America/New_York")
HORIZON_DAYS = 15
SESSIONS = ("BMO", "AMC")


class Adapters:
    """Interface. Cache-reading methods must never start a provider request; refresh_*/verify_* may, and are only ever called through ProviderAccess."""
    model_labels: dict = {}

    def calendar(self) -> list:                       # [{symbol, company, date, session, sources?:[{vendor,date,session}]}]  from caches
        raise NotImplementedError

    def models(self) -> dict:                         # {model: {SYMBOL: {signal, score, coverage, tier, as_of, evidence_as_of, source, engine_version}}}  from caches / stored snapshots
        raise NotImplementedError

    def result(self, symbol: str, d: str) -> Optional[dict]:      # {published_at} once the earnings result is public (cache / database read)
        return None

    def outcome(self, symbol: str, d: str) -> Optional[dict]:     # {move_pct, direction, available_at, verified, source} once the reaction is known (cache / database read)
        return None

    def smart_move_plan(self, events: list, now: datetime) -> Optional[dict]:      # offline: which Smart Move evidence is missing for these events (no provider call)
        return None

    def evidence(self, symbol: str, d: str) -> list:  # cache reads only: [{kind, provider, ext_id, occurred_at, title, summary, data, provider_structured}]
        return []

    def prepare_evidence(self, symbols: list) -> None:   # optional: read all symbols' cached inputs in one batch before a scan
        return None

    def model_sources(self) -> dict:                  # {model: source text of the model's own code}, used only to see which KIND of evidence each model reads
        return {}

    def model_rows(self) -> dict:                     # {model: {SYMBOL: row with params}} when the cached scans carry the scored inputs
        return {}

    def smart_move_details(self, symbol: str, d: str) -> Optional[dict]:      # stored Smart Move evidence / feature detail for one event (no provider call)
        return None

    def smart_move_windows(self) -> Optional[dict]:   # the live engine's own window verdicts (None without the live engine)
        return None

    def smart_move_coverage(self) -> Optional[dict]:  # what the Smart Money Move engine / snapshot covered on its latest read: source, generated_at, event_dates, rows
        return None

    def warm_universe(self) -> Optional[dict]:        # {"symbols": [...], "horizon_days": calendar days the warmer covers} when the application's warm universe can be read without a provider call
        return None

    def provider_ledger(self) -> Optional[dict]:      # the application's per-provider request ledger (counts, outcomes, limits, owner-configured cost): read only, no provider call
        return None

    def infra_status(self) -> Optional[dict]:         # {"broker_ok": bool|None, "provider_down": bool|None}: the shared-lease broker and the provider breaker, when those exist (None = nothing to report)
        return None

    def collector_status(self) -> Optional[dict]:     # the scheduled Smart Money Move collector's own run log, read-only (None when no collector is configured)
        return None

    def app_requests_today(self) -> Optional[int]:    # every Unusual Whales request the application itself has made today (warmers, Options Flow, monitor), or None when unknown
        return None

    def refresh_calendar(self) -> list:               # provider
        raise ProviderBlocked("NO_CALENDAR_REFRESH_ADAPTER")

    def refresh_event(self, symbol: str) -> None:     # provider
        raise ProviderBlocked("NO_EVENT_REFRESH_ADAPTER")

    def verify_outcome(self, symbol: str, d: str) -> None:   # provider
        raise ProviderBlocked("NO_OUTCOME_VERIFY_ADAPTER")


def today_et(now: datetime) -> date:
    return now.astimezone(ET).date()


def in_session_window(cfg: C.Config, now: datetime, ev: dict) -> bool:
    d = date.fromisoformat(ev["earnings_date"])
    if ev["session"] not in SESSIONS or d != today_et(now) or not CAL.is_trading_day(d):
        return False
    lo, hi = CAL.window_et(d, ev["session"], cfg.bmo_window_et if ev["session"] == "BMO" else cfg.amc_window_et)
    t = now.astimezone(ET).strftime("%H:%M")
    return lo <= t <= hi


def prediction_cutoff(cfg: C.Config, ev: dict) -> datetime:
    """The last moment a prediction may be used to grade this event without look-ahead: the START of the session's reporting window (before open for BMO, from 15:00 ET for AMC; an unconfirmed session uses the earlier one)."""
    d = date.fromisoformat(ev["earnings_date"])
    lo = cfg.amc_window_et[0] if ev["session"] == "AMC" else cfg.bmo_window_et[0]
    h, m = (int(x) for x in lo.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=ET).astimezone(UTC)


def reaction_session_date(ev: dict) -> date:
    return CAL.reaction_session(date.fromisoformat(ev["earnings_date"]), ev["session"])


def outcome_ready_at(cfg: C.Config, ev: dict) -> datetime:
    return CAL.close_utc(reaction_session_date(ev)) + timedelta(minutes=cfg.outcome_delay_min)


class Monitor:
    def __init__(self, store: Store, cfg: C.Config, adapters: Adapters, access: ProviderAccess, clock: Callable[[], datetime], sinks: Optional[list] = None):
        self.store, self.cfg, self.ad, self.access, self.clock = store, cfg, adapters, access, clock
        self.sinks = sinks or []

    # ------------------------------------------------------------ helpers
    def _change(self, now, ev: Optional[dict], symbol: str, kind: str, model=None, old=None, new=None, detail=None, key_extra="") -> Optional[int]:
        key = "|".join(str(x) for x in (ev["id"] if ev else symbol, kind, model, old, new, key_extra))
        try:
            with self.store.tx() as c:
                return c.execute("INSERT INTO changes(event_id,symbol,kind,model,old_value,new_value,detail,detected_at,dedupe_key) VALUES(?,?,?,?,?,?,?,?,?)",
                                 (ev["id"] if ev else None, symbol, kind, model, None if old is None else str(old), None if new is None else str(new), json.dumps(detail or {}, default=str), iso(now), key)).lastrowid
        except Exception as e:  # noqa: BLE001
            if "UNIQUE" in str(e):
                return None                                   # the same change was already recorded: never duplicate
            raise

    def _alerts_for(self, now, change: dict, ev: dict, pred_ts=None, extra=None):
        if (change.get("detail") or {}).get("confirmed"):
            return 0
        return A.enqueue(self.store, now, A.build(change, ev, pred_ts, self.cfg, extra), self.cfg)

    def _touch_event(self, c, ev_id, now, **fields):
        sets = ", ".join("%s=?" % k for k in fields)
        c.execute("UPDATE events SET %s, last_changed=? WHERE id=?" % sets, tuple(fields.values()) + (iso(now), ev_id))

    def event(self, ev_id: int) -> dict:
        return self.store.one("SELECT * FROM events WHERE id=?", (ev_id,))

    def _new_event(self, c, r, sym, d, sess, now, ver, sources):
        eid = c.execute("INSERT INTO events(symbol,company,earnings_date,session,first_seen,last_seen,last_changed,source,verification,sources) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (sym, r.get("company"), d, sess, iso(now), iso(now), iso(now), r.get("source"), ver, json.dumps(sources) if sources else None)).lastrowid
        c.execute("INSERT INTO event_versions(event_id,earnings_date,session,observed_at,reason) VALUES(?,?,?,?,?)", (eid, d, sess, iso(now), "NEW_EVENT"))
        return eid

    def _update_verification(self, ev: dict, ver: str, sources: list, now: datetime):
        if sources:
            with self.store.tx() as c:
                c.execute("UPDATE events SET sources=?, last_verified=? WHERE id=?", (json.dumps(sources), iso(now), ev["id"]))
        if ver != ev["verification"] and (sources or ver != "UNVERIFIED"):
            with self.store.tx() as c:
                self._touch_event(c, ev["id"], now, verification=ver)
            cur = self.event(ev["id"])
            cid = self._change(now, cur, ev["symbol"], "VERIFICATION_CHANGED", old=ev["verification"], new=ver, key_extra=iso(now))
            if cid:
                self._alerts_for(now, {"kind": "VERIFICATION_CHANGED", "old": ev["verification"], "new": ver}, cur)

    # ------------------------------------------------------------ calendar
    def observe_calendar(self, rows: list, now: datetime, complete: bool) -> dict:
        """rows: [{symbol, company, date, session, sources?}]. complete=True only for a real, successful calendar fetch; only then does an absent event count towards cancellation. One commit for the whole scan."""
        with self.store.tx():
            return self._observe_calendar(rows, now, complete)

    def _observe_calendar(self, rows: list, now: datetime, complete: bool) -> dict:
        stats = {"new": 0, "rescheduled": 0, "session": 0, "canceled": 0, "unchanged": 0, "ignored": 0}
        seen = set()
        baseline = (self.store.one("SELECT COUNT(*) n FROM events") or {}).get("n", 0) == 0        # the very first scan populates the calendar: that is not 300 "newly announced" events
        fresh: list = []
        today = today_et(now).isoformat()
        for r in rows:
            sym = (r.get("symbol") or "").upper().strip()
            d = str(r.get("date") or "")[:10]
            sess = r.get("session") if r.get("session") in SESSIONS else "UNKNOWN"
            sources = r.get("sources") or []
            if not sym or len(d) != 10 or d < today:
                stats["ignored"] += 1
                continue
            if sym in seen:
                stats["ignored"] += 1                           # a duplicate row in the same scan
                continue
            seen.add(sym)
            ver = L.verify_calendar(d, sources)
            ev = self.store.one("SELECT * FROM events WHERE symbol=? AND status='ACTIVE'", (sym,))
            if ev is None:
                done = self.store.one("SELECT id, status FROM events WHERE symbol=? AND earnings_date=? ORDER BY id DESC", (sym, d))
                if done and done["status"] == "CANCELED":
                    with self.store.tx() as c:                  # the calendar lists it again: the same event comes back, history kept
                        self._touch_event(c, done["id"], now, status="ACTIVE", canceled_at=None, last_seen=iso(now), missing_scans=0, session=sess)
                        c.execute("INSERT INTO event_versions(event_id,earnings_date,session,observed_at,reason) VALUES(?,?,?,?,?)", (done["id"], d, sess, iso(now), "REACTIVATED"))
                    self._change(now, self.event(done["id"]), sym, "NEW_EVENT", new="%s %s" % (d, sess), detail={"reactivated": True}, key_extra=iso(now))
                    self._update_verification(self.event(done["id"]), ver, sources, now)
                    stats["new"] += 1
                    continue
                if done:
                    stats["ignored"] += 1                       # that exact event already happened (reported): not re-added
                    continue
                with self.store.tx() as c:
                    eid = self._new_event(c, r, sym, d, sess, now, ver, sources)
                fresh.append((eid, self._change(now, self.event(eid), sym, "NEW_EVENT", new="%s %s" % (d, sess), detail={"verification": ver})))
                stats["new"] += 1
                continue
            changed = False
            if ev["earnings_date"] != d:
                gap = abs((date.fromisoformat(d) - date.fromisoformat(ev["earnings_date"])).days)
                if ev["earnings_date"] >= today and gap <= 60:
                    old = ev["earnings_date"]
                    with self.store.tx() as c:
                        self._touch_event(c, ev["id"], now, earnings_date=d, last_seen=iso(now), missing_scans=0)
                        c.execute("INSERT INTO event_versions(event_id,earnings_date,session,observed_at,reason) VALUES(?,?,?,?,?)", (ev["id"], d, ev["session"], iso(now), "RESCHEDULED"))
                    ev = self.event(ev["id"])
                    cid = self._change(now, ev, sym, "EVENT_RESCHEDULED", old=old, new=d)
                    if cid:
                        self._alerts_for(now, {"kind": "EVENT_RESCHEDULED", "old": old, "new": d}, ev)
                    stats["rescheduled"] += 1
                    changed = True
                else:
                    with self.store.tx() as c:
                        self._touch_event(c, ev["id"], now, status="REPORTED", reported_at=iso(now))     # the previous event's date has passed: it is history now
                    prev = ev
                    with self.store.tx() as c:
                        eid = self._new_event(c, r, sym, d, sess, now, ver, sources)
                    fresh.append((eid, self._change(now, self.event(eid), sym, "NEW_EVENT", new="%s %s" % (d, sess), detail={"previous": prev["earnings_date"], "verification": ver})))
                    stats["new"] += 1
                    continue
            if sess != "UNKNOWN" and ev["session"] != sess:
                old = ev["session"]
                with self.store.tx() as c:
                    self._touch_event(c, ev["id"], now, session=sess, last_seen=iso(now), missing_scans=0)
                    c.execute("INSERT INTO event_versions(event_id,earnings_date,session,observed_at,reason) VALUES(?,?,?,?,?)", (ev["id"], ev["earnings_date"], sess, iso(now), "SESSION_CHANGED"))
                ev = self.event(ev["id"])
                confirmed = old == "UNKNOWN"
                cid = self._change(now, ev, sym, "SESSION_CHANGED", old=old, new=sess, detail={"confirmed": confirmed})
                if cid:
                    self._alerts_for(now, {"kind": "SESSION_CHANGED", "old": old, "new": sess, "detail": {"confirmed": confirmed}}, ev)
                stats["session"] += 1
                changed = True
            if not changed:
                with self.store.tx() as c:
                    c.execute("UPDATE events SET last_seen=?, missing_scans=0, company=COALESCE(?,company) WHERE id=?", (iso(now), r.get("company"), ev["id"]))
                stats["unchanged"] += 1
            self._update_verification(self.event(ev["id"]), ver, sources, now)
        if complete:
            for ev in self.store.q("SELECT * FROM events WHERE status='ACTIVE' AND earnings_date >= ?", (today,)):
                if ev["symbol"] in seen:
                    continue
                miss = ev["missing_scans"] + 1
                if miss >= self.cfg.cancel_after_missing_scans:
                    with self.store.tx() as c:
                        self._touch_event(c, ev["id"], now, status="CANCELED", canceled_at=iso(now), missing_scans=miss)
                    ev = self.event(ev["id"])
                    cid = self._change(now, ev, ev["symbol"], "EVENT_CANCELED", old="%s %s" % (ev["earnings_date"], ev["session"]), new="CANCELED")
                    if cid:
                        self._alerts_for(now, {"kind": "EVENT_CANCELED", "old": "%s %s" % (ev["earnings_date"], ev["session"]), "new": "CANCELED"}, ev)
                    stats["canceled"] += 1
                else:
                    with self.store.tx() as c:
                        c.execute("UPDATE events SET missing_scans=? WHERE id=?", (miss, ev["id"]))
        if fresh and not baseline:
            if len(fresh) > self.cfg.new_event_alert_burst:                         # a window that suddenly widened: one audit line, not a flood of alerts
                self.store.audit(now, "monitor", "CALENDAR_EXPANSION", {"new_events": len(fresh)})
            else:
                for eid, cid in fresh:
                    if cid:
                        ev = self.event(eid)
                        self._alerts_for(now, {"kind": "NEW_EVENT", "old": None, "new": "%s %s" % (ev["earnings_date"], ev["session"]), "detail": {}}, ev)
        return stats

    # ------------------------------------------------------------ predictions
    def eligible(self, ev: dict, now: datetime) -> dict:
        reasons = []
        if ev["status"] != "ACTIVE":
            reasons.append("NOT_ACTIVE")
        if ev["session"] not in SESSIONS:
            reasons.append("SESSION_NOT_CONFIRMED")
        d = date.fromisoformat(ev["earnings_date"])
        if not CAL.is_trading_day(d):
            reasons.append("NOT_A_TRADING_DAY")
        if ev.get("verification") == "CONFLICT":
            reasons.append("CALENDAR_VENDORS_DISAGREE")
        days = (d - today_et(now)).days
        if days < 0:
            reasons.append("ALREADY_HAPPENED")
        elif days > self.cfg.model_window_days:
            reasons.append("BEYOND_%d_DAY_MODEL_WINDOW" % self.cfg.model_window_days)
        return {"eligible": not reasons, "reasons": reasons, "days_to": days}

    def recompute(self, now: datetime, event_id: Optional[int] = None, reason: Optional[str] = None) -> dict:
        """Record a new prediction row only when a model's real call actually changed; detect every change; refresh consensus. Reads caches only."""
        self._reason = reason
        models = self.ad.models()
        stats = {"events": 0, "new_predictions": 0, "changes": 0, "stale": 0, "refreshed": 0, "alerts": 0, "date_mismatch": 0}
        cov = self.ad.smart_move_coverage()
        if cov is not None and self.store.kv_get("smd:coverage") != cov:
            self.store.kv_set("smd:coverage", cov)
        if event_id is None:
            try:
                wu = self.ad.warm_universe()
                if wu and wu.get("symbols"):
                    self.store.kv_set("warm:universe", {"symbols": sorted(wu["symbols"]), "horizon_days": wu.get("horizon_days"), "size": len(wu["symbols"]), "read_at": iso(now)})
            except Exception as e:  # noqa: BLE001
                self.store.kv_set("warm:universe_error", {"at": iso(now), "error": type(e).__name__})
        full_wrep = None
        wrep = self.ad.smart_move_windows()
        if wrep is not None:
            full_wrep = wrep
            self.store.kv_set("smd:wstate", wrep.get("wstate") or {})
            wrep = {k: v for k, v in wrep.items() if k not in ("open_missing_events", "wstate")}                       # the per-event list is kept separately, not in the page payload
            self.store.kv_set("smd:windows", wrep)
            try:
                closes_in = (parse(wrep["first_cutoff_utc"]) - now).total_seconds() / 3600 if wrep.get("first_cutoff_utc") else None
            except (TypeError, ValueError):
                closes_in = None
            if closes_in is not None and 0 <= closes_in <= self.cfg.sm_window_alert_h:
                stats["alerts"] += A.enqueue(self.store, now, [A.smart_move_window(now, wrep, self.cfg)], self.cfg)
        if event_id is None:
            stats["alerts"] += self._capacity_and_collector_alerts(now, full_wrep)
        sql, args = "SELECT * FROM events WHERE status='ACTIVE'", ()
        if event_id is not None:
            sql, args = "SELECT * FROM events WHERE id=?", (event_id,)
        evs = self.store.q(sql, args)
        # state that is read for every event x model is loaded once, not queried 2,700 times
        mstate = {(r["event_id"], r["model"]): r for r in self.store.q("SELECT * FROM model_state")}
        lastp = {(r["event_id"], r["model"]): r for r in self.store.q("SELECT p.* FROM predictions p JOIN (SELECT event_id, model, MAX(id) mid FROM predictions GROUP BY event_id, model) m ON p.id = m.mid")}
        for ev in evs:
            if not self.eligible(ev, now)["eligible"] and event_id is None:
                continue
            stats["events"] += 1
            with self.store.tx():                                       # everything for one event is ONE commit
                self._recompute_event(ev, now, models, mstate, lastp, stats)
        if event_id is None and wrep is not None:                       # after the states are updated: an event that gained a result in this read is not "missed"
            self._record_missed_windows(now, full_wrep, stats)
        return stats

    def _capacity_and_collector_alerts(self, now: datetime, wrep: Optional[dict]) -> int:
        """Stage E alerts from facts the monitor can read without any provider call: the application's request count against the monitor's own guard, and the collector's run log."""
        n = 0
        try:
            used = self.ad.app_requests_today()
            if used is not None:
                n += A.enqueue(self.store, now, [A.api_capacity(now, "application requests (Unusual Whales)", int(used), int(self.cfg.hard_daily_cap), self.cfg)], self.cfg)
            col = self.ad.collector_status()
            if col:
                n += A.enqueue(self.store, now, [A.collector_failed(now, col, int((wrep or {}).get("open_without_result") or 0), self.cfg)], self.cfg)
                if col.get("attempts_today") is not None and col.get("daily_limit"):
                    n += A.enqueue(self.store, now, [A.api_capacity(now, "Smart Money Move collector (daily governor)", int(col["attempts_today"]), int(col["daily_limit"]), self.cfg)], self.cfg)
        except Exception as e:  # noqa: BLE001 - a broken status read must never stop a scan
            self.store.kv_set("alerts:capacity_error", {"at": iso(now), "error": type(e).__name__})
        return n

    MISSED_REASON = {"PENDING FINAL REFRESH": "EVIDENCE_NOT_REFRESHED_BEFORE_CUTOFF", "NO VALID PREDICTION": "EVIDENCE_MISSING_OR_INSUFFICIENT"}

    def _record_missed_windows(self, now: datetime, wrep: dict, stats: dict) -> None:
        """An event that was inside its prediction window WITHOUT a result at the previous read and is not any more, without having gained a result, missed its window for good. Recorded once, append-only, with the last
        verdict the engine gave as the reason. The engine's own rows decide: nothing is inferred from dates."""
        cur = {(x["symbol"], x["date"]): x for x in (wrep.get("open_missing_events") or [])}
        prev = {tuple(k.split("|", 1)): v for k, v in (self.store.kv_get("smd:open_missing") or {}).items()}
        missed: dict = {}
        for key, old in prev.items():
            if key in cur:
                continue
            ev = self.store.one("SELECT * FROM events WHERE symbol=? AND earnings_date=? ORDER BY id DESC", key)
            ms = self.store.one("SELECT status, signal FROM model_state WHERE event_id=? AND model='smart_move'", (ev["id"],)) if ev else None
            if ms and ms["status"] == "VALID":                                                          # it gained a result in time: not missed
                continue
            reason = self.MISSED_REASON.get(old.get("result"), "NO_RESULT_BEFORE_CLOSE")
            if ev and self._change(now, ev, key[0], "SMART_MOVE_WINDOW_MISSED", model="smart_move", old=old.get("result"), new="WINDOW_CLOSED", detail={"reason_code": reason, "cutoff": old.get("cutoff"), "event_date": key[1]}, key_extra=old.get("cutoff")):
                missed.setdefault(key[1], {}).setdefault(reason, 0)
                missed[key[1]][reason] += 1
        for dte, reasons in missed.items():
            stats["alerts"] += A.enqueue(self.store, now, [A.smart_move_missed(now, dte, sum(reasons.values()), reasons, self.cfg)], self.cfg)
            stats["changes"] += sum(reasons.values())
        self.store.kv_set("smd:open_missing", {"%s|%s" % k: {"cutoff": v.get("cutoff"), "result": v.get("result")} for k, v in cur.items()})

    def _recompute_event(self, ev: dict, now: datetime, models: dict, mstate: dict, lastp: dict, stats: dict):
        if True:
            states, stale_models = [], []
            for model in self.ad.model_labels:
                obs = (models.get(model) or {}).get(ev["symbol"])
                if obs and obs.get("event_date") and str(obs["event_date"])[:10] != ev["earnings_date"]:         # the model scored a DIFFERENT report date of this symbol: it is not a call for this event
                    obs = None
                    stats["date_mismatch"] += 1
                st = L.model_status(obs, now, self.cfg.stale_after_h)
                if obs is None and model != "smart_move" and (models.get(model) or {}):               # the model DID score other stocks but not this one: it is outside the model's universe, not "pending"
                    st = {"status": "NOT_COVERED", "stale": False}
                prev = mstate.get((ev["id"], model))
                pred_id = prev["prediction_id"] if prev else None
                src = (obs or {}).get("source") or "unknown"
                if st["status"] == "VALID":
                    h = L.input_hash(obs["signal"], obs.get("score"), obs.get("coverage"), obs.get("tier"))
                    last = lastp.get((ev["id"], model))
                    if last is None or last["input_hash"] != h:
                        old_state = None if last is None else {"signal": last["signal"], "score": last["score"], "coverage": None if last["coverage"] is None else json.loads(last["coverage"])}
                        why = ("first stored call for this event" if last is None else "the model's own call changed: %s %s -> %s %s" % (last["signal"], last["score"], obs["signal"], obs.get("score")))
                        if getattr(self, "_reason", None):
                            why += " (re-read after: %s)" % self._reason
                        prev_pred_id = None if last is None else last["id"]
                        with self.store.tx() as c:
                            pred_id = c.execute("INSERT INTO predictions(event_id,model,signal,score,coverage,tier,as_of,evidence_as_of,recorded_at,input_hash,source,engine_version,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                                (ev["id"], model, obs["signal"], obs.get("score"), None if obs.get("coverage") is None else json.dumps(obs.get("coverage")), obs.get("tier"),
                                                 obs["as_of"], obs.get("evidence_as_of"), iso(now), h, src, obs.get("engine_version"), why)).lastrowid
                        lastp[(ev["id"], model)] = {"id": pred_id, "signal": obs["signal"], "score": obs.get("score"), "coverage": None if obs.get("coverage") is None else json.dumps(obs.get("coverage")), "input_hash": h}
                        stats["new_predictions"] += 1
                        if model == "smart_move":
                            stats["alerts"] += A.enqueue(self.store, now, [A.prediction_ready(now, ev, obs, pred_id, self.cfg)], self.cfg)
                        for ch in L.prediction_changes(old_state, obs, self.cfg):
                            if self._change(now, ev, ev["symbol"], ch["kind"], model=model, old=ch["old"], new=ch["new"], detail=dict(ch["detail"], source=src, previous_prediction_id=prev_pred_id, new_prediction_id=pred_id, reason=why), key_extra=pred_id):
                                stats["changes"] += 1
                                stats["alerts"] += self._alerts_for(now, dict(ch, model=model), ev, pred_ts=obs["as_of"])
                    else:
                        pred_id = last["id"]
                    was_stale = bool(prev and prev["stale"])
                    if st["stale"] and not was_stale:
                        if self._change(now, ev, ev["symbol"], "PREDICTION_STALE", model=model, old=obs["signal"], new="STALE", detail={"age_h": st.get("age_h")}, key_extra=obs["as_of"]):
                            stats["stale"] += 1
                            stats["changes"] += 1
                            stale_models.append((model, obs["as_of"]))
                    elif was_stale and not st["stale"]:
                        if self._change(now, ev, ev["symbol"], "PREDICTION_REFRESHED", model=model, old="STALE", new=obs["signal"], detail={"age_h": st.get("age_h")}, key_extra=obs["as_of"]):
                            stats["refreshed"] += 1
                            stats["changes"] += 1
                new_row = {"event_id": ev["id"], "model": model, "status": st["status"], "signal": obs.get("signal") if obs else None, "score": obs.get("score") if obs else None,
                           "coverage": None if not obs or obs.get("coverage") is None else json.dumps(obs.get("coverage")), "as_of": obs.get("as_of") if obs else None, "prediction_id": pred_id,
                           "stale": 1 if st["stale"] else 0, "source": (obs or {}).get("source")}
                keys = ("status", "signal", "score", "coverage", "as_of", "prediction_id", "stale", "source")
                if prev is None or any(prev.get(k) != new_row[k] for k in keys):         # an unchanged row is not rewritten (the check time lives in kv below)
                    with self.store.tx() as c:
                        c.execute("INSERT INTO model_state(event_id,model,status,signal,score,coverage,as_of,prediction_id,last_checked,stale,source) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                                  "ON CONFLICT(event_id,model) DO UPDATE SET status=excluded.status, signal=excluded.signal, score=excluded.score, coverage=excluded.coverage, as_of=excluded.as_of, "
                                  "prediction_id=excluded.prediction_id, last_checked=excluded.last_checked, stale=excluded.stale, source=excluded.source",
                                  (ev["id"], model, st["status"], new_row["signal"], new_row["score"], new_row["coverage"], new_row["as_of"], pred_id, iso(now), new_row["stale"], new_row["source"]))
                    mstate[(ev["id"], model)] = dict(new_row, last_checked=iso(now))
                states.append({"model": model, "status": st["status"], "stale": st["stale"], "signal": obs.get("signal") if obs else None, "as_of": obs.get("as_of") if obs else None})
            if stale_models:                                          # ONE digest per event instead of one alert per model
                stats["alerts"] += A.enqueue(self.store, now, [A.stale_digest(now, ev, stale_models, self.cfg)], self.cfg)
            det = self.ad.smart_move_details(ev["symbol"], ev["earnings_date"])         # the engine's own row, kept so the dashboard never has to run the engine per request
            if det is not None and self.store.kv_get("smd:%d" % ev["id"]) != json.loads(json.dumps(det, default=str)):
                self.store.kv_set("smd:%d" % ev["id"], json.loads(json.dumps(det, default=str)))
            cons = L.consensus(states, self.cfg)
            key = "consensus:%d" % ev["id"]
            prev_level = self.store.kv_get(key)
            if (prev_level is not None and prev_level != cons["level"]) or (prev_level is None and cons["level"] in ("STRONG", "CONFLICT")):
                if self._change(now, ev, ev["symbol"], "AGREEMENT_CHANGED", old=prev_level or "NONE_YET", new=cons["level"], detail={"majority": cons["majority"], "comparable": cons["comparable"]}, key_extra=iso(now)):
                    stats["changes"] += 1
                    stats["alerts"] += self._alerts_for(now, {"kind": "AGREEMENT_CHANGED", "old": prev_level or "NONE_YET", "new": cons["level"], "detail": {"majority": cons["majority"], "comparable": cons["comparable"]}}, ev)
            if prev_level != cons["level"]:
                self.store.kv_set(key, cons["level"])
            days = (date.fromisoformat(ev["earnings_date"]) - today_et(now)).days
            if days <= 1 and not any(s["status"] == "VALID" for s in states):
                stats["alerts"] += A.enqueue(self.store, now, [A.missing_evidence(now, ev, None, "no model has a valid prediction for an event %s" % ("today" if days == 0 else "tomorrow"), self.cfg)], self.cfg)

    # ------------------------------------------------------------ results and outcomes
    def check_results(self, now: datetime) -> dict:
        n_res = n_out = n_unv = 0
        for ev in self.store.q("SELECT * FROM events WHERE status IN ('ACTIVE','REPORTED') AND outcome_at IS NULL AND earnings_date <= ?", (today_et(now).isoformat(),)):
            if ev["reported_at"] is None or ev["status"] == "ACTIVE":
                res = self.ad.result(ev["symbol"], ev["earnings_date"])
                if res and res.get("published_at"):
                    with self.store.tx() as c:
                        self._touch_event(c, ev["id"], now, status="REPORTED", reported_at=res["published_at"])
                    self._change(now, ev, ev["symbol"], "RESULT_PUBLISHED", new=res["published_at"])
                    n_res += 1
                    ev = self.event(ev["id"])
            if ev["status"] == "REPORTED" or now >= outcome_ready_at(self.cfg, ev):
                out = self.ad.outcome(ev["symbol"], ev["earnings_date"])
                if out and out.get("move_pct") is not None:
                    if out.get("verified") is False:                  # seen, but not trustworthy (for example a suspected split): recorded, never used for accuracy
                        if self._change(now, ev, ev["symbol"], "OUTCOME_UNVERIFIED", new="%s%%" % out["move_pct"], detail=out, key_extra=out.get("move_pct")):
                            n_unv += 1
                        continue
                    out = dict(out, verified=True)
                    with self.store.tx() as c:
                        self._touch_event(c, ev["id"], now, outcome_json=json.dumps(out, default=str), outcome_at=iso(now), status="REPORTED")
                    if self._change(now, ev, ev["symbol"], "OUTCOME_AVAILABLE", new="%s%%" % out["move_pct"], detail=out):
                        self._alerts_for(now, {"kind": "OUTCOME_AVAILABLE", "old": None, "new": "%s%%" % out["move_pct"], "detail": out}, self.event(ev["id"]))
                    n_out += 1
        return {"results_published": n_res, "outcomes_available": n_out, "outcomes_unverified": n_unv}

    # ------------------------------------------------------------ job handlers
    def calendar_refresh(self, now: datetime) -> dict:
        if self.access.state.mode == "REFRESH" and "calendar" in self.access.state.purposes:
            rows = self.access.call("calendar", C.COST_CALENDAR_REFRESH, self.ad.refresh_calendar, purpose="calendar")
            complete = True
            known = self.store.q("SELECT COUNT(*) n FROM events WHERE status='ACTIVE'")[0]["n"]
            if known >= 5 and len(rows) < 0.7 * known:                      # a half-answered calendar (one date failed) must never cancel events
                complete = False
                self.store.audit(now, "monitor", "CALENDAR_REFRESH_LOOKS_PARTIAL", {"rows": len(rows), "known_active": known})
        else:
            rows, complete = self.ad.calendar(), False
        out = self.observe_calendar(rows, now, complete)
        out["source"] = "provider_refresh" if complete else "cache"
        return out

    def eligibility_check(self, now: datetime) -> dict:
        rows = self.ad.calendar()
        obs = self.observe_calendar(rows, now, complete=False)          # picks up events that the caches show before the next calendar refresh
        rec = self.recompute(now)
        res = self.check_results(now)
        return {"observed": obs, "recompute": rec, "results": res}

    # ------------------------------------------------------------ evidence ledger
    def _flow_alert(self, it: dict) -> Optional[str]:
        d = it.get("data") or {}
        if it["kind"] != "OPTIONS_FLOW" or d.get("total_premium") is None or d.get("bullish_share") is None:
            return None
        share = float(d["bullish_share"]) / 100.0
        lean = max(share, 1 - share)
        if d["total_premium"] >= self.cfg.flow_alert_premium and lean >= self.cfg.flow_alert_share and (d.get("prints") or 0) >= self.cfg.flow_alert_min_prints:
            return "%s premium $%.1fM on %d prints, %.0f%% %s" % ("options", d["total_premium"] / 1e6, d["prints"], 100 * lean, "bullish" if share >= 0.5 else "bearish")
        return None

    def evidence_scan(self, now: datetime, kinds: Optional[tuple] = None) -> dict:
        """Cache reads only. Records what each supported evidence kind holds for every active event inside the horizon, compares with the stored copy, and records a NEW / CHANGED evidence change. A changed
        input that a model reads makes that one event's models re-read their own cached calls (never the other events, and never a call of one model copied to another). News and filings are stored and shown; they never
        change a score."""
        relevant = EV.relevance_map(self.ad.model_sources())
        model_rows = self.ad.model_rows()
        stats = {"events": 0, "items": 0, "new": 0, "changed": 0, "recomputed": 0, "alerts": 0, "errors": 0}
        evs = self.store.q("SELECT * FROM events WHERE status='ACTIVE' ORDER BY earnings_date, symbol")
        try:
            self.ad.prepare_evidence(sorted({e["symbol"] for e in evs}))
        except Exception:  # noqa: BLE001 - a failed batch read only means the per-symbol path is used
            pass
        for ev in evs:
            days = (date.fromisoformat(ev["earnings_date"]) - today_et(now)).days
            if days < 0 or days > self.cfg.horizon_days:
                continue
            try:
                items = [i for i in self.ad.evidence(ev["symbol"], ev["earnings_date"]) if kinds is None or i["kind"] in kinds]
            except Exception as e:  # noqa: BLE001
                stats["errors"] += 1
                self.store.audit(now, "monitor", "EVIDENCE_READ_FAILED", {"symbol": ev["symbol"], "error": "%s: %s" % (type(e).__name__, e)})
                continue
            stats["events"] += 1
            stats["items"] += len(items)
            res = EV.record(self.store, ev, items, now, relevant, model_rows)
            trigger = None
            for r in res:
                it, st = r["item"], r["status"]
                stats["new" if st == "NEW" else "changed"] += 1
                alert, reason = None, None
                if it["kind"] == "OPTIONS_FLOW":
                    reason = self._flow_alert(it)
                    alert = "UNUSUAL_OPTIONS_FLOW" if reason else None
                elif it["kind"] in ("NEWS", "FILING", "GUIDANCE") and (it.get("data") or {}).get("guidance_keyword"):
                    alert, reason = "NEWS_OR_GUIDANCE", "%s: %s" % ("headline mentions guidance / results", (it.get("title") or "")[:140])
                fp = EV.fingerprint(it)
                detail = {"kind": it["kind"], "provider": it["provider"], "ext_id": it["ext_id"], "fp": fp, "title": it.get("title"), "stage": "VERIFIED" if it.get("provider_structured") else "COLLECTED",
                          "relevant_models": r["relevant"], "used_by_models": r["used_by"], "alert": alert, "reason": reason, "changes_a_score": False}
                if st == "CHANGED":
                    detail["previous_fp"] = (r.get("previous") or {}).get("_fp")
                cid = self._change(now, ev, ev["symbol"], "EVIDENCE_NEW" if st == "NEW" else "EVIDENCE_CHANGED", old=detail.get("previous_fp"), new=fp, detail=detail, key_extra="%s|%s" % (it["provider"], it["ext_id"]))
                if cid and alert:
                    stats["alerts"] += self._alerts_for(now, {"kind": "EVIDENCE_NEW" if st == "NEW" else "EVIDENCE_CHANGED", "old": None, "new": fp, "detail": detail}, ev)
                if st == "CHANGED" and r["relevant"] and it["kind"] not in EV.NOT_MODEL_INPUT and trigger is None:
                    trigger = "%s evidence changed" % it["kind"]
            if trigger:
                self.recompute(now, event_id=ev["id"], reason=trigger)
                stats["recomputed"] += 1
        self.store.kv_set("evidence_scan:last" if kinds is None else "news_flow:last", dict(stats, at=iso(now)))
        return stats

    def news_flow_check(self, now: datetime) -> dict:
        """The slower news / options-tape pass: the same cache-only ledger restricted to the kinds that move during a day. Provider refresh of the underlying caches stays a separate, approved purpose."""
        return self.evidence_scan(now, kinds=("NEWS", "FILING", "GUIDANCE", "OPTIONS_FLOW", "DARK_POOL"))

    # ------------------------------------------------------------ priority-based refresh planning (LEAN baseline)
    def refresh_plan(self, now: datetime) -> dict:
        """Decides, from the budget alone and with no provider request, which events an authorised refresh could cover today and which could not. Priority: soonest report first, Before-Open before After-Close, then the event
        with the most models lacking a call, then alphabetical. Every event that does not fit is LISTED with its reason: nothing is dropped silently."""
        c, st = self.cfg, self.access.state
        cap = min(st.daily_budget, c.hard_daily_cap) if st.daily_budget else c.hard_daily_cap        # cache-only mode has no budget: plan against the hard cap to show what WOULD fit
        today = today_et(now)
        evs, outside = [], 0
        for e in self.store.q("SELECT * FROM events WHERE status='ACTIVE'"):
            el = self.eligible(e, now)
            if el["eligible"]:
                evs.append((e, el["days_to"]))
            elif el["days_to"] is not None and el["days_to"] > c.model_window_days:
                outside += 1
        have = {r["event_id"]: r["n"] for r in self.store.q("SELECT event_id, COUNT(*) n FROM model_state WHERE status='VALID' GROUP BY event_id")}

        def key(x):
            e, days = x
            return (days, e["session"] != "BMO", -(len(self.ad.model_labels) - have.get(e["id"], 0)), e["symbol"])
        ranked = sorted(evs, key=key)
        fresh_cut = timedelta(minutes=c.day_refresh_min_age_min)
        pending = [x for x in ranked if not ((self.store.kv_get("refreshed:%d" % x[0]["id"])) and now - parse(self.store.kv_get("refreshed:%d" % x[0]["id"])) < fresh_cut)]
        cal_cost = math.ceil(24 / c.calendar_every_h) * C.COST_CALENDAR_REFRESH
        out_reserve = sum(1 for e, d in ranked if d <= 1) * C.COST_OUTCOME_VERIFY
        used = self.store.usage(day_key(now))
        app_total = self.ad.app_requests_today()
        other_today = max(0, app_total - used) if app_total is not None else 0                      # what the existing warmers, Options Flow and Smart Move have ALREADY used today
        reserve = max(c.external_reserve, other_today)                                               # the shared cap is shared: what they used (or the planning reserve, if larger) is not available to the monitor
        avail = max(0, cap - reserve - cal_cost - out_reserve)
        slots = avail // C.COST_EVENT_REFRESH
        row = lambda x, i: {"symbol": x[0]["symbol"], "earnings_date": x[0]["earnings_date"], "session": x[0]["session"], "days_to": x[1], "rank": i + 1}
        funded = [row(x, i) for i, x in enumerate(pending[:slots])]
        unfunded = [dict(row(x, slots + i), reason="NOT_FUNDED_BY_DAILY_BUDGET_PLAN") for i, x in enumerate(pending[slots:])]
        plan = {"at": iso(now), "profile": "lean", "daily_cap": cap, "external_reserve": reserve, "planning_reserve": c.external_reserve, "other_requests_today": other_today, "calendar_reserve": cal_cost, "outcome_reserve": out_reserve, "available_for_events": avail,
                "cost_per_event_refresh": C.COST_EVENT_REFRESH, "event_slots": int(slots), "eligible_events": len(ranked), "needing_refresh": len(pending), "outside_model_window": outside,
                "funded": funded, "unfunded": unfunded[:300], "unfunded_count": len(unfunded), "monitor_requests_today": used, "app_requests_today": app_total,
                "note": "Planning only. A refresh runs only in REFRESH mode with the 'event' purpose approved; the plan is recomputed hourly."}
        self.store.kv_set("refresh:plan", plan)
        return {k: plan[k] for k in ("event_slots", "eligible_events", "needing_refresh", "unfunded_count", "available_for_events")}

    def event_refresh(self, now: datetime) -> dict:
        """Runs the funded part of the plan (a few events per job, one at a time through the shared gate). Does nothing unless an authorised REFRESH mode with the 'event' purpose is active."""
        st = self.access.state
        if st.mode != "REFRESH" or "event" not in st.purposes:
            return {"skipped": "NOT_IN_REFRESH_MODE_WITH_EVENT_PURPOSE", "refreshed": 0}
        self.refresh_plan(now)
        plan = self.store.kv_get("refresh:plan") or {}
        done, skipped_rows, stop = 0, [], None
        for r in plan.get("funded", []):
            if stop:
                skipped_rows.append(dict(r, reason=stop))
                continue
            if done >= self.cfg.refresh_batch:
                break
            ev = self.store.one("SELECT * FROM events WHERE symbol=? AND status='ACTIVE' ORDER BY id DESC", (r["symbol"],))
            if not ev:
                continue
            try:
                self.access.call("event", C.COST_EVENT_REFRESH, lambda s=ev["symbol"]: self.ad.refresh_event(s), purpose="event")
                self.store.kv_set("refreshed:%d" % ev["id"], iso(now))
                done += 1
                self.recompute(now, event_id=ev["id"], reason="evidence refreshed")
            except ProviderBlocked as b:
                skipped_rows.append(dict(r, reason=b.reason))
                self.store.audit(now, "monitor", "REFRESH_BLOCKED", {"symbol": ev["symbol"], "reason": b.reason})
                if b.reason.startswith(("DAILY_BUDGET_EXHAUSTED", "KILL_SWITCH", "CIRCUIT_BREAKER_OPEN")):
                    stop = b.reason
        if skipped_rows:
            self.store.kv_set("refresh:skipped", {"at": iso(now), "rows": skipped_rows})
        return {"refreshed": done, "skipped": len(skipped_rows)}

    def earnings_day_check(self, now: datetime) -> dict:
        refreshed = skipped = 0
        skipped_rows: list = []
        todays = [e for e in self.store.q("SELECT * FROM events WHERE status='ACTIVE' AND earnings_date=?", (today_et(now).isoformat(),)) if in_session_window(self.cfg, now, e)]
        todays.sort(key=lambda e: (e["session"] != "BMO", e["symbol"]))             # a stated order: stocks reporting soonest first, then alphabetical (never random)
        stop_reason = None
        for ev in todays:
            if stop_reason:                                                        # the budget / kill switch / breaker stopped refreshing: every remaining stock is listed, not dropped
                skipped += 1
                skipped_rows.append({"symbol": ev["symbol"], "session": ev["session"], "reason": stop_reason})
                continue
            last = self.store.kv_get("refreshed:%d" % ev["id"])
            if last and now - parse(last) < timedelta(minutes=self.cfg.day_refresh_min_age_min):
                skipped += 1
                continue
            if self.access.state.mode == "REFRESH":
                try:
                    self.access.call("event", C.COST_EVENT_REFRESH, lambda s=ev["symbol"]: self.ad.refresh_event(s), purpose="event")
                    self.store.kv_set("refreshed:%d" % ev["id"], iso(now))
                    refreshed += 1
                except ProviderBlocked as b:
                    skipped += 1
                    skipped_rows.append({"symbol": ev["symbol"], "session": ev["session"], "reason": b.reason})
                    self.store.audit(now, "monitor", "REFRESH_BLOCKED", {"symbol": ev["symbol"], "reason": b.reason})
                    if b.reason.startswith(("DAILY_BUDGET_EXHAUSTED", "KILL_SWITCH", "CIRCUIT_BREAKER_OPEN")):
                        stop_reason = b.reason
        if skipped_rows:
            self.store.kv_set("refresh:skipped", {"at": iso(now), "rows": skipped_rows})
        return {"refreshed": refreshed, "skipped": skipped, "skipped_symbols": [r["symbol"] for r in skipped_rows], "recompute": self.recompute(now), "results": self.check_results(now)}

    def outcome_verify(self, now: datetime, event_id: Optional[int] = None) -> dict:
        ev = self.event(event_id) if event_id else None
        if ev and now >= outcome_ready_at(self.cfg, ev) and self.access.state.mode == "REFRESH":
            try:
                self.access.call("outcome", C.COST_OUTCOME_VERIFY, lambda: self.ad.verify_outcome(ev["symbol"], ev["earnings_date"]), purpose="outcome")
            except ProviderBlocked as b:
                if b.reason.startswith(("CIRCUIT_BREAKER_OPEN", "DAILY_BUDGET_EXHAUSTED", "KILL_SWITCH")):
                    raise
                self.store.audit(now, "monitor", "OUTCOME_VERIFY_BLOCKED", {"symbol": ev["symbol"], "reason": b.reason})      # not approved / adapter declined: still read what the caches already hold
        return self.check_results(now)

    def smart_move_plan(self, now: datetime) -> dict:
        """Offline: which Smart Move evidence the eligible events still need. Records a REQUEST for the separately authorised collector; sends nothing."""
        evs = [e for e in self.store.q("SELECT * FROM events WHERE status='ACTIVE' ORDER BY earnings_date, symbol") if self.eligible(e, now)["eligible"]]
        plan = self.ad.smart_move_plan(evs, now)
        if plan is None:
            return {"available": False, "reason": "Smart Move engine / evidence store not installed on this server"}
        self.store.kv_set("smart_move:plan", dict(plan, planned_at=iso(now)))
        self.store.audit(now, "monitor", "SMART_MOVE_PLAN", {k: plan.get(k) for k in ("symbols", "requests_needed", "funded", "unfunded")})
        return dict(plan, available=True)

    def _health_alerts(self, now: datetime) -> int:
        """Raised from the light lane, so a stalled heavy lane can still be reported. Reads only the monitor's own tables and the adapter's status."""
        out = []
        try:
            last = self.store.q("SELECT MAX(finished_at) f FROM jobs WHERE kind='ELIGIBILITY_CHECK' AND status='DONE'")[0]["f"]
            if last and (now - parse(last)).total_seconds() / 3600 > self.cfg.scheduler_stall_h:
                out.append(A.infra_alert(now, "SCHEDULER_STALLED", "no eligibility scan has succeeded since %s (more than %.1f h); the scheduler may be stalled or its jobs failing" % (last, self.cfg.scheduler_stall_h), self.cfg))
            st = self.ad.infra_status() or {}
            if st.get("provider_down"):
                out.append(A.infra_alert(now, "PROVIDER_OUTAGE", "the provider circuit breaker is open: requests are paused until it recovers", self.cfg))
            if st.get("broker_ok") is False:
                out.append(A.infra_alert(now, "LEASE_BROKER_DOWN", "the shared request-lease broker is unreachable: provider calls are refused (fail closed) until it returns", self.cfg))
        except Exception as e:  # noqa: BLE001
            self.store.kv_set("alerts:health_error", {"at": iso(now), "error": type(e).__name__})
        return A.enqueue(self.store, now, out, self.cfg)

    def _provider_snapshot(self, now: datetime) -> None:
        try:
            snap = self.ad.provider_ledger()
            if snap:
                self.store.kv_set("providers:ledger", dict(snap, read_at=iso(now)))
        except Exception as e:  # noqa: BLE001 - a broken ledger read must never stop a scan
            self.store.kv_set("providers:ledger_error", {"at": iso(now), "error": type(e).__name__})

    def alert_dispatch(self, now: datetime) -> dict:
        self._provider_snapshot(now)
        self._health_alerts(now)
        return A.dispatch(self.store, now, self.sinks, self.cfg)
