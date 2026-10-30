"""Peak provider-demand model for the shadow pipeline: capture + calendar refresh + retries, against the 1,700/day planning ceiling and the shared hourly caps with the
existing warmer. Pure arithmetic, no request. The 1,700/day figure is a planning ceiling, never an authorization.

Facts used (from the code and notes, not measured on the VPS): warmer hourly cap 400 in live hours (04:00-20:00 ET, 16 h) and 6,000 off-hours; the warmer was observed
at about 406 calls/hour while the market is open, i.e. it can use the whole live-hour cap. The daily account limit is unlimited on the current plan, so the binding limits
are the 1,700 planning ceiling, the hourly caps, the 3-concurrent-request limit and 2 s spacing (max 1,800 requests/hour from one sequential worker).
"""
import math

LIVE_HOURS, OFF_HOURS = 16, 8
SPACING_MAX_PER_HOUR = 1800
CORE_PER_EVENT, FULL_EXTRA, SHARED_PER_DAY = 5, 13, 24


def capture_requests(core, full):
    core, full = min(core, 10 ** 6), min(full, core)
    return core * CORE_PER_EVENT + full * FULL_EXTRA + SHARED_PER_DAY


def calendar_requests(*, near_trading_days=3, far_trading_days=12, sides=2, pages=2, near_refreshes=1, far_refreshes=1):
    """UW calendar calls per day: one request per trading day and side (premarket/afterhours), up to `pages` pages each."""
    return near_trading_days * sides * pages * near_refreshes + far_trading_days * sides * pages * far_refreshes


def with_margin(n, margin):
    return math.ceil(round(n * (1 + margin), 6))          # rounding first: 1610 * 1.1 must not become 1771.0000000000002


def max_full_that_fits(core, calendar, margin, ceiling):
    """Largest number of full-capture events (0..30) so that (capture + calendar) * (1 + margin) stays within the ceiling; None if even 0 does not fit."""
    for full in range(min(30, core), -1, -1):
        if with_margin(capture_requests(core, full) + calendar, margin) <= ceiling:
            return full
    return None


def hourly_capacity(warmer_live_per_hour, warmer_off_per_hour=1000, *, live_cap=400, off_cap=6000, live_reserve=300, off_reserve=1000):
    """Requests per day the shadow worker may send: the shared hourly caps minus the warmer (never below the reservation), limited by 2 s spacing."""
    live = max(0, live_cap - max(warmer_live_per_hour, live_reserve))
    off = max(0, off_cap - max(warmer_off_per_hour, off_reserve))
    return {"live_per_hour": live, "off_per_hour": off, "daily_capacity": LIVE_HOURS * min(live, SPACING_MAX_PER_HOUR) + OFF_HOURS * min(off, SPACING_MAX_PER_HOUR),
            "night_only_capacity": OFF_HOURS * min(off, SPACING_MAX_PER_HOUR)}


def scenarios(core=220, full=30, margin=0.10, ceiling=1700):
    cap = capture_requests(core, full)
    out = {"capture_core220_full30": cap, "ceiling": ceiling, "retry_margin": margin, "options": {}}
    for name, cal in (("calendar_UW_once_daily_flag_only", calendar_requests()),
                      ("calendar_UW_near_events_every_6h", calendar_requests(near_refreshes=4)),
                      ("calendar_via_Finviz_Finnhub_only_no_UW", 0)):
        total = cap + cal
        wm = with_margin(total, margin)
        out["options"][name] = {"calendar_uw_requests": cal, "total_without_retries": total, "total_with_retry_margin": wm, "fits_ceiling_without_retries": total <= ceiling,
                                "fits_ceiling_with_margin": wm <= ceiling, "over_ceiling_with_margin": max(0, wm - ceiling), "max_full_events_that_fit_with_margin": max_full_that_fits(core, cal, margin, ceiling)}
    need = out["options"]["calendar_UW_once_daily_flag_only"]["total_with_retry_margin"]
    out["hourly"] = {}
    for w in (0, 300, 400):
        h = hourly_capacity(w)
        h["hours_of_night_capture_needed"] = round(need / SPACING_MAX_PER_HOUR, 2)
        h["fits_in_night_only"] = need <= h["night_only_capacity"]
        h["fits_in_day_capacity"] = need <= h["daily_capacity"]
        out["hourly"]["warmer_live_%d_per_hour" % w] = h
    return out
