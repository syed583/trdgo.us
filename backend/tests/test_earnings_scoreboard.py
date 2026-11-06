"""Verified scoreboard: eligible pre-release snapshots only, one per stock/event/model, separate BUY/SELL/
NEUTRAL, pending / missing-outcome / excluded counts, provisional labelling, originals untouched."""
import copy
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import earnings_outcomes as eo
import earnings_scoreboard as sb

UTC = timezone.utc
MODELS = {"m1": "Model One", "m2": "Model Two"}
NOW = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)


def at(y, mo, d, h=4, mi=21, s=7):
    return datetime(y, mo, d, h, mi, s, tzinfo=UTC)


def row(model, sym, earn, timing, pred, signal, score=60.0):
    return {"model": model, "symbol": sym, "earnings_date": earn, "report_timing": timing,
            "predicted_at": pred, "signal": signal, "score": score}


def graded(sym, earn, move):
    return {(sym, earn): {"status": eo.GRADED, "move_pct": move, "direction": eo.direction_of(move),
                          "timing": "BMO"}}


# ------------------------------------------------------------------ classification ----
@pytest.mark.parametrize("timing, pred, expected", [
    ("Before Open", at(2026, 10, 28, 9, 59), "BEFORE_RELEASE"),     # 05:59 EDT
    ("Before Open", at(2026, 10, 28, 10, 0), "AMBIGUOUS"),          # 06:00 EDT
    ("Before Open", at(2026, 10, 28, 13, 29), "AMBIGUOUS"),         # 09:29 EDT
    ("Before Open", at(2026, 10, 28, 13, 30), "AFTER_RELEASE"),     # 09:30 EDT
    ("After Close", at(2026, 10, 28, 19, 59), "BEFORE_RELEASE"),    # 15:59 EDT
    ("After Close", at(2026, 10, 28, 20, 0), "AFTER_RELEASE"),      # 16:00 EDT
    ("After Close", at(2026, 10, 27, 20, 0), "BEFORE_RELEASE"),     # earlier ET date
    ("Before Open", at(2026, 10, 29, 15, 0), "AFTER_RELEASE"),      # later ET date
    ("Before Open", at(2026, 10, 27, 0, 0, 0), "RETROSPECTIVE"),    # exactly 00:00 UTC = date-only record
    (None, at(2026, 10, 28, 12, 0), "AMBIGUOUS"),                   # same day, timing unknown
    (None, at(2026, 10, 27, 12, 0), "BEFORE_RELEASE"),              # earlier date is safe whatever the timing
])
def test_classify_oct_28(timing, pred, expected):
    assert sb.classify(date(2026, 10, 28), timing, pred) == expected


def test_classify_uses_new_york_time_across_the_dst_change():
    e = date(2026, 11, 2)                      # EST: UTC-5
    assert sb.classify(e, "Before Open", at(2026, 11, 2, 10, 55)) == "BEFORE_RELEASE"      # 05:55 EST
    assert sb.classify(e, "Before Open", at(2026, 11, 2, 11, 5)) == "AMBIGUOUS"            # 06:05 EST
    assert sb.classify(e, "After Close", at(2026, 11, 2, 20, 55)) == "BEFORE_RELEASE"      # 15:55 EST
    assert sb.classify(e, "After Close", at(2026, 11, 2, 21, 5)) == "AFTER_RELEASE"        # 16:05 EST
    # 00:30 ET on the report morning is still the report day even though it is 05:30 UTC
    assert sb.classify(e, "Before Open", at(2026, 11, 2, 5, 30)) == "BEFORE_RELEASE"


# ------------------------------------------------------------------- selection ----
def test_one_prediction_per_event_not_one_per_snapshot():
    e = date(2026, 10, 28)
    rows = [row("m1", "AAPL", e, "Before Open", at(2026, 10, 24 + i), "BUY") for i in range(4)]    # 4 daily snapshots
    res = sb.build(rows, graded("AAPL", e, 3.0), MODELS, now=NOW)
    m = next(x for x in res["models"] if x["model"] == "m1")
    assert m["events_total"] == 1 and m["events_eligible"] == 1 and m["events_graded"] == 1
    assert m["buy"]["graded"] == 1 and m["buy"]["correct"] == 1          # counted once, not four times


def test_last_vs_first_definition():
    e = date(2026, 10, 28)
    rows = [row("m1", "AAPL", e, "Before Open", at(2026, 10, 25), "SELL"),
            row("m1", "AAPL", e, "Before Open", at(2026, 10, 27), "BUY")]
    last = sb.build(rows, graded("AAPL", e, 3.0), MODELS, now=NOW, definition="last")
    first = sb.build(rows, graded("AAPL", e, 3.0), MODELS, now=NOW, definition="first")
    pick = lambda r: next(x for x in r["models"] if x["model"] == "m1")
    assert pick(last)["buy"]["correct"] == 1 and pick(last)["sell"]["graded"] == 0
    assert pick(first)["sell"]["graded"] == 1 and pick(first)["sell"]["correct"] == 0


