"""Phase 2A final integrity changes: legacy sync OFF by default, legacy outcomes labelled invalid, WSO.B stays unresolved
and pending, and the verified scoreboard reproduces the 2026-10-29 audit sample figures."""
import importlib
import inspect
import pathlib
import sys
import types
from datetime import date, datetime, timezone

import pytest

import earnings_outcomes as eo
import earnings_scoreboard as sb
import model_prediction_service as mp

UTC = timezone.utc
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------- legacy sync default OFF ----
@pytest.mark.parametrize("val, expected", [(None, False), ("", False), ("0", False), ("false", False), ("no", False),
                                           ("1", True), ("true", True), ("YES", True), ("on", True)])
def test_legacy_sync_flag(monkeypatch, val, expected):
    if val is None:
        monkeypatch.delenv("EARNINGS_LEGACY_SYNC", raising=False)
    else:
        monkeypatch.setenv("EARNINGS_LEGACY_SYNC", val)
    assert mp.legacy_sync_enabled() is expected


def _patch_grade(monkeypatch):
    calls = []

    class FakeDb:
        def rollback(self):
            pass

        def close(self):
            pass
    monkeypatch.setattr(mp, "_ensure_table", lambda: True)
    fake_database = types.ModuleType("database")
    fake_database.SessionLocal = lambda: FakeDb()
    monkeypatch.setitem(sys.modules, "database", fake_database)
    live_eo = importlib.import_module("earnings_outcomes")        # the module grade() will import (other tests may reload it)
    monkeypatch.setattr(live_eo, "run", lambda max_symbols=120, db=None, **kw: calls.append("run") or {"graded_new": 3})
    monkeypatch.setattr(live_eo, "sync_legacy_columns", lambda db, now=None: calls.append("sync") or 7)
    return calls


def test_grade_never_writes_legacy_columns_by_default(monkeypatch):
    monkeypatch.delenv("EARNINGS_LEGACY_SYNC", raising=False)
    calls = _patch_grade(monkeypatch)
    assert mp.grade() == 3 and calls == ["run"]


def test_grade_syncs_legacy_columns_only_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("EARNINGS_LEGACY_SYNC", "true")
    calls = _patch_grade(monkeypatch)
    assert mp.grade() == 3 and calls == ["run", "sync"]


# ---------------------------------------------------------------- legacy outcomes labelled invalid ----
def test_legacy_label_text_states_what_is_wrong_and_where_to_look():
    n = mp.LEGACY_OUTCOMES_NOTE
    assert "LEGACY" in n and "regular-session" in n and "28 of 33" in n and "/model-accuracy/scoreboard" in n


def test_history_overlay_and_legacy_scoreboard_carry_the_invalid_flag(monkeypatch):
    monkeypatch.setattr(mp, "_ensure_table", lambda: False)
    assert mp.history()["status"] == "NO_TABLE"
    out = mp.overlay([])
    assert out["legacy_outcomes_invalid"] is True and out["legacy_note"] == mp.LEGACY_OUTCOMES_NOTE
    src = inspect.getsource(mp.history)
    assert '"legacy_outcomes_invalid": True' in src and '"legacy_note": LEGACY_OUTCOMES_NOTE' in src
    import api_routes
    route_src = inspect.getsource(api_routes.model_accuracy_scoreboard_legacy)
    assert "legacy_outcomes_invalid" in route_src and "INVALID" in route_src


def test_frontend_labels_legacy_outcomes():
    web = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"
    ma = (web / "ModelAccuracyPage.tsx").read_text(encoding="utf-8")
    assert "Legacy outcomes" in ma and "INVALID for verified performance" in ma
    assert "Actual move (legacy)" in ma and "Result (legacy)" in ma
    am = (web / "AllModelsPage.tsx").read_text(encoding="utf-8")
    assert am.count("legacy outcome") + am.count("LEGACY outcome") >= 2


