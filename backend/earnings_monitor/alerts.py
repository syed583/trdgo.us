"""Alerts: a configurable rule per kind, deduplicated, noise-limited, delivered only to explicit sinks (an outbox file by default). Every alert names the model, stock, event, prediction timestamp and the reason.
No sink here sends a message to anyone: external delivery (e-mail, chat) would be a separate, owner-approved integration plugged into dispatch().

Noise control, all auditable (a suppressed alert is still stored, with the reason):
  * the same content is never inserted twice (dedupe key);
  * COOLDOWN: the same rule for the same event and model is not repeated inside alert_cooldown_min;
  * FLAPPING: a model that changes its call flap_changes_24h times in 24 h is reported once as unstable, then its further direction alerts are suppressed;
  * stale calls are ONE digest per event, not one alert per model;
  * HOURLY_CAP: past alert_hourly_cap ordinary alerts in an hour the rest are suppressed and summarised in a single alert. Scanner failures are never capped."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from .store import Store, iso

COOLDOWN_RULES = ("DIRECTION_CHANGE", "STRONG_AGREEMENT", "MODEL_DISAGREEMENT", "STALE_OR_MISSING_EVIDENCE", "NEWS_OR_GUIDANCE", "UNUSUAL_OPTIONS_FLOW")
NEVER_CAPPED = ("SCANNER_FAILURE", "ALERT_SUMMARY", "COLLECTOR_FAILED")


def _key(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:24]


def build(change: dict, event: dict, pred_ts: Optional[str], cfg, extra: Optional[dict] = None) -> list:
    """Alert candidates for one detected change. change keys: kind, model, old, new, detail."""
    rules = cfg.alert_rules
    kind, model, new, old = change["kind"], change.get("model"), change.get("new"), change.get("old")
    base = {"symbol": event["symbol"], "company": event.get("company"), "earnings_date": event["earnings_date"], "session": event["session"], "model": model, "prediction_ts": pred_ts, "old": old, "new": new}
    out = []

    def add(rule, reason, dedupe):
        if rules.get(rule, {}).get("enabled", True):
            out.append({"rule": rule, "event_id": event["id"], "symbol": event["symbol"], "model": model, "dedupe_key": _key(rule, event["id"], model, dedupe), "payload": dict(base, rule=rule, reason=reason, **(extra or {}))})

    if kind == "NEW_PREDICTION" and new in ("BUY", "SELL"):
        add("NEW_BUY_SELL", "%s published a new %s prediction" % (model, new), (new, pred_ts))
    elif kind == "SIGNAL_CHANGED" and (old in ("BUY", "SELL") or new in ("BUY", "SELL")):
        add("DIRECTION_CHANGE", "%s changed from %s to %s (%s)" % (model, old, new, (change.get("detail") or {}).get("subtype", "")), (old, new, pred_ts))
    elif kind == "AGREEMENT_CHANGED" and new == "STRONG":
        add("STRONG_AGREEMENT", "strong agreement across comparable models: %s" % (change.get("detail") or {}).get("majority"), ((change.get("detail") or {}).get("majority"), (change.get("detail") or {}).get("comparable")))
    elif kind == "NEW_EVENT" and not (change.get("detail") or {}).get("reactivated") and not (change.get("detail") or {}).get("baseline"):
        add("NEW_EARNINGS", "newly announced earnings: %s" % new, ("new", new))
    elif kind == "AGREEMENT_CHANGED" and new == "CONFLICT":
        add("MODEL_DISAGREEMENT", "comparable models now disagree (BUY and SELL both present)", ("conflict", (change.get("detail") or {}).get("comparable")))
    elif kind == "OUTCOME_AVAILABLE":
        add("OUTCOME_VERIFIED", "the real post-earnings reaction is verified: %s" % new, ("outcome", new))
    elif kind in ("EVIDENCE_NEW", "EVIDENCE_CHANGED") and (change.get("detail") or {}).get("alert") == "NEWS_OR_GUIDANCE":
        add("NEWS_OR_GUIDANCE", (change.get("detail") or {}).get("reason") or "significant news or guidance evidence", ((change.get("detail") or {}).get("ext_id"), (change.get("detail") or {}).get("fp")))
    elif kind in ("EVIDENCE_NEW", "EVIDENCE_CHANGED") and (change.get("detail") or {}).get("alert") == "UNUSUAL_OPTIONS_FLOW":
        add("UNUSUAL_OPTIONS_FLOW", (change.get("detail") or {}).get("reason") or "unusual options flow", ((change.get("detail") or {}).get("fp"),))
    elif kind in ("EVENT_RESCHEDULED", "SESSION_CHANGED", "EVENT_CANCELED"):
        add("DATE_CHANGE", "earnings %s: %s -> %s" % ({"EVENT_RESCHEDULED": "date changed", "SESSION_CHANGED": "session changed", "EVENT_CANCELED": "event canceled"}[kind], old, new), (kind, old, new))
    elif kind == "VERIFICATION_CHANGED" and new == "CONFLICT":
        add("DATE_CHANGE", "calendar vendors disagree about this event's date", ("verification", new))
    return out


def stale_digest(now: datetime, event: dict, stale: list, cfg) -> Optional[dict]:
    """One alert per event listing every model whose call has gone stale in this scan (stale = list of (model, as_of))."""
    if not cfg.alert_rules.get("STALE_OR_MISSING_EVIDENCE", {}).get("enabled", True) or not stale:
        return None
    models = sorted(m for m, _ in stale)
    return {"rule": "STALE_OR_MISSING_EVIDENCE", "event_id": event["id"], "symbol": event["symbol"], "model": None, "dedupe_key": _key("STALE_DIGEST", event["id"], ",".join(models), max(t for _, t in stale)),
            "payload": {"rule": "STALE_OR_MISSING_EVIDENCE", "symbol": event["symbol"], "earnings_date": event["earnings_date"], "session": event["session"], "model": None, "models": models,
                        "prediction_ts": max(t for _, t in stale), "reason": "%d model call(s) are stale for %s: %s" % (len(models), event["symbol"], ", ".join(models))}}


def scanner_failure(now: datetime, job_kind: str, error: str, cfg, event_id=None) -> Optional[dict]:
    if not cfg.alert_rules.get("SCANNER_FAILURE", {}).get("enabled", True):
        return None
    return {"rule": "SCANNER_FAILURE", "event_id": event_id, "symbol": None, "model": None, "dedupe_key": _key("SCANNER_FAILURE", job_kind, error[:80], now.strftime("%Y-%m-%d-%H")),
            "payload": {"rule": "SCANNER_FAILURE", "reason": "scanner job %s failed: %s" % (job_kind, error[:200]), "job": job_kind, "detected_at": iso(now), "prediction_ts": None, "model": None}}


def approval_expiring(now: datetime, expires_at: str, cfg) -> Optional[dict]:
    """One alert a day while the owner's approval is about to end (when it does, the monitor stops: nothing is renewed automatically)."""
    if not cfg.alert_rules.get("APPROVAL_EXPIRING", {}).get("enabled", True):
        return None
    return {"rule": "APPROVAL_EXPIRING", "event_id": None, "symbol": None, "model": None, "dedupe_key": _key("APPROVAL_EXPIRING", expires_at, now.strftime("%Y-%m-%d")),
            "payload": {"rule": "APPROVAL_EXPIRING", "reason": "the monitor approval ends %s; the scheduler stops until a new approval is signed" % expires_at, "expires_at": expires_at, "detected_at": iso(now), "prediction_ts": None, "model": None}}


