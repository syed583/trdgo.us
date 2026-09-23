"""
The model must not produce a reading for something that is not a stock.

Asked about ZZZZZ the app answered "DO NOT TRADE", confidence and all, with
exactly one live parameter behind it: "Pays no dividend" -- true of every
string that is not a company. The blocked reasons made it *look* careful,
but a page that renders a score for a typo teaches the reader to trust the
next one less.

The guard has to be one-sided: refusing a real company because a provider is
down is the worse mistake, so an unanswerable check means "real".
"""

import directional_score_service as ds


def _validate(result):
    import symbol_service as symbols
    original = symbols.validate
    symbols.validate = lambda s: result
    return original


def test_a_symbol_nobody_lists_is_not_real():
    import symbol_service as symbols
    original = _validate({"valid": False, "status": "SYMBOL_NOT_FOUND"})
    try:
        assert ds._symbol_exists("ZZZZZ") is False
    finally:
        symbols.validate = original


def test_a_listed_symbol_is_real():
    import symbol_service as symbols
    original = _validate({"valid": True, "status": "OK"})
    try:
        assert ds._symbol_exists("NVDA") is True
    finally:
        symbols.validate = original


def test_a_provider_outage_never_condemns_a_symbol():
    import symbol_service as symbols
    original = _validate({"valid": False, "status": "PROVIDER_OFFLINE"})
    try:
        assert ds._symbol_exists("NVDA") is True, \
            "an unanswerable check must not read as 'not a stock'"
    finally:
        symbols.validate = original


def test_a_check_that_raises_never_condemns_a_symbol(monkeypatch):
    import symbol_service as symbols

    def boom(_):
        raise RuntimeError("down")

    monkeypatch.setattr(symbols, "validate", boom)
    assert ds._symbol_exists("NVDA") is True
