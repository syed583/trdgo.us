"""
Matching a ticker to the CUSIP institutions filed it under.

13F filers and the SEC's own registry spell the same company differently, and
the old prefix test missed two of the largest names in the market: the
registry calls XOM "ExxonMobil Holdings Corp" where filers write "EXXON MOBIL
CORP", and Disney is "Walt Disney Co" against their "DISNEY WALT CO". Both
sat in the table under the correct CUSIP while the app reported "no 13F
issuer matched this ticker" -- which reads as "no institution holds it".

The matcher has to be loose enough to cross that gap and tight enough never
to cross companies: a wrong CUSIP shows one company's institutional holders
under another's name, and nothing on the screen would look wrong.
"""

import institutional_service as ins


def test_the_same_words_in_a_different_order_are_the_same_company():
    assert ins._name_match("WALT DISNEY", "DISNEY WALT") == 1.0


def test_spacing_is_not_a_difference():
    assert ins._name_match("EXXONMOBIL", "EXXON MOBIL") == 1.0


def test_a_longer_registered_name_still_matches():
    assert ins._name_match("MICRON TECHNOLOGY", "MICRON") >= 0.5


def test_one_shared_word_is_not_a_company():
    """"MOBIL" appears in T-Mobile; that is not Exxon."""
    assert ins._name_match("EXXON MOBIL", "T MOBILE US") == 0.0
    assert ins._name_match("FIRST SOLAR", "FIRST REPUBLIC") == 0.0


def test_unrelated_names_do_not_match():
    assert ins._name_match("APPLE", "APPLIED MATERIALS") == 0.0
    assert ins._name_match("", "APPLE") == 0.0
    assert ins._name_match("APPLE", "") == 0.0


def test_a_shared_noise_word_is_not_a_match():
    assert ins._name_match("AMERICAN AIRLINES", "AMERICAN EXPRESS") == 0.0


def test_a_former_name_counts_as_the_company(monkeypatch):
    """BAC's filers use the spelling of its old name, not its registered one."""
    import sec_filings_service as filings
    import sec_service as sec

    monkeypatch.setattr(sec, "get_company_cik",
                        lambda t: {"company_name": "BANK OF AMERICA CORP /DE/"})
    monkeypatch.setattr(filings, "_submissions", lambda t: {
        "name": "BANK OF AMERICA CORP /DE/",
        "formerNames": [{"name": "BANKAMERICA CORP/DE/"},
                        {"name": "NATIONSBANK CORP"}]})

    names = ins._known_names("BAC")
    assert "BANKAMERICA DE" in names, "the former spelling has to be tried too"
    # And that spelling is what reaches the filers' "BANK AMERICA".
    assert ins._name_match("BANKAMERICA DE", "BANK AMERICA") >= 0.9


def test_a_registry_name_that_no_filer_uses_falls_back_to_the_ticker():
    """GE files as "GE AEROSPACE"; no spelling of "General Electric" reaches it."""
    class _Row:
        def __init__(self):
            self.match_name = "GE AEROSPACE"
            self.holders = 9695
            self.cusip = "369604301"
            self.ticker = None

    row = _Row()

    class _Query:
        def filter(self, *a): return self
        def order_by(self, *a): return self
        def limit(self, *a): return self
        def all(self): return [row]

    class _Session:
        def query(self, *a): return _Query()
        def commit(self): pass

    assert ins._by_ticker_word(_Session(), "GE") == "369604301"
    assert row.ticker == "GE", "a resolved ticker is remembered"


def test_the_ticker_must_be_a_whole_word_not_a_fragment():
    class _Query:
        def filter(self, *a): return self
        def order_by(self, *a): return self
        def limit(self, *a): return self
        def all(self):
            row = type("R", (), {"match_name": "GENERAL MILLS", "holders": 1,
                                 "cusip": "x", "ticker": None})()
            return [row]

    class _Session:
        def query(self, *a): return _Query()
        def commit(self): pass

    assert ins._by_ticker_word(_Session(), "GE") is None
