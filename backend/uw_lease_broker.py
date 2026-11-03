"""A tiny lease broker so several MACHINES can share the provider's three concurrent REST requests.

Server side: `LeaseTable` grants at most `max_leases` leases at once; every lease has a time-to-live, so a client that dies (or loses the network) frees its lease on its own. `handle()` is the whole HTTP contract:

    POST /acquire  {"holder": "name"}            -> 200 {"lease": id, "ttl": s}    or 429 {"retry_in": s}
    POST /release  {"lease": id}                 -> 200 {"released": true}
    GET  /status                                 -> 200 {"in_use": n, "max": 3, ...}

All three need the shared-secret header. `uw_lease_broker_routes.py` exposes `handle()` as a FastAPI router; it is NOT mounted in the application, so nothing here is reachable until the owner approves deploying it.
Client side: `LeaseClient.hold()` is a context manager used by `uw_slots.global_slot()`. If the broker cannot be reached the client FAILS CLOSED after the timeout (it raises) rather than calling the provider unchecked,
unless UW_BROKER_FAIL_OPEN=1.

Limits, stated plainly: the table lives in memory, so a broker restart forgets its leases for up to one TTL; a TTL shorter than a request lets a slow request outlive its lease. TTL defaults to request timeout + margin."""
from __future__ import annotations

import contextlib
import hmac
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Optional

TTL_DEFAULT = 45.0


class LeaseTable:
    def __init__(self, max_leases: int = 3, ttl: float = TTL_DEFAULT, clock=time.monotonic):
        self.max, self.ttl, self.clock = max_leases, ttl, clock
        self._leases: dict = {}
        self._lock = threading.Lock()

    def _expire(self) -> None:
        now = self.clock()
        for k in [k for k, v in self._leases.items() if v["until"] <= now]:
            del self._leases[k]

    def acquire(self, holder: str = "") -> Optional[str]:
        with self._lock:
            self._expire()
            if len(self._leases) >= self.max:
                return None
            lease = uuid.uuid4().hex
            self._leases[lease] = {"holder": holder[:40], "until": self.clock() + self.ttl}
            return lease

    def release(self, lease: str) -> bool:
        with self._lock:
            return self._leases.pop(lease, None) is not None

    def status(self) -> dict:
        with self._lock:
            self._expire()
            return {"in_use": len(self._leases), "max": self.max, "ttl": self.ttl, "holders": sorted(v["holder"] for v in self._leases.values())}


def handle(table: LeaseTable, secret: str, method: str, path: str, header_secret: str, body: dict) -> tuple:
    """(status code, JSON body). Constant-time secret check; an empty configured secret refuses everything."""
    if not secret or not hmac.compare_digest(str(header_secret or ""), secret):
        return 401, {"error": "unauthorized"}
    if method == "POST" and path == "/acquire":
        lease = table.acquire(str((body or {}).get("holder") or ""))
        if lease is None:
            return 429, {"retry_in": round(random.uniform(0.05, 0.4), 2)}
        return 200, {"lease": lease, "ttl": table.ttl}
    if method == "POST" and path == "/release":
        return 200, {"released": table.release(str((body or {}).get("lease") or ""))}
    if method == "GET" and path == "/status":
        return 200, table.status()
    return 404, {"error": "not found"}


class LeaseClient:
    def __init__(self, url: str, secret: str, holder: Optional[str] = None, timeout: float = 5.0):
        self.url, self.secret, self.timeout = url.rstrip("/"), secret, timeout
        self.holder = holder or "%s:%d" % (os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "host", os.getpid())

    def _call(self, path: str, body: dict) -> tuple:
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json", "X-Broker-Secret": self.secret})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode() or "{}")
            except ValueError:
                return e.code, {}

    @contextlib.contextmanager
    def hold(self, timeout: float = 30.0):
        from uw_slots import SlotTimeout
        deadline = time.monotonic() + timeout
        lease, attempt = None, 0
        while lease is None:
            try:
                code, body = self._call("/acquire", {"holder": self.holder})
            except Exception as exc:  # noqa: BLE001 - broker unreachable
                if str(os.environ.get("UW_BROKER_FAIL_OPEN", "")).strip() == "1":
                    yield                                           # explicit owner choice: carry on without the broker
                    return
                if time.monotonic() >= deadline:
                    raise SlotTimeout("lease broker unreachable (%s): refusing to call the provider unchecked" % type(exc).__name__)
                time.sleep(random.uniform(0.1, 0.5))
                continue
            if code == 200:
                lease = body["lease"]
            elif code == 429:
                if time.monotonic() >= deadline:
                    raise SlotTimeout("no provider lease free within %.0f s" % timeout)
                attempt += 1
                time.sleep(max(float(body.get("retry_in") or 0.05), random.uniform(0.0, min(1.0, 0.05 * 2 ** attempt))))      # Retry-After style floor + jittered backoff
            else:
                raise SlotTimeout("lease broker refused the request (HTTP %s)" % code)
        try:
            yield
        finally:
            try:
                self._call("/release", {"lease": lease})
            except Exception:  # noqa: BLE001 - the lease expires by itself after its TTL
                pass
