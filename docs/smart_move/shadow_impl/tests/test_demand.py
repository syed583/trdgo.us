from smvshadow import demand as dm


def test_capture_formula_matches_the_earlier_count():
    assert dm.capture_requests(220, 30) == 1514 and dm.capture_requests(115, 30) == 989


def test_calendar_cost_is_per_day_and_side():
    assert dm.calendar_requests() == 3 * 2 * 2 + 12 * 2 * 2 == 60
    assert dm.calendar_requests(near_refreshes=4) == 48 + 48 == 96


def test_scenarios_and_ceiling():
    s = dm.scenarios()
    a, b, c = (s["options"][k] for k in ("calendar_UW_once_daily_flag_only", "calendar_UW_near_events_every_6h", "calendar_via_Finviz_Finnhub_only_no_UW"))
    assert (a["total_without_retries"], a["total_with_retry_margin"]) == (1574, 1732) and a["fits_ceiling_without_retries"] and not a["fits_ceiling_with_margin"]
    assert (b["total_without_retries"], b["total_with_retry_margin"]) == (1610, 1771) and b["over_ceiling_with_margin"] == 71
    assert (c["total_without_retries"], c["total_with_retry_margin"]) == (1514, 1666) and c["fits_ceiling_with_margin"]
    assert a["max_full_events_that_fit_with_margin"] == 27 and c["max_full_events_that_fit_with_margin"] == 30
    for o in s["options"].values():
        if o["max_full_events_that_fit_with_margin"] is not None:
            assert dm.with_margin(dm.capture_requests(220, o["max_full_events_that_fit_with_margin"]) + o["calendar_uw_requests"], 0.10) <= 1700


def test_warmer_at_its_whole_cap_leaves_night_capacity_only():
    s = dm.scenarios()["hourly"]
    assert s["warmer_live_400_per_hour"]["live_per_hour"] == 0 and s["warmer_live_400_per_hour"]["night_only_capacity"] == 8 * 1800
    assert s["warmer_live_400_per_hour"]["fits_in_night_only"] and s["warmer_live_0_per_hour"]["live_per_hour"] == 100
    assert s["warmer_live_300_per_hour"]["daily_capacity"] == 16 * 100 + 8 * 1800