def smart_move_window(now: datetime, rep: dict, cfg) -> Optional[dict]:
    """One alert per closing time while events are inside their prediction window with no usable result: after the window closes the frozen rules allow no prediction, so this is the last chance to collect evidence."""
    if not cfg.alert_rules.get("SMART_MOVE_WINDOW", {}).get("enabled", True) or not rep.get("open_without_result") or not rep.get("first_cutoff_utc"):
        return None
    dates = ", ".join("%s (%d)" % (d, n) for d, n in sorted((rep.get("missing_by_date") or {}).items()))
    return {"rule": "SMART_MOVE_WINDOW", "event_id": None, "symbol": None, "model": "smart_move", "dedupe_key": _key("SM_WINDOW", rep["first_cutoff_utc"][:13]),
            "payload": {"rule": "SMART_MOVE_WINDOW", "model": "smart_move", "prediction_ts": None, "reason": "%d event(s) are inside the Smart Money Move prediction window with no result yet (%s); the first window closes at %s. After that no prediction is allowed." % (rep["open_without_result"], dates, rep["first_cutoff_utc"])}}


def smart_move_missed(now: datetime, date_: str, n: int, reasons: dict, cfg) -> Optional[dict]:
    """One digest per market date: events whose Smart Money Move prediction window closed with no result, and why."""
    if not cfg.alert_rules.get("SMART_MOVE_WINDOW", {}).get("enabled", True) or n <= 0:
        return None
    why = ", ".join("%s %d" % (k, v) for k, v in sorted(reasons.items()))
    return {"rule": "SMART_MOVE_WINDOW", "event_id": None, "symbol": None, "model": "smart_move", "dedupe_key": _key("SM_MISSED", date_, n),
            "payload": {"rule": "SMART_MOVE_WINDOW", "model": "smart_move", "prediction_ts": None, "reason": "%d event(s) dated %s left their Smart Money Move prediction window with no result (%s). No prediction can be made for them any more." % (n, date_, why)}}


