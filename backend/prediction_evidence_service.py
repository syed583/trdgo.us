"""
Append-only, point-in-time evidence for earnings predictions.

Problem this solves (audit 2026-10-29): ``model_predictions`` keeps only model, symbol, event, signal and score. The inputs
behind a call (the 12 parameters with their weights and values, the provider each came from, how old the data was) live only
in ``swr_cache``, which is overwritten at every refresh. A stored BUY/SELL could therefore never be re-derived, audited or
checked for stale data after the fact.

What is stored, at the moment a prediction is captured (never reconstructed later):
  * ``earnings_input_snapshots``  one row per distinct input snapshot of a symbol: the 12 parameters (weight, bias, points,
    availability, unavailable reason, data kind, provider/source description, zero-bias and saturation flags), the raw
    directional signals, the short-interest block, the cache key and the cache build time. Content-addressed
    (``snapshot_hash``), so identical inputs are stored once however many models and runs reference them.
  * ``prediction_evidence``       one row per capture decision per model/symbol/event: signal, score, genuine UTC capture
    time, timing label and basis (always provisional: no verified release timestamp exists), cache age, freshness verdict,
    the capture-guard decision (OK / STALE / NO_CACHE / POST_CUTOFF) and mode, a reference to the input snapshot, a revision
    number and the id of the row it supersedes. A new row is appended only when the signal, the score (to 0.1), the guard
    decision or the status changes, so the original and every revision stay visible.

Append-only is enforced by the database itself (a trigger rejects UPDATE and DELETE), not by convention.

Honest limits, recorded in every row rather than hidden:
  * ``source_ts_basis`` is CACHE_BUILD_TIME: the cache has one build time per entry, not a timestamp per provider field, so
    the age is an upper bound for every value.
  * ``inputs_status`` is RELATED_EQUITY_INPUTS_ONLY for every model: each model scores with its own formula, but only the
    shared Equity-engine cache entry is snapshotted here. A model's private intermediate values are NOT captured, so a
    model's score is not reproducible from this evidence alone (UNVERIFIED for every model except the Equity engine itself).
  * Nothing about past predictions is back-filled. History starts the day this is enabled.

Switch: EARN_EVIDENCE=on. Default off; with it off this module writes nothing and creates no table.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

CALC_VERSION = "evidence-1"
SNAP_TABLE = "earnings_input_snapshots"
EVID_TABLE = "prediction_evidence"
INPUTS_STATUS = "RELATED_EQUITY_INPUTS_ONLY"
SOURCE_TS_BASIS = "CACHE_BUILD_TIME"

STATUS_CAPTURED = "CAPTURED"
STATUS_SKIPPED = "SKIPPED"


def enabled() -> bool:
    return os.environ.get("EARN_EVIDENCE", "").strip().lower() in ("1", "true", "yes", "on")


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha(obj) -> str:
    return hashlib.sha256(_canon(obj).encode("utf-8")).hexdigest()


def _dialect(db) -> str:
    try:
        return db.get_bind().dialect.name
    except Exception:  # noqa: BLE001
        return "postgresql"


# --------------------------------------------------------------------------- schema
_PG_DDL = [
    f"""CREATE TABLE IF NOT EXISTS {SNAP_TABLE} (
        snapshot_hash   VARCHAR(64) PRIMARY KEY,
        symbol          VARCHAR(20) NOT NULL,
        cache_key       VARCHAR(80),
        cache_updated_at TIMESTAMPTZ,
        source_ts_basis VARCHAR(24) NOT NULL,
        payload_json    TEXT NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now())""",
    f"""CREATE TABLE IF NOT EXISTS {EVID_TABLE} (
        id              BIGSERIAL PRIMARY KEY,
        model           VARCHAR(24) NOT NULL,
        symbol          VARCHAR(20) NOT NULL,
        earnings_date   DATE,
        report_timing   VARCHAR(16),
        timing_basis    VARCHAR(48) NOT NULL,
        predicted_at    TIMESTAMPTZ NOT NULL,
        signal          VARCHAR(10),
        score           DOUBLE PRECISION,
        status          VARCHAR(12) NOT NULL,
        guard_mode      VARCHAR(10) NOT NULL,
        guard_decision  VARCHAR(16) NOT NULL,
        guard_detail    TEXT,
        cache_age_h     DOUBLE PRECISION,
        freshness_status VARCHAR(10),
        freshness_json  TEXT,
        snapshot_hash   VARCHAR(64),
        inputs_status   VARCHAR(40) NOT NULL,
        calc_version    VARCHAR(24) NOT NULL,
        evidence_hash   VARCHAR(64) NOT NULL,
        revision_no     INTEGER NOT NULL DEFAULT 1,
        supersedes_id   BIGINT,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now())""",
    f"CREATE INDEX IF NOT EXISTS ix_prev_event ON {EVID_TABLE} (model, symbol, earnings_date, id)",
    f"""CREATE OR REPLACE FUNCTION {EVID_TABLE}_append_only() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'prediction evidence tables are append-only'; END $$ LANGUAGE plpgsql""",
    f"DROP TRIGGER IF EXISTS {EVID_TABLE}_immutable ON {EVID_TABLE}",
    f"""CREATE TRIGGER {EVID_TABLE}_immutable BEFORE UPDATE OR DELETE ON {EVID_TABLE}
        FOR EACH ROW EXECUTE FUNCTION {EVID_TABLE}_append_only()""",
    f"DROP TRIGGER IF EXISTS {SNAP_TABLE}_immutable ON {SNAP_TABLE}",
    f"""CREATE TRIGGER {SNAP_TABLE}_immutable BEFORE UPDATE OR DELETE ON {SNAP_TABLE}
        FOR EACH ROW EXECUTE FUNCTION {EVID_TABLE}_append_only()""",
    # TRUNCATE does not fire row triggers; it needs its own statement-level trigger or it would bypass the append-only rule.
    f"DROP TRIGGER IF EXISTS {EVID_TABLE}_no_truncate ON {EVID_TABLE}",
    f"""CREATE TRIGGER {EVID_TABLE}_no_truncate BEFORE TRUNCATE ON {EVID_TABLE}
        FOR EACH STATEMENT EXECUTE FUNCTION {EVID_TABLE}_append_only()""",
    f"DROP TRIGGER IF EXISTS {SNAP_TABLE}_no_truncate ON {SNAP_TABLE}",
    f"""CREATE TRIGGER {SNAP_TABLE}_no_truncate BEFORE TRUNCATE ON {SNAP_TABLE}
        FOR EACH STATEMENT EXECUTE FUNCTION {EVID_TABLE}_append_only()""",
]

_SQLITE_DDL = [
    f"""CREATE TABLE IF NOT EXISTS {SNAP_TABLE} (
        snapshot_hash TEXT PRIMARY KEY, symbol TEXT NOT NULL, cache_key TEXT, cache_updated_at TEXT,
        source_ts_basis TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    f"""CREATE TABLE IF NOT EXISTS {EVID_TABLE} (
        id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT NOT NULL, symbol TEXT NOT NULL, earnings_date TEXT, report_timing TEXT,
        timing_basis TEXT NOT NULL, predicted_at TEXT NOT NULL, signal TEXT, score REAL, status TEXT NOT NULL,
        guard_mode TEXT NOT NULL, guard_decision TEXT NOT NULL, guard_detail TEXT, cache_age_h REAL, freshness_status TEXT,
        freshness_json TEXT, snapshot_hash TEXT, inputs_status TEXT NOT NULL, calc_version TEXT NOT NULL,
        evidence_hash TEXT NOT NULL, revision_no INTEGER NOT NULL DEFAULT 1, supersedes_id INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    f"CREATE INDEX IF NOT EXISTS ix_prev_event ON {EVID_TABLE} (model, symbol, earnings_date, id)",
    f"""CREATE TRIGGER IF NOT EXISTS {EVID_TABLE}_no_update BEFORE UPDATE ON {EVID_TABLE}
        BEGIN SELECT RAISE(ABORT, 'prediction_evidence is append-only'); END""",
    f"""CREATE TRIGGER IF NOT EXISTS {EVID_TABLE}_no_delete BEFORE DELETE ON {EVID_TABLE}
        BEGIN SELECT RAISE(ABORT, 'prediction_evidence is append-only'); END""",
    f"""CREATE TRIGGER IF NOT EXISTS {SNAP_TABLE}_no_update BEFORE UPDATE ON {SNAP_TABLE}
        BEGIN SELECT RAISE(ABORT, 'earnings_input_snapshots is append-only'); END""",
    f"""CREATE TRIGGER IF NOT EXISTS {SNAP_TABLE}_no_delete BEFORE DELETE ON {SNAP_TABLE}
        BEGIN SELECT RAISE(ABORT, 'earnings_input_snapshots is append-only'); END""",
]

_ready_for: set = set()


def ensure_tables(db) -> bool:
    """Create the two tables and their append-only triggers (once per database engine). Additive: touches no existing table."""
    from sqlalchemy import text
    key = id(db.get_bind())
    if key in _ready_for:
        return True
    try:
        for stmt in (_SQLITE_DDL if _dialect(db) == "sqlite" else _PG_DDL):
            db.execute(text(stmt))
        db.commit()
        _ready_for.add(key)
        return True
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("prediction_evidence: could not create tables")
        return False


# --------------------------------------------------------------------------- building records
def _param_rows(payload: dict) -> list[dict]:
    import freshness_policy as fp
    rows = []
    for p in (payload or {}).get("params") or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name"))
        bias = p.get("bias")
        avail = bool(p.get("available"))
        rows.append({
            "name": name, "label": p.get("label"), "weight": p.get("weight"), "available": avail, "bias": bias,
            "points": p.get("points"), "leaning": p.get("leaning"),
            "unavailable_reason": p.get("unavailable_reason") or "",
            "data_type": fp.PARAM_DATA_TYPE.get(name, "UNMAPPED"), "source": fp.PARAM_SOURCE.get(name),
            "flags": [f for f, on in (("ZERO_BIAS_MAY_BE_A_DEFAULT", avail and bias == 0.0),
                                      ("SATURATED", avail and bias is not None and abs(bias) >= 0.999),
                                      ("UNAVAILABLE", not avail)) if on],
        })
    return rows


def build_snapshot(symbol: str, payload: dict, cache_key: str, cache_updated_epoch: Optional[float]) -> dict:
    """The input snapshot of one symbol, as stored: parameters + raw signals + short interest. Content-addressed."""
    body = {
        "symbol": symbol,
        "engine": (payload or {}).get("engine"), "profile": (payload or {}).get("profile"),
        "decision": (payload or {}).get("decision"), "score": (payload or {}).get("score"),
        "coverage_pct": (payload or {}).get("coverage_pct"), "confidence": (payload or {}).get("confidence"),
        "params": _param_rows(payload),
        "signals": (payload or {}).get("signals") or {},
        "short_interest": (payload or {}).get("short_interest"),
        "cache_key": cache_key,
        "cache_updated_epoch": None if cache_updated_epoch is None else round(float(cache_updated_epoch), 3),
    }
    return {"hash": _sha(body), "body": body}


def _iso(epoch: Optional[float]) -> Optional[str]:
    return None if epoch is None else datetime.fromtimestamp(float(epoch), timezone.utc).isoformat()


def _adapt(db, value):
    """SQLite stores dates / datetimes as text; PostgreSQL takes them natively."""
    if _dialect(db) == "sqlite" and hasattr(value, "isoformat"):
        return value.isoformat()
    return value


# --------------------------------------------------------------------------- writing
def _latest_rows(db, entries: list[dict]) -> dict:
    """{(model, symbol, earnings_date): (id, signal, score, guard_decision, status, revision_no)} for the newest evidence row of each key.

    PostgreSQL: ONE statement for the whole run (this runs against a remote database; a statement per cell would be thousands of round
    trips). SQLite (tests): one statement per key."""
    from sqlalchemy import text
    out: dict = {}
    if _dialect(db) == "sqlite":
        for e in entries:
            k = (e["model"], e["symbol"], e["earnings_date"])
            if k in out:
                continue
            r = db.execute(text(f"SELECT id, signal, score, guard_decision, status, revision_no FROM {EVID_TABLE} "
                                "WHERE model = :m AND symbol = :s AND earnings_date IS :e ORDER BY id DESC LIMIT 1"),
                           {"m": e["model"], "s": e["symbol"], "e": _adapt(db, e["earnings_date"])}).fetchone()
            if r is not None:
                out[k] = tuple(r)
        return out
    rows = db.execute(text(f"SELECT DISTINCT ON (model, symbol, earnings_date) model, symbol, earnings_date, id, signal, score, "
                           f"guard_decision, status, revision_no FROM {EVID_TABLE} WHERE symbol = ANY(:s) "
                           "ORDER BY model, symbol, earnings_date, id DESC"), {"s": sorted({e["symbol"] for e in entries})}).fetchall()
    for r in rows:
        out[(r[0], r[1], r[2])] = tuple(r[3:])
    return out


def _existing_snapshots(db, hashes: list[str]) -> set:
    from sqlalchemy import text
    if not hashes:
        return set()
    if _dialect(db) == "sqlite":
        return {h for h in hashes if db.execute(text(f"SELECT 1 FROM {SNAP_TABLE} WHERE snapshot_hash = :h"), {"h": h}).fetchone()}
    return {r[0] for r in db.execute(text(f"SELECT snapshot_hash FROM {SNAP_TABLE} WHERE snapshot_hash = ANY(:h)"), {"h": hashes})}


def record(db, entries: list[dict]) -> dict:
    """Append evidence rows for one capture run. ``entries`` come from model_prediction_service.capture.

    Each entry: model, symbol, earnings_date, report_timing, timing_basis, predicted_at (genuine UTC), signal, score,
    status, guard_mode, guard_decision, guard_detail, cache_age_h, freshness (the freshness_policy.assess dict) and snapshot
    (build_snapshot result or None). Returns counts. Never raises; a failure leaves the predictions untouched.

    Cost: a handful of statements per run (one lookup of the newest rows, one batch for new snapshots, one batch for evidence),
    not a statement per cell."""
    from sqlalchemy import text
    out = {"written": 0, "unchanged": 0, "revisions": 0, "snapshots": 0, "errors": 0}
    if not entries or not ensure_tables(db):
        return out
    try:
        snaps: dict = {}
        for e in entries:
            if e.get("snapshot"):
                snaps.setdefault(e["snapshot"]["hash"], (e["symbol"], e["snapshot"]))
        have = _existing_snapshots(db, list(snaps))
        new_snaps = [{"h": h, "s": sym, "k": sn["body"].get("cache_key"), "u": _iso(sn["body"].get("cache_updated_epoch")),
                      "b": SOURCE_TS_BASIS, "p": _canon(sn["body"])} for h, (sym, sn) in snaps.items() if h not in have]
        if new_snaps:
            db.execute(text(f"INSERT INTO {SNAP_TABLE} (snapshot_hash, symbol, cache_key, cache_updated_at, source_ts_basis, payload_json) "
                            "VALUES (:h, :s, :k, :u, :b, :p) ON CONFLICT (snapshot_hash) DO NOTHING"), new_snaps)
            out["snapshots"] = len(new_snaps)
        latest = _latest_rows(db, entries)
        batch: list[dict] = []
        seen: set = set()
        for e in entries:
            snap = e.get("snapshot")
            key = (e["model"], e["symbol"], e["earnings_date"])
            if key in seen:                                  # the same cell twice in one run: the first one is the record
                out["unchanged"] += 1
                continue
            seen.add(key)
            prev = latest.get(key)
            score = None if e.get("score") is None else round(float(e["score"]), 1)
            if prev is not None:
                pscore = None if prev[2] is None else round(float(prev[2]), 1)
                if (prev[1], pscore, prev[3], prev[4]) == (e.get("signal"), score, e["guard_decision"], e["status"]):
                    out["unchanged"] += 1
                    continue
            rev = 1 if prev is None else int(prev[5]) + 1
            fr = e.get("freshness") or {}
            ehash = _sha([e["model"], e["symbol"], str(e["earnings_date"]), e.get("signal"), score,
                          snap["hash"] if snap else None, e["guard_decision"], e["status"], CALC_VERSION])
            batch.append({"model": e["model"], "symbol": e["symbol"], "ed": _adapt(db, e["earnings_date"]), "rt": e.get("report_timing"),
                          "tb": e["timing_basis"], "pa": _adapt(db, e["predicted_at"]), "sig": e.get("signal"), "sc": e.get("score"),
                          "st": e["status"], "gm": e["guard_mode"], "gd": e["guard_decision"], "gdet": e.get("guard_detail"),
                          "age": fr.get("cache_age_h", e.get("cache_age_h")), "fs": fr.get("status"), "fj": _canon(fr) if fr else None,
                          "sh": snap["hash"] if snap else None, "inp": INPUTS_STATUS, "cv": CALC_VERSION, "eh": ehash, "rev": rev,
                          "sup": None if prev is None else prev[0]})
            out["written"] += 1
            out["revisions"] += 1 if prev is not None else 0
        if batch:
            db.execute(text(
                f"INSERT INTO {EVID_TABLE} (model, symbol, earnings_date, report_timing, timing_basis, predicted_at, signal, score, "
                "status, guard_mode, guard_decision, guard_detail, cache_age_h, freshness_status, freshness_json, snapshot_hash, "
                "inputs_status, calc_version, evidence_hash, revision_no, supersedes_id) "
                "VALUES (:model, :symbol, :ed, :rt, :tb, :pa, :sig, :sc, :st, :gm, :gd, :gdet, :age, :fs, :fj, :sh, :inp, :cv, :eh, :rev, :sup)"),
                batch)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        out["errors"] += 1
        log.exception("prediction_evidence: write failed (predictions are unaffected)")
    return out


# --------------------------------------------------------------------------- reading
def history(db, model: str, symbol: str, earnings_date) -> list[dict]:
    """Every evidence row for one model/symbol/event, oldest first, with the referenced input snapshot attached."""
    from sqlalchemy import text
    if not ensure_tables(db):
        return []
    rows = db.execute(text(
        f"SELECT id, predicted_at, signal, score, status, guard_decision, guard_detail, cache_age_h, freshness_status, "
        f"snapshot_hash, revision_no, supersedes_id, timing_basis FROM {EVID_TABLE} "
        "WHERE model = :m AND symbol = :s AND earnings_date = :e ORDER BY id"),
        {"m": model, "s": symbol, "e": _adapt(db, earnings_date)}).fetchall()
    out = []
    for r in rows:
        snap = None
        if r[9]:
            s = db.execute(text(f"SELECT payload_json FROM {SNAP_TABLE} WHERE snapshot_hash = :h"), {"h": r[9]}).fetchone()
            snap = json.loads(s[0]) if s else None
        out.append({"id": r[0], "predicted_at": str(r[1]), "signal": r[2], "score": r[3], "status": r[4], "guard_decision": r[5],
                    "guard_detail": r[6], "cache_age_h": r[7], "freshness_status": r[8], "snapshot_hash": r[9],
                    "revision_no": r[10], "supersedes_id": r[11], "timing_basis": r[12], "inputs": snap})
    return out
