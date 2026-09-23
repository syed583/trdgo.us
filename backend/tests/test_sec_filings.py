"""
Form 4 parsing, exercised against fixed XML rather than the live SEC feed.

The whole value of this module is one distinction: which rows represent an
insider deciding something, and which are compensation machinery. Get that
wrong and the indicator reads bearish at every large issuer, because routine
vesting and pre-arranged plans dwarf real trades. These tests pin that
boundary.
"""

from __future__ import annotations

import sec_filings_service as filings


def _form4(code: str, shares: str = "1000", price: str = "100.00",
           planned: str = "false", owner: str = "Doe Jane") -> str:
    return f"""<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>{owner}</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>0</isDirector>
      <isOfficer>1</isOfficer>
      <officerTitle>Chief Financial Officer</officerTitle>
      <isTenPercentOwner>0</isTenPercentOwner>
    </reportingOwnerRelationship>
  </reportingOwner>
  <aff10b5One>{planned}</aff10b5One>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-09-08</value></transactionDate>
      <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>{shares}</value></transactionShares>
        <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>5000</value></sharesOwnedFollowingTransaction>
      </postTransactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
  <derivativeTable>
    <derivativeTransaction>
      <transactionCoding><transactionCode>A</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>99999</value></transactionShares>
      </transactionAmounts>
    </derivativeTransaction>
  </derivativeTable>
</ownershipDocument>"""


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------


def test_open_market_purchase_is_a_buy():
    row = filings._parse_form4(_form4("P"), "2026-09-09", "u")[0]

    assert row["direction"] == "Buy"
    assert row["discretionary"] is True
    assert row["shares"] == 1000
    assert row["value"] == 100000.0
    assert row["role"] == "Chief Financial Officer"


def test_open_market_sale_is_a_sell():
    row = filings._parse_form4(_form4("S"), "2026-09-09", "u")[0]

    assert row["direction"] == "Sell"
    assert row["discretionary"] is True


def test_grants_and_exercises_are_not_decisions():
    """A vesting award is not the executive expressing a view."""
    for code in ("A", "M", "F", "G"):
        row = filings._parse_form4(_form4(code), "2026-09-09", "u")[0]
        assert row["discretionary"] is False, code
        assert row["direction"] is None, code


def test_scheduled_plan_sales_are_not_decisions():
    """
    A 10b5-1 plan is adopted months ahead and then runs on its own. Counting
    it as bearish is how an insider signal ends up firing every week at a
    company whose executives simply sell on a schedule.
    """
    row = filings._parse_form4(_form4("S", planned="true"), "2026-09-09", "u")[0]

    assert row["planned_10b5_1"] is True
    assert row["discretionary"] is False


def test_derivative_rows_are_ignored():
    """Option grants live in the derivative table and must not inflate size."""
    rows = filings._parse_form4(_form4("P"), "2026-09-09", "u")

    assert len(rows) == 1
    assert all(r["shares"] != 99999 for r in rows)


# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------


def _rows(*specs) -> list[dict]:
    out = []
    for code, planned, owner in specs:
        out.extend(filings._parse_form4(
            _form4(code, planned=planned, owner=owner), "2026-09-09", "u"))
    return out


def test_summary_counts_only_real_decisions():
    rows = _rows(
        ("P", "false", "Buyer One"),
        ("S", "false", "Seller One"),
        ("S", "true", "Planner One"),     # scheduled, excluded
        ("A", "false", "Grantee One"),    # award, excluded
    )

    summary = filings.summarise_insiders(rows)

    assert summary["buy_count"] == 1
    assert summary["sell_count"] == 1
    assert summary["planned_count"] == 1
    assert summary["mechanical_count"] == 1
    assert summary["buy_share"] == 50.0


def test_a_company_selling_only_on_plans_is_not_reported_as_selling():
    """The AAPL case: large sale totals that are entirely pre-arranged."""
    rows = _rows(
        ("S", "true", "Exec A"),
        ("S", "true", "Exec B"),
        ("S", "true", "Exec C"),
    )

    summary = filings.summarise_insiders(rows)

    assert summary["sell_count"] == 0
    assert summary["planned_count"] == 3
    assert summary["planned_value"] > 0
    # No discretionary trades at all, so there is no ratio to report.
    assert summary["buy_share"] is None


def test_cluster_needs_several_distinct_buyers():
    one_person = _rows(("P", "false", "Solo Buyer"))
    assert filings.summarise_insiders(one_person)["cluster"]["detected"] is False

    crowd = _rows(
        ("P", "false", "Buyer One"),
        ("P", "false", "Buyer Two"),
        ("P", "false", "Buyer Three"),
    )
    cluster = filings.summarise_insiders(crowd)["cluster"]
    assert cluster["detected"] is True
    assert cluster["buyers"] == 3


def test_the_same_person_buying_repeatedly_is_not_a_cluster():
    """Three trades by one executive is one opinion, not three."""
    rows = _rows(
        ("P", "false", "Repeat Buyer"),
        ("P", "false", "Repeat Buyer"),
        ("P", "false", "Repeat Buyer"),
    )

    assert filings.summarise_insiders(rows)["cluster"]["detected"] is False


# --------------------------------------------------------------------------
# failure handling
# --------------------------------------------------------------------------


def test_unknown_symbol_degrades_instead_of_raising(monkeypatch):
    monkeypatch.setattr(filings, "_submissions", lambda s: None)

    assert filings.insider_transactions("NOPE")["status"] == "NO_DATA"
    assert filings.ownership_filings("NOPE")["status"] == "NO_DATA"


def test_malformed_xml_yields_no_rows_rather_than_an_error():
    assert filings._parse_form4("<not-a-form/>", "2026-09-09", "u") == []
