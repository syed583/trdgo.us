import time

import boot_guard as bg
import swr


def test_no_record_means_due_now(monkeypatch):
    monkeypatch.setattr(bg, "last_run", lambda n: None)
    assert bg.seconds_until_due("x", 3600) == 0.0
    assert not bg.recent("x", 3600)
    slept = []
    assert bg.wait_until_due("x", 3600, sleep=slept.append) == 0.0 and slept == []


def test_recent_run_defers_the_next_pass(monkeypatch):
    monkeypatch.setattr(bg, "last_run", lambda n: time.time() - 600)
    assert bg.recent("x", 3600)
    wait = bg.seconds_until_due("x", 3600)
    assert 2900 < wait <= 3000
    slept = []
    bg.wait_until_due("x", 3600, sleep=slept.append)
    assert slept and 2900 < slept[0] <= 3000


def test_old_run_is_due(monkeypatch):
    monkeypatch.setattr(bg, "last_run", lambda n: time.time() - 7200)
    assert bg.seconds_until_due("x", 3600) == 0.0


def test_mark_and_read_back(monkeypatch):
    store = {}
    import db_cache
    monkeypatch.setattr(db_cache, "put", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(db_cache, "get", lambda k: (time.time(), store[k]) if k in store else None)
    assert bg.last_run("loop-a") is None
    bg.mark("loop-a")
    assert abs(bg.last_run("loop-a") - time.time()) < 5


def test_warm_once_skips_keys_still_fresh_in_the_db_tier(monkeypatch):
    """After a restart a key this process never built must not be rebuilt if the DB copy is fresh."""
    import db_cache
    swr.clear()
    built = []
    now = time.time()
    swr._fns["fresh-key"] = (lambda: built.append("fresh") or {"v": 1}, 600.0)
    swr._fns["stale-key"] = (lambda: built.append("stale") or {"v": 2}, 600.0)
    swr._asked["fresh-key"] = swr._asked["stale-key"] = now
    monkeypatch.setattr(db_cache, "get_many",
                        lambda ks: {"fresh-key": (now - 60, {"v": 1}), "stale-key": (now - 3600, {"v": 2})})
    monkeypatch.setattr(swr, "db_cache", db_cache)
    swr.warm_once()
    assert built == ["stale"]                      # only the genuinely stale key was rebuilt
    assert swr._values["fresh-key"][1] == {"v": 1}
    swr.clear()


def test_demo_tracker_ticks_only_when_needed(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import demo_trade_service as dts
    et = ZoneInfo("America/New_York")
    monkeypatch.setattr(dts, "has_open_trades", lambda: False)
    assert dts.should_tick(datetime(2026, 10, 22, 11, 0, tzinfo=et))          # entries allowed
    assert not dts.should_tick(datetime(2026, 10, 22, 22, 0, tzinfo=et))      # night, nothing open
    assert not dts.should_tick(datetime(2026, 10, 24, 11, 0, tzinfo=et))      # Saturday
    monkeypatch.setattr(dts, "has_open_trades", lambda: True)
    assert dts.should_tick(datetime(2026, 10, 22, 22, 0, tzinfo=et))          # still has to close trades
