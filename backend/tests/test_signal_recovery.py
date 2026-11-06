"""A forward cut-over may carry open live signals only if the new engine can safely recover and manage every one of them
(signal_recovery.py); open paper positions must still be zero; a rollback still needs an empty book. Plus the recovery
behaviour itself: late-session signals, overnight / weekend carry, duplicate symbols, restart recovery and the exit
arithmetic of a recovered row (BUY and SELL symmetric)."""
import copy
import types
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import entry_lock
import intraday_signal_v2 as eng
import market_calendar as mc
import release_check
import release_tool as rt
import signal_recovery as sr

ET = ZoneInfo("America/New_York")


def et(y, m, d, hh=10, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


MON, TUE, WED, FRI = (2026, 10, 19), (2026, 10, 20), (2026, 10, 21), (2026, 10, 23)
NIGHT = et(*TUE, 21, 0)                       # Tuesday 21:00 ET: entries are impossible by time
CUT = "2026-10-19"                            # on Tuesday the carry window starts on Monday


def good(**kw):
    r = {"id": 1, "symbol": "AAA", "session_date": "2026-10-20", "state": "BUY", "side": "BUY", "is_open": 1, "entry": 100.0,
         "tp": 102.0, "sl": 99.0, "best_price": 100.0, "atr": 2.0, "be_moved": 0, "score_in": 72.0,
         "signal_ts": "2026-10-20T15:19:00-04:00", "status_ts": "2026-10-20T15:19:00-04:00"}
    r.update(kw)
    return r


def sell(**kw):
    return good(**{"state": "SELL", "side": "SELL", "tp": 98.0, "sl": 101.0, **kw})


def check(rows, **kw):
    return sr.validate(rows, now=NIGHT, cutoff=CUT, **kw)


# ============================================================ the validator ===
def test_valid_buy_and_sell_signals_are_recoverable_symmetrically():
    r = check([good(id=1, symbol="AAA"), sell(id=2, symbol="BBB", state="STRONG SELL"), good(id=3, symbol="CCC", state="STRONG BUY")])
    assert r["ok"] and r["fresh"] == 3 and r["stale"] == 0 and r["problems"] == []


@pytest.mark.parametrize("row, needle", [
    (good(state="SELL"), "does not agree with side"),                                   # state vs side
    (sell(state="BUY"), "does not agree with side"),
    (good(side="HOLD"), "neither BUY nor SELL"),
    (good(is_open=0), "is_open is not true"),
    (good(symbol="SPX"), "not a US stock"),                                             # an index
    (good(symbol="/ES"), "not a US stock"),                                             # a future
    (good(symbol="ES=F"), "not a US stock"),
    (good(symbol="bad symbol!"), "not a US stock"),
    (good(symbol=None), "not a US stock"),
    (good(session_date="2026-10-21"), "in the future"),                                 # the session has not happened
    (good(session_date="nonsense"), "session_date is not a date"),
    (good(signal_ts="2026-10-19T15:19:00-04:00"), "not on session"),                    # timestamp on another day
    (good(signal_ts="2026-10-20T23:30:00-04:00"), "in the future"),                     # after "now" (Tue 21:00)
    (good(signal_ts=None), "signal_ts is missing"),
    (good(signal_ts="garbage"), "signal_ts is missing"),
    (good(status_ts="2026-10-20T14:00:00-04:00"), "status_ts is before signal_ts"),
    (good(status_ts=None), "status_ts is missing"),
    (good(entry=None), "entry is not a positive number"),
    (good(entry=-1.0), "entry is not a positive number"),
    (good(entry=float("nan")), "entry is not a positive number"),
    (good(best_price=0), "best_price is not a positive number"),
    (good(atr=-0.5), "atr is negative"),
    (good(tp=99.0, sl=101.0), "BUY levels are inconsistent"),                           # reversed levels
    (sell(tp=102.0, sl=99.0), "SELL levels are inconsistent"),
    (good(sl=100.0), "BUY levels are inconsistent"),                                    # sl == entry
    (good(tp=None), "only one of tp / sl"),
    (good(tp=None, sl=None), "empty although an ATR is recorded"),
    (good(be_moved="maybe"), "be_moved is not true/false"),
    (good(id=None), "no id"),
])
def test_each_kind_of_defect_blocks(row, needle):
    r = check([row])
    assert not r["ok"] and any(needle in p for p in r["problems"]), r["problems"]


def test_levels_may_be_empty_only_when_there_is_no_atr():
    assert check([good(tp=None, sl=None, atr=None)])["ok"] and check([good(tp=None, sl=None, atr=0)])["ok"]


def test_duplicates_are_conflicts_and_opposite_sides_are_named():
    same = check([good(id=1), good(id=2)])
    assert not same["ok"] and any("AAA has 2 open signals (the same side)" in p for p in same["problems"])
    opp = check([good(id=1), sell(id=2)])
    assert not opp["ok"] and any("opposite sides" in p and "1, 2" in p for p in opp["problems"])


def test_stale_rows_are_counted_and_never_block():
    stale = good(id=9, session_date="2026-10-16", signal_ts="garbage", entry=None, symbol="SPX")      # hopeless, but stale
    r = check([stale, good(id=10, symbol="AAA")])
    assert r["ok"] and r["stale"] == 1 and r["fresh"] == 1
    assert check([stale])["ok"]


def test_missing_columns_matter_only_when_a_signal_is_open():
    assert check([], columns=["id", "is_open", "session_date"])["ok"]
    r = check([good()], columns=["id", "is_open", "session_date"])
    assert not r["ok"] and any("lacks columns" in p for p in r["problems"])


def test_the_validator_never_raises_on_garbage():
    for rows in ([None], ["x"], [{"id": 1, "session_date": object()}], [{"symbol": ["a"]}]):
        r = check(rows)
        assert r["ok"] is False and r["problems"]
    assert sr.validate([good()], now=NIGHT, cutoff=None)["ok"] is False                       # unknown cutoff: refuse


def test_timestamps_from_a_driver_and_from_text_are_both_read():
    aware = datetime(2026, 10, 20, 19, 19, tzinfo=timezone.utc)                                # 15:19 ET
    r = check([good(signal_ts=aware, status_ts=aware, session_date=aware.astimezone(ET).date())])
    assert r["ok"], r["problems"]
    assert check([good(signal_ts="2026-10-20T19:19:00Z", status_ts="2026-10-20T19:19:00Z")])["ok"]


def test_instrument_kind_matches_the_calendar_module():
    for sym in ("AAPL", "SPY", "BRK.B", "ES", "/ES", "ES=F", "SPX", "^VIX", "VIX", "", "A B", None):
        assert sr.instrument_kind(sym) == mc.instrument_kind(sym or ""), sym


def test_the_cutoff_matches_the_lifecycle_rule():
    assert sr.cutoff_iso(et(*TUE, 21)) == "2026-10-19"
    assert sr.cutoff_iso(et(*MON, 21)) == "2026-10-16"                                          # Monday looks back to Friday
    assert sr.cutoff_iso(et(2026, 10, 24, 12)) == "2026-10-23"                                  # Saturday: Friday is the window start
    assert sr.cutoff_iso(et(*TUE, 21)) == entry_lock._cutoff(et(*TUE, 21))


# ============================================ the cut-over preflight and rollback ===
@pytest.fixture
def book(monkeypatch):
    engine = create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE demo_trades (id INTEGER PRIMARY KEY, status TEXT)"))
        c.execute(text("CREATE TABLE intraday_sig_v2 (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, session_date TEXT, state TEXT,"
                       " side TEXT, is_open BOOLEAN DEFAULT 1, entry REAL, now_price REAL, exit_price REAL, tp REAL, sl REAL,"
                       " best_price REAL, atr REAL, be_moved BOOLEAN DEFAULT 0, score_in REAL, score_now REAL, signal_ts TEXT,"
                       " status_ts TEXT, exit_reason TEXT, was_state TEXT)"))
    monkeypatch.setattr(entry_lock, "_cutoff", lambda now=None: CUT)
    monkeypatch.setattr(release_check, "check", lambda reader: {"ok": True, "checks": []})
    monkeypatch.setattr(release_check, "identify", lambda repo: {"head": "abc123", "uncommitted_changes": False})
    return engine


def put(engine, **kw):
    r = good(**kw)
    r.pop("id", None) if kw.get("id") is None else None
    cols = ", ".join(r)
    with engine.begin() as c:
        c.execute(text(f"INSERT INTO intraday_sig_v2 ({cols}) VALUES ({', '.join(':' + k for k in r)})"), r)


def pre(engine):
    return rt.preflight("repo", "HEAD", now=NIGHT, engine=engine, reader=lambda p: "")


def test_late_valid_signals_do_not_block_a_forward_cutover(book):
    put(book, symbol="INTC", id=None); put(book, symbol="MSFT", id=None)
    put(book, symbol="NVDA", id=None, **{k: v for k, v in sell().items() if k in ("state", "side", "tp", "sl")})
    r = pre(book)
    assert r["ok"], r["problems"]
    assert r["signals"]["fresh"] == 3 and r["signals"]["ok"] is True


def test_open_paper_positions_still_block_even_when_every_signal_is_fine(book):
    put(book, symbol="INTC", id=None)
    with book.begin() as c:
        c.execute(text("INSERT INTO demo_trades (status) VALUES ('OPEN')"))
    r = pre(book)
    assert not r["ok"] and any("open paper positions exist: 1" in p for p in r["problems"])


def test_one_bad_signal_blocks_the_cutover_and_is_named(book):
    put(book, symbol="INTC", id=None)
    put(book, symbol="MSFT", id=None, tp=99.0, sl=101.0)                                      # reversed BUY levels
    r = pre(book)
    assert not r["ok"] and any("open signals cannot be recovered safely" in p and "MSFT" in p for p in r["problems"])


def test_a_duplicate_symbol_blocks_the_cutover(book):
    put(book, symbol="AMD", id=None); put(book, symbol="AMD", id=None, **{k: v for k, v in sell().items() if k in ("state", "side", "tp", "sl")})
    r = pre(book)
    assert not r["ok"] and any("AMD has 2 open signals (opposite sides)" in p for p in r["problems"])


def test_stale_signals_are_reported_and_do_not_block(book):
    for i, s in enumerate(("ORCL", "MSFT", "NVDA")):
        put(book, symbol=s, id=None, session_date="2026-10-16", signal_ts="2026-10-16T15:00:00-04:00", status_ts="2026-10-16T15:00:00-04:00")
    put(book, symbol="QCOM", id=None)
    r = pre(book)
    assert r["ok"], r["problems"]
    assert (r["signals"]["fresh"], r["signals"]["stale"]) == (1, 3)


def test_the_cutover_never_changes_a_signal_record(book):
    put(book, symbol="INTC", id=None); put(book, symbol="ORCL", id=None, session_date="2026-10-16")
    with book.connect() as c:
        before = [dict(r) for r in c.execute(text("SELECT * FROM intraday_sig_v2 ORDER BY id")).mappings()]
    pre(book); sr.assess(book, now=NIGHT)
    with book.connect() as c:
        assert [dict(r) for r in c.execute(text("SELECT * FROM intraday_sig_v2 ORDER BY id")).mappings()] == before


def test_an_unreadable_signal_table_blocks_instead_of_passing():
    class Broken:
        def connect(self):
            raise RuntimeError("db down")
    r = sr.assess(Broken(), now=NIGHT)
    assert r["ok"] is False and r["problems"]
    class NoTable:
        pass
    assert sr.assess(NoTable(), now=NIGHT)["ok"] is False


def test_no_signal_table_means_nothing_is_open(book):
    other = create_engine("sqlite://")
    assert sr.assess(other, now=NIGHT)["ok"] is True


def test_rollback_stays_blocked_while_ANY_open_signal_exists_recoverable_or_not(book):
    put(book, symbol="INTC", id=None)                                                          # valid and recoverable
    r = rt.rollback_prepare(engine=book)
    assert r["ok"] is False and r["open_positions"]["intraday_sig_v2"] == 1 and "empty book" in r["why"]
    put(book, symbol="MSFT", id=None, tp=99.0, sl=101.0)                                       # incompatible too
    assert rt.rollback_prepare(engine=book)["ok"] is False
    with book.begin() as c:
        c.execute(text("UPDATE intraday_sig_v2 SET is_open = 0"))
    assert rt.rollback_prepare(engine=book)["ok"] is True                                      # flat: allowed


def test_rollback_ignores_stale_rows_nothing_can_manage(book):
    put(book, symbol="ORCL", id=None, session_date="2026-10-16")
    assert rt.rollback_prepare(engine=book)["ok"] is True


# ====================================== the new engine recovers and manages them ===
@pytest.fixture
def sigdb(monkeypatch):
    engine = create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE intraday_sig_v2 (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, session_date TEXT,"
                       " state TEXT, side TEXT, is_open BOOLEAN DEFAULT 1, entry REAL, now_price REAL, exit_price REAL,"
                       " tp REAL, sl REAL, best_price REAL, atr REAL, be_moved BOOLEAN DEFAULT 0, score_in REAL,"
                       " score_now REAL, signal_ts TEXT, status_ts TEXT, exit_reason TEXT, was_state TEXT)"))
        c.execute(text("CREATE TABLE intraday_sig_v2_changes (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT,"
                       " session_date TEXT, ts TEXT, from_state TEXT, to_state TEXT, pl_pct REAL)"))
        c.execute(text("CREATE TABLE intraday_sig_v2_pending (symbol TEXT, session_date TEXT, side TEXT, count INTEGER,"
                       " first_ts TEXT, last_ts TEXT, PRIMARY KEY (symbol, session_date))"))
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(eng, "_db", lambda: Session())
    monkeypatch.setattr(eng, "_ready", True)
    return types.SimpleNamespace(engine=engine, Session=Session)


