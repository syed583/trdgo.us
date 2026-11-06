"""Grading lifecycle: Before Open / After Close timing, holidays, early closes, DST, missing prices,
safe retries and idempotency. In-memory SQLite only; no provider calls."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import earnings_outcomes as eo

UTC = timezone.utc


def utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


# ---------------------------------------------------------------- reaction windows ----
@pytest.mark.parametrize("earn, kind, base, react", [
    (date(2026, 10, 28), "BMO", date(2026, 10, 27), date(2026, 10, 28)),      # Wednesday, before open
    (date(2026, 10, 28), "AMC", date(2026, 10, 28), date(2026, 10, 29)),      # Wednesday, after close
    (date(2026, 10, 30), "AMC", date(2026, 10, 30), date(2026, 11, 2)),       # Friday AMC -> Monday
    (date(2026, 11, 2), "BMO", date(2026, 10, 30), date(2026, 11, 2)),        # Monday BMO: base is Friday
    (date(2026, 11, 25), "AMC", date(2026, 11, 25), date(2026, 11, 27)),      # day before Thanksgiving
    (date(2026, 11, 26), "AMC", date(2026, 11, 25), date(2026, 11, 27)),      # report dated on the holiday
    (date(2026, 1, 16), "AMC", date(2026, 1, 16), date(2026, 1, 20)),         # Friday before MLK Monday
    (date(2026, 1, 20), "BMO", date(2026, 1, 16), date(2026, 1, 20)),         # Tuesday after MLK: base is Friday
    (date(2026, 4, 2), "AMC", date(2026, 4, 2), date(2026, 4, 6)),            # Thursday before Good Friday
    (date(2026, 10, 31), "BMO", date(2026, 10, 30), date(2026, 11, 2)),       # Saturday-dated BMO -> next session
])
def test_reaction_window(earn, kind, base, react):
    assert eo.reaction_window(earn, kind) == (base, react)


def test_ready_times_follow_the_session_close_and_daylight_saving():
    # EDT: 16:00 ET = 20:00Z, plus the 45-minute availability buffer
    assert eo.ready_at(date(2026, 10, 28)) == utc(2026, 10, 28, 20, 45)
    # EST after the 1 Nov change: 16:00 ET = 21:00Z
    assert eo.ready_at(date(2026, 11, 2)) == utc(2026, 11, 2, 21, 45)
    # early close: 13:00 EST = 18:00Z
    assert eo.ready_at(date(2026, 11, 27)) == utc(2026, 11, 27, 18, 45)
    assert eo.ready_at(date(2026, 12, 24)) == utc(2026, 12, 24, 18, 45)
    # spring forward (2026-03-08): Monday after is EDT
    assert eo.ready_at(date(2026, 3, 9)) == utc(2026, 3, 9, 20, 45)


# ------------------------------------------------------------------------ evaluate ----
CLOSES = {date(2026, 10, 27): 100.0, date(2026, 10, 28): 104.0, date(2026, 10, 29): 98.0}


def test_bmo_is_pending_until_the_earnings_day_session_has_closed():
    before = eo.evaluate("AAPL", date(2026, 10, 28), "BMO", utc(2026, 10, 28, 20, 44), CLOSES)
    assert before["status"] == eo.PENDING_REACTION and before["move_pct"] is None
    after = eo.evaluate("AAPL", date(2026, 10, 28), "BMO", utc(2026, 10, 28, 20, 45), CLOSES)
    assert after["status"] == eo.GRADED and after["move_pct"] == 4.0 and after["direction"] == "BUY"
    assert (after["base_close_date"], after["reaction_close_date"]) == (date(2026, 10, 27), date(2026, 10, 28))


def test_amc_is_not_graded_on_the_report_evening_or_next_morning():
    """The old grader ran at ~04:21 UTC the next day, before the reaction session: it must stay pending."""
    for now in (utc(2026, 10, 28, 21, 0), utc(2026, 10, 29, 4, 21), utc(2026, 10, 29, 20, 44)):
        o = eo.evaluate("MSFT", date(2026, 10, 28), "AMC", now, CLOSES)
        assert o["status"] == eo.PENDING_REACTION, now
    o = eo.evaluate("MSFT", date(2026, 10, 28), "AMC", utc(2026, 10, 29, 20, 45), CLOSES)
    assert o["status"] == eo.GRADED and o["move_pct"] == round((98.0 - 104.0) / 104.0 * 100, 2)
    assert o["direction"] == "SELL"


def test_friday_amc_waits_for_monday_close():
    closes = {date(2026, 10, 30): 50.0, date(2026, 11, 2): 55.0}
    assert eo.evaluate("X", date(2026, 10, 30), "AMC", utc(2026, 11, 1, 12, 0), closes)["status"] == eo.PENDING_REACTION
    assert eo.evaluate("X", date(2026, 10, 30), "AMC", utc(2026, 11, 2, 21, 44), closes)["status"] == eo.PENDING_REACTION
    o = eo.evaluate("X", date(2026, 10, 30), "AMC", utc(2026, 11, 2, 21, 45), closes)
    assert o["status"] == eo.GRADED and o["move_pct"] == 10.0


def test_early_close_session_is_gradable_earlier():
    closes = {date(2026, 11, 25): 10.0, date(2026, 11, 27): 11.0}
    assert eo.evaluate("X", date(2026, 11, 25), "AMC", utc(2026, 11, 27, 18, 44), closes)["status"] == eo.PENDING_REACTION
    assert eo.evaluate("X", date(2026, 11, 25), "AMC", utc(2026, 11, 27, 18, 45), closes)["status"] == eo.GRADED


def test_missing_price_is_pending_price_never_unverifiable():
    now = utc(2026, 10, 30, 12, 0)
    for closes in (None, {}, {date(2026, 10, 27): 100.0}, {date(2026, 10, 28): 104.0}):
        o = eo.evaluate("AAPL", date(2026, 10, 28), "BMO", now, closes)
        assert o["status"] == eo.PENDING_PRICE
        assert o["move_pct"] is None and "not available" in o["note"]
    bad = eo.evaluate("AAPL", date(2026, 10, 28), "BMO", now, {date(2026, 10, 27): 0.0, date(2026, 10, 28): 5.0})
    assert bad["status"] == eo.PENDING_PRICE


def test_unknown_timing_is_pending_timing_not_guessed():
    o = eo.evaluate("AAPL", date(2026, 10, 28), None, utc(2026, 11, 5), CLOSES)
    assert o["status"] == eo.PENDING_TIMING and o["move_pct"] is None
    assert eo.timing_kind("Before Open") == "BMO" and eo.timing_kind("After Close") == "AMC"
    assert eo.timing_kind("") is None and eo.timing_kind(None) is None and eo.timing_kind("TBD") is None


@pytest.mark.parametrize("react_close, direction", [
    (100.50, "NEUTRAL"), (100.51, "BUY"), (99.50, "NEUTRAL"), (99.49, "SELL"), (100.0, "NEUTRAL")])
def test_flat_band_boundaries(react_close, direction):
    closes = {date(2026, 10, 27): 100.0, date(2026, 10, 28): react_close}
    o = eo.evaluate("X", date(2026, 10, 28), "BMO", utc(2026, 10, 29), closes)
    assert o["status"] == eo.GRADED and o["direction"] == direction


def test_retry_schedule_backs_off_but_never_stops():
    ready = utc(2026, 10, 28, 20, 45)
    assert eo.retry_due(None, ready, ready)
    prev = {"status": eo.PENDING_PRICE, "last_attempt_at": ready}
    assert not eo.retry_due(prev, ready, ready + timedelta(minutes=54))
    assert eo.retry_due(prev, ready, ready + timedelta(minutes=55))
    late = {"status": eo.PENDING_PRICE, "last_attempt_at": ready + timedelta(days=3)}
    assert not eo.retry_due(late, ready, ready + timedelta(days=3, hours=5))
    assert eo.retry_due(late, ready, ready + timedelta(days=3, hours=6))
    very_late = {"status": eo.PENDING_PRICE, "last_attempt_at": ready + timedelta(days=100)}
    assert not eo.retry_due(very_late, ready, ready + timedelta(days=106))
    assert eo.retry_due(very_late, ready, ready + timedelta(days=107))       # weekly, forever
    assert not eo.retry_due({"status": eo.GRADED, "last_attempt_at": ready}, ready, ready + timedelta(days=400))


# ------------------------------------------------------------------ database lifecycle ----
@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    S = sessionmaker(bind=engine)
    s = S()
    s.execute(text(
        "CREATE TABLE model_predictions (id INTEGER PRIMARY KEY AUTOINCREMENT, model VARCHAR(24) NOT NULL,"
        " symbol VARCHAR(20) NOT NULL, earnings_date DATE, report_timing VARCHAR(16), snapshot_date DATE NOT NULL,"
        " predicted_at TIMESTAMP NOT NULL, signal VARCHAR(10) NOT NULL, score DOUBLE PRECISION,"
        " actual_move_pct DOUBLE PRECISION, actual_direction VARCHAR(10), correct SMALLINT, evaluated_at TIMESTAMP,"
        " UNIQUE (model, symbol, earnings_date, snapshot_date))"))
    s.commit()
    yield s
    s.close()


def add(db, model, sym, earn, timing, snap, signal, pred=None, evaluated=None, move=None):
    db.execute(text(
        "INSERT INTO model_predictions (model, symbol, earnings_date, report_timing, snapshot_date, predicted_at,"
        " signal, score, actual_move_pct, evaluated_at) VALUES (:m, :s, :e, :t, :sn, :p, :sg, 50.0, :mv, :ev)"),
        {"m": model, "s": sym, "e": earn, "t": timing, "sn": snap,
         "p": pred or datetime(snap.year, snap.month, snap.day, 4, 21, 7, tzinfo=UTC), "sg": signal, "mv": move,
         "ev": evaluated})
    db.commit()


def outcome(db, sym, earn):
    r = db.execute(text("SELECT status, move_pct, attempts, direction, note FROM earnings_event_outcomes "
                        "WHERE symbol = :s AND earnings_date = :e"), {"s": sym, "e": earn}).first()
    return None if r is None else dict(r._mapping)


def test_full_lifecycle_pending_then_graded_and_idempotent(db):
    e = date(2026, 10, 28)
    add(db, "m1", "AAPL", e, "Before Open", date(2026, 10, 28), "BUY")
    add(db, "m2", "AAPL", e, "Before Open", date(2026, 10, 28), "SELL")
    add(db, "m1", "MSFT", e, "After Close", date(2026, 10, 28), "BUY")
    add(db, "m1", "TSLA", date(2026, 12, 31), "After Close", date(2026, 10, 27), "BUY")        # future event
    prices = {"AAPL": {date(2026, 10, 27): 100.0, date(2026, 10, 28): 104.0},
              "MSFT": {date(2026, 10, 28): 400.0}}                                            # no Oct 29 close yet
    fetched = []

    def fetcher(sym):
        fetched.append(sym)
        return prices[sym]

    # 1. evening of the report day: BMO gradable, AMC reaction session still ahead (nothing fetched for it)
    s1 = eo.run(now=utc(2026, 10, 28, 21, 0), fetcher=fetcher, db=db)
    assert s1["graded_new"] == 1 and s1["pending_reaction"] == 1 and fetched == ["AAPL"]
    assert outcome(db, "AAPL", e)["status"] == "GRADED" and outcome(db, "MSFT", e) is None
    # legacy columns filled only for the graded event's never-evaluated rows
    eo.sync_legacy_columns(db, utc(2026, 10, 28, 21, 0))
    rows = {r.model: r for r in db.execute(text("SELECT model, signal, actual_move_pct, actual_direction, correct, evaluated_at "
                                                "FROM model_predictions WHERE symbol = 'AAPL'"))}
    assert rows["m1"].correct == 1 and rows["m2"].correct == 0 and rows["m1"].actual_move_pct == 4.0
    assert db.execute(text("SELECT evaluated_at FROM model_predictions WHERE symbol = 'MSFT'")).scalar() is None

    # 2. next day after the MSFT reaction close but the close is not published: PENDING_PRICE, retried, not terminal
    s2 = eo.run(now=utc(2026, 10, 29, 21, 0), fetcher=fetcher, db=db)
    o = outcome(db, "MSFT", e)
    assert o["status"] == "PENDING_PRICE" and o["attempts"] == 1 and s2["graded_new"] == 0
    # 3. immediate re-run: not due (back-off) -> idempotent, attempts unchanged, no extra fetch
    n_fetch = len(fetched)
    eo.run(now=utc(2026, 10, 29, 21, 10), fetcher=fetcher, db=db)
    assert outcome(db, "MSFT", e)["attempts"] == 1 and len(fetched) == n_fetch
    # 4. the price arrives: graded on the next due retry
    prices["MSFT"][date(2026, 10, 29)] = 396.0
    s4 = eo.run(now=utc(2026, 10, 29, 23, 0), fetcher=fetcher, db=db)
    o = outcome(db, "MSFT", e)
    assert s4["graded_new"] == 1 and o["status"] == "GRADED" and o["move_pct"] == -1.0 and o["direction"] == "SELL"
    assert o["attempts"] == 2
    # 5. a graded outcome is never rewritten, even if later prices differ
    prices["MSFT"][date(2026, 10, 29)] = 1.0
    prices["AAPL"][date(2026, 10, 28)] = 1.0
    eo.run(now=utc(2026, 11, 20), fetcher=fetcher, db=db)
    assert outcome(db, "MSFT", e)["move_pct"] == -1.0 and outcome(db, "AAPL", e)["move_pct"] == 4.0
    # the future TSLA event never produced an outcome row
    assert outcome(db, "TSLA", date(2026, 12, 31)) is None


def test_previously_stamped_legacy_rows_are_not_rewritten(db):
    e = date(2026, 10, 27)
    stamped = datetime(2026, 10, 28, 4, 30, tzinfo=UTC)
    add(db, "m1", "NVDA", e, "After Close", date(2026, 10, 26), "BUY", evaluated=stamped)       # old premature stamp, no move
    add(db, "m2", "NVDA", e, "After Close", date(2026, 10, 26), "BUY")                          # never evaluated
    prices = {"NVDA": {date(2026, 10, 27): 100.0, date(2026, 10, 28): 110.0}}
    eo.run(now=utc(2026, 10, 29, 22, 0), fetcher=lambda s: prices[s], db=db)
    assert outcome(db, "NVDA", e)["move_pct"] == 10.0                  # the event itself is now properly graded
    eo.sync_legacy_columns(db, utc(2026, 10, 29, 22, 0))
    old = db.execute(text("SELECT actual_move_pct, correct, evaluated_at FROM model_predictions WHERE model = 'm1'")).first()
    assert old.actual_move_pct is None and old.correct is None          # historical record left exactly as it was
    new = db.execute(text("SELECT actual_move_pct, correct FROM model_predictions WHERE model = 'm2'")).first()
    assert new.actual_move_pct == 10.0 and new.correct == 1


def test_missing_timing_label_and_fetch_failure_do_not_crash_or_terminate(db):
    e = date(2026, 10, 28)
    add(db, "m1", "ZZZ", e, None, date(2026, 10, 28), "BUY")
    add(db, "m1", "FAIL", e, "Before Open", date(2026, 10, 28), "BUY")

    def fetcher(sym):
        raise RuntimeError("provider down")

    s = eo.run(now=utc(2026, 10, 29, 12, 0), fetcher=fetcher, db=db)
    assert outcome(db, "ZZZ", e)["status"] == "PENDING_TIMING"
    f = outcome(db, "FAIL", e)
    assert f["status"] == "PENDING_PRICE" and "fetch failed" in f["note"] and s["fetch_errors"] == 1
    # provider recovers later -> graded
    prices = {"FAIL": {date(2026, 10, 27): 10.0, date(2026, 10, 28): 12.0}}
    eo.run(now=utc(2026, 10, 30, 12, 0), fetcher=lambda x: prices[x], db=db)
    assert outcome(db, "FAIL", e)["status"] == "GRADED"
    # a timing label arriving later releases the PENDING_TIMING event
    add(db, "m2", "ZZZ", e, "After Close", date(2026, 10, 27), "BUY")
    eo.run(now=utc(2026, 10, 30, 12, 0), fetcher=lambda x: {date(2026, 10, 28): 10.0, date(2026, 10, 29): 11.0}, db=db)
    assert outcome(db, "ZZZ", e)["status"] == "GRADED"
