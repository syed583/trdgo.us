"""The read-only outcome diagnostic: population reconciliation, stored-outcome sources, sample audit reasons."""
import importlib.util
import pathlib
import sys
import types
from datetime import date, datetime, timezone

import pytest
from decimal import Decimal

UTC = timezone.utc
TOOL = pathlib.Path(__file__).resolve().parent.parent / "tools" / "outcome_diag.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("outcome_diag_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def snap(sym, earn, timing, predicted, model="claude_new", move=None, evaluated=None, signal="BUY"):
    return {"model": model, "symbol": sym, "earnings_date": earn, "report_timing": timing, "snapshot_date": predicted.date(),
            "predicted_at": predicted, "signal": signal, "actual_move_pct": move,
            "actual_direction": None if move is None else ("BUY" if move > 0.5 else "SELL" if move < -0.5 else "NEUTRAL"),
            "correct": None, "evaluated_at": evaluated}


def at(d, h=4, mi=21):
    return datetime(2026, 10, d, h, mi, tzinfo=UTC)


NOW = datetime(2026, 10, 30, 15, 0, tzinfo=UTC)          # Friday 11:00 ET


def data():
    e28, e29 = date(2026, 10, 28), date(2026, 10, 29)
    snaps = [
        snap("AAA", e28, "Before Open", at(27), move=3.0, evaluated=at(29, 4, 30)),
        snap("BBB", e28, "Before Open", at(27), evaluated=at(29, 4, 30)),
        snap("CCC", e28, "After Close", at(27), evaluated=at(29, 4, 30)),
        snap("DDD", e28, "After Close", at(27), evaluated=at(29, 4, 30)),
        snap("EEE", e28, None, datetime(2026, 10, 27, 0, 0, tzinfo=UTC), model="ped"),              # only a retrospective row
        snap("FFF", e28, "Before Open", datetime(2026, 10, 28, 19, 58, tzinfo=UTC)),                # only a post-release row
        snap("GGG", e29, "After Close", at(28)),                                                    # reaction session still open
        snap("HHH", date(2026, 11, 15), "Before Open", at(29)),                                     # not reported yet
        snap("III", date(2026, 10, 27), None, datetime(2026, 10, 26, 0, 0, tzinfo=UTC), model="ped"),  # timing unknown
    ]
    cal = [{"symbol": "EEE", "earnings_date": e28, "reporting_time": "After Close", "report_time": None, "reaction_date": None,
            "post_earnings_move_percent": None, "eps_actual": None, "lifecycle": "x", "data_status": "x", "fetched_at": None, "updated_at": None}]
    trades = [{"cache_key": "earn:trade:default:ZZZ", "updated_at": at(30, 9, 0).timestamp(), "hm": None},
              {"cache_key": "earn:trade:default:CCC", "updated_at": at(30, 9, 0).timestamp(), "hm": [{"date": "2026-10-28", "move_pct": 5.0}]},
              {"cache_key": "earn:trade:default:AAA", "updated_at": at(30, 9, 0).timestamp(), "hm": [{"date": "2026-10-28", "move_pct": 3.0}]},
              {"cache_key": "earn:trade:default:DDD", "updated_at": at(28, 9, 0).timestamp(), "hm": [{"date": "2026-07-29", "move_pct": 1.0}]}]
    # production types: price is a Decimal, created_at a naive timestamp
    prices = [{"symbol": "CCC", "snapshot_date": date(2026, 10, 28), "price": Decimal("100.0"), "created_at": at(28, 21, 5).replace(tzinfo=None)},
              {"symbol": "CCC", "snapshot_date": date(2026, 10, 29), "price": Decimal("105.0"), "created_at": at(29, 21, 5).replace(tzinfo=None)}]
    return snaps, cal, trades, prices, [{"table_name": "score_snapshots", "column_name": "price"}]


def test_populations_are_reconciled_event_by_event():
    t = load_tool()
    res = t.diagnose(*data(), now=NOW)
    p = res["population"]
    assert p["reported_events"] == 8 and p["not_reported_yet"] == 1
    assert p["ready_past_reaction_close"] == {"Before Open": 3, "After Close": 3}      # AAA,BBB,FFF / CCC,DDD,EEE
    assert p["window_open"] == {"Before Open": 0, "After Close": 1}                    # GGG
    assert p["timing_unknown"] == 1 and p["union_ready_or_unknown (the '380')"] == 7
    assert p["claude_new_eligible_in_that_union (the '309')"] == 4                      # AAA,BBB,CCC,DDD
    assert p["union_events_not_eligible_for_claude_new"] == 3
    assert p["why_not_eligible"] == {"claude_new has only post-release snapshots": 1,
                                     "claude_new has only ambiguous/retrospective snapshots": 0,
                                     "no claude_new snapshot (other models only, e.g. Pre-Earnings Direction)": 2}


