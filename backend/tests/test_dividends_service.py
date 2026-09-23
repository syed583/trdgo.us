"""
Dividends: what was declared, read back without inventing a schedule.

Nasdaq's payload is stubbed. What these pin is the reading of it -- American
dates become ISO, "$0.25" becomes a number, growth compares like with like,
and a company that pays nothing says so rather than showing an empty table.
"""

import json

import dividends_service as dv


def _payload(rows, headers=None):
    return {
        "data": {
            "dividendHeaderValues": headers or [
                {"label": "Ex-Dividend Date", "value": "09/10/2026"},
                {"label": "Dividend Yield", "value": "0.44%"},
                {"label": "Annual Dividend", "value": "$1.00"},
            ],
            "dividends": {"rows": rows},
        }
    }


def _row(ex, amount, declared="08/26/2026", pay="10/01/2026"):
    return {"exOrEffDate": ex, "type": "Cash", "amount": amount,
            "declarationDate": declared, "recordDate": ex,
            "paymentDate": pay, "currency": "USD"}


def _stub(monkeypatch, payload):
    class Response:
        def __init__(self, body):
            self.body = body

        def read(self):
            return json.dumps(self.body).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(dv.urllib.request, "urlopen",
                        lambda *a, **k: Response(payload))
    dv.cache.purge("ZZDIV")
    dv.cache.purge("ZZNONE")
    dv.cache.purge("ZZDEAD")


def test_dates_and_amounts_are_normalised(monkeypatch):
    _stub(monkeypatch, _payload([_row("09/10/2026", "$0.25")]))
    out = dv.get_dividends("ZZDIV")
    assert out["status"] == "OK"
    payment = out["payments"][0]
    assert payment["ex_date"] == "2026-09-10"
    assert payment["amount"] == 0.25
    assert payment["pay_date"] == "2026-10-01"
    assert out["dividend_yield_pct"] == 0.44
    assert out["annual_dividend"] == 1.0


def test_growth_compares_four_quarters_with_the_four_before(monkeypatch):
    rows = ([_row(f"0{i}/10/2026", "$0.25") for i in range(1, 5)]
            + [_row(f"0{i}/10/2025", "$0.20") for i in range(1, 5)])
    _stub(monkeypatch, _payload(rows))
    out = dv.get_dividends("ZZDIV", limit=20)
    assert out["ttm_total"] == 1.0
    assert out["growth_pct"] == 25.0
    assert out["trend"] == "rising"


def test_a_cut_payout_is_called_a_cut(monkeypatch):
    rows = ([_row(f"0{i}/10/2026", "$0.10") for i in range(1, 5)]
            + [_row(f"0{i}/10/2025", "$0.20") for i in range(1, 5)])
    _stub(monkeypatch, _payload(rows))
    assert dv.get_dividends("ZZDIV", limit=20)["trend"] == "cut"


def test_a_company_that_pays_nothing_says_so(monkeypatch):
    _stub(monkeypatch, _payload([]))
    out = dv.get_dividends("ZZNONE")
    assert out["status"] == "NO_DATA"
    assert out["pays_dividend"] is False
    assert out["payments"] == []


def test_a_dead_feed_is_a_status_not_a_crash(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("nasdaq is down")

    monkeypatch.setattr(dv.urllib.request, "urlopen", boom)
    dv.cache.purge("ZZDIV")
    dv.cache.purge("ZZNONE")
    dv.cache.purge("ZZDEAD")
    out = dv.get_dividends("ZZDEAD")
    assert out["status"] == "PROVIDER_OFFLINE" and out["payments"] == []
