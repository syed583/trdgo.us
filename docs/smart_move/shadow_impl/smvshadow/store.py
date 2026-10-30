"""Append-only PostgreSQL metadata and the immutable hashed payload store (component 4).

Payload files: <root>/payloads/ab/cd/<sha256>.json.gz, named by the SHA-256 of the RAW provider bytes. Atomic write: temporary file in the same folder, flush + fsync,
re-read and verify, os.replace, read-only attribute. Metadata rows are inserted only after the file is durable, with ON CONFLICT DO NOTHING, so every step can be
repeated after a crash without duplicates (idempotent) and a crash can leave an orphan file or a temporary file but never a row pointing at a missing file.
"""
import gzip
import hashlib
import json
import os
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[1] / "schema" / "smv_schema_v1.sql"
SCHEMA_CAL = Path(__file__).resolve().parents[1] / "schema" / "smv_schema_v2_calendar.sql"


def rel_for(sha):
    return "payloads/%s/%s/%s.json.gz" % (sha[:2], sha[2:4], sha)


class PayloadStore:
    def __init__(self, root):
        self.root = Path(root)

    def put(self, raw: bytes):
        sha = hashlib.sha256(raw).hexdigest()
        final = self.root / rel_for(sha)
        if final.exists():
            if hashlib.sha256(gzip.decompress(final.read_bytes())).hexdigest() != sha:
                raise RuntimeError("existing file for %s is corrupt: quarantine, never overwrite" % sha)
            return sha, rel_for(sha), False
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp = final.parent / (".tmp-" + uuid.uuid4().hex)
        with open(tmp, "xb") as fh:
            fh.write(gzip.compress(raw, 6, ))
            fh.flush()
            os.fsync(fh.fileno())
        if hashlib.sha256(gzip.decompress(tmp.read_bytes())).hexdigest() != sha:
            tmp.unlink()
            raise RuntimeError("verification of the temporary file failed")
        os.replace(tmp, final)
        os.chmod(final, stat.S_IREAD)
        return sha, rel_for(sha), True

    def get(self, sha):
        raw = gzip.decompress((self.root / rel_for(sha)).read_bytes())
        if hashlib.sha256(raw).hexdigest() != sha:
            raise RuntimeError("payload %s fails its hash" % sha)
        return raw

    def files(self):
        return sorted((self.root / "payloads").rglob("*.json.gz")) if (self.root / "payloads").exists() else []

    def verify_all(self):
        bad = []
        for f in self.files():
            sha = f.name[:-8]
            try:
                ok = hashlib.sha256(gzip.decompress(f.read_bytes())).hexdigest() == sha and str(f.relative_to(self.root)).replace("\\", "/") == rel_for(sha)
            except Exception:
                ok = False
            if not ok:
                bad.append(str(f))
        return len(self.files()), bad

    def recover(self):
        """After a crash: remove temporary files (never a final file) and report what is left over."""
        removed = []
        base = self.root / "payloads"
        for t in (list(base.rglob(".tmp-*")) if base.exists() else []):
            t.unlink()
            removed.append(t.name)
        return removed

    def orphans(self, known_hashes):
        have = {f.name[:-8] for f in self.files()}
        return sorted(have - set(known_hashes)), sorted(set(known_hashes) - have)

    def seal(self, day, prev_seal_sha):
        shas = sorted(f.name[:-8] for f in self.files())
        body = json.dumps({"day": day, "prev": prev_seal_sha, "payloads": shas}, sort_keys=True).encode("ascii")
        return hashlib.sha256(body).hexdigest(), len(shas)


