"""The redesigned Historical Backtest: the limitation first, the model's ingredients withheld.

/api/earnings/historical-backtest applies no model_privacy wrap, and its payload carries:

  * `by_model`, keyed by the vendor's own name - the same gap as the forward scoreboard;
  * `present_factors` and `missing_factors`, which together enumerate the model's COMPLETE input list.
    A.29 gated exactly that to administrators on six routes; this is not one of them.

The factor lists are also the reason the page is honest: only four of seventeen inputs can be rebuilt
point-in-time, so the result is a fraction of the live model. The page therefore states the limitation
WITH ITS COUNT - "4 of 17" - and keeps the names for an administrator. A reader learns the result is
partial without being handed the ingredient list.

`test_the_route_is_still_unwrapped` is a reminder: when the server starts masking this payload, it fails,
and the page's workaround can be relaxed.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend" / "src" / "pages" / "NewHistoricalBacktestPage.tsx"
ROUTES = ROOT / "backend" / "api_routes.py"


def _code() -> str:
    s = PAGE.read_text(encoding="utf-8")
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"//[^\n]*", "", s)
    return s


def test_the_page_exists_and_reads_the_backtest():
    assert PAGE.exists()
    assert "api2.historicalBacktest(" in _code()


@pytest.mark.parametrize("vendor", ["grok", "meta", "kimi", "gemini", "claude", "chatgpt"])
def test_no_vendor_name_is_written_into_the_page(vendor):
    assert vendor not in _code().lower(), f"{vendor!r} appears in the page source"


@pytest.mark.parametrize("factor", ["options_flow", "unusual_activity", "oi_positioning",
                                    "implied_volatility", "expected_move", "dark_pool",
                                    "short_interest", "prior_beats", "ema_trend"])
def test_no_factor_name_is_hard_coded(factor):
    """The names arrive in the payload; they must never be typed into the page."""
    assert factor not in _code(), f"factor {factor!r} is written into the page"


def test_the_factor_names_are_administrator_only():
    """Rendered from the payload, but only inside the admin block."""
    code = _code()
    gate = code.find("{isAdmin && (")
    assert gate != -1, "the page needs an administrator-only region"
    for expr in ("present.join(", "missing.join("):
        idx = code.find(expr)
        if idx != -1:
            assert idx > gate, f"{expr} renders factor names before the admin gate"


def test_the_by_model_block_is_administrator_only():
    """Every use of d.by_model must sit immediately behind an isAdmin guard.

    The old form looked for the FIRST literal "{isAdmin && (" and required by_model to come after
    it. That broke the moment the gate was written as `{isAdmin && !!d.by_model && ...}` - a
    correctly gated block the test could not see, because it matched one SPELLING of the guard
    rather than the guard itself. Checking adjacency catches both and still fails if the gate goes.
    """
    code = _code()
    first = code.find("d.by_model")
    if first == -1:
        return                      # the block is absent entirely, which is also not a leak
    # Only the FIRST use needs the gate beside it; everything after is inside that guarded block.
    window = code[max(0, first - 80):first]
    assert "isAdmin" in window, (
        "the first use of d.by_model has no isAdmin guard within 80 characters: "
        + repr(code[max(0, first - 80):first + 40])
    )


def test_the_limitation_is_stated_with_its_count():
    """'4 of 17' tells a reader how partial this is without naming the seventeen."""
    code = _code()
    assert "present.length" in code and "totalFactors" in code, \
        "the page must state how many inputs were reconstructable, out of how many"


def test_the_limitation_comes_before_the_numbers():
    """A partial backtest read as a full one is worse than no backtest."""
    code = _code()
    warn = code.find("nfs-warn")
    stats = code.find("nd-stats")
    assert warn != -1 and stats != -1 and warn < stats, \
        "the limitation banner must precede the figures"


def test_it_says_it_is_never_mixed_with_the_recorded_record():
    src = PAGE.read_text(encoding="utf-8")
    assert "never mixed" in src, \
        "the page must say this simulation is kept apart from the genuinely recorded predictions"


def test_the_route_is_wrapped(self_check=None):
    """Kit L closed this. The page's workaround can now be relaxed whenever we choose to.

    This replaces a reminder that failed on purpose while the route was open; keeping it as a real
    assertion means the leak cannot quietly come back.
    """
    src = ROUTES.read_text(encoding="utf-8")
    route = src.split('@router.get("/earnings/historical-backtest")')[1].split("@router.")[0]
    assert "_privacy.public_payload" in route, "the route must not return the payload unwrapped"
    assert "_is_admin(request)" in route, "the decision must depend on the caller"
    assert "map_models=True" in route, "this payload is organised by model"
