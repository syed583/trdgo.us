from datetime import date, timedelta

import uw_earnings_calendar as c


def test_ttl_by_date():
    today = date.today()
    assert c.ttl_for((today - timedelta(days=1)).isoformat()) == c.TTL_SETTLED
    assert c.ttl_for(today.isoformat()) == c.TTL_LIVE == 900.0
    assert c.ttl_for((today + timedelta(days=5)).isoformat()) == c.TTL_FUTURE == 6 * 3600.0