def prediction_ready(now: datetime, event: dict, obs: dict, pred_id: int, cfg) -> Optional[dict]:
    """A NEW live Smart Money Move call (the frozen engine, not a stored snapshot) is ready, with the time of the evidence behind it and the engine version."""
    if not cfg.alert_rules.get("PREDICTION_READY", {}).get("enabled", True) or obs.get("source") != "smart_move_engine" or obs.get("signal") not in ("BUY", "SELL", "NO_TRADE"):
        return None
    return {"rule": "PREDICTION_READY", "event_id": event["id"], "symbol": event["symbol"], "model": "smart_move", "dedupe_key": _key("PREDICTION_READY", pred_id),
            "payload": {"rule": "PREDICTION_READY", "symbol": event["symbol"], "earnings_date": event["earnings_date"], "session": event["session"], "model": "smart_move", "signal": obs["signal"], "coverage": obs.get("coverage"),
                        "prediction_ts": obs.get("as_of"), "evidence_as_of": obs.get("evidence_as_of"), "engine_version": obs.get("engine_version"), "prediction_id": pred_id,
                        "reason": "%s %s %s: Smart Money Move prediction ready: %s (coverage %s, evidence as of %s, %s)" % (event["symbol"], event["earnings_date"], event["session"], obs["signal"], obs.get("coverage"), obs.get("evidence_as_of"), obs.get("engine_version"))}}


def collector_failed(now: datetime, col: dict, windows_open: int, cfg) -> Optional[dict]:
    """The scheduled collector (separate from the monitor) failed, was blocked, or has not finished a run while prediction windows are open. `col` comes from the collector's own run log."""
    if not cfg.alert_rules.get("COLLECTOR_FAILED", {}).get("enabled", True) or not col:
        return None
    why = None
    if col.get("last_exit") not in (None, 0) and (col.get("last_success_at") or "") < (col.get("last_finished_at") or ""):
        why = "the last collector run ended with exit code %s at %s" % (col["last_exit"], col.get("last_finished_at"))
    elif col.get("blocked_recent"):
        why = "the collector was blocked: %s" % col["blocked_recent"]
    elif windows_open > 0 and col.get("hours_since_finished") is not None and col["hours_since_finished"] > cfg.collector_stale_h:
        why = "no collector run has finished for %.1f h while %d event(s) are inside their prediction window" % (col["hours_since_finished"], windows_open)
    if not why:
        return None
    return {"rule": "COLLECTOR_FAILED", "event_id": None, "symbol": None, "model": "smart_move", "dedupe_key": _key("COLLECTOR_FAILED", why[:60], now.strftime("%Y-%m-%d-%H")),
            "payload": {"rule": "COLLECTOR_FAILED", "model": "smart_move", "prediction_ts": None, "detected_at": iso(now), "reason": why}}


