"""
Provider rows must survive a database that cannot answer.

On a fresh server the seed table is empty and the database may not even be
reachable yet. The calendar asked it anyway -- after the provider had
already returned every row -- and a query nobody needed threw the answer
away, so the screen read "Data unavailable" over a working subscription.
"""

import earnings_calendar_service as cal


def _no_database(monkeypatch):
    def refuse(*a, **k):
        raise RuntimeError("connection to server at 127.0.0.1 failed")

    monkeypatch.setattr(cal, "SessionLocal", refuse)


def _provider(monkeypatch, rows):
    monkeypatch.setattr(cal, "_unusual_whales_rows",
                        lambda start, end, query, limit: list(rows))


def test_the_calendar_still_answers_when_the_database_is_down(monkeypatch):
    _no_database(monkeypatch)
    _provider(monkeypatch, [{"symbol": "CTAS", "company": "CINTAS",
                             "date": "2026-09-23", "eps_estimate": 1.35,
                             "expected_move_percent": 3.77}])
    monkeypatch.setattr(cal.market.cache, "get", lambda *a, **k: None)

    out = cal.get_calendar(range_key="THIS_WEEK", with_quotes=False)
    assert out["status"] == "OK"
    assert out["count"] == 1
    assert out["rows"][0]["symbol"] == "CTAS"
    assert out["source"]["provider"] == "UNUSUAL_WHALES"


def test_the_seed_query_is_skipped_when_a_provider_answered(monkeypatch):
    """
    The seed table is a fallback, so it is not queried for rows we have.

    Other local lookups -- sector, prior EPS -- may still run; they decorate
    a row rather than supply it, and each is allowed to fail on its own.
    """
    queried = []

    class _Session:
        def query(self, *a):
            queried.append(a)
            raise AssertionError("the seed table was queried anyway")

        def close(self):
            pass

    monkeypatch.setattr(cal, "SessionLocal", _Session)
    monkeypatch.setattr(cal, "_sector_map", lambda s: {})
    monkeypatch.setattr(cal, "_prior_eps", lambda s, d: {})
    monkeypatch.setattr(cal, "_profiles_for", lambda s: {})
    _provider(monkeypatch, [{"symbol": "PAYX", "date": "2026-09-23"}])
    monkeypatch.setattr(cal.market.cache, "get", lambda *a, **k: None)

    out = cal.get_calendar(range_key="THIS_WEEK", with_quotes=False)
    assert out["count"] == 1 and queried == []


def test_with_no_provider_the_database_is_still_consulted(monkeypatch):
    """The fallback has to remain a fallback, not be removed."""
    _provider(monkeypatch, [])
    monkeypatch.setattr(cal, "_provider_rows",
                        lambda start, end, query, limit: [])
    monkeypatch.setattr(cal.market.cache, "get", lambda *a, **k: None)

    asked = []

    class _Session:
        def query(self, *a):
            asked.append(1)
            raise RuntimeError("no table here")

        def close(self):
            pass

    monkeypatch.setattr(cal, "SessionLocal", _Session)
    out = cal.get_calendar(range_key="THIS_WEEK", with_quotes=False)
    assert asked, "with nothing from a provider, the seed table is the point"
    assert out["count"] == 0, "and a failure there is an empty calendar, not a crash"
