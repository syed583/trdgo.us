"""Shared concurrency for the Unusual Whales token: machine-wide slots across real processes, the cross-machine lease broker, Retry-After honoured, and a refused slot answered cleanly.
No network and no provider: slots are files, the broker is an in-process HTTP server, the HTTP opener is a fake."""
import io
import json
import subprocess
import sys
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
import unusualwhales_service as uw  # noqa: E402
import uw_lease_broker as LB  # noqa: E402
import uw_slots as S  # noqa: E402

WORKER = """
import sys, time
sys.path.insert(0, sys.argv[1])
from uw_slots import MachineSlots
with MachineSlots(sys.argv[2]).hold(30):
    t0 = time.time(); time.sleep(0.35); t1 = time.time()
with open(sys.argv[3], "a") as f:
    f.write("%f %f\\n" % (t0, t1))
"""

HOLDER = """
import sys, time
sys.path.insert(0, sys.argv[1])
from uw_slots import MachineSlots
m = MachineSlots(sys.argv[2])
held = [m.hold(5) for _ in range(3)]
for x in held:
    x.__enter__()
print("held", flush=True)
time.sleep(60)
"""


def _max_overlap(spans):
    ev = sorted([(a, 1) for a, _ in spans] + [(b, -1) for _, b in spans])
    cur = best = 0
    for _, d in ev:
        cur += d
        best = max(best, cur)
    return best


def test_machine_slots_cap_real_processes_at_three(tmp_path):
    w = tmp_path / "worker.py"
    w.write_text(WORKER)
    out = tmp_path / "spans.txt"
    procs = [subprocess.Popen([sys.executable, "-I", str(w), str(BACKEND), str(tmp_path / "slots"), str(out)]) for _ in range(9)]
    for p in procs:
        assert p.wait(timeout=90) == 0
    spans = [tuple(map(float, ln.split())) for ln in out.read_text().splitlines()]
    assert len(spans) == 9
    assert _max_overlap(spans) == 3                                  # contention really happened (3) and never exceeded the provider's limit