def add(db, **kw):
    r = good(**kw); r.pop("id", None)
    with db.engine.begin() as c:
        c.execute(text(f"INSERT INTO intraday_sig_v2 ({', '.join(r)}) VALUES ({', '.join(':' + k for k in r)})"), r)


def go(sym, price, now, score=72.0, spy=None):
    return eng.step(sym, {"score": score, "spot": price, "call": "NO EDGE", "side": None, "status": "OK", "spy_above_vwap": spy}, now)


def rows(db):
    with db.engine.connect() as c:
        return [dict(r) for r in c.execute(text("SELECT * FROM intraday_sig_v2 ORDER BY id")).mappings()]


def n_changes(db, to="CLOSED"):
    with db.engine.connect() as c:
        return c.execute(text("SELECT COUNT(*) FROM intraday_sig_v2_changes WHERE to_state=:t"), {"t": to}).scalar()


def test_a_late_session_signal_is_recovered_next_day_and_exits_once_at_the_end_of_the_day(sigdb):
    add(sigdb, symbol="INTC", session_date="2026-10-19", signal_ts="2026-10-19T15:19:00-04:00", status_ts="2026-10-19T15:19:00-04:00")
    assert sr.validate(rows(sigdb), now=et(*TUE, 9, 0), cutoff="2026-10-19")["ok"]             # recoverable
    out = go("INTC", 100.6, et(*TUE, 10, 0))                                                   # next morning, inside the levels
    assert out["is_open"] is True and out["now"] == 100.6 and len(rows(sigdb)) == 1             # recovered: same row, no new one
    assert go("INTC", 100.7, et(*TUE, 15, 54))["is_open"] is True                                # not closed because the date changed
    closed = go("INTC", 100.9, et(*TUE, 15, 55))
    assert closed["is_open"] is False and closed["exit_reason"] == "EOD" and closed["exit"] == 100.9
    assert go("INTC", 95.0, et(*TUE, 15, 56)) is None and n_changes(sigdb) == 1                 # nothing left to close: exactly one close
    assert rows(sigdb)[0]["session_date"] == "2026-10-19" and rows(sigdb)[0]["entry"] == 100.0  # history untouched