def test_post_release_ambiguous_and_retrospective_snapshots_never_count():
    e = date(2026, 10, 28)
    rows = [
        # eligible earlier snapshot (SELL, wrong) + a later POST-release BUY that would have been right
        row("m1", "AAA", e, "Before Open", at(2026, 10, 27), "SELL"),
        row("m1", "AAA", e, "Before Open", at(2026, 10, 28, 15, 58), "BUY"),
        # only a post-release row
        row("m1", "BBB", e, "Before Open", at(2026, 10, 28, 15, 58), "BUY"),
        # only an ambiguous row
        row("m1", "CCC", e, "Before Open", at(2026, 10, 28, 11, 0), "BUY"),
        # only a retrospective row (midnight UTC, no timing)
        row("m1", "DDD", e, None, at(2026, 10, 27, 0, 0, 0), "BUY"),
    ]
    out = {}
    for s in "AAA BBB CCC DDD".split():
        out.update(graded(s, e, 4.0))
    res = sb.build(rows, out, MODELS, now=NOW)
    m = next(x for x in res["models"] if x["model"] == "m1")
    assert m["events_total"] == 4 and m["events_eligible"] == 1 and m["events_graded"] == 1
    assert m["sell"]["graded"] == 1 and m["sell"]["correct"] == 0 and m["buy"]["graded"] == 0
    assert m["excluded"] == {"post_release_only": 1, "ambiguous_only": 1, "retrospective_only": 1}
    assert m["rows_by_class"]["AFTER_RELEASE"] == 2 and m["rows_by_class"]["RETROSPECTIVE"] == 1


def test_signals_are_separate_with_counts_and_intervals():
    e = date(2026, 10, 28)
    rows, out = [], {}
    plan = [("S1", "BUY", 3.0), ("S2", "BUY", -2.0), ("S3", "BUY", 0.2),          # BUY: right, wrong, flat(wrong)
            ("S4", "SELL", -3.0), ("S5", "SELL", 5.0),                               # SELL: right, wrong
            ("S6", "NEUTRAL", 0.1), ("S7", "NEUTRAL", 6.0)]                          # NEUTRAL: no accuracy
    for sym, sig, mv in plan:
        rows.append(row("m1", sym, e, "Before Open", at(2026, 10, 27), sig))
        out.update(graded(sym, e, mv))
    res = sb.build(rows, out, MODELS, now=NOW)
    m = next(x for x in res["models"] if x["model"] == "m1")
    assert (m["buy"]["graded"], m["buy"]["correct"], m["buy"]["accuracy"]) == (3, 1, 33.3)
    assert (m["sell"]["graded"], m["sell"]["correct"], m["sell"]["accuracy"]) == (2, 1, 50.0)
    assert m["neutral"]["graded"] == 2 and m["neutral"]["flat_hits"] == 1 and "accuracy" not in m["neutral"]
    assert (m["directional"]["graded"], m["directional"]["correct"]) == (5, 2)
    assert m["buy"]["ci95"][0] < 33.3 < m["buy"]["ci95"][1]
    assert m["baseline"]["graded"] == 7 and m["baseline"]["up"] == 3 and m["baseline"]["down"] == 2 and m["baseline"]["flat"] == 2
    assert m["baseline"]["always_buy_accuracy"] == round(3 / 7 * 100, 1)


def test_pending_missing_and_future_events_are_counted_separately():
    rows = [
        row("m1", "DONE", date(2026, 10, 28), "Before Open", at(2026, 10, 27), "BUY"),
        row("m1", "MISS", date(2026, 10, 28), "Before Open", at(2026, 10, 27), "BUY"),     # reaction closed, no outcome stored
        row("m1", "AMCW", date(2026, 11, 5), "After Close", at(2026, 11, 4), "BUY"),     # reported today, reaction session ahead
        row("m1", "FUT", date(2026, 11, 20), "After Close", at(2026, 11, 4), "BUY"),     # future event
        row("m1", "NOTM", date(2026, 10, 28), None, at(2026, 10, 27), "BUY"),             # timing unknown, reported
    ]
    res = sb.build(rows, graded("DONE", date(2026, 10, 28), 2.0), MODELS, now=NOW)
    m = next(x for x in res["models"] if x["model"] == "m1")
    assert m["events_graded"] == 1
    assert m["events_missing_outcome"] == 2          # MISS and NOTM
    assert m["events_pending"] == 2                  # AMCW and FUT
    assert m["unverifiable"] == 2 and m["pending"] == 2 and m["verified"] == 1


def test_only_scored_models_and_provisional_labelling():
    e = date(2026, 10, 28)
    rows = [row("m1", "A", e, "Before Open", at(2026, 10, 27), "BUY"),
            row("retired", "A", e, "Before Open", at(2026, 10, 27), "BUY")]
    res = sb.build(rows, {}, MODELS, now=NOW)
    assert {m["model"] for m in res["models"]} == {"m1", "m2"}
    assert res["provisional"] is True and len(res["provisional_reasons"]) >= 2
    assert res["totals"]["ranking_withheld"] is True and res["totals"]["best_model"] is None
    assert "release" in " ".join(res["provisional_reasons"]).lower()


