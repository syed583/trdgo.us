"""
The category breakdown has to add up to the score it summarises.

The panel is headed "100 pts". When four parameters were added to the model
and this table was not updated, the column quietly described 83 points of a
100-point score and said nothing about the rest. These tests make that a
failing build rather than something a reader has to notice.
"""

import analysis_stream_service as stream
import directional_model as dm


def _every_parameter():
    """
    Every parameter any outlook puts on the screen.

    That is wider than what it scores: the session readings are computed at
    zero weight and shown as context, so they need a category to live in
    even though they add nothing to the hundred.
    """
    import horizon_model as hm

    names = set(dm.WEIGHTS) | set(hm.LABELS)
    for weights in hm.WEIGHTS.values():
        names |= set(weights)
    return names


def test_every_weighted_parameter_is_in_exactly_one_category():
    grouped = [p for group in stream.CATEGORIES for p in group["params"]]
    assert len(grouped) == len(set(grouped)), "a parameter is in two groups"

    missing = _every_parameter() - set(grouped)
    unknown = set(grouped) - _every_parameter()
    assert not missing, f"not shown in any category: {sorted(missing)}"
    assert not unknown, f"category names a parameter no outlook has: {sorted(unknown)}"


def test_the_categories_sum_to_a_hundred_for_every_outlook():
    """
    Each outlook weighs its own parameters to a hundred, and the breakdown has
    to cover all of them -- otherwise the panel's "100 pts" heading describes
    a score the column does not add up to.
    """
    import horizon_model as hm

    grouped = {p for group in stream.CATEGORIES for p in group["params"]}
    for name, weights in [("SWING", dm.WEIGHTS)] + list(hm.WEIGHTS.items()):
        covered = sum(w for param, w in weights.items() if param in grouped)
        assert covered == sum(weights.values()) == 100, name


def test_each_category_reports_what_it_is_worth():
    signals = [{"name": name, "weight": weight, "available": True, "bias": 0.5,
                "directional": True}
               for name, weight in dm.WEIGHTS.items()]
    rows = stream.categorise(signals)

    assert sum(r["weight_possible"] for r in rows) == 100
    for row in rows:
        expected = sum(dm.WEIGHTS.get(p, 0) for p in
                       next(g["params"] for g in stream.CATEGORIES
                            if g["key"] == row["key"]))
        assert row["weight_possible"] == expected, row["key"]


def test_a_group_with_nothing_measured_says_so_rather_than_scoring_zero():
    signals = [{"name": name, "weight": weight, "available": False,
                "bias": None, "directional": True}
               for name, weight in dm.WEIGHTS.items()]
    rows = stream.categorise(signals)
    assert rows and all(r["available"] is False for r in rows)
    assert all(r["score"] is None for r in rows)
    # The weight is still reported: the points exist, they just went unmeasured.
    assert sum(r["weight_possible"] for r in rows) == 100
