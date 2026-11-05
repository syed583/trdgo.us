"""P1: new / rescheduled / missing / removed earnings events from per-slot refreshes. A removal needs two COMPLETE refreshes of the same slot; an incomplete or unrefreshed slot never removes anything."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_calendar_changes as CC  # noqa: E402

UTC = timezone.utc
T = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
MON_PRE, MON_AFT, TUE_PRE = "2026-11-09|premarket", "2026-11-09|afterhours", "2026-11-10|premarket"


def slot(key, syms, complete=True):
    d, side = key.split("|")
    return {"date": d, "side": side, "pages_fetched": 1, "symbols": list(syms), "complete": complete, "reason": None if complete else "PAGE_CAP_REACHED"}


def kinds(changes):
    return [(c["symbol"], c["change"]) for c in changes]


def test_a_symbol_seen_for_the_first_time_is_new_and_a_repeat_is_silent():
    st = {}
    assert kinds(CC.track(st, [slot(MON_PRE, ["AAA", "BBB"])], T)) == [("AAA", "NEW"), ("BBB", "NEW")]
    assert CC.track(st, [slot(MON_PRE, ["AAA", "BBB"])], T + timedelta(hours=6)) == []


def test_a_new_date_or_session_is_a_reschedule_and_the_old_slot_does_not_call_it_missing():
    st = {}
    CC.track(st, [slot(MON_PRE, ["AAA"]), slot(TUE_PRE, [])], T)
    ch = CC.track(st, [slot(MON_PRE, []), slot(TUE_PRE, ["AAA"])], T + timedelta(hours=6))              # both slots refreshed in the same run: the symbol moved
    assert kinds(ch) == [("AAA", "RESCHEDULED")] and ch[0]["from_slot"] == MON_PRE and ch[0]["to_slot"] == TUE_PRE
    assert st["symbol_index"]["AAA"]["state"] == "LISTED" and st["symbol_index"]["AAA"]["slot"] == TUE_PRE
    st2 = {}
    CC.track(st2, [slot(MON_PRE, ["BBB"])], T)
    ch2 = CC.track(st2, [slot(MON_AFT, ["BBB"])], T + timedelta(hours=6))                               # session changed BMO -> AMC
    assert kinds(ch2) == [("BBB", "RESCHEDULED")] and ch2[0]["from_slot"] == MON_PRE


def test_removal_needs_two_complete_refreshes_in_a_row_missing_comes_first():
    st = {}
    CC.track(st, [slot(MON_PRE, ["AAA"])], T)
    first = CC.track(st, [slot(MON_PRE, [])], T + timedelta(hours=6))
    assert kinds(first) == [("AAA", "MISSING")] and st["symbol_index"]["AAA"]["state"] == "MISSING"           # it may have moved to a slot that was not refreshed yet: only a warning
    second = CC.track(st, [slot(MON_PRE, [])], T + timedelta(hours=12))
    assert kinds(second) == [("AAA", "REMOVED")] and second[0]["missing_refreshes"] == 2
    assert CC.track(st, [slot(MON_PRE, [])], T + timedelta(hours=18)) == []                                  # already removed: no repeat
    back = CC.track(st, [slot(MON_PRE, ["AAA"])], T + timedelta(hours=24))
    assert kinds(back) == [("AAA", "REAPPEARED")] and back[0]["was"] == "REMOVED" and st["symbol_index"]["AAA"]["state"] == "LISTED"


def test_an_incomplete_or_unrefreshed_slot_never_removes_or_flags_anything():
    st = {}
    CC.track(st, [slot(MON_PRE, ["AAA"]), slot(TUE_PRE, ["BBB"])], T)
    assert CC.track(st, [slot(MON_PRE, [], complete=False)], T + timedelta(hours=6)) == []                   # cut short: it never saw the whole slot
    assert CC.track(st, [slot(MON_AFT, ["CCC"])], T + timedelta(hours=6))[0]["change"] == "NEW"             # an unrelated slot does not touch the others
    assert st["symbol_index"]["AAA"]["state"] == "LISTED" and st["symbol_index"]["BBB"]["state"] == "LISTED" and st["symbol_index"]["AAA"]["missing_count"] == 0


def test_changes_are_appended_never_rewritten_and_a_half_written_last_line_is_ignored(tmp_path):
    st = {}
    n1 = CC.append_changes(tmp_path, CC.track(st, [slot(MON_PRE, ["AAA"])], T))
    n2 = CC.append_changes(tmp_path, CC.track(st, [slot(MON_PRE, [])], T + timedelta(hours=6)))
    lines = (tmp_path / CC.CHANGES_FILE).read_text(encoding="utf-8").splitlines()
    assert (n1, n2, len(lines)) == (1, 1, 2) and [json.loads(x)["change"] for x in lines] == ["NEW", "MISSING"]
    with (tmp_path / CC.CHANGES_FILE).open("a", encoding="utf-8") as fh:
        fh.write('{"at": "x", "symbol": "ZZ", "chan')                                                         # a write in progress
    assert [c["change"] for c in CC.read_changes(tmp_path)] == ["NEW", "MISSING"]
    assert CC.append_changes(tmp_path, []) == 0
