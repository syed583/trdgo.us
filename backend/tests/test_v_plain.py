"""Kit V: what the user reads. A sentence they can act on, and never our supplier's name."""
import user_mt5_plain as P

EXPIRED = "API_ERROR: CloudTrial failed(mt5rest.5.9.138.245): Trial expired. Please purchase full version. https://mtapi.online (:250094)"


def test_the_live_reasons_from_the_audit_read_as_sentences():
    assert P.plain(EXPIRED, "SKIPPED").startswith("The trading connection's licence had expired")
    assert P.plain("MAX_OPEN_POSITIONS: 20 open, your limit is 20", "SKIPPED") == "Your position limit was reached (20 open, limit 20)."
    assert P.plain("MAX_OPEN_POSITIONS", "SKIPPED") == "Your position limit was reached."          # rows written before kit V
    assert P.plain("DAILY_LOSS_LIMIT", "SKIPPED").startswith("Your daily loss limit was reached")
    assert P.plain("NOT_ENOUGH_MARGIN: this order needs 5000.00 and 100.00 is free") == "Not enough margin: this order needed 5000.00 and 100.00 was free."
    assert P.plain("NOT_MAPPED: map TSLA to your broker's symbol on My Broker to trade it").startswith("This stock is not mapped")
    assert P.plain("AUTO_TRADE_OFF") == "Auto-trade was off."
    assert P.plain("AT_BROKER:STOP at 2026-11-19T15:01:02; software exit: SCORE_EXIT", "CLOSED") == "Closed at your broker by the stop-loss."
    assert P.plain("AT_BROKER:PRICE_UNKNOWN - the position is not open at the broker; x", "CLOSED").endswith("closing price is not available.")
    assert P.plain("SCORE_EXIT", "CLOSED") == "Closed with the software: score exit."


def test_no_supplier_name_reaches_the_user_whatever_the_text():
    for raw in (EXPIRED, "TRANSPORT: URLError mt5full3.mtapi.io timed out", "something odd from https://mtapi.online/x happened",
                "the broker confirmed no fill for this ticket: no price returned", "retcode_text=NO_RETRY; comment=Invalid stops"):
        for ev in ("SKIPPED", "CLOSE_FAIL", "REJECTED", None):
            out = P.plain(raw, ev).lower()
            assert "mtapi" not in out and "mt5rest" not in out and "cloudtrial" not in out and "http" not in out, (raw, ev, out)
    assert P.plain("retcode_text=NO_RETRY; comment=Invalid stops", "REJECTED") == "retcode_text=NO_RETRY; comment=Invalid stops"   # the broker's own words survive


def test_an_empty_reason_stays_empty():
    assert P.plain(None) == "" and P.plain("") == ""
