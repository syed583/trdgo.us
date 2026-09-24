"""Served from the last answer; refreshed behind it; the first caller waits."""

import time

import singleflight
import swr


def setup_function():
    swr.clear()
    singleflight.reset()


def test_first_call_builds_then_later_calls_are_instant():
    calls = {"n": 0}

    def slow():
        calls["n"] += 1
        return {"n": calls["n"]}

    assert swr.serve("k", slow, fresh_for=60) == {"n": 1}
    assert swr.serve("k", slow, fresh_for=60) == {"n": 1}
    assert calls["n"] == 1


def test_a_stale_answer_is_returned_now_and_refreshed_behind():
    calls = {"n": 0}

    def build():
        calls["n"] += 1
        return calls["n"]

    swr.serve("k", build, fresh_for=0)
    started = time.monotonic()
    assert swr.serve("k", build, fresh_for=0) == 1  # the held answer, at once
    assert time.monotonic() - started < 0.5
    # The refresh runs on a background daemon thread; on a loaded box (a
    # deploy running the build and the service at once) it can take a moment
    # to be scheduled, so this waits generously rather than racing it.
    deadline = time.monotonic() + 6
    while calls["n"] < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert swr.serve("k", build, fresh_for=60) == 2


def test_a_failed_refresh_keeps_the_last_good_answer():
    swr.serve("k", lambda: "good", fresh_for=0)

    def boom():
        raise RuntimeError("provider down")

    assert swr.serve("k", boom, fresh_for=0) == "good"
    time.sleep(0.1)
    assert swr.serve("k", boom, fresh_for=60) == "good"
