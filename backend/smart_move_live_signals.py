"""Live earnings signals: normalisation, coverage and the idempotent post-batch job (offline; no provider code, no scoring of its own).

What it does and does NOT do
* It never computes a score. Each signal comes from an EXISTING model: the frozen Smart Move engine (continuation / contrarian, read from the saved batch store) and, when rows are supplied, the production scan models
  (Pre-Earnings Direction, Claude Last, Grok, Meta) exactly as their own scan endpoints returned them. It only maps vocabularies, attaches provenance and labels freshness.
* Display rule, a DESIGN CHOICE that needs owner approval (it is a filter, not a score): a row shows BUY or SELL only when every model with valid inputs agrees; otherwise NO TRADE with the reason.
* NO TRADE is split into three meanings so they are never confused: INSUFFICIENT_INPUTS (a model could not score: missing/blocked data, LOW_DATA, PENDING, unknown timing), MODEL_NEUTRAL (a model scored and found no edge), MODELS_DISAGREE
  (valid models point different ways). A row with no valid model is "no valid prediction", never a recommendation.
* Confidence is always null: no existing model has a calibrated probability, so none is shown. Raw scores are shown as scores, not confidence.
* A row is LIVE only when it comes from a validated, sealed batch (smart_move_batch_marker) or from a production scan row that carries its own update time, is recent, and the event has not happened yet. Everything else is labelled
  STALE, HISTORICAL or UNVERIFIED, and synthetic fixtures are labelled SYNTHETIC.
* The job needs an explicit completion marker (never "a file appeared"), re-verifies it, appends rows once (dedupe key) and records every failure; missing data is never turned into a trade."""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smart_move_batch_marker as BM  # noqa: E402

UTC = timezone.utc
SCHEMA = "smart_move.live_signal.v1"
LIVE_MAX_AGE_H = 36.0
SESSION_LABEL = {"BMO": "Before Open", "AMC": "After Close"}
SIGNALS = ("BUY", "SELL", "NO_TRADE")
# production scan vocabulary -> (signal, kind). NEUTRAL is a real model output; LOW_DATA / PENDING mean the model had nothing valid to say.
PROD_MAP = {"BUY": ("BUY", None), "SELL": ("SELL", None), "NEUTRAL": ("NO_TRADE", "MODEL_NEUTRAL"), "NO TRADE": ("NO_TRADE", "MODEL_NEUTRAL"), "NO_TRADE": ("NO_TRADE", "MODEL_NEUTRAL"),
            "LOW_DATA": ("NO_TRADE", "INSUFFICIENT_INPUTS"), "LOW DATA": ("NO_TRADE", "INSUFFICIENT_INPUTS"), "PENDING": ("NO_TRADE", "INSUFFICIENT_INPUTS")}
REASON_TEXT = {"UNKNOWN_TIMING": "the announcement session (before open / after close) is not confirmed", "NOT_TRADABLE": "the stock failed the tradability check", "ILLIQUID_OR_UNKNOWN": "liquidity is unknown or too low",
               "INSUFFICIENT_COVERAGE": "not enough of the model's required inputs are available", "OUT_OF_WINDOW": "the event is outside its 48 h to 24 h prediction window",
               "CALENDAR_ESTIMATED": "the earnings date is only estimated, not confirmed", "SINGLE_VENDOR": "only one calendar vendor reports this event", "STALE_CALENDAR": "the calendar evidence is too old"}


def _dt(x) -> datetime:
    d = x if isinstance(x, datetime) else datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _reasons(codes) -> str:
    return "; ".join(REASON_TEXT.get(c, c.replace("_", " ").lower()) for c in codes)


# ------------------------------------------------------------------ model adapters (map only; never score)
def smart_move_models(row: dict) -> list:
    """The frozen engine's two variants for one live-scorer row (smart_move_live.LiveScorer.score_all)."""
    out = []
    blocked = list(row.get("blocked_by") or [])
    for name, e in (row.get("engine") or {}).items():
        codes = list(dict.fromkeys(list(e.get("reason_codes") or []) + blocked))
        sig = e.get("signal")
        if sig in ("BUY", "SELL"):
            out.append({"name": "smart_move_" + name, "signal": sig, "kind": None, "score": None, "points": {"bull": e.get("bull"), "bear": e.get("bear"), "available": e.get("available"), "max": e.get("max")}, "reason_codes": codes, "source": "SMART_MOVE_FROZEN_ENGINE", "primary": name == "continuation"})
        else:
            kind = "INSUFFICIENT_INPUTS" if codes else "MODEL_NEUTRAL"
            out.append({"name": "smart_move_" + name, "signal": "NO_TRADE", "kind": kind, "score": None, "points": {"bull": e.get("bull"), "bear": e.get("bear"), "available": e.get("available"), "max": e.get("max")},
                        "reason_codes": codes, "source": "SMART_MOVE_FROZEN_ENGINE", "primary": name == "continuation"})
    return out