def api_capacity(now: datetime, what: str, used: int, limit: int, cfg) -> Optional[dict]:
    """Warn at the configured share of an INTERNAL limit (the monitor guard, the collector daily governor); again at 100%. One alert per day and level. The provider has no daily limit."""
    if not cfg.alert_rules.get("API_CAPACITY", {}).get("enabled", True) or not limit or used < cfg.capacity_warn_share * limit:
        return None
    level = 100 if used >= limit else int(cfg.capacity_warn_share * 100)
    return {"rule": "API_CAPACITY", "event_id": None, "symbol": None, "model": None, "dedupe_key": _key("API_CAPACITY", what, level, now.strftime("%Y-%m-%d")),
            "payload": {"rule": "API_CAPACITY", "model": None, "prediction_ts": None, "detected_at": iso(now), "what": what, "used": used, "limit": limit,
                        "reason": "%s: %d of the internal limit %d used today (%d%%). This is the owner's configured limit, not the provider's." % (what, used, limit, round(100 * used / limit))}}


def missing_evidence(now: datetime, event: dict, model: Optional[str], why: str, cfg) -> Optional[dict]:
    """At most one alert per event/model/day for evidence that is missing near the event."""
    if not cfg.alert_rules.get("STALE_OR_MISSING_EVIDENCE", {}).get("enabled", True):
        return None
    return {"rule": "STALE_OR_MISSING_EVIDENCE", "event_id": event["id"], "symbol": event["symbol"], "model": model, "dedupe_key": _key("MISSING", event["id"], model, now.strftime("%Y-%m-%d")),
            "payload": {"rule": "STALE_OR_MISSING_EVIDENCE", "symbol": event["symbol"], "earnings_date": event["earnings_date"], "session": event["session"], "model": model, "prediction_ts": None, "reason": why}}


def _insert(store: Store, now: datetime, a: dict, status: str = "PENDING", detail: Optional[str] = None) -> bool:
    try:
        with store.tx() as c:
            c.execute("INSERT INTO alerts(rule,event_id,symbol,model,payload,dedupe_key,created_at,status,detail) VALUES(?,?,?,?,?,?,?,?,?)",
                      (a["rule"], a.get("event_id"), a.get("symbol"), a.get("model"), json.dumps(dict(a["payload"], created_at=iso(now)), default=str), a["dedupe_key"], iso(now), status, detail))
        return True
    except Exception as e:  # noqa: BLE001
        if "UNIQUE" in str(e):
            return False
        raise


