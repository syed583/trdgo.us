"""Replay invariants (one simulated trading day, six strategies, identical snapshots). Prices are SIMULATED."""
from collections import Counter
from datetime import date

import legacy_v2_compare as L
import market_calendar as mc
import models_demo_trades as m


def test_v2_replay_invariants_one_day(monkeypatch):
    monkeypatch.setattr(L, "DAYS", [date(2026, 10, 27)])
    res = L.run_engine("v2", L.Market())
    try:
        book = res["book"]
        trades, events = book.trades(), book.events()
        assert trades and events
        assert not [t for t in trades if t.status == "OPEN"]                                    # everything closed at the bell
        assert not [t for t in trades if t.status == "PENDING_RECON" and t.exit_price is not None]
        assert Counter((t.strategy, t.symbol, t.session_date, t.entry_session) for t in trades).most_common(1)[0][1] == 1
        assert {t.entry_session for t in trades} <= {"PREMARKET", "REGULAR"}
        for t in trades:                                                                         # never opened outside a session, never after the bell
            et_ = t.entry_time.replace(tzinfo=__import__("datetime").timezone.utc).astimezone(mc.ET)
            assert mc.entries_allowed(et_), (t.strategy, t.symbol, et_)
        assert all(e.snapshot_id for e in events)                                                # every signal event carries its evidence
        assert all(t.snapshot_id and t.validation for t in trades)
        assert {e.session_type for e in events} == {"PREMARKET", "REGULAR"}
    finally:
        res["mp"].undo()
