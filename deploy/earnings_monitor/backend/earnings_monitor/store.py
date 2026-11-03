"""Durable store for the automatic earnings monitor: events, versions, predictions, changes, alerts, the job queue, provider usage and an audit log.

SQLite (stdlib, WAL): it survives restarts, needs no Postgres connection (the production database is capped at 15) and works across processes. History tables are append-only: triggers refuse UPDATE and DELETE,
so a prediction, a change, an event version or an audit line can never be rewritten. Nothing here talks to a provider."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

UTC = timezone.utc
APPEND_ONLY = ("predictions", "changes", "event_versions", "audit_log")
SCHEMA_VERSION = 3
# Existing stores (schema 2, the one running on the server) are upgraded in place: columns are added, nothing is dropped or rewritten.
MIGRATIONS = {3: ["ALTER TABLE jobs ADD COLUMN lane TEXT NOT NULL DEFAULT 'heavy'", "ALTER TABLE events ADD COLUMN last_verified TEXT", "ALTER TABLE predictions ADD COLUMN reason TEXT",
                  "CREATE TABLE IF NOT EXISTS evidence_items(id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL, kind TEXT NOT NULL, provider TEXT NOT NULL, ext_id TEXT NOT NULL, occurred_at TEXT, title TEXT, summary TEXT, data TEXT, stage TEXT NOT NULL DEFAULT 'DISCOVERED', verified INTEGER NOT NULL DEFAULT 0, verified_by TEXT, relevant_models TEXT, used_by_models TEXT, discovered_at TEXT NOT NULL, collected_at TEXT, last_seen TEXT NOT NULL, dedupe_key TEXT NOT NULL UNIQUE)",
                  "CREATE INDEX IF NOT EXISTS ix_evidence_ev ON evidence_items(event_id, kind, id)"]}

SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, company TEXT, earnings_date TEXT NOT NULL, session TEXT NOT NULL DEFAULT 'UNKNOWN',
  status TEXT NOT NULL DEFAULT 'ACTIVE', first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, last_changed TEXT NOT NULL, missing_scans INTEGER NOT NULL DEFAULT 0,
  reported_at TEXT, canceled_at TEXT, outcome_json TEXT, outcome_at TEXT, source TEXT, verification TEXT NOT NULL DEFAULT 'UNVERIFIED', sources TEXT, last_verified TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_events_active_symbol ON events(symbol) WHERE status='ACTIVE';
CREATE INDEX IF NOT EXISTS ix_events_date ON events(earnings_date);
CREATE TABLE IF NOT EXISTS event_versions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL, earnings_date TEXT NOT NULL, session TEXT NOT NULL, observed_at TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS predictions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL, model TEXT NOT NULL, signal TEXT NOT NULL, score REAL, coverage TEXT, tier TEXT,
  as_of TEXT NOT NULL, evidence_as_of TEXT, recorded_at TEXT NOT NULL, input_hash TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'unknown', engine_version TEXT, reason TEXT);
CREATE INDEX IF NOT EXISTS ix_pred_ev ON predictions(event_id, model, id);
CREATE TABLE IF NOT EXISTS model_state(
  event_id INTEGER NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL, signal TEXT, score REAL, coverage TEXT, as_of TEXT, prediction_id INTEGER,
  last_checked TEXT NOT NULL, stale INTEGER NOT NULL DEFAULT 0, source TEXT, PRIMARY KEY(event_id, model));
CREATE TABLE IF NOT EXISTS changes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER, symbol TEXT NOT NULL, kind TEXT NOT NULL, model TEXT, old_value TEXT, new_value TEXT, detail TEXT,
  detected_at TEXT NOT NULL, dedupe_key TEXT NOT NULL UNIQUE);
CREATE INDEX IF NOT EXISTS ix_changes_ev ON changes(event_id, id);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, rule TEXT NOT NULL, event_id INTEGER, symbol TEXT, model TEXT, payload TEXT NOT NULL, dedupe_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING', delivered_at TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, run_after TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'QUEUED',
  attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 4, locked_by TEXT, lease_until TEXT, dedupe_key TEXT, last_error TEXT,
  created_at TEXT NOT NULL, finished_at TEXT, result TEXT, lane TEXT NOT NULL DEFAULT 'heavy');
CREATE UNIQUE INDEX IF NOT EXISTS ux_jobs_dedupe ON jobs(dedupe_key) WHERE dedupe_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_jobs_due ON jobs(status, run_after);
CREATE TABLE IF NOT EXISTS usage(day TEXT NOT NULL, provider TEXT NOT NULL, requests INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(day, provider));
CREATE TABLE IF NOT EXISTS scan_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, ok INTEGER, summary TEXT);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL, kind TEXT NOT NULL, provider TEXT NOT NULL, ext_id TEXT NOT NULL, occurred_at TEXT, title TEXT, summary TEXT, data TEXT,
  stage TEXT NOT NULL DEFAULT 'DISCOVERED', verified INTEGER NOT NULL DEFAULT 0, verified_by TEXT, relevant_models TEXT, used_by_models TEXT, discovered_at TEXT NOT NULL, collected_at TEXT,
  last_seen TEXT NOT NULL, dedupe_key TEXT NOT NULL UNIQUE);
CREATE INDEX IF NOT EXISTS ix_evidence_ev ON evidence_items(event_id, kind, id);
CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT);
"""


