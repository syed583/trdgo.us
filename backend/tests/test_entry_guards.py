"""Every path that opens a NEW trade asks the 15-minute gate itself, at the moment of opening; exits and other
risk-reducing actions never do. The gate has no switch. Everything is mocked: no API calls, no production DB."""
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, text

import demo_trade_service as dts
import entry_lock
import intraday_15m as c15
import intraday_safety as safety
import swr

ET = ZoneInfo("America/New_York")
RTH = datetime(2026, 10, 22, 11, 0, tzinfo=ET)


def seed(symbol="AAA", bias=+1, end_ago=300.0, status="OK", fetched_ago=60.0):
    t = time.time()
    swr._values[c15.KEY.format(symbol)] = (t, {
        "symbol": symbol, "status": status, "bias": bias, "votes": {"vwap": bias, "ema": bias, "rsi": bias},
        "bars": 60, "rejected": {}, "detail": "15m reading", "ts": t - fetched_ago, "last_bar_end_epoch": t - end_ago})


# ---------------------------------------------------------------- the gate itself ---
@pytest.mark.parametrize("kw, status", [
    (dict(), "CONFIRMED"),
    (dict(bias=-1), "CONFLICT"), (dict(bias=0), "MIXED"),
    (dict(end_ago=1500.0), "STALE"),
    (dict(status="INVALID"), "INVALID"), (dict(status="WARMUP"), "WARMUP"),
])
def test_only_a_fresh_valid_agreeing_reading_allows_a_buy(kw, status):
    seed(**kw)
    out = c15.entry_check("AAA", "BUY", "rth")
    assert out["status"] == status and out["allowed"] is (status == "CONFIRMED")


def test_a_missing_reading_and_a_non_trade_side_never_allow():
    assert c15.entry_check("ZZZ", "BUY", "rth")["status"] == "MISSING"
    for side in (None, "NEUTRAL", "WAIT", "HOLD"):
        assert c15.entry_check("AAA", side, "rth")["allowed"] is False


def test_sell_is_confirmed_by_a_bearish_reading_only():
    seed(bias=-1)
    assert c15.entry_check("AAA", "SELL", "rth")["allowed"] is True
    assert c15.entry_check("AAA", "BUY", "rth")["allowed"] is False


def test_the_operator_lock_blocks_the_moment_of_opening_but_not_the_display(monkeypatch):
    seed(bias=+1)
    monkeypatch.setattr(entry_lock, "is_locked", lambda *a, **k: True)
    assert c15.entry_check("AAA", "BUY", "rth")["allowed"] is True               # the board may still show the signal
    out = c15.entry_check("AAA", "BUY", "rth", execution=True)
    assert out["allowed"] is False and out["status"] == "ENTRIES_LOCKED"


def test_an_unreadable_lock_refuses_a_new_entry(monkeypatch):
    seed(bias=+1)

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(entry_lock, "is_locked", boom)
    assert c15.entry_check("AAA", "BUY", "rth", execution=True)["status"] == "ENTRIES_LOCKED"


def test_tradable_asks_the_gate_directly_even_for_a_row_that_looked_fine_earlier():
    seed(bias=+1)
    r = safety.gate({"symbol": "AAA", "signal": "BUY", "score": 72.0, "coverage": 100.0, "now": 100.0, "entry": 100.0,
                     "params": []}, phase="rth", signals_age_s=30.0, price_age=30.0)
    assert r["signal"] == "BUY" and safety.tradable(r)
    seed(bias=-1)                                                                # the 15-minute picture flipped since
    assert not safety.tradable(r)                                                # the cached row can no longer enter
    swr._values.pop(c15.KEY.format("AAA"), None)                                 # or the reading vanished
    assert not safety.tradable(r)


