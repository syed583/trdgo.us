"""Pure logic, no I/O: signal vocabulary, validity, coverage normalisation, consensus among genuinely comparable models, and the change-detection rules.

A model's own signal is always kept as it came (BUY / SELL / NEUTRAL for the direction models, BUY / SELL / NO_TRADE for Smart Move score40-v1). Nothing is translated from one model into another,
and anything that is not a real, scored call (PENDING, LOW_DATA, NO_VALID_PREDICTION, missing) is NOT a prediction and is never stored as one."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from .store import parse

UTC = timezone.utc
VALID_SIGNALS = ("BUY", "SELL", "NEUTRAL", "NO_TRADE")
DIRECTIONAL = ("BUY", "SELL")
CHANGE_KINDS = ("NEW_EVENT", "EVENT_RESCHEDULED", "SESSION_CHANGED", "EVENT_CANCELED", "VERIFICATION_CHANGED", "NEW_PREDICTION", "SIGNAL_CHANGED", "AGREEMENT_CHANGED", "COVERAGE_IMPROVED",
                "COVERAGE_DETERIORATED", "PREDICTION_STALE", "PREDICTION_REFRESHED", "RESULT_PUBLISHED", "OUTCOME_AVAILABLE", "OUTCOME_UNVERIFIED")


def direction_class(signal: Optional[str]) -> Optional[str]:
    return {"BUY": "LONG", "SELL": "SHORT", "NEUTRAL": "FLAT", "NO_TRADE": "FLAT"}.get(signal or "")


def coverage_fraction(v) -> Optional[float]:
    """0..1 from the shapes the models use: a fraction, a percentage, 'a/b', {'available','max'} or points/max. None when unknown (never guessed)."""
    try:
        if v is None or isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            f = float(v)
            return f if 0 <= f <= 1 else (f / 100.0 if 1 < f <= 100 else None)
        if isinstance(v, str) and "/" in v:
            a, b = v.split("/", 1)
            return float(a) / float(b) if float(b) > 0 else None
        if isinstance(v, str):
            return coverage_fraction(float(v.strip().rstrip("%")))
        if isinstance(v, dict):
            a = v.get("available", v.get("points"))
            b = v.get("max")
            return float(a) / float(b) if a is not None and b else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return None


def input_hash(signal, score, coverage, tier) -> str:
    return hashlib.sha256(json.dumps([signal, None if score is None else round(float(score), 1), coverage, tier], sort_keys=True, default=str).encode()).hexdigest()[:20]


def model_status(obs: Optional[dict], now: datetime, stale_after_h: float) -> dict:
    """VALID / PENDING / INSUFFICIENT_DATA / ABSENT plus a stale flag, from one raw model observation."""
    if not obs:
        return {"status": "ABSENT", "stale": False}
    sig = obs.get("signal")
    if sig in VALID_SIGNALS and obs.get("as_of"):
        age = (now - parse(obs["as_of"])).total_seconds() / 3600
        return {"status": "VALID", "stale": age > stale_after_h, "age_h": round(age, 2)}
    if sig in (None, "PENDING"):
        return {"status": "PENDING", "stale": False}
    return {"status": "INSUFFICIENT_DATA", "stale": False}                      # LOW_DATA, NO_VALID_PREDICTION, ...


def consensus(states: list, cfg) -> dict:
    """Agreement among models that are genuinely comparable: a VALID, fresh call on the same event, built within comparable_max_spread_h of the newest one."""
    valid = [s for s in states if s.get("status") == "VALID" and not s.get("stale") and s.get("signal") in VALID_SIGNALS and s.get("as_of")]
    if valid:
        newest = max(parse(s["as_of"]) for s in valid)
        comp = [s for s in valid if (newest - parse(s["as_of"])) <= timedelta(hours=cfg.comparable_max_spread_h)]
    else:
        comp = []
    excluded = sorted({s["model"] for s in states} - {s["model"] for s in comp})
    n = len(comp)
    buy = sorted(s["model"] for s in comp if s["signal"] == "BUY")
    sell = sorted(s["model"] for s in comp if s["signal"] == "SELL")
    flat = sorted(s["model"] for s in comp if s["signal"] in ("NEUTRAL", "NO_TRADE"))
    if n < cfg.strong_agreement_min_models:
        level, majority = "INSUFFICIENT", None
    elif buy and sell:
        level, majority = "CONFLICT", None
    else:
        top, name = (buy, "BUY") if len(buy) >= len(sell) else (sell, "SELL")
        if top and len(top) / n >= cfg.strong_agreement_share:
            level, majority = "STRONG", name
        elif top:
            level, majority = "WEAK", name
        else:
            level, majority = "NONE", None                                        # everyone comparable is flat
    return {"level": level, "majority": majority, "comparable": n, "buy": buy, "sell": sell, "flat": flat, "excluded": excluded, "total_models": len(states)}


def classify_signal_change(old: str, new: str) -> str:
    if {old, new} == {"BUY", "SELL"}:
        return "REVERSAL"
    if old in DIRECTIONAL and new in ("NEUTRAL", "NO_TRADE"):
        return "DIRECTION_LOST"
    if old in ("NEUTRAL", "NO_TRADE") and new in DIRECTIONAL:
        return "DIRECTION_GAINED"
    return "OTHER"


def prediction_changes(old: Optional[dict], new: dict, cfg) -> list:
    """Changes between a model's previous recorded state and its new VALID observation. old is None the first time a valid call appears."""
    out = []
    if old is None or old.get("signal") not in VALID_SIGNALS:
        return [{"kind": "NEW_PREDICTION", "old": None, "new": new["signal"], "detail": {"score": new.get("score")}}]
    if old["signal"] != new["signal"]:
        out.append({"kind": "SIGNAL_CHANGED", "old": old["signal"], "new": new["signal"], "detail": {"subtype": classify_signal_change(old["signal"], new["signal"]), "score_old": old.get("score"), "score_new": new.get("score")}})
    a, b = coverage_fraction(old.get("coverage")), coverage_fraction(new.get("coverage"))
    if a is not None and b is not None:
        if b - a >= cfg.coverage_delta:
            out.append({"kind": "COVERAGE_IMPROVED", "old": round(a, 3), "new": round(b, 3), "detail": {}})
        elif a - b >= cfg.coverage_delta:
            out.append({"kind": "COVERAGE_DETERIORATED", "old": round(a, 3), "new": round(b, 3), "detail": {}})
    return out