def test_a_friday_late_signal_survives_the_weekend_and_is_managed_on_monday(sigdb):
    add(sigdb, symbol="MSFT", session_date="2026-10-23", signal_ts="2026-10-23T15:19:00-04:00", status_ts="2026-10-23T15:19:00-04:00")
    assert sr.validate(rows(sigdb), now=et(2026, 10, 24, 12), cutoff=sr.cutoff_iso(et(2026, 10, 24, 12)))["ok"]   # recoverable on Saturday
    assert go("MSFT", 100.2, et(2026, 10, 24, 12, 0))["is_open"] is True                          # a weekend scan: no end-of-day exit
    assert go("MSFT", 100.3, et(2026, 10, 25, 16, 30))["is_open"] is True                         # nor on Sunday after 15:55
    assert go("MSFT", 100.4, et(2026, 10, 26, 10, 0))["is_open"] is True                          # Monday: still managed, same row
    assert len(rows(sigdb)) == 1 and n_changes(sigdb) == 0
    assert go("MSFT", 100.5, et(2026, 10, 26, 15, 55))["exit_reason"] == "EOD" and n_changes(sigdb) == 1


@pytest.mark.parametrize("side, entry, sl, tp, favourable, adverse", [
    ("BUY", 100.0, 99.0, 102.0, 103.0, 98.5), ("SELL", 100.0, 101.0, 98.0, 97.0, 101.5)])
