"""
One computation per key.

The failure this prevents is not slowness, it is a pile-up: a page that gives
up on a slow fetch and lets the next request start the same fetch again gets
progressively slower, because every abandoned copy is still running and
competing for the same provider.
"""

import threading
import time

import singleflight


def test_concurrent_callers_share_one_run():
    runs = {"n": 0}
    started = threading.Event()
    release = threading.Event()

    def slow():
        runs["n"] += 1
        started.set()
        release.wait(2.0)
        return "value"

    key = f"test:share:{time.time()}"
    results = []

    def caller():
        results.append(singleflight.call(key, slow, timeout=3.0))

    threads = [threading.Thread(target=caller) for _ in range(4)]
    threads[0].start()
    started.wait(2.0)
    for t in threads[1:]:
        t.start()
    release.set()
    for t in threads:
        t.join(4.0)

    assert runs["n"] == 1, "four callers must not start four computations"
    assert all(r == ("value", True) for r in results)


def test_timeout_does_not_start_a_second_copy():
    """
    Giving up is the point -- but the work carries on, and the next caller
    joins it rather than starting again. This is exactly the case that made
    the overview take 16s, then 24s, then 36s for one symbol.
    """
    runs = {"n": 0}
    release = threading.Event()

    def slow():
        runs["n"] += 1
        release.wait(3.0)
        return "late"

    key = f"test:timeout:{time.time()}"

    value, done = singleflight.call(key, slow, timeout=0.2)
    assert value is None and done is False

    value, done = singleflight.call(key, slow, timeout=0.2)
    assert value is None and done is False
    assert runs["n"] == 1, "the second caller must join the first run"

    release.set()
    value, done = singleflight.call(key, slow, timeout=2.0)
    assert (value, done) == ("late", True)
    assert runs["n"] == 1


def test_a_finished_key_runs_again_next_time():
    runs = {"n": 0}

    def quick():
        runs["n"] += 1
        return runs["n"]

    key = f"test:done:{time.time()}"
    assert singleflight.call(key, quick, timeout=1.0) == (1, True)
    assert singleflight.call(key, quick, timeout=1.0) == (2, True)


def test_a_failure_is_reported_as_completed():
    """A provider that raises has answered. Retrying it on every panel render
    would hammer something already known to be failing."""
    def boom():
        raise RuntimeError("provider down")

    key = f"test:boom:{time.time()}"
    value, done = singleflight.call(key, boom, timeout=1.0)
    assert value is None and done is True


def test_two_chain_requests_share_one_build(monkeypatch):
    """
    The chain window is ten seconds during market hours and the page polls.
    Two viewers asking in the same moment must not each start a full round of
    TWS snapshots for the same ladder.
    """
    import threading
    import time

    import live_options_service as opt

    builds = {"n": 0}
    release = threading.Event()

    def slow_build(symbol, expiry=None, extra_expiries=0):
        builds["n"] += 1
        release.wait(2.0)
        return {"symbol": symbol, "status": "OK", "rows": [1]}

    monkeypatch.setattr(opt, "_load_chain", slow_build)
    symbol = f"TST{int(time.time() * 1000) % 100000}"

    results = []
    threads = [threading.Thread(target=lambda: results.append(opt.load_chain(symbol)))
               for _ in range(3)]
    for t in threads:
        t.start()
    time.sleep(0.3)
    release.set()
    for t in threads:
        t.join(4.0)

    assert builds["n"] == 1, "three simultaneous requests, one TWS build"
    assert len(results) == 3 and all(r["status"] == "OK" for r in results)
