"""
Company events: the issuer's own filing tags, read back as plain events.

EDGAR is stubbed, so these say nothing about the network -- what they pin is
the reading: one 8-K reporting two things becomes two events, an item the app
has no words for is left out rather than shown as a code, and every event
carries a link to the filing it came from.
"""

import corporate_events_service as ce


SUBMISSIONS = {
    "filings": {
        "recent": {
            "form": ["8-K", "8-K", "10-Q", "425", "8-K"],
            "items": ["5.02,2.02", "9.01", "", "", "0.00"],
            "filingDate": ["2026-09-10", "2026-09-01", "2026-08-20",
                           "2026-07-15", "2020-01-02"],
            "accessionNumber": ["0001-26-000001", "0001-26-000002",
                                "0001-26-000003", "0001-26-000004",
                                "0001-26-000005"],
            "primaryDocument": ["a.htm", "b.htm", "c.htm", "d.htm", "e.htm"],
        }
    }
}


def _stub(monkeypatch):
    monkeypatch.setattr(ce.sec, "_submissions", lambda symbol: SUBMISSIONS)
    monkeypatch.setattr(ce.sec, "_cik", lambda symbol: "0000320193")
    ce.cache.purge("corpevents")


def test_one_filing_reporting_two_things_becomes_two_events(monkeypatch):
    _stub(monkeypatch)
    out = ce.get_events("ZZEV")
    heads = [(e["filed"], e["category"], e["headline"]) for e in out["events"]]
    assert ("2026-09-10", "leadership", "Management change") in heads
    assert ("2026-09-10", "earnings", "Results released") in heads


def test_items_the_app_has_no_words_for_are_left_out(monkeypatch):
    _stub(monkeypatch)
    out = ce.get_events("ZZEV")
    # 9.01 is exhibits -- housekeeping on every 8-K -- and 0.00 is nothing.
    assert all(e["item"] != "9.01" for e in out["events"])
    assert all(e["filed"] != "2020-01-02" for e in out["events"])


def test_merger_paperwork_counts_as_a_deal(monkeypatch):
    _stub(monkeypatch)
    out = ce.get_events("ZZEV")
    deal = [e for e in out["events"] if e["form"] == "425"]
    assert deal and deal[0]["category"] == "deal" and deal[0]["impact"] == "high"


def test_the_window_excludes_older_filings(monkeypatch):
    _stub(monkeypatch)
    assert ce.get_events("ZZEV", days=3650)["count"] >= \
        ce.get_events("ZZEV", days=365)["count"]


def test_every_event_links_to_its_filing(monkeypatch):
    _stub(monkeypatch)
    for event in ce.get_events("ZZEV")["events"]:
        assert event["url"].startswith("https://www.sec.gov/Archives/edgar/data/320193/")


def test_a_symbol_edgar_does_not_know_says_so(monkeypatch):
    monkeypatch.setattr(ce.sec, "_submissions", lambda symbol: None)
    out = ce.get_events("ZZNOPE")
    assert out["status"] == "NO_DATA" and out["events"] == []