def iso(t: datetime) -> str:
    return (t if t.tzinfo else t.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


def parse(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()                      # one connection per thread, reused: opening a connection per statement was the slow part on the server
        try:
            ready = self._conn().execute("SELECT v FROM kv WHERE k='schema_version'").fetchone()
        except sqlite3.Error:
            ready = None
        if ready:
            have = int(json.loads(ready["v"]))
            if have >= SCHEMA_VERSION:
                return                                       # an up-to-date store: no DDL, so opening it never waits for a writer
            with self.tx() as c:                             # upgrade in place
                for ver in sorted(v for v in MIGRATIONS if v > have):
                    for stmt in MIGRATIONS[ver]:
                        try:
                            c.execute(stmt)
                        except sqlite3.OperationalError as e:
                            if "duplicate column" not in str(e).lower():
                                raise
                c.execute("UPDATE kv SET v=? WHERE k='schema_version'", (json.dumps(SCHEMA_VERSION),))
            return
        with self.tx() as c:
            for stmt in [s.strip() for s in SCHEMA.split(";") if s.strip()]:
                c.execute(stmt)
            for t in APPEND_ONLY:
                for op in ("UPDATE", "DELETE"):
                    c.execute("CREATE TRIGGER IF NOT EXISTS %s_no_%s BEFORE %s ON %s BEGIN SELECT RAISE(ABORT, '%s is append-only'); END" % (t, op.lower(), op, t, t))
            c.execute("INSERT INTO kv(k,v) VALUES('schema_version',?) ON CONFLICT(k) DO NOTHING", (json.dumps(SCHEMA_VERSION),))

    # ---------------------------------------------------------------- plumbing
    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")               # WAL + NORMAL never corrupts; at worst the last few commits are lost in a power cut, which a monitor rebuilds on its next pass
        c.execute("PRAGMA busy_timeout=30000")
        return c

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = self._local.c = self._connect()
            self._local.depth = 0
        return c

    def close(self):
        c = getattr(self._local, "c", None)
        if c is not None:
            c.close()
            self._local.c = None

    @contextmanager
    def tx(self):
        """A transaction. Re-entrant: a tx() opened inside another joins it, so a caller can group many small writes into ONE commit (one fsync) and the callee code does not change."""
        c = self._conn()
        if self._local.depth > 0:
            self._local.depth += 1
            try:
                yield c
            finally:
                self._local.depth -= 1
            return
        c.execute("BEGIN IMMEDIATE")
        self._local.depth = 1
        try:
            yield c
            c.execute("COMMIT")
        except Exception:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            self._local.depth = 0

    def q(self, sql: str, args: Iterable = ()) -> list:
        return [dict(r) for r in self._conn().execute(sql, tuple(args)).fetchall()]

    def one(self, sql: str, args: Iterable = ()) -> Optional[dict]:
        r = self.q(sql, args)
        return r[0] if r else None

    # ---------------------------------------------------------------- audit / kv
    def audit(self, ts: datetime, actor: str, action: str, detail: Any = None):
        with self.tx() as c:
            c.execute("INSERT INTO audit_log(ts,actor,action,detail) VALUES(?,?,?,?)", (iso(ts), actor, action, json.dumps(detail, default=str) if detail is not None else None))

    def kv_get(self, k: str, default=None):
        r = self.one("SELECT v FROM kv WHERE k=?", (k,))
        return json.loads(r["v"]) if r else default

    def kv_set(self, k: str, v: Any):
        with self.tx() as c:
            c.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v, default=str)))

    # ---------------------------------------------------------------- usage
    def add_usage(self, day: str, provider: str, requests: int = 0, failures: int = 0):
        with self.tx() as c:
            c.execute("INSERT INTO usage(day,provider,requests,failures) VALUES(?,?,?,?) ON CONFLICT(day,provider) DO UPDATE SET requests=requests+excluded.requests, failures=failures+excluded.failures",
                      (day, provider, requests, failures))

    def usage(self, day: str, provider: Optional[str] = None) -> int:
        r = self.one("SELECT COALESCE(SUM(requests),0) n FROM usage WHERE day=?" + (" AND provider=?" if provider else ""), (day, provider) if provider else (day,))
        return int(r["n"]) if r else 0

    # ---------------------------------------------------------------- scan runs
    def start_run(self, kind: str, now: datetime) -> int:
        with self.tx() as c:
            return c.execute("INSERT INTO scan_runs(kind,started_at) VALUES(?,?)", (kind, iso(now))).lastrowid

    def finish_run(self, run_id: int, now: datetime, ok: bool, summary: Any):
        with self.tx() as c:
            c.execute("UPDATE scan_runs SET finished_at=?, ok=?, summary=? WHERE id=?", (iso(now), 1 if ok else 0, json.dumps(summary, default=str), run_id))

    def last_run(self, kind: str, ok_only: bool = True) -> Optional[dict]:
        return self.one("SELECT * FROM scan_runs WHERE kind=? AND finished_at IS NOT NULL" + (" AND ok=1" if ok_only else "") + " ORDER BY id DESC LIMIT 1", (kind,))

    # ---------------------------------------------------------------- jobs
    def enqueue(self, kind: str, run_after: datetime, payload: Optional[dict] = None, dedupe_key: Optional[str] = None, max_attempts: int = 4, now: Optional[datetime] = None, lane: str = "heavy") -> Optional[int]:
        """Returns the job id, or None when a job with the same dedupe key was ever created: a recurring bucket (for example the 6-hour calendar slot) runs once, however often tick() is called."""
        try:
            with self.tx() as c:
                return c.execute("INSERT INTO jobs(kind,run_after,payload,dedupe_key,max_attempts,created_at,lane) VALUES(?,?,?,?,?,?,?)",
                                 (kind, iso(run_after), json.dumps(payload or {}), dedupe_key, max_attempts, iso(now or run_after), lane)).lastrowid
        except sqlite3.IntegrityError:
            return None

    def recover_expired(self, now: datetime) -> int:
        """Jobs left RUNNING by a process that died: back to the queue (or dead after max attempts)."""
        with self.tx() as c:
            rows = c.execute("SELECT id,attempts,max_attempts FROM jobs WHERE status='RUNNING' AND lease_until < ?", (iso(now),)).fetchall()
            for r in rows:
                dead = r["attempts"] >= r["max_attempts"]
                c.execute("UPDATE jobs SET status=?, locked_by=NULL, lease_until=NULL, last_error=?, finished_at=? WHERE id=?",
                          ("DEAD" if dead else "QUEUED", "lease expired (worker stopped)", iso(now) if dead else None, r["id"]))
            return len(rows)

    def claim(self, now: datetime, worker: str, lease_s: int, lane: str = "heavy") -> Optional[dict]:
        from datetime import timedelta
        with self.tx() as c:
            r = c.execute("SELECT * FROM jobs WHERE status='QUEUED' AND lane=? AND run_after <= ? ORDER BY run_after, id LIMIT 1", (lane, iso(now))).fetchone()
            if not r:
                return None
            c.execute("UPDATE jobs SET status='RUNNING', locked_by=?, lease_until=?, attempts=attempts+1 WHERE id=?", (worker, iso(now + timedelta(seconds=lease_s)), r["id"]))
            d = dict(r)
            d["attempts"] += 1
            d["payload"] = json.loads(d["payload"] or "{}")
            return d

    def finish_job(self, job_id: int, now: datetime, ok: bool, result: Any = None, error: Optional[str] = None, retry_at: Optional[datetime] = None, dead: bool = False):
        with self.tx() as c:
            if ok:
                c.execute("UPDATE jobs SET status='DONE', finished_at=?, result=?, locked_by=NULL, lease_until=NULL, last_error=NULL WHERE id=?", (iso(now), json.dumps(result, default=str), job_id))
            elif retry_at is not None and not dead:
                c.execute("UPDATE jobs SET status='QUEUED', run_after=?, last_error=?, locked_by=NULL, lease_until=NULL WHERE id=?", (iso(retry_at), error, job_id))
            else:
                c.execute("UPDATE jobs SET status=?, finished_at=?, last_error=?, locked_by=NULL, lease_until=NULL, result=? WHERE id=?",
                          ("DEAD" if dead else "FAILED", iso(now), error, json.dumps(result, default=str), job_id))

    def job_counts(self) -> dict:
        return {r["status"]: r["n"] for r in self.q("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
