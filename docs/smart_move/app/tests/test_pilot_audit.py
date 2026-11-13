"""Pilot evidence audit helpers (read only): heuristic quarter candidate, near-duplicate headline detection, and the audit on the real saved pilot when it is present."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_pilot_audit as PA  # noqa: E402

STORE, RUN = Path("C:/Users/initi/SmartMoveRun/pilot_store_20261102"), Path("C:/Users/initi/SmartMoveRun/pilot_run_20261102")


def test_candidate_quarter_is_the_latest_quarter_end_before_the_report_and_is_a_heuristic():
    rows = [{"horizon": "fiscal quarter", "date": d} for d in ("2026-12-31", "2026-09-30", "2026-06-30")] + [{"horizon": "fiscal year", "date": "2026-12-31"}]
    q, days = PA.candidate_quarter(rows, "2026-11-03")
    assert q["date"] == "2026-09-30" and days == 34                                                      # the future quarter and the fiscal-year row are never candidates
    assert PA.candidate_quarter([{"horizon": "fiscal quarter", "date": "2026-12-31"}], "2026-11-03") is None


def test_near_duplicates_find_the_same_story_from_two_publishers_but_not_different_stories():
    hs = [{"headline": "Acme Corp announces Q3 results date"}, {"headline": "ACME Corp. announces Q3 results date!"}, {"headline": "Acme recalls product line"}]
    pairs = PA.near_duplicates(hs)
    assert len(pairs) == 1 and pairs[0][0] is hs[0] and pairs[0][1] is hs[1]


@pytest.mark.skipif(not (STORE.exists() and RUN.exists()), reason="pilot evidence not on this machine")
def test_the_saved_pilot_passes_the_accounting_and_integrity_audit():
    from datetime import datetime, timezone
    r = PA.audit(STORE, RUN, datetime(2026, 11, 2, 16, 14, 30, tzinfo=timezone.utc))
    assert r["A"]["attempts_logged"] == 50 and r["A"]["all_ok"] and r["A"]["duplicate_requests"] == [] and r["A"]["records_match_attempts"]
    assert r["B"]["eligible"] == 269 and r["B"]["duplicates"] == 0        # kit v6.7: 236 under the two-vendor-only rule + 33 single-vendor dates now scanned and labelled and r["F"]["hash_mismatch"] == 0 and r["F"]["payload_missing"] == 0
    assert r["C"]["responses"] == 18 and r["D"]["headlines"] == 119 and r["D"]["problems"]["future_dated"] == 0