def test_recovered_signals_exit_on_their_levels_symmetrically(sigdb, side, entry, sl, tp, favourable, adverse):
    st = "BUY" if side == "BUY" else "SELL"
    add(sigdb, symbol="WIN", side=side, state=st, entry=entry, sl=sl, tp=tp, session_date="2026-10-19",
        signal_ts="2026-10-19T15:19:00-04:00", status_ts="2026-10-19T15:19:00-04:00")
    add(sigdb, symbol="LOS", side=side, state=st, entry=entry, sl=sl, tp=tp, session_date="2026-10-19",
        signal_ts="2026-10-19T15:19:00-04:00", status_ts="2026-10-19T15:19:00-04:00")
    w, l = go("WIN", favourable, et(*TUE, 10, 0)), go("LOS", adverse, et(*TUE, 10, 0))
    assert (w["exit_reason"], l["exit_reason"]) == ("TP", "SL")
    assert w["pl_pct"] > 0 and l["pl_pct"] < 0 and w["exit"] == favourable and l["exit"] == adverse
    assert abs(w["pl_pct"]) == pytest.approx(abs(favourable - entry) / entry * 100, rel=1e-3)


@pytest.mark.parametrize("side, state, entry, sl, tp, up, down", [
    ("BUY", "BUY", 100.0, 99.0, 104.0, 101.0, 100.0), ("SELL", "SELL", 100.0, 101.0, 96.0, 99.0, 100.0)])
