"""
13F ingest and scoring, exercised on a synthetic dataset.

A raw INFOTABLE is not a list of share positions, and every one of the traps
below silently produces a plausible-looking wrong number rather than an error:
option lines inflate share counts, bond principal is not shares, "hold
nothing" notices look like empty portfolios, and an amendment re-states a
filing that is already loaded. These tests pin each one.
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import institutional_service as inst


SUBMISSION_COLS = ["ACCESSION_NUMBER", "FILING_DATE", "SUBMISSIONTYPE",
                   "CIK", "PERIODOFREPORT"]
COVER_COLS = ["ACCESSION_NUMBER", "REPORTCALENDARORQUARTER", "ISAMENDMENT",
              "AMENDMENTNO", "AMENDMENTTYPE", "FILINGMANAGER_NAME"]
INFO_COLS = ["ACCESSION_NUMBER", "NAMEOFISSUER", "TITLEOFCLASS", "CUSIP",
             "VALUE", "SSHPRNAMT", "SSHPRNAMTTYPE", "PUTCALL"]


def _tsv(cols: list[str], rows: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=cols, delimiter="\t",
                            lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return out.getvalue()


def _dataset(tmp_path: Path, submissions, covers, infos) -> Path:
    path = tmp_path / "test_form13f.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("SUBMISSION.tsv", _tsv(SUBMISSION_COLS, submissions))
        archive.writestr("COVERPAGE.tsv", _tsv(COVER_COLS, covers))
        archive.writestr("INFOTABLE.tsv", _tsv(INFO_COLS, infos))
    return path


def _sub(acc, cik, kind="13F-HR", period="31-MAR-2026"):
    return {"ACCESSION_NUMBER": acc, "FILING_DATE": "15-MAY-2026",
            "SUBMISSIONTYPE": kind, "CIK": cik, "PERIODOFREPORT": period}


def _cover(acc, name, amendment_type=""):
    return {"ACCESSION_NUMBER": acc, "REPORTCALENDARORQUARTER": "31-MAR-2026",
            "ISAMENDMENT": "Y" if amendment_type else "N", "AMENDMENTNO": "1",
            "AMENDMENTTYPE": amendment_type, "FILINGMANAGER_NAME": name}


def _info(acc, cusip="037833100", shares="1000", value="250000",
          kind="SH", putcall="", issuer="APPLE INC"):
    return {"ACCESSION_NUMBER": acc, "NAMEOFISSUER": issuer,
            "TITLEOFCLASS": "COM", "CUSIP": cusip, "VALUE": value,
            "SSHPRNAMT": shares, "SSHPRNAMTTYPE": kind, "PUTCALL": putcall}


# --------------------------------------------------------------------------
# quarter handling
# --------------------------------------------------------------------------


def test_period_maps_to_a_calendar_quarter():
    assert inst._quarter("31-MAR-2026") == "2026-Q1"
    assert inst._quarter("31-DEC-2025") == "2025-Q4"
    assert inst._quarter("30-JUN-2025") == "2025-Q2"
    assert inst._quarter("nonsense") is None


def test_only_the_targeted_quarter_is_read(tmp_path):
    """
    A dataset is a filing window, not a quarter. The March-May 2026 file holds
    ten thousand Q1 reports plus stragglers for five earlier quarters, and
    mixing those in compares a fund against the wrong baseline.
    """
    path = _dataset(
        tmp_path,
        [_sub("A1", "111", period="31-MAR-2026"),
         _sub("A2", "222", period="31-DEC-2025")],
        [_cover("A1", "Fund One"), _cover("A2", "Fund Two")],
        [_info("A1"), _info("A2")],
    )

    parsed = inst.parse_dataset(path, quarter="2026-Q1")

    assert parsed["quarter"] == "2026-Q1"
    assert list(parsed["funds"]) == ["111"]


# --------------------------------------------------------------------------
# rows that are not share positions
# --------------------------------------------------------------------------


def test_option_rows_are_not_counted_as_shares(tmp_path):
    """A put on Apple is not a holding of Apple."""
    path = _dataset(
        tmp_path, [_sub("A1", "111")], [_cover("A1", "Fund One")],
        [_info("A1", shares="1000"),
         _info("A1", shares="9999", putcall="Put"),
         _info("A1", shares="8888", putcall="Call")],
    )

    parsed = inst.parse_dataset(path, quarter="2026-Q1")

    assert parsed["skipped_options"] == 2
    assert parsed["funds"]["111"]["positions"]["037833100"]["shares"] == 1000


def test_bond_principal_is_not_counted_as_shares(tmp_path):
    path = _dataset(
        tmp_path, [_sub("A1", "111")], [_cover("A1", "Fund One")],
        [_info("A1", shares="1000"),
         _info("A1", shares="500000", kind="PRN")],
    )

    parsed = inst.parse_dataset(path, quarter="2026-Q1")

    assert parsed["skipped_non_share"] == 1
    assert parsed["funds"]["111"]["positions"]["037833100"]["shares"] == 1000


def test_notice_filings_report_no_holdings(tmp_path):
    """13F-NT says "I hold nothing reportable" and carries no positions."""
    path = _dataset(
        tmp_path,
        [_sub("A1", "111", kind="13F-NT")],
        [_cover("A1", "Fund One")],
        [_info("A1")],
    )

    parsed = inst.parse_dataset(path, quarter="2026-Q1")

    assert parsed["funds"] == {}


# --------------------------------------------------------------------------
# amendments
# --------------------------------------------------------------------------


def test_a_restatement_replaces_rather_than_adds(tmp_path):
    """
    Loading both copies of a restated filing doubles that manager's stake.
    The restated figure is the truth; the original is superseded.
    """
    path = _dataset(
        tmp_path,
        [_sub("A1", "111"), _sub("A2", "111", kind="13F-HR/A")],
        [_cover("A1", "Fund One"),
         _cover("A2", "Fund One", amendment_type="RESTATEMENT")],
        [_info("A1", shares="1000"), _info("A2", shares="400")],
    )

    parsed = inst.parse_dataset(path, quarter="2026-Q1")
    shares = parsed["funds"]["111"]["positions"]["037833100"]["shares"]

    assert shares == 400


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------


def test_position_changes_are_classified():
    assert inst._classify(0, 100) == "NEW POSITION"
    assert inst._classify(100, 0) == "CLOSED"
    assert inst._classify(100, 200) == "INCREASED"
    assert inst._classify(200, 100) == "REDUCED"
    assert inst._classify(100, 100) == "UNCHANGED"


def test_rounding_noise_is_not_a_position_change():
    """A tenth of a percent drift is share-count noise, not a decision."""
    assert inst._classify(100_000, 100_020) == "UNCHANGED"


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------


def test_a_thinly_held_security_scores_neutral():
    """Three funds moving is not an institutional opinion."""
    score, signal = inst._score(3, 0, 1, 0, 40.0, total_funds=4)

    assert score == 0.0
    assert signal == "NEUTRAL"


def test_broad_accumulation_with_real_size_scores_strong():
    score, signal = inst._score(
        funds_increasing=140, funds_decreasing=60, new_positions=30,
        closed_positions=10, net_pct=8.0, total_funds=500)

    assert score == 5.0
    assert signal == "BULLISH"


def test_broad_distribution_scores_strong_negative():
    score, signal = inst._score(
        funds_increasing=60, funds_decreasing=140, new_positions=10,
        closed_positions=30, net_pct=-8.0, total_funds=500)

    assert score == -5.0
    assert signal == "BEARISH"


def test_breadth_without_size_is_not_strong():
    """
    Many funds nudging their positions moves the counts without moving real
    money. Both breadth and net size have to agree before this reads strong.
    """
    score, _ = inst._score(
        funds_increasing=200, funds_decreasing=60, new_positions=40,
        closed_positions=5, net_pct=0.3, total_funds=500)

    assert score == 0.0


def test_size_without_breadth_is_not_strong():
    """One giant fund doubling up is not the institutions agreeing."""
    score, _ = inst._score(
        funds_increasing=55, funds_decreasing=50, new_positions=2,
        closed_positions=2, net_pct=20.0, total_funds=500)

    assert score == 0.0


# --------------------------------------------------------------------------
# name matching
# --------------------------------------------------------------------------


def test_corporate_suffixes_do_not_block_a_match():
    assert inst._normalise("Apple Inc.") == "APPLE"
    assert inst._normalise("MICROSOFT CORP") == "MICROSOFT"
    assert inst._normalise("Alphabet Inc. Class A") == "ALPHABET A"
