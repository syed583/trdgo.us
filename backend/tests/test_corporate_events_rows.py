"""
Every tab has rows behind it, and every row has something to open.

Two ways this panel goes dead on the screen: a tab counts the whole history
while the payload carries a slice of it, so the tab opens on an empty list;
and a row whose source gave no filing URL has nothing to click at all. Both
look like a broken button to whoever is using it.
"""

import corporate_events_service as ce


def _payload(events):
    return {"symbol": "TST", "status": "OK", "events": events, "source": "SEC"}


def _event(filed, category, url=None, facts=None):
    row = {"symbol": "TST", "filed": filed, "days_ago": 1, "form": "8-K",
           "item": None, "category": category, "category_label": category,
           "headline": "x", "detail": "y", "impact": "low", "url": url,
           "source": "SEC"}
    if facts is not None:
        row["facts"] = facts
    return row


def test_tab_counts_describe_the_rows_actually_sent():
    events = [_event(f"2026-09-{i:02d}", "deal" if i > 3 else "dividend")
              for i in range(1, 10)]
    out = ce._shipped(_payload(events), limit=4)

    assert out["count"] == 4 and len(out["events"]) == 4
    assert sum(out["counts_by_category"].values()) == 4, \
        "a tab is offering rows the payload does not contain"
    assert out["total_in_window"] == 9


def test_no_category_is_counted_without_a_row_to_show():
    out = ce._shipped(_payload([_event("2026-09-09", "deal"),
                                _event("2026-09-08", "dividend")]), limit=1)
    shown = {e["category"] for e in out["events"]}
    assert set(out["counts_by_category"]) == shown


def test_a_dividend_row_carries_its_dates(monkeypatch):
    """
    Dividends are not an 8-K item, so they are merged in from the dividend
    feed -- and a row with no filing behind it still has to be openable.
    """
    import uw_company_service as uwc

    monkeypatch.setattr(uwc, "dividends", lambda s, limit=6: {
        "status": "OK", "payments": [{
            "amount": 0.27, "declared": "2026-08-01", "ex_date": "2026-08-10",
            "pay_date": "2026-08-13", "record_date": "2026-08-11",
            "type": "Cash"}]})

    rows = ce.get_events("NVDA")["events"]
    dividend = next((r for r in rows if r["category"] == "dividend"), None)
    assert dividend is not None
    assert dividend["url"] is None, "there is no filing to link to"
    facts = {f["label"]: f["value"] for f in dividend["facts"]}
    assert facts["Amount"] == "0.27" and facts["Payable"] == "2026-08-13"
