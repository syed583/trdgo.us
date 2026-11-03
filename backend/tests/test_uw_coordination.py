"""Shared-token coordination, end to end: several PROCESSES (standing in for several machines) call the real Unusual Whales client through one lease broker; leases expire when a client dies; the broker can restart or
vanish; Retry-After from the broker is honoured. The provider is replaced by a fake opener that records how many requests were in flight."""
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))
import uw_lease_broker as LB  # noqa: E402
import uw_slots as S  # noqa: E402
from test_uw_concurrency import _max_overlap, _serve  # noqa: E402

CLIENT = """
import os, sys, time, threading
sys.path.insert(0, sys.argv[1])
os.environ["UW_BROKER_URL"] = sys.argv[2]
os.environ["UW_BROKER_SECRET"] = "k"
os.environ["SESSION_SECRET"] = "x"
import unusualwhales_service as uw
uw.api_key = lambda: "k" * 40
uw.MIN_INTERVAL = 0.0
spans = sys.argv[3] + "." + str(os.getpid())          # one file per process: appends from several processes are not atomic on Windows
lock = threading.Lock()


class Resp:
    headers = {}

    def read(self):
        return b'{"data": {}}'

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Op:
    def open(self, req, timeout=None):
        t0 = time.time(); time.sleep(0.12); t1 = time.time()
        with lock, open(spans, "a") as f:
            f.write("%f %f\\n" % (t0, t1))
        return Resp()


uw._OPENER = Op()
res = []
ts = [threading.Thread(target=lambda i=i: res.append(uw.get("/api/stock/S%d/quote" % i)["status"])) for i in range(6)]
[t.start() for t in ts]
[t.join(60) for t in ts]
print(",".join(sorted(set(res))))
"""


def test_four_machines_calling_the_real_client_never_exceed_three_in_flight_in_total(tmp_path):
    tab = LB.LeaseTable(3, ttl=30)
    srv = _serve(tab, "k")
    url = "http://127.0.0.1:%d" % srv.server_address[1]
    script = tmp_path / "client.py"
    script.write_text(CLIENT)
    spans = tmp_path / "spans.txt"
    procs = [subprocess.Popen([sys.executable, "-I", str(script), str(BACKEND), url, str(spans)], stdout=subprocess.PIPE, text=True) for _ in range(4)]
    outs = [p.communicate(timeout=120)[0].strip() for p in procs]
    srv.shutdown()
    rows = [tuple(map(float, ln.split())) for f in tmp_path.glob("spans.txt.*") for ln in f.read_text().splitlines()]
    assert len(rows) == 24 and outs == ["OK"] * 4                      # 24 requests from 4 "machines" all completed
    assert _max_overlap(rows) == 3                                     # in-process caps alone would allow 12; the broker held the whole fleet to three
    assert tab.status()["in_use"] == 0                                 # every lease was returned


def test_a_client_that_dies_holding_a_lease_frees_it_after_the_ttl():
    tab = LB.LeaseTable(3, ttl=0.6)
    srv = _serve(tab, "k")
    url = "http://127.0.0.1:%d" % srv.server_address[1]
    dead = LB.LeaseClient(url, "k", holder="crashed")
    for _ in range(3):
        assert dead._call("/acquire", {"holder": "crashed"})[0] == 200          # takes all three and never releases (a crash)
    t0 = time.time()
    with LB.LeaseClient(url, "k", holder="healthy").hold(10):
        waited = time.time() - t0
    srv.shutdown()
    assert 0.4 <= waited < 3.0                                         # it had to wait for the expiry, and did not wait forever


def test_a_broker_restart_forgets_leases_but_clients_recover_and_the_cap_still_holds():
    tab1 = LB.LeaseTable(3, ttl=30)
    srv1 = _serve(tab1, "k")
    port = srv1.server_address[1]
    url = "http://127.0.0.1:%d" % port
    c = LB.LeaseClient(url, "k", holder="m1")
    with c.hold(5):
        srv1.shutdown()
        srv1.server_close()                                            # the broker vanishes while a request is in flight
    # release failed silently (the lease would simply expire); a new broker comes up on the same port with an empty table
    tab2 = LB.LeaseTable(3, ttl=30)
    srv2 = None
    for _ in range(30):
        try:
            srv2 = _serve(tab2, "k", port)
            break
        except OSError:
            time.sleep(0.1)
    if srv2 is None:
        pytest.skip("could not rebind the broker port on this machine")
    with c.hold(5):
        assert tab2.status()["in_use"] == 1 and tab2.acquire("a") and tab2.acquire("b") and tab2.acquire("c") is None     # the new table enforces three again
    srv2.shutdown()


def test_the_client_honours_the_brokers_retry_after_as_a_floor(monkeypatch):
    sleeps = []
    monkeypatch.setattr(LB.time, "sleep", lambda s: sleeps.append(s))
    answers = [(429, {"retry_in": 0.4}), (429, {"retry_in": 0.7}), (200, {"lease": "L1"})]
    c = LB.LeaseClient("http://x", "k")
    monkeypatch.setattr(c, "_call", lambda path, body: answers.pop(0) if path == "/acquire" else (200, {}))
    with c.hold(10):
        pass
    assert len(sleeps) == 2 and sleeps[0] >= 0.4 and sleeps[1] >= 0.7


def test_the_broker_going_away_mid_run_fails_closed_for_new_requests():
    tab = LB.LeaseTable(3, ttl=30)
    srv = _serve(tab, "k")
    c = LB.LeaseClient("http://127.0.0.1:%d" % srv.server_address[1], "k")
    with c.hold(5):
        pass
    srv.shutdown()
    srv.server_close()
    with pytest.raises(S.SlotTimeout):
        with c.hold(0.5):
            pass