def test_a_recovered_signal_keeps_its_trailing_stop_state(sigdb, side, state, entry, sl, tp, up, down):
    add(sigdb, symbol="TRL", side=side, state=state, entry=entry, sl=sl, tp=tp, session_date="2026-10-19",
        signal_ts="2026-10-19T15:19:00-04:00", status_ts="2026-10-19T15:19:00-04:00")
    score = 72.0 if side == "BUY" else 28.0                                                      # a score that agrees with the side
    mid = go("TRL", entry + (1.5 if side == "BUY" else -1.5), et(*TUE, 10, 0), score=score)     # in profit: stop moves to break-even
    r = rows(sigdb)[0]
    assert r["is_open"] == 1 and r["be_moved"] in (1, True)
    assert (r["sl"] >= entry) if side == "BUY" else (r["sl"] <= entry)
    out = go("TRL", entry, et(*TUE, 10, 30), score=score)                                      # back to the entry: trailing stop out
    assert out["is_open"] is False and out["exit_reason"] == "TRAIL"


def test_a_market_flip_closes_a_recovered_signal_exactly_once(sigdb):
    add(sigdb, symbol="FLP", session_date="2026-10-19", signal_ts="2026-10-19T15:19:00-04:00", status_ts="2026-10-19T15:19:00-04:00")
    assert go("FLP", 100.5, et(*TUE, 10, 0), spy=False)["exit_reason"] == "MARKET_FLIP"
    assert go("FLP", 100.5, et(*TUE, 10, 1), spy=False) is None and n_changes(sigdb) == 1