def production_model(name: str, scan_row: dict) -> Optional[dict]:
    """One production scan row (as /pre-earnings-direction/scan, /claude-last/scan, /grok/scan, /meta/scan return it) -> a model entry. Claude Last's implied-move gate downgrade is kept visible."""
    raw = str(scan_row.get("signal") or "").upper()
    if raw not in PROD_MAP:
        return None
    sig, kind = PROD_MAP[raw]
    codes = []
    if scan_row.get("raw_signal") in ("BUY", "SELL") and sig == "NO_TRADE":
        kind, codes = "GATED", ["IMPLIED_MOVE_GATE"]
    if raw in ("LOW_DATA", "LOW DATA", "PENDING"):
        codes = [raw.replace(" ", "_")]
    return {"name": name, "signal": sig, "kind": kind, "score": scan_row.get("score"), "points": None, "coverage": scan_row.get("coverage"), "reason_codes": codes, "source": "PRODUCTION_SCAN", "primary": True,
            "updated_at": scan_row.get("held_at") or scan_row.get("updated_at")}


def consensus(models: list) -> dict:
    """Counts over PRIMARY models only. The Smart Move contrarian variant is a separate pre-registered hypothesis that is shown but never mixed into the headline."""
    models = [m for m in models if m.get("primary", True)]
    valid = [m for m in models if m["kind"] not in ("INSUFFICIENT_INPUTS",)]
    c = Counter(m["signal"] for m in valid)
    return {"models_total": len(models), "models_valid": len(valid), "buy": c.get("BUY", 0), "sell": c.get("SELL", 0), "no_trade": c.get("NO_TRADE", 0),
            "unanimous": bool(valid) and len({m["signal"] for m in valid}) == 1, "rule": "BUY or SELL is shown only when every model with valid inputs agrees (display filter, not a score)"}


def headline(models: list) -> tuple:
    """-> (signal, kind, reason). Never BUY/SELL without a valid, unanimous model set."""
    cons = consensus(models)
    models = [m for m in models if m.get("primary", True)]
    if not models:
        return "NO_TRADE", "NO_VALID_PREDICTION", "no model produced an output for this event"
    if not cons["models_valid"]:
        codes = list(dict.fromkeys(c for m in models for c in m["reason_codes"]))
        return "NO_TRADE", "INSUFFICIENT_INPUTS", "no model has enough valid inputs: " + (_reasons(codes) or "inputs missing")
    valid = [m for m in models if m["kind"] != "INSUFFICIENT_INPUTS"]
    sigs = {m["signal"] for m in valid}
    if len(sigs) > 1:
        return "NO_TRADE", "MODELS_DISAGREE", "valid models disagree (%d BUY, %d SELL, %d no trade)" % (cons["buy"], cons["sell"], cons["no_trade"])
    s = next(iter(sigs))
    if s in ("BUY", "SELL"):
        return s, "MODEL_SIGNAL", "%d of %d valid model(s) say %s%s" % (cons["models_valid"], cons["models_total"], s, "" if cons["models_valid"] > 1 else " (single model)")
    kinds = {m["kind"] for m in valid}
    if "GATED" in kinds:
        return "NO_TRADE", "GATED", "a model's own risk gate downgraded its signal to no trade"
    return "NO_TRADE", "MODEL_NEUTRAL", "the valid model(s) scored the event and found no edge"


