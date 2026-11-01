import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_analytics as A  # noqa: E402


def r(v, s, mv, d="2026-11-02", **k):
    return dict(variant=v, signal=s, move_pct=mv, mode="LIVE", earnings_date=d, **k)


def test_hit_rate_ci_sample_size_and_no_claim_below_minimum():
    rows = [r("continuation", "BUY", 3.0, sector="Tech", kind="AMC", coverage=40), r("continuation", "BUY", -2.0, sector="Tech", kind="AMC", coverage=40),
            r("continuation", "SELL", -4.0, sector="Energy", kind="BMO", coverage=30), r("continuation", "NO_TRADE", 1.0), r("continuation", "BUY", None),
            r("contrarian", "SELL", 2.0)]
    o = A.breakdown(rows)
    c = o["variants"]["continuation"]
    assert c["overall"]["decided"] == 3 and c["overall"]["hits"] == 2 and c["overall"]["ungraded"] == 1 and c["no_trade"] == 1
    assert c["overall"]["ci95"][0] < 2 / 3 < c["overall"]["ci95"][1]
    assert c["by"]["sector"]["Tech"]["decided"] == 2 and c["by"]["coverage"]["40/40"]["hits"] == 1
    assert o["variants"]["contrarian"]["overall"]["hits"] == 0
    assert o["claim_allowed"] is False and not o["minimum_sample_met_both"]


def test_flat_move_is_not_a_hit_and_simulation_is_refused():
    assert A.breakdown([r("continuation", "BUY", 0.3)])["variants"]["continuation"]["overall"]["hits"] == 0
    with pytest.raises(ValueError):
        A.breakdown([dict(r("continuation", "BUY", 3.0), mode="SIMULATION")])


def test_minimum_sample_needs_100_decided_over_8_weeks():
    from datetime import date, timedelta
    d0 = date(2026, 11, 2)
    many = [r(v, "BUY", 2.0, d=(d0 + timedelta(days=7 * (i % 8))).isoformat()) for v in ("continuation", "contrarian") for i in range(100)]
    o = A.breakdown(many)
    assert o["minimum_sample_met_both"] and o["claim_allowed"] is False      # the flag only says the sample is large enough; no claim is made here
    few_weeks = [r("continuation", "BUY", 2.0) for _ in range(100)]
    assert not A.breakdown(few_weeks)["variants"]["continuation"]["minimum_sample_met"]