class Db:
    """Thin wrapper over a psycopg connection (autocommit). Every insert is idempotent."""

    def __init__(self, conn):
        self.c = conn

    def apply_schema(self):
        self.c.execute(SCHEMA.read_text(encoding="utf-8"))

    def apply_calendar_schema(self):
        self.c.execute(SCHEMA_CAL.read_text(encoding="utf-8"))

    def add_calendar_observations(self, log):
        """Insert every observation of an ObservationLog in append order, idempotently."""
        for o, chain in log._rows:
            self.c.execute("INSERT INTO smv.calendar_observations(source,vendor,symbol,event_date,label_raw,clock_raw,vendor_flag_raw,raw_sha256,calendar_observed_at,published_at,chain_sha256) "
                           "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                           (o.source, o.vendor, o.symbol, o.date, o.label, o.clock, o.vendor_flag, o.raw_sha256, o.observed_at, o.published_at, chain))

    def add_calendar_evaluation(self, run_id, ev, evaluated_at):
        self.c.execute("INSERT INTO smv.calendar_evaluations(run_id,symbol,event_date,status,kind,eligible_for_shadow,reasons,no_trade_reasons,calendar_observed_at,prediction_snapshot_at,detail,evaluated_at) "
                       "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT DO NOTHING",
                       (run_id, ev["symbol"], ev["event_date"], ev["status"], ev["kind"], ev["eligible_for_shadow"], ev["reasons"], ev["no_trade_reasons"], ev["calendar_observed_at"],
                        ev["prediction_snapshot_at"], json.dumps({k: ev[k] for k in ("confidence", "moved_from", "dependency", "vendors", "timing_status")}, default=str), evaluated_at))

    def _one(self, sql, args=()):
        r = self.c.execute(sql, args).fetchone()
        return r[0] if r else None

    def add_event_version(self, v, plan, scope="PILOT"):
        self.c.execute(
            "INSERT INTO smv.event_versions(symbol,earnings_date,version,timing_label,timing_label_source,assumed_release_utc,window_start_utc,deadline_utc,scope) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (v.symbol, v.event_date, v.version, v.kind if v.kind in ("BMO", "AMC") else "UNKNOWN", "calendar:" + v.timing_status, plan["assumed_release_utc"],
             plan["window_start_utc"], plan["deadline_utc"], scope))

    def add_payload(self, sha, rel, provider, endpoint, params_hash, status, raw_len, stored_len, captured_at):
        self.c.execute("INSERT INTO smv.payloads(sha256,provider,endpoint,params_hash,http_status,bytes_raw,bytes_stored,compression,rel_path,captured_at) "
                       "VALUES (%s,%s,%s,%s,%s,%s,%s,'gzip',%s,%s) ON CONFLICT DO NOTHING", (sha, provider, endpoint, params_hash, status, raw_len, stored_len, rel, captured_at))

    def add_snapshot(self, ev, symbol, edate, version, run_id):
        args = (symbol, edate, version, ev.factor, ev.source_name, ev.payload_sha256 if ev.status == "OK" else None, ev.provider_as_of, ev.capture_ts_utc, ev.session_reflected,
                ev.as_of_quality, ev.freshness_class, ev.status, ev.missing_reason, json.dumps(ev.extracted, default=str), run_id)
        sid = self._one("INSERT INTO smv.input_snapshots(symbol,earnings_date,event_version,factor,source_name,payload_sha256,provider_as_of,capture_ts_utc,session_reflected,"
                        "as_of_quality,freshness_class,status,missing_reason,extracted,run_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) "
                        "ON CONFLICT DO NOTHING RETURNING snapshot_id", args)
        if sid is None:
            sid = self._one("SELECT snapshot_id FROM smv.input_snapshots WHERE run_id=%s AND symbol=%s AND earnings_date=%s AND event_version=%s AND factor=%s AND source_name=%s",
                            (run_id, symbol, edate, version, ev.factor, ev.source_name))
        return sid

    def add_prediction(self, p):
        args = (p["symbol"], p["earnings_date"], p["event_version"], p["scoring_version"], p["variant"], p["prereg_sha256"], p["prediction_version"], p["predicted_at_utc"],
                p["cutoff_utc"], p["in_window"], p["signal"], p["bull_points"], p["bear_points"], p["weight_available"], p["reason_codes"], json.dumps(p["explanation"], default=str))
        pid = self._one("INSERT INTO smv.predictions(symbol,earnings_date,event_version,scoring_version,variant,prereg_sha256,prediction_version,predicted_at_utc,cutoff_utc,"
                        "in_window,signal,bull_points,bear_points,weight_available,reason_codes,explanation) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) "
                        "ON CONFLICT DO NOTHING RETURNING prediction_id", args)
        if pid is None:
            pid = self._one("SELECT prediction_id FROM smv.predictions WHERE symbol=%s AND earnings_date=%s AND event_version=%s AND scoring_version=%s AND variant=%s AND prediction_version=%s",
                            (p["symbol"], p["earnings_date"], p["event_version"], p["scoring_version"], p["variant"], p["prediction_version"]))
        return pid

    def link_inputs(self, pid, snapshot_ids):
        for sid in snapshot_ids:
            self.c.execute("INSERT INTO smv.prediction_inputs VALUES (%s,%s) ON CONFLICT DO NOTHING", (pid, sid))

    def add_ledger(self, run_id, entries):
        for e in entries:
            at = e.get("at") or datetime.now(timezone.utc).isoformat()
            self.c.execute("INSERT INTO smv.ledger(run_id,at_utc,entry_type,symbol,earnings_date,detail) VALUES (%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING",
                           (run_id, at, e["type"], e.get("symbol"), e.get("event_date") or None, json.dumps(e, default=str)))

    def run_event(self, run_id, phase, detail=None):
        self.c.execute("INSERT INTO smv.capture_runs(run_id,phase,detail) VALUES (%s,%s,%s::jsonb)", (run_id, phase, json.dumps(detail or {}, default=str)))
