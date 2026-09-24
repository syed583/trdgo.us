"""
The gather's time budget has to be real.

Twelve provider calls run in parallel behind a 75-second budget. They ran
inside a `with ThreadPoolExecutor(...)` block, and leaving one calls
shutdown(wait=True) -- so after the budget expired the code waited for every
straggler anyway. One slow provider held the entire analysis until the
stream gave up at two minutes, with stages still spinning on screen, and the
budget above it was decorative.
"""

import time

import directional_score_service as ds


def test_a_hung_job_cannot_outlast_the_budget(monkeypatch):
    """The test that would have caught this: one job that never returns."""
    monkeypatch.setattr(ds, "OVERVIEW_BUDGET", 1.5)

    started = []

    def hang():
        started.append(1)
        time.sleep(30)          # longer than any budget here
        return {"late": True}

    import live_options_analytics as options
    monkeypatch.setattr(options, "get_overview", lambda s: hang())

    begin = time.time()
    ds._gather("NVDA")
    elapsed = time.time() - begin

    assert started, "the slow job should still have been started"
    assert elapsed < 20, (
        f"the gather took {elapsed:.1f}s against a 1.5s budget -- it is "
        "waiting for a straggler instead of leaving it behind")


def test_the_pool_is_not_a_context_manager():
    """
    Pinning the shape, not just the timing: a `with` block here reintroduces
    shutdown(wait=True) and the budget silently stops meaning anything.
    """
    import inspect

    source = inspect.getsource(ds._gather)
    assert "with ThreadPoolExecutor" not in source, \
        "leaving a with-block waits for every straggler"
    assert "cancel_futures=True" in source