def verify_calendar(event_date: str, sources: list) -> str:
    """CONFIRMED when at least two independent vendors list the event on the same date, CONFLICT when any vendor lists a different date, SINGLE_VENDOR when only one does, UNVERIFIED when none is known.
    A vendor that says nothing counts for nothing: absence is not agreement."""
    by = {}
    for o in sources or []:
        v, d = (o.get("vendor") or "").upper(), str(o.get("date") or "")[:10]
        if v and d:
            by[v] = d
    if not by:
        return "UNVERIFIED"
    if any(d != event_date for d in by.values()):
        return "CONFLICT"
    return "CONFIRMED" if len(by) >= 2 else "SINGLE_VENDOR"


def display_status(state: dict) -> str:
    """The label the dashboard shows for one model on one event. NO_TRADE (a real, scored call that says do nothing) and NO_VALID_PREDICTION (no usable call at all) are never the same thing."""
    st, sig = state.get("status"), state.get("signal")
    if st == "VALID":
        if state.get("stale"):
            return "STALE"
        return "NO_TRADE" if sig == "NO_TRADE" else sig
    if st == "PENDING":
        return "PENDING"
    if st == "INSUFFICIENT_DATA":
        return "NO_VALID_PREDICTION" if sig == "NO_VALID_PREDICTION" else "LOW_DATA"
    return "PENDING" if st == "ABSENT" else "NO_VALID_PREDICTION"