def test_a_dead_process_cannot_leak_a_slot(tmp_path):
    h = tmp_path / "hold.py"
    h.write_text(HOLDER)
    p = subprocess.Popen([sys.executable, "-I", str(h), str(BACKEND), str(tmp_path / "s")], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    with pytest.raises(S.SlotTimeout):                               # all three are taken by the other process
        with S.MachineSlots(tmp_path / "s").hold(0.3):
            pass
    p.kill()
    p.wait()
    with S.MachineSlots(tmp_path / "s").hold(5):                     # the kernel released them when the holder died
        pass


def test_lease_table_enforces_three_expires_and_releases():
    t = [0.0]
    tab = LB.LeaseTable(3, ttl=10, clock=lambda: t[0])
    a, b, c = tab.acquire("a"), tab.acquire("b"), tab.acquire("c")
    assert all((a, b, c)) and tab.acquire("d") is None
    assert tab.release(b) and tab.acquire("d") is not None and tab.release("nope") is False
    t[0] = 11                                                         # every remaining lease has expired (a crashed client frees its lease on its own)
    assert tab.status()["in_use"] == 0 and tab.acquire("e") is not None


def test_broker_contract_requires_the_secret_and_a_configured_one():
    tab = LB.LeaseTable()
    assert LB.handle(tab, "s3cret", "POST", "/acquire", "wrong", {})[0] == 401
    assert LB.handle(tab, "", "POST", "/acquire", "", {})[0] == 401                  # no secret configured: refuses everything
    code, body = LB.handle(tab, "s3cret", "POST", "/acquire", "s3cret", {"holder": "vps"})
    assert code == 200 and "lease" in body
    assert LB.handle(tab, "s3cret", "GET", "/status", "s3cret", {})[1]["in_use"] == 1


def _serve(table, secret, port=0):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _do(self, method):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            code, out = LB.handle(table, secret, method, self.path, self.headers.get("X-Broker-Secret", ""), body)
            data = json.dumps(out).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            self._do("POST")

        def do_GET(self):
            self._do("GET")
    srv = HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_two_machines_through_the_broker_never_exceed_three():
    tab = LB.LeaseTable(3, ttl=30)
    srv = _serve(tab, "k")
    url = "http://127.0.0.1:%d" % srv.server_address[1]
    cur, peak, lock = [0], [0], threading.Lock()

    def one(name):
        c = LB.LeaseClient(url, "k", holder=name)
        with c.hold(20):
            with lock:
                cur[0] += 1
                peak[0] = max(peak[0], cur[0])
            time.sleep(0.15)
            with lock:
                cur[0] -= 1
    ts = [threading.Thread(target=one, args=("m%d" % (i % 2),)) for i in range(10)]     # ten requests from "two machines"
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    srv.shutdown()
    assert peak[0] == 3 and tab.status()["in_use"] == 0


def test_broker_down_fails_closed_unless_the_owner_chose_otherwise(monkeypatch):
    c = LB.LeaseClient("http://127.0.0.1:9", "k")
    with pytest.raises(S.SlotTimeout):
        with c.hold(0.4):
            pass
    monkeypatch.setenv("UW_BROKER_FAIL_OPEN", "1")
    with c.hold(0.4):                                                  # explicit owner choice
        pass


class _Opener:
    def __init__(self, script):
        self.script, self.calls = list(script), 0

    def open(self, req, timeout=None):
        self.calls += 1
        r = self.script.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class _Resp:
    headers = {}

    def read(self):
        return b'{"data": {"ok": 1}}'

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _h429(retry_after):
    return urllib.error.HTTPError("https://x", 429, "Too Many", {"Retry-After": str(retry_after)}, io.BytesIO(b'{"message":"concurrent_limit_exceeded"}'))


@pytest.fixture()
def quiet(monkeypatch):
    monkeypatch.setattr(uw, "api_key", lambda: "k" * 40)
    monkeypatch.setattr(uw, "MIN_INTERVAL", 0.0)
    monkeypatch.setattr(uw, "_blocked_until", 0.0)
    uw.set_background(False)
    slept = []
    monkeypatch.setattr(uw.time, "sleep", lambda s: slept.append(s))
    return slept


def test_retry_after_is_honoured_not_capped_at_five_seconds(monkeypatch, quiet):
    op = _Opener([_h429(12), _Resp()])
    monkeypatch.setattr(uw, "_OPENER", op)
    assert uw.get("/api/stock/AAPL/quote")["status"] == "OK"
    assert op.calls == 2 and max(quiet) >= 12                          # waited at least the 12 s the provider asked for
    monkeypatch.setattr(uw, "RETRY_AFTER_CAP", 20.0)
    op2 = _Opener([_h429(300), _Resp()])
    monkeypatch.setattr(uw, "_OPENER", op2)
    quiet.clear()
    uw.get("/api/stock/MSFT/quote")
    assert 12 < max(quiet) <= 20.0 + 0.001                             # an absurd Retry-After is bounded by the configured cap


def test_a_refused_slot_is_a_clean_rate_limited_answer_and_nothing_is_sent(monkeypatch, quiet):
    class Boom:
        def __enter__(self):
            raise S.SlotTimeout("no slot")

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(uw.uw_slots, "global_slot", lambda t: Boom())
    op = _Opener([_Resp()])
    monkeypatch.setattr(uw, "_OPENER", op)
    out = uw.get("/api/stock/AAPL/quote")
    assert out["status"] == "RATE_LIMITED" and "no slot" in out["detail"] and op.calls == 0


def test_the_layers_are_off_by_default(monkeypatch):
    monkeypatch.delenv("UW_MACHINE_SLOTS", raising=False)
    monkeypatch.delenv("UW_BROKER_URL", raising=False)
    assert S.machine() is None and S.broker() is None and S.status()["machine_slots"] is False
    with S.global_slot(1):
        pass