# ------------------------------------------------------------------ one output row
def build_row(*, symbol: str, company: Optional[str], event_date: str, session: str, models: list, generated_at, evidence_times: Iterable = (), batch_id: Optional[str] = None, marker_valid: bool = False,
              synthetic: bool = False, now=None, missing: Iterable = ()) -> dict:
    now = _dt(now or generated_at)
    sig, kind, why = headline(models)
    times = [_dt(t) for t in evidence_times if t]
    prod_times = [_dt(m["updated_at"]) for m in models if m.get("updated_at")]
    newest = max(times + prod_times) if (times or prod_times) else None
    oldest = min(times) if times else None
    age_h = round((now - newest).total_seconds() / 3600, 1) if newest else None
    upcoming = date.fromisoformat(event_date) >= now.astimezone(UTC).date()
    batch_based = any(m["source"] == "SMART_MOVE_FROZEN_ENGINE" for m in models)
    if synthetic:
        status = "SYNTHETIC"
    elif not upcoming:
        status = "HISTORICAL"
    elif newest is None:
        status = "UNVERIFIED"
    elif age_h > LIVE_MAX_AGE_H:
        status = "STALE"
    elif batch_based and not marker_valid:
        status = "UNVERIFIED"
    else:
        status = "LIVE"
    warnings = list(missing)
    if kind == "INSUFFICIENT_INPUTS" or kind == "NO_VALID_PREDICTION":
        warnings.append("no valid prediction: this is missing data, not a neutral view")
    if age_h is None:
        warnings.append("update time unknown")
    elif age_h > LIVE_MAX_AGE_H:
        warnings.append("data is %.0f h old" % age_h)
    if session not in SESSION_LABEL:
        warnings.append("announcement session unknown")
    body = {"schema": SCHEMA, "symbol": symbol, "company": company, "event_date": event_date, "session": session, "session_label": SESSION_LABEL.get(session, "Unknown"), "signal": sig, "signal_kind": kind,
            "reason": why, "confidence": None, "confidence_note": "not shown: no existing model has a calibrated probability", "models": models, "consensus": consensus(models), "generated_at": _dt(generated_at).isoformat(),
            "data_status": status, "freshness": {"newest_evidence": newest.isoformat() if newest else None, "oldest_evidence": oldest.isoformat() if oldest else None, "age_hours": age_h}, "warnings": warnings,
            "batch_id": batch_id, "synthetic": bool(synthetic), "evidence_complete": not warnings}
    body["dedupe_key"] = hashlib.sha256(json.dumps([batch_id, symbol, event_date, [(m["name"], m["signal"], m["kind"], m["score"]) for m in models], body["freshness"]["newest_evidence"]], sort_keys=True, default=str).encode()).hexdigest()
    return body


# ------------------------------------------------------------------ coverage
def coverage(rows: list, dates: Iterable[str]) -> dict:
    """Per event date and in total. 'complete' = no warnings and every model valid; 'partial' = some valid model but warnings; 'none' = no valid prediction. Never claims a batch covers everything."""
    dates = list(dates)
    out = {}
    for d in dates + ["TOTAL"]:
        rs = [r for r in rows if d == "TOTAL" or r["event_date"] == d]
        out[d] = {"eligible_companies": len(rs), "complete_data": sum(1 for r in rs if r["evidence_complete"] and r["consensus"]["models_valid"] == r["consensus"]["models_total"] and r["consensus"]["models_total"] > 0),
                  "partial_data": sum(1 for r in rs if r["consensus"]["models_valid"] and not (r["evidence_complete"] and r["consensus"]["models_valid"] == r["consensus"]["models_total"])),
                  "buy": sum(1 for r in rs if r["signal"] == "BUY"), "sell": sum(1 for r in rs if r["signal"] == "SELL"), "no_trade": sum(1 for r in rs if r["signal"] == "NO_TRADE"),
                  "no_valid_prediction": sum(1 for r in rs if not r["consensus"]["models_valid"]), "live": sum(1 for r in rs if r["data_status"] == "LIVE"),
                  "no_trade_reasons": dict(Counter(r["signal_kind"] for r in rs if r["signal"] == "NO_TRADE"))}
    return out


# ------------------------------------------------------------------ store + idempotent job
class SignalStore:
    """Append-only signals.jsonl with a dedupe key: the same batch, event, model outputs and evidence time are written once."""

    def __init__(self, path):
        self.path = Path(path)
        self.rows = [json.loads(ln) for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()] if self.path.exists() else []
        self.keys = {r["dedupe_key"] for r in self.rows}

    def add(self, row: dict) -> bool:
        if row["dedupe_key"] in self.keys:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True, default=str) + chr(10))
        self.rows.append(row)
        self.keys.add(row["dedupe_key"])
        return True


def _log(out_dir: Path, event: dict):
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "processing_log.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, sort_keys=True, default=str) + chr(10))


