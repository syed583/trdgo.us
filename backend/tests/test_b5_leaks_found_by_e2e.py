"""B.5: the leaks the end-to-end suite found in real traffic, which a source audit had missed.

B.1 shaped the routes a reading of 243 route definitions pointed at. B.0 then drove the live site as an
ordinary user and found five more, because these names are not written in the route - they arrive inside a
payload some service builds several layers down. That is the argument for the suite in one sentence.

Four are fixed here. The fifth, /api/earnings-monitor/smart-move/live, lives in earnings_monitor/routes.py:
a Smart Move PROTECTED file, pinned by SHA-256 in every installer and frozen by the owner until ~2026-11-19.
It is deliberately left alone and reported instead.
"""
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import model_privacy as mp  # noqa: E402

# Shaped like the real answers, each carrying the string B.0 actually saw.
HEALTH = {"providers": {"feed": {"provider": "Unusual Whales", "host": "api.unusualwhales.com", "status": "OK"},
                        "database": {"status": "OK"}},
          "live": True}
STRIP = {"rows": [{"symbol": "NVDA", "score": 61, "source": "Unusual Whales"}]}
OVERVIEW = {"symbol": "NVDA", "stats": {"pe": 30}, "source": "Unusual Whales options tape"}
DESK = {"items": [{"headline": "x", "provider": "Unusual Whales"},
                  {"headline": "y", "model": "gemini", "note": "scored by Gemini"}]}

BANNED = ("Unusual Whales", "unusualwhales", "Gemini", "Finnhub", "Benzinga")


def clean(payload, **kw):
    return json.dumps(mp.public_payload(payload, admin=False, **kw), default=str)


@pytest.mark.parametrize("name,payload,kw", [
    ("/api/health", HEALTH, {}),
    ("/api/tickers/strip", STRIP, {}),
    ("/api/overview/{symbol}", OVERVIEW, {}),
    ("/api/news/desk", DESK, {"map_models": True}),
])
def test_no_vendor_or_model_name_survives_for_a_non_admin(name, payload, kw):
    blob = clean(payload, **kw)
    bad = [w for w in BANNED if w in blob]
    assert not bad, "%s still sends %s to an ordinary reader" % (name, bad)


def test_a_vendor_host_is_scrubbed_too():
    """api.unusualwhales.com names the vendor just as plainly as 'Unusual Whales' does."""
    assert "unusualwhales" not in clean(HEALTH)


def test_the_news_desk_model_name_becomes_an_agent_label():
    out = mp.public_payload(DESK, admin=False, map_models=True)
    assert out["items"][1]["model"] == "Agent 4"


def test_an_administrator_still_sees_the_real_names():
    for payload in (HEALTH, STRIP, OVERVIEW, DESK):
        assert mp.public_payload(payload, admin=True) is payload


def test_what_a_reader_keeps():
    """Shaping must not empty the page: the facts a reader needs survive."""
    out = mp.public_payload(HEALTH, admin=False)
    assert out["live"] is True
    assert out["providers"]["feed"]["status"] == "OK"
    assert out["providers"]["database"]["status"] == "OK"
    strip = mp.public_payload(STRIP, admin=False)
    assert strip["rows"][0]["symbol"] == "NVDA" and strip["rows"][0]["score"] == 61


def test_the_four_routes_take_a_request_so_they_can_tell_who_is_asking():
    """A route that cannot see the session cannot shape its answer. This is how the leak happened."""
    src = (BACKEND / "api_routes.py").read_text(encoding="utf-8")
    for sig in ("def provider_matrix(request: Request",
                "def ticker_strip(request: Request",
                "def ticker_overview(request: Request",
                "def news_desk(request: Request"):
        assert sig in src, "missing: %s" % sig


def test_the_smart_move_route_is_left_alone_and_recorded():
    """The fifth leak is inside a Smart Move protected file - pinned by SHA-256 in every installer and frozen
    until ~2026-11-19. This test exists so the exception is written down rather than forgotten."""
    routes = (BACKEND / "earnings_monitor" / "routes.py").read_text(encoding="utf-8")
    assert "model_privacy" not in routes, (
        "earnings_monitor/routes.py was modified. It is a protected Smart Move file: changing it breaks the "
        "installer's pinned hash and the owner's freeze. Fix /api/earnings-monitor/smart-move/live only after "
        "the freeze lifts, and regenerate the pin."
    )