def test_stale_prices_close_nothing_and_a_missing_price_changes_nothing(sigdb):
    add(sigdb, symbol="OLD", session_date="2026-10-19", signal_ts="2026-10-19T15:19:00-04:00", status_ts="2026-10-19T15:19:00-04:00")
    before = rows(sigdb)
    stale = eng.step("OLD", {"score": 72.0, "spot": 50.0, "call": "NO EDGE", "side": None, "status": "STOCK_STALE", "stock_stale": True,
                             "spy_above_vwap": None}, et(*TUE, 10, 0))
    assert stale["is_open"] is True and rows(sigdb) == before
    eng.step("OLD", {"score": 72.0, "spot": None, "call": None, "side": None, "status": "MISSING", "spy_above_vwap": None}, et(*TUE, 10, 5))
    assert rows(sigdb) == before


def test_a_restart_changes_nothing_and_never_duplicates_or_recloses(sigdb, monkeypatch):
    add(sigdb, symbol="RST", session_date="2026-10-19", signal_ts="2026-10-19T15:19:00-04:00", status_ts="2026-10-19T15:19:00-04:00")
    add(sigdb, symbol="DUP", session_date="2026-10-19", signal_ts="2026-10-19T15:19:00-04:00", status_ts="2026-10-19T15:19:00-04:00")
    go("RST", 100.4, et(*TUE, 10, 0))
    for _ in range(2):                                                                         # two "restarts": new process state
        monkeypatch.setattr(eng, "_ready", False)
        assert eng._ensure() is True
        go("RST", 100.4, et(*TUE, 10, 0))
        go("DUP", 100.4, et(*TUE, 10, 0))
    assert [r["symbol"] for r in rows(sigdb)] == ["RST", "DUP"]                                 # still two rows, none added
    closed = go("RST", 98.0, et(*TUE, 10, 5))
    assert closed["exit_reason"] == "SL" and n_changes(sigdb) == 1
    monkeypatch.setattr(eng, "_ready", False); eng._ensure()
    assert go("RST", 90.0, et(*TUE, 10, 6)) is None and n_changes(sigdb) == 1
    assert [r["is_open"] for r in rows(sigdb)] == [0, 1]


def test_recovery_with_the_production_shape_manages_the_fresh_rows_and_leaves_the_stale_ones_alone(sigdb):
    for s, d in (("ORCL", "2026-10-16"), ("AMD", "2026-10-19"), ("AMD", "2026-10-22"), ("NVDA", "2026-10-16")):
        add(sigdb, symbol=s, session_date=d, signal_ts=d + "T15:00:00-04:00", status_ts=d + "T15:00:00-04:00")
    for s in ("INTC", "MSFT", "NVDA", "QCOM"):                                                 # last night's four late signals
        add(sigdb, symbol=s, session_date="2026-10-26", signal_ts="2026-10-26T15:19:00-04:00", status_ts="2026-10-26T15:19:00-04:00")
    nxt = et(2026, 10, 27, 10, 0)                                                              # the morning after the cut-over night
    stale_before = [r for r in rows(sigdb) if r["session_date"] < "2026-10-27"]
    for s in ("INTC", "MSFT", "NVDA", "QCOM", "ORCL", "AMD"):
        go(s, 100.5, nxt)
    after = rows(sigdb)
    assert [r for r in after if r["session_date"] < "2026-10-26"] == [r for r in stale_before if r["session_date"] < "2026-10-26"]
    assert {r["symbol"]: r["now_price"] for r in after if r["session_date"] == "2026-10-26"} == {"INTC": 100.5, "MSFT": 100.5, "NVDA": 100.5, "QCOM": 100.5}
    assert len(after) == 8                                                                      # nothing created, nothing deleted