def test_scoreboard_never_modifies_the_snapshots():
    e = date(2026, 10, 28)
    rows = [row("m1", "A", e, "Before Open", at(2026, 10, 27), "BUY"),
            row("m1", "A", e, "Before Open", at(2026, 10, 28, 15, 58), "SELL")]
    before = copy.deepcopy(rows)
    sb.build(rows, graded("A", e, 2.0), MODELS, now=NOW)
    assert rows == before


def test_consensus_uses_eligible_one_per_event_calls():
    e = date(2026, 10, 28)
    models = {f"m{i}": f"M{i}" for i in range(6)}
    rows = [row(m, "A", e, "Before Open", at(2026, 10, 27), "BUY") for m in models]
    rows += [row("m0", "A", e, "Before Open", at(2026, 10, 28, 15, 58), "SELL")]       # post-release flip ignored
    res = sb.build(rows, graded("A", e, 3.0), models, now=NOW)
    assert res["consensus"]["6"]["verified"] == 1 and res["consensus"]["6"]["correct"] == 1
    assert res["consensus"]["7"]["signals"] == 0


# ---------------------------------------------------------------- database layer ----
def test_database_scoreboard_reads_snapshots_and_outcomes_without_writing():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    db = sessionmaker(bind=engine)()
    db.execute(text(
        "CREATE TABLE model_predictions (id INTEGER PRIMARY KEY AUTOINCREMENT, model VARCHAR(24) NOT NULL,"
        " symbol VARCHAR(20) NOT NULL, earnings_date DATE, report_timing VARCHAR(16), snapshot_date DATE NOT NULL,"
        " predicted_at TIMESTAMP NOT NULL, signal VARCHAR(10) NOT NULL, score DOUBLE PRECISION,"
        " actual_move_pct DOUBLE PRECISION, actual_direction VARCHAR(10), correct SMALLINT, evaluated_at TIMESTAMP)"))
    eo.ensure_table(db)
    e = date(2026, 10, 28)
    for i, sig in enumerate(("BUY", "BUY", "SELL")):
        db.execute(text("INSERT INTO model_predictions (model, symbol, earnings_date, report_timing, snapshot_date,"
                        " predicted_at, signal, score) VALUES ('m1', 'AAPL', :e, 'Before Open', :sn, :p, :sg, 60)"),
                   {"e": e, "sn": date(2026, 10, 24 + i), "p": at(2026, 10, 24 + i), "sg": sig})
    db.execute(text("INSERT INTO model_predictions (model, symbol, earnings_date, report_timing, snapshot_date,"
                    " predicted_at, signal, score) VALUES ('m1', 'AAPL', :e, 'Before Open', :sn, :p, 'BUY', NULL)"),
               {"e": e, "sn": date(2026, 10, 27), "p": at(2026, 10, 27, 0, 0, 0)})        # retrospective stamp
    db.execute(text("INSERT INTO earnings_event_outcomes (symbol, earnings_date, timing, status, move_pct, direction,"
                    " attempts) VALUES ('AAPL', :e, 'BMO', 'GRADED', 4.0, 'BUY', 1)"), {"e": e})
    db.commit()
    n_before = db.execute(text("SELECT COUNT(*) FROM model_predictions")).scalar()
    res = sb.scoreboard(MODELS, db=db, now=NOW)
    m = next(x for x in res["models"] if x["model"] == "m1")
    assert m["events_total"] == 1 and m["events_graded"] == 1
    assert (m["sell"]["graded"], m["buy"]["graded"]) == (1, 0)             # last eligible snapshot is the SELL
    assert m["rows_by_class"]["RETROSPECTIVE"] == 1
    assert db.execute(text("SELECT COUNT(*) FROM model_predictions")).scalar() == n_before


def test_scoreboard_without_outcome_table_degrades_to_pending():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    db = sessionmaker(bind=engine)()
    db.execute(text(
        "CREATE TABLE model_predictions (id INTEGER PRIMARY KEY AUTOINCREMENT, model VARCHAR(24) NOT NULL,"
        " symbol VARCHAR(20) NOT NULL, earnings_date DATE, report_timing VARCHAR(16), snapshot_date DATE NOT NULL,"
        " predicted_at TIMESTAMP NOT NULL, signal VARCHAR(10) NOT NULL, score DOUBLE PRECISION)"))
    db.execute(text("INSERT INTO model_predictions (model, symbol, earnings_date, report_timing, snapshot_date,"
                    " predicted_at, signal, score) VALUES ('m1', 'AAPL', :e, 'After Close', :sn, :p, 'BUY', 60)"),
               {"e": date(2026, 12, 1), "sn": date(2026, 11, 1), "p": at(2026, 11, 1)})
    db.commit()
    res = sb.scoreboard(MODELS, db=db, now=NOW)
    m = next(x for x in res["models"] if x["model"] == "m1")
    assert m["events_graded"] == 0 and m["events_pending"] == 1