def process_batch(run_dir, store_dir, out_dir, now, *, engine_rows: Optional[list] = None, production_scans: Optional[dict] = None, dates: Optional[list] = None) -> dict:
    """Idempotent post-batch job. Refuses without a valid sealed marker; otherwise maps the supplied model outputs (engine rows from the batch store, optional production scan rows {model_name: [scan rows]}) into
    signal rows, appends the new ones once, and logs every refusal or failure. `engine_rows` is the frozen scorer's output on the batch store, supplied by the caller (this module never builds a provider client)."""
    now, out = _dt(now), Path(out_dir)
    mk = BM.verify_marker(run_dir)
    if not mk["valid"]:
        res = {"status": "REFUSED", "reason": mk["reason"], "added": 0}
        _log(out, dict(res, at=now.isoformat()))
        return res
    v = BM.validate_batch(run_dir, store_dir, now)
    if not v["ok"]:
        res = {"status": "REFUSED", "reason": "BATCH_NO_LONGER_VALIDATES:" + ",".join(c["check"] for c in v["checks"] if c["result"] == "FAIL"), "added": 0}
        _log(out, dict(res, at=now.isoformat()))
        return res
    batch_id = mk["marker"]["batch_id"]
    store, added, dup, failed = SignalStore(out / "signals.jsonl"), 0, 0, []
    by_key: dict = {}
    for r in engine_rows or []:
        try:
            by_key.setdefault((r["symbol"], r["earnings_date"]), {"company": r.get("company"), "session": r.get("kind") or "UNKNOWN", "models": [], "evidence": []})
            e = by_key[(r["symbol"], r["earnings_date"])]
            e["models"] += smart_move_models(r)
            e["evidence"] += [x.get("retrieved_at") for x in (r.get("evidence") or [])]
        except Exception as ex:  # noqa: BLE001
            failed.append({"symbol": r.get("symbol"), "error": type(ex).__name__})
    for mname, scan_rows in (production_scans or {}).items():
        for sr in scan_rows:
            try:
                m = production_model(mname, sr)
                key = (sr["symbol"], str(sr.get("date"))[:10])
                e = by_key.setdefault(key, {"company": sr.get("company"), "session": {"Before Open": "BMO", "After Close": "AMC"}.get(sr.get("time_label"), sr.get("session") or "UNKNOWN"), "models": [], "evidence": []})
                if m is None:
                    failed.append({"symbol": sr.get("symbol"), "model": mname, "error": "UNRECOGNISED_SIGNAL:" + str(sr.get("signal"))})
                else:
                    e["models"].append(m)
            except Exception as ex:  # noqa: BLE001
                failed.append({"symbol": sr.get("symbol"), "model": mname, "error": type(ex).__name__})
    for (sym, d), e in sorted(by_key.items()):
        try:
            row = build_row(symbol=sym, company=e["company"], event_date=d, session=e["session"], models=e["models"], generated_at=now, evidence_times=e["evidence"], batch_id=batch_id, marker_valid=True, now=now)
            if store.add(row):
                added += 1
            else:
                dup += 1
        except Exception as ex:  # noqa: BLE001
            failed.append({"symbol": sym, "error": type(ex).__name__})
    res = {"status": "PROCESSED", "batch_id": batch_id, "added": added, "duplicates_skipped": dup, "failed": len(failed), "events": len(by_key), "coverage": coverage(store.rows, dates or sorted({r["event_date"] for r in store.rows}))["TOTAL"]}
    _log(out, dict(res, at=now.isoformat(), failures=failed[:50]))
    return res


def refresh_status(row: dict, now, marker_valid: Optional[bool] = None) -> dict:
    """The data status of a stored row AT READ TIME: a LIVE row ages into STALE, an event that has happened becomes HISTORICAL, and a batch row whose marker no longer verifies becomes UNVERIFIED. Never upgrades a status."""
    now = _dt(now)
    r = dict(row)
    st = r["data_status"]
    if st == "SYNTHETIC":
        return r
    if date.fromisoformat(r["event_date"]) < now.astimezone(UTC).date():
        st = "HISTORICAL"
    elif st == "LIVE":
        newest = r["freshness"].get("newest_evidence")
        if not newest or (now - _dt(newest)).total_seconds() / 3600 > LIVE_MAX_AGE_H:
            st = "STALE"
        elif r.get("batch_id") and marker_valid is False:
            st = "UNVERIFIED"
        if newest:
            r["freshness"] = dict(r["freshness"], age_hours=round((now - _dt(newest)).total_seconds() / 3600, 1))
    r["data_status"] = st
    return r