# -------------------------------------------------------------- the paper-trade tracker ---
class _Sess:
    def __init__(self):
        self.added, self.committed = [], False

    def query(self, *a, **k):
        return self

    def filter_by(self, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def execute(self, *a, **k):
        return self

    def all(self):
        return []

    def add(self, x):
        self.added.append(x)

    def commit(self):
        self.committed = True

    def close(self):
        pass


def _tick(monkeypatch, bias, *, locked=False):
    seed(bias=bias)
    s = _Sess()
    import database
    monkeypatch.setattr(database, "SessionLocal", lambda: s)
    monkeypatch.setattr(dts, "ensure_schema", lambda: None)
    monkeypatch.setattr(dts, "_et_now", lambda: RTH)
    monkeypatch.setattr(dts, "_can_open", lambda n=None: True)
    monkeypatch.setattr(dts, "_board", lambda spec: {"buyers": [{"symbol": "AAA", "signal": "BUY", "score": 72.0,
                                                                  "entry": 100.0, "now": 100.0, "sl": 99.0, "tp": 102.0,
                                                                  "data": {"ok": True}}]})
    monkeypatch.setattr(dts, "MODELS", {"grok": ("Grok", "x:y")})
    monkeypatch.setattr(dts, "_live_price", lambda *a, **k: 100.0)
    monkeypatch.setattr(safety, "tradable", lambda r: True)                      # prove the LAST gate stands on its own
    monkeypatch.setattr(entry_lock, "is_locked", lambda *a, **k: locked)
    dts.tick(force=True)
    return s


def test_the_tracker_opens_only_on_a_confirmed_reading_at_the_moment_of_opening(monkeypatch):
    assert len(_tick(monkeypatch, +1).added) == 1                                 # confirmed: it opens
    assert _tick(monkeypatch, -1).added == []                                     # conflicting: it does not
    assert _tick(monkeypatch, 0).added == []                                      # mixed: it does not
    assert _tick(monkeypatch, +1, locked=True).added == []                        # locked: it does not


def test_the_tracker_still_commits_exits_when_entries_are_blocked(monkeypatch):
    s = _tick(monkeypatch, -1)
    assert s.committed is True                                                    # the tick (exits included) completes


# -------------------------------------------------------------------- the live engine ---
class _Rec:
    def __init__(self):
        self.sql = []

    def execute(self, stmt, params=None):
        self.sql.append(str(stmt))
        return self


def _engine_scan(monkeypatch, bias):
    import intraday_signal_v2 as eng
    seed(bias=bias)
    pend = {"side": "BUY", "count": 1, "last_ts": datetime(2026, 10, 22, 10, 50, tzinfo=ET)}
    monkeypatch.setattr(eng, "_pending", lambda *a, **k: pend)
    monkeypatch.setattr(eng, "_open_rows", lambda *a, **k: [])                   # this fake session cannot read rows
    monkeypatch.setattr(eng, "step_public_new", lambda *a, **k: {"state": "BUY"})
    monkeypatch.setattr(entry_lock, "is_locked", lambda *a, **k: False)
    sc = {"call": "BUY", "side": "BUY", "score": 72.0, "spot": 100.0, "atr": 2.0, "status": "OK"}
    rec = _Rec()
    out = eng._maybe_open(rec, "AAA", "2026-10-22", sc, RTH)
    return rec, out


def test_the_live_engine_opens_a_signal_only_when_the_reading_confirms(monkeypatch):
    rec, out = _engine_scan(monkeypatch, +1)
    assert any("INSERT INTO intraday_sig_v2 " in q for q in rec.sql) and out
    rec, out = _engine_scan(monkeypatch, -1)
    assert not any("INSERT INTO intraday_sig_v2 " in q for q in rec.sql) and out is None


def test_the_live_engine_rechecks_at_the_insert_and_drops_the_pending_signal(monkeypatch):
    import intraday_signal_v2 as eng
    seed(bias=+1)
    calls = []
    real = c15.entry_check

    def flaky(symbol, side, phase=None, *, execution=False, now=None):
        calls.append(execution)
        if not execution:
            return real(symbol, side, phase, execution=execution, now=now)
        return {"allowed": False, "status": "STALE", "reason": "went stale between scans", "confirm15": None}
    monkeypatch.setattr(c15, "entry_check", flaky)
    monkeypatch.setattr(eng, "_pending", lambda *a, **k: {"side": "BUY", "count": 1,
                                                          "last_ts": datetime(2026, 10, 22, 10, 50, tzinfo=ET)})
    rec = _Rec()
    out = eng._maybe_open(rec, "AAA", "2026-10-22", {"call": "BUY", "side": "BUY", "score": 72.0, "spot": 100.0,
                                                    "atr": 2.0, "status": "OK"}, RTH)
    assert out is None and calls == [False, True]
    assert not any("INSERT INTO intraday_sig_v2 " in q for q in rec.sql)
    assert any("DELETE FROM intraday_sig_v2_pending" in q for q in rec.sql)


def test_exits_never_consult_the_entry_gate():
    import intraday_signal_v2 as eng
    assert "entry_check" not in eng._manage_open.__code__.co_names
    assert "entry_check" not in dts._exit_reason.__code__.co_names
    assert "entry_check" not in dts._exit_timed.__code__.co_names


# ------------------------------------------------------------------ the entry lock ---
@pytest.fixture
def lock_db():
    eng = create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE demo_trades (id INTEGER PRIMARY KEY, status TEXT)"))
        c.execute(text("CREATE TABLE intraday_sig_v2 (id INTEGER PRIMARY KEY, is_open BOOLEAN, session_date TEXT)"))
    return eng


def test_install_creates_the_table_and_triggers_and_starts_locked(lock_db):
    r = entry_lock.install(lock_db)
    assert sorted(r["installed_on"]) == ["demo_trades", "intraday_sig_v2"]
    s = entry_lock.status(lock_db)
    assert s["installed"] and s["locked"] is True and set(s["triggers"]) == {"demo_trades", "intraday_sig_v2"}
    assert entry_lock.is_locked(lock_db) is True


def test_the_trigger_rejects_inserts_while_locked_for_any_build_and_allows_updates(lock_db):
    entry_lock.install(lock_db)
    with pytest.raises(Exception) as e:                                           # locked: even an OLDER build fails
        with lock_db.begin() as c:
            c.execute(text("INSERT INTO demo_trades (status) VALUES ('OPEN')"))
    assert "locked" in str(e.value).lower()
    entry_lock.set_locked(False, "verified", lock_db)
    with lock_db.begin() as c:
        c.execute(text("INSERT INTO demo_trades (status) VALUES ('OPEN')"))
    entry_lock.set_locked(True, "rollback", lock_db)
    with lock_db.begin() as c:                                                    # exits (UPDATE) are never blocked
        c.execute(text("UPDATE demo_trades SET status = 'STOP' WHERE status = 'OPEN'"))
        assert c.execute(text("SELECT status FROM demo_trades")).scalar() == "STOP"


def test_locking_for_a_rollback_refuses_while_a_position_is_open(lock_db):
    entry_lock.set_locked(False, "live", lock_db)
    with lock_db.begin() as c:
        c.execute(text("INSERT INTO demo_trades (status) VALUES ('OPEN')"))
        c.execute(text("INSERT INTO intraday_sig_v2 (is_open, session_date) VALUES (1, date('now'))"))
    r = entry_lock.set_locked(True, "rollback", lock_db, for_rollback=True)
    assert r["ok"] is False and r["open_positions"] == {"demo_trades": 1, "intraday_sig_v2": 1}
    assert entry_lock.is_locked(lock_db) is False                                  # nothing changed
    with lock_db.begin() as c:
        c.execute(text("UPDATE demo_trades SET status = 'TARGET'"))
        c.execute(text("UPDATE intraday_sig_v2 SET is_open = 0"))
    assert entry_lock.set_locked(True, "rollback", lock_db, for_rollback=True)["ok"] is True


def test_no_lock_table_means_not_locked_but_an_unreadable_one_means_locked(lock_db):
    assert entry_lock.is_locked(lock_db) is False

    class Broken:
        def connect(self):
            raise RuntimeError("down")
    assert entry_lock.is_locked(Broken()) is True