def _suppression(store: Store, now: datetime, a: dict, cfg) -> Optional[str]:
    """The reason an otherwise new alert should be held back, or None."""
    if a["rule"] in NEVER_CAPPED:
        return None
    if a["rule"] in COOLDOWN_RULES and a.get("event_id") is not None:
        since = iso(now - timedelta(minutes=cfg.alert_cooldown_min))
        r = store.one("SELECT 1 FROM alerts WHERE rule=? AND event_id=? AND IFNULL(model,'')=IFNULL(?,'') AND status IN ('PENDING','DELIVERED') AND created_at >= ?", (a["rule"], a["event_id"], a.get("model"), since))
        if r:
            return "COOLDOWN (same rule, event and model within %d min)" % cfg.alert_cooldown_min
    if a["rule"] == "DIRECTION_CHANGE" and a.get("event_id") is not None and a.get("model"):
        n = store.one("SELECT COUNT(*) n FROM changes WHERE event_id=? AND model=? AND kind='SIGNAL_CHANGED' AND detected_at >= ?", (a["event_id"], a["model"], iso(now - timedelta(hours=24))))["n"]
        if n >= cfg.flap_changes_24h:
            return "FLAPPING (%d signal changes in 24 h)" % n
    n = store.one("SELECT COUNT(*) n FROM alerts WHERE status IN ('PENDING','DELIVERED') AND rule NOT IN ('SCANNER_FAILURE','ALERT_SUMMARY') AND created_at >= ?", (iso(now - timedelta(hours=1)),))["n"]
    if n >= cfg.alert_hourly_cap:
        return "HOURLY_CAP (%d alerts in the last hour)" % cfg.alert_hourly_cap
    return None


def enqueue(store: Store, now: datetime, alerts: list, cfg=None) -> int:
    """Insert candidates. An alert whose dedupe key already exists is dropped; one held back by cooldown / flapping / the hourly cap is stored as SUPPRESSED with the reason. Returns how many became PENDING."""
    n = 0
    for a in alerts:
        if not a:
            continue
        why = _suppression(store, now, a, cfg) if cfg is not None else None
        if why is None:
            n += 1 if _insert(store, now, a) else 0
            continue
        if _insert(store, now, a, "SUPPRESSED", why):
            if why.startswith("FLAPPING"):
                _insert(store, now, {"rule": "DIRECTION_CHANGE", "event_id": a.get("event_id"), "symbol": a.get("symbol"), "model": a.get("model"), "dedupe_key": _key("FLAP", a.get("event_id"), a.get("model"), now.strftime("%Y-%m-%d")),
                                     "payload": {"rule": "DIRECTION_CHANGE", "symbol": a.get("symbol"), "model": a.get("model"), "prediction_ts": a["payload"].get("prediction_ts"), "reason": "%s keeps changing its call for %s; further direction alerts for it are held back today" % (a.get("model"), a.get("symbol"))}})
            if why.startswith("HOURLY_CAP"):
                _insert(store, now, {"rule": "ALERT_SUMMARY", "event_id": None, "symbol": None, "model": None, "dedupe_key": _key("SUMMARY", now.strftime("%Y-%m-%d-%H")),
                                     "payload": {"rule": "ALERT_SUMMARY", "prediction_ts": None, "model": None, "reason": "more than %d alerts in an hour: the rest were held back (see the alert list, status SUPPRESSED)" % cfg.alert_hourly_cap}})
    return n


class FileSink:
    """Appends each delivered alert as one JSON line to an outbox file. Delivery = written to the outbox; nothing leaves the machine."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, alert: dict):
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(alert, sort_keys=True, default=str) + "\n")


def dispatch(store: Store, now: datetime, sinks: list, cfg, limit: int = 100) -> dict:
    done = failed = suppressed = 0
    for r in store.q("SELECT * FROM alerts WHERE status='PENDING' ORDER BY id LIMIT ?", (limit,)):
        payload = json.loads(r["payload"])
        if not cfg.alert_rules.get(r["rule"], {}).get("enabled", True) and r["rule"] != "ALERT_SUMMARY":
            status, detail = "SUPPRESSED", "rule disabled"
            suppressed += 1
        else:
            try:
                for s in sinks:
                    s(dict(payload, alert_id=r["id"]))
                status, detail = "DELIVERED", None
                done += 1
            except Exception as e:  # noqa: BLE001
                status, detail = "PENDING", "sink error: %s" % e
                failed += 1
        with store.tx() as c:
            c.execute("UPDATE alerts SET status=?, delivered_at=?, detail=? WHERE id=?", (status, iso(now) if status != "PENDING" else None, detail, r["id"]))
    return {"delivered": done, "failed": failed, "suppressed": suppressed}