# ---------------------------------------------------------------- WSO.B stays unresolved + retryable ----
def test_wso_b_sparse_history_stays_pending_price_and_is_never_remapped():
    rows = [{"date": f"2026-10-{d:02d}", "market_time": "r", "close": str(40 + d / 10), "total_volume": 1000}
            for d in range(1, 13) if date(2026, 10, d).weekday() < 5]
    o = eo.evaluate("WSO.B", date(2026, 10, 27), "BMO", NOW, eo.regular_session_closes(rows))     # history stops at 12 Oct
    assert o["status"] == eo.PENDING_PRICE and o["move_pct"] is None
    far = datetime(2027, 6, 1, tzinfo=UTC)
    assert eo.retry_due(o, eo.ready_at(date(2026, 10, 27)), far) is True            # still retried, never terminal
    fetcher_src = inspect.getsource(eo.default_bars_fetcher)
    assert "replace(" not in fetcher_src and "split(" not in fetcher_src                   # symbol used exactly as stored


# ---------------------------------------------------------------- scoreboard vs the audit sample ----
def _audit_gemini_sample():
    """99 events with the audit's outcome mix (45 up / 43 down / 11 flat) and Gemini's call mix (BUY 41 -> 16 right,
    SELL 24 -> 13 right, NEUTRAL 34)."""
    plan = []
    plan += [("BUY", "up")] * 16 + [("BUY", "down")] * 20 + [("BUY", "flat")] * 5
    plan += [("SELL", "down")] * 13 + [("SELL", "up")] * 8 + [("SELL", "flat")] * 3
    plan += [("NEUTRAL", "up")] * 21 + [("NEUTRAL", "down")] * 10 + [("NEUTRAL", "flat")] * 3
    assert len(plan) == 99
    rows, outcomes = [], {}
    for i, (call, res) in enumerate(plan):
        sym = f"S{i:03d}"
        e = date(2026, 10, 21)
        move = {"up": 2.0, "down": -2.0, "flat": 0.1}[res]
        rows.append({"model": "gemini", "symbol": sym, "earnings_date": e, "report_timing": "Before Open",
                     "predicted_at": datetime(2026, 10, 20, 4, 21, 7, tzinfo=UTC), "signal": call, "score": 60.0})
        outcomes[(sym, e)] = {"status": eo.GRADED, "move_pct": move, "direction": eo.direction_of(move), "timing": "BMO"}
    return rows, outcomes


def test_scoreboard_reproduces_the_audit_sample_figures():
    rows, outcomes = _audit_gemini_sample()
    m = sb.build(rows, outcomes, {"gemini": "Gemini"}, NOW)["models"][0]
    assert m["buy"]["graded"] == 41 and m["buy"]["correct"] == 16 and list(m["buy"]["ci95"]) == [25.7, 54.3]
    assert m["sell"]["graded"] == 24 and m["sell"]["correct"] == 13 and list(m["sell"]["ci95"]) == [35.1, 72.1]
    assert m["neutral"]["graded"] == 34
    b = m["baseline"]
    assert (b["up"], b["down"], b["flat"]) == (45, 43, 11)
    assert b["always_buy_accuracy"] == 45.5 and list(b["always_buy_ci95"]) == [36.0, 55.2]
    assert m["events_graded"] == 99


@pytest.mark.parametrize("ok, n, lo, hi", [(36, 82, 33.7, 54.7), (32, 70, 34.6, 57.3), (28, 64, 32.3, 55.9), (23, 55, 29.7, 55.0),
                                           (23, 51, 32.3, 58.6), (21, 56, 26.0, 50.6), (5, 11, 21.3, 72.0), (4, 13, 12.7, 57.6),
                                           (5, 13, 17.7, 64.5), (1, 4, 4.6, 69.9), (1, 3, 6.1, 79.2), (1, 1, 20.7, 100.0)])
def test_wilson_intervals_match_every_audit_figure(ok, n, lo, hi):
    assert list(sb.wilson(ok, n)) == [lo, hi]


def test_sample_exclusions_are_counted_not_silently_dropped():
    rows, outcomes = _audit_gemini_sample()
    rows.append({**rows[0], "symbol": "WSO.B", "earnings_date": date(2026, 10, 27)})
    outcomes[("WSO.B", date(2026, 10, 27))] = {"status": eo.PENDING_PRICE, "move_pct": None, "direction": None, "timing": "BMO"}
    rows.append({**rows[1], "symbol": "LATE", "predicted_at": datetime(2026, 10, 21, 15, 0, tzinfo=UTC)})
    m = sb.build(rows, outcomes, {"gemini": "Gemini"}, NOW)["models"][0]
    assert m["events_missing_outcome"] == 1 and m["events_pending"] == 0 and m["events_graded"] == 99
    assert m["excluded"]["post_release_only"] == 1
