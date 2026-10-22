"""Local and production must never share DB cache rows."""
import db_cache


def test_non_production_is_namespaced(monkeypatch):
    for v in (None, "", "local", "dev", "staging"):
        if v is None:
            monkeypatch.delenv("APP_ENV", raising=False)
        else:
            monkeypatch.setenv("APP_ENV", v)
        assert db_cache.namespace() == "local:"


def test_production_keeps_bare_keys(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    assert db_cache.namespace() == ""
    monkeypatch.setenv("APP_ENV", " Production ")
    assert db_cache.namespace() == ""