def test_stored_outcome_sources_by_timing():
    t = load_tool()
    s = t.diagnose(*data(), now=NOW)["stored_outcome_sources_for_completed_events"]
    assert s["Before Open"]["completed_events"] == 3 and s["Before Open"]["legacy_outcome_stored"] == 1
    assert s["Before Open"]["no_stored_outcome_needs_price_fetch"] == 2
    assert s["After Close"]["completed_events"] == 3 and s["After Close"]["cached_move_stored_exact_date"] == 1
    assert s["After Close"]["no_stored_outcome_needs_price_fetch"] == 2 and s["After Close"]["calendar_row_exists"] == 1
    assert s["timing unknown"]["completed_events"] == 1 and s["timing unknown"]["no_stored_outcome_needs_price_fetch"] == 1


def test_legacy_vs_cached_cross_check():
    t = load_tool()
    x = t.diagnose(*data(), now=NOW)["legacy_vs_cached_moves"]
    assert x["events_with_both"] == 1 and x["within_0.05pct"] == 1                       # AAA: 3.0 vs 3.0


def test_sample_audit_gives_dates_stamps_cached_moves_prices_and_reasons():
    t = load_tool()
    sa = t.diagnose(*data(), now=NOW)["sample_audit"]
    early = sa["AMC stamped early, cached move exists"][0]
    assert early["symbol"] == "CCC" and early["expected_base_close_date"] == "2026-10-28"
    assert early["expected_reaction_close_date"] == "2026-10-29" and early["ready_at_utc"] == "2026-10-29T20:45+00:00"
    assert early["legacy_stamped_at_utc"] == "2026-10-29T04:30+00:00"
    assert "before the reaction session closed" in early["why_no_new_outcome"]
    assert "cached earnings-move history has this event" in early["why_no_new_outcome"]
    assert early["cached_move"][0]["move_pct"] == 5.0 and early["trade_cache_built_utc"] == "2026-10-30T09:00+00:00"
    assert [float(p["price"]) for p in early["stored_prices_score_snapshots"]] == [100.0, 105.0]
    bmo = sa["BMO legacy stamped, no move"][0]
    assert bmo["symbol"] == "BBB" and "after the close but recorded no move" in bmo["why_no_new_outcome"]
    assert sa["AMC stamped early, no stored move"][0]["symbol"] == "DDD"
    assert sa["BMO legacy graded"][0]["symbol"] == "AAA" and "legacy move stored" in sa["BMO legacy graded"][0]["why_no_new_outcome"]


def test_main_prints_everything_with_only_selects(tmp_path, monkeypatch, capsys):
    t = load_tool()
    snaps, cal, trades, prices, cols = data()
    monkeypatch.setitem(sys.modules, "database", types.ModuleType("database"))
    monkeypatch.setenv("APP_ENV", "production")
    seen = []

    def fake(sql):
        seen.append(sql.split()[0])
        for key, rows in (("model_predictions", snaps), ("earnings_calendar", cal), ("swr_cache", trades),
                          ("score_snapshots", prices), ("information_schema", cols)):
            if key in sql:
                return rows
        raise AssertionError(sql)

    monkeypatch.setattr(t, "_run_ro", fake)
    monkeypatch.setattr(sys, "argv", ["outcome_diag.py", "--out", str(tmp_path / "o.json")])
    t.main()
    text = capsys.readouterr().out
    for needle in ("POPULATIONS", "STORED OUTCOME SOURCES", "LEGACY vs CACHED", "SAMPLE AUDIT", "SYMBOL HYGIENE", "TABLES WITH PRICE-LIKE"):
        assert needle in text, needle
    assert seen == ["SELECT"] * 5 and (tmp_path / "o.json").exists()


def test_read_only_by_construction():
    src = TOOL.read_text(encoding="utf-8").split("def load_embedded", 1)[1]
    for word in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "CREATE ", "TRUNCATE", "commit("):
        assert word not in src, word
    assert src.count("SET TRANSACTION READ ONLY") == 1 and "import swr" not in src
    t = load_tool()
    mc, eo, sb = t.load_embedded()
    for fn in ("run", "ensure_table", "load_outcomes", "_save", "sync_legacy_columns", "events_to_review", "default_bars_fetcher"):
        with pytest.raises(RuntimeError, match="disabled in the read-only diagnostic"):
            getattr(eo, fn)()


def test_epoch_float_timestamps_are_accepted_as_production_stores_them():
    t = load_tool()
    assert t._utc(1793203200.0) == datetime.fromtimestamp(1793203200.0, tz=UTC)
    assert t._utc(1793203200) == datetime.fromtimestamp(1793203200, tz=UTC)
    assert t._utc("2026-10-30T09:00:00Z") == datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
    assert t._utc(datetime(2026, 10, 30, 9, 0)) == datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
    assert t._utc(None) is None
