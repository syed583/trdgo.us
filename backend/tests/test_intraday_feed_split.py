"""The shared feed refreshes each KIND of data on its own schedule and stamps every signal with the age
of ITS data. Everything is mocked: no API calls, no production database."""
import pytest

import intraday_feed as feed
import swr
import tab_settings as cfg

T0 = 1_760_000_000.0
LIVE_POS = cfg.INTRADAY_FRESHNESS["options_positioning"][0]            # 1800 s
LIVE_OI = cfg.INTRADAY_FRESHNESS["daily_oi"][0]                        # 6 h
FRAC = cfg.INTRADAY_FEED["slow_refresh_fraction"]
SLOW_UP = cfg.INTRADAY_FEED["slow_upstream_cache_s"]


class Calls:
    def __init__(self):
        self.pos = self.iv = self.oi = self.overview = 0
        self.overview_kwargs = []
        self.dp_ok = True
        self.disparity_overview = []


@pytest.fixture
def stub(monkeypatch):
    import directional_score_service as dss
    import disparity_service
    import live_market_service as market
    import live_options_analytics as options
    import live_options_service as svc
    import uw_flow_service as odflow
    c = Calls()

    def overview(sym, **kw):
        c.overview += 1
        c.overview_kwargs.append(kw)
        return {"marker": "overview"}

    def dealer(sym):
        c.pos += 1
        return {"status": "OK" if c.dp_ok else "NO_DATA", "net_gex": 1.0}

    def iv(sym):
        c.iv += 1
        return {"status": "OK", "iv_rank": 40.0}

    def oi(sym):
        c.oi += 1
        return {"rows": [1]}

    monkeypatch.setattr(options, "get_overview", overview)
    monkeypatch.setattr(options, "get_dealer_positioning", dealer)
    monkeypatch.setattr(svc, "get_iv_history", iv)
    monkeypatch.setattr(odflow, "daily_oi_change", oi)
    monkeypatch.setattr(disparity_service, "get_disparity",
                        lambda s, **k: c.disparity_overview.append(k.get("overview")) or {})
    monkeypatch.setattr(market, "_fallback_bars", lambda s, d: ([], None))
    full = {"signals": [{"name": n, "bias": 0.4, "available": True} for n in feed.FAST_SIGNALS]}
    monkeypatch.setattr(dss, "build_signals", lambda data: [])
    monkeypatch.setattr(dss, "score_direction", lambda sigs: full)
    swr._values.pop(feed.SLOW_KEY.format("AAA"), None)
    return c


def test_every_signal_belongs_to_a_group_with_its_own_freshness_type():
    groups = {n: feed.group_of(n) for n in feed.FAST_SIGNALS}
    assert groups["options_flow"] == groups["unusual_activity"] == groups["volume_pcr"] == "flow"
    assert groups["ema_trend"] == groups["rsi"] == groups["price_action"] == "price"
    assert groups["key_levels"] == groups["implied_volatility"] == groups["disparity"] == "positioning"
    assert groups["daily_oi_change"] == "daily_oi"


def test_the_first_cycle_fetches_everything_and_hands_the_slow_parts_to_the_overview(stub):
    got = feed.compute_fast("AAA", now=T0, phase="rth")
    assert (stub.pos, stub.iv, stub.oi, stub.overview) == (1, 1, 1, 1)
    kw = stub.overview_kwargs[0]
    assert kw["iv_history"]["iv_rank"] == 40.0 and kw["positioning"]["net_gex"] == 1.0   # not fetched a second time
    assert stub.disparity_overview == [{"marker": "overview"}]                            # disparity reuses it too
    assert set(got["signals"]) == set(feed.FAST_SIGNALS)


def test_later_cycles_refetch_flow_but_keep_the_slow_parts_and_their_age(stub):
    first = feed.compute_fast("AAA", now=T0, phase="rth")
    second = feed.compute_fast("AAA", now=T0 + 420, phase="rth")
    assert (stub.pos, stub.iv, stub.oi) == (1, 1, 1)                      # slow groups NOT refetched
    assert stub.overview == 2                                             # flow / price are
    assert second["group_ts"]["positioning"] == first["group_ts"]["positioning"]   # keeps the OLD timestamp
    assert second["group_ts"]["daily_oi"] == first["group_ts"]["daily_oi"] == T0 - SLOW_UP
    up = cfg.INTRADAY_FEED["flow_upstream_cache_s"]
    assert second["group_ts"]["flow"] == T0 + 420 - up == second["group_ts"]["price"]


def test_positioning_is_refetched_when_it_nears_its_own_limit_and_daily_oi_much_later(stub):
    feed.compute_fast("AAA", now=T0, phase="rth")
    feed.compute_fast("AAA", now=T0 + FRAC * LIVE_POS - 5, phase="rth")
    assert (stub.pos, stub.oi) == (1, 1)
    again = feed.compute_fast("AAA", now=T0 + FRAC * LIVE_POS + 5, phase="rth")
    assert (stub.pos, stub.iv, stub.oi) == (2, 2, 1)
    assert again["group_ts"]["positioning"] == T0 + FRAC * LIVE_POS + 5 - SLOW_UP
    feed.compute_fast("AAA", now=T0 + FRAC * LIVE_OI + 5, phase="rth")
    assert stub.oi == 2


def test_a_failed_slow_fetch_keeps_the_old_copy_and_its_old_age(stub):
    feed.compute_fast("AAA", now=T0, phase="rth")
    stub.dp_ok = False
    later = feed.compute_fast("AAA", now=T0 + 2000, phase="rth")
    assert stub.pos == 2                                                  # it tried
    assert later["group_ts"]["positioning"] == T0 - SLOW_UP               # not made to look fresh


def test_with_the_split_off_everything_is_refetched_every_cycle(stub, monkeypatch):
    monkeypatch.setitem(cfg.INTRADAY_FEED, "split", False)
    feed.compute_fast("AAA", now=T0, phase="rth")
    feed.compute_fast("AAA", now=T0 + 60, phase="rth")
    assert (stub.pos, stub.oi) == (2, 2)
    assert stub.overview_kwargs[0] == {"iv_history": None, "positioning": None}


def test_a_stale_iv_cache_is_dropped_so_old_iv_cannot_pass_as_fresh(stub, monkeypatch):
    import live_market_service as market
    dropped, ages = [], iter([2000.0, None, None])
    monkeypatch.setattr(market.cache, "age", lambda k: next(ages, None))
    monkeypatch.setattr(market.cache, "drop", lambda k: dropped.append(k))
    feed.compute_fast("AAA", now=T0, phase="rth")
    assert dropped == ["ivhist:AAA"]


# ----------------------------------------------- per-signal freshness on the boards ---
def stored(group_ts, ts=T0, bias=0.5):
    swr._values[feed.KEY.format("AAA")] = (ts, {"symbol": "AAA", "ts": ts, "group_ts": group_ts,
                                                 "signals": {n: bias for n in feed.FAST_SIGNALS}})


def test_each_signal_is_judged_by_the_age_of_its_own_data():
    # flow & price are 30 s old, positioning 40 minutes (past its 30-minute "live" mark, inside 60), OI 2 h
    stored({"flow": T0 - 30, "price": T0 - 30, "positioning": T0 - 2400, "daily_oi": T0 - 7200})
    out = feed.effective_inputs("AAA", None, None, "rth", now=T0)
    assert out["meta"]["options_flow"]["status"] == "LIVE" and out["meta"]["options_flow"]["age_s"] == 30
    assert out["meta"]["key_levels"]["status"] == "DELAYED" and out["meta"]["key_levels"]["age_s"] == 2400
    assert out["meta"]["daily_oi_change"]["status"] == "LIVE" and out["missing_critical"] == []


def test_stale_slow_data_is_dropped_but_never_blocks_or_fakes_the_critical_groups():
    stored({"flow": T0 - 30, "price": T0 - 30, "positioning": T0 - 5000, "daily_oi": None})
    out = feed.effective_inputs("AAA", None, None, "rth", now=T0)
    for n in ("key_levels", "implied_volatility", "expected_move", "oi_positioning", "disparity"):
        assert n not in out["signals"] and n in out["dropped"]            # removed: the model sees it as missing
    assert "daily_oi_change" not in out["signals"] and "daily_oi_change" not in out["meta"]   # no timestamp: unusable
    assert "options_flow" in out["signals"] and "ema_trend" in out["signals"]
    assert out["missing_critical"] == []


@pytest.mark.parametrize("group, critical", [("flow", "options flow"),
                                             ("price", "price action (daily bars + live bar)")])
def test_stale_critical_data_is_never_used_whatever_the_slow_groups_say(group, critical):
    ts = {"flow": T0 - 30, "price": T0 - 30, "positioning": T0 - 30, "daily_oi": T0 - 30}
    ts[group] = T0 - 5000                                                  # past the 20 / 30 minute maximum
    stored(ts)
    out = feed.effective_inputs("AAA", None, None, "rth", now=T0)
    assert critical in out["missing_critical"]


def test_a_record_from_before_the_split_is_still_read_with_its_single_timestamp():
    swr._values[feed.KEY.format("AAA")] = (T0, {"symbol": "AAA", "ts": T0 - 100,
                                                 "signals": {n: 0.3 for n in feed.FAST_SIGNALS}})
    out = feed.effective_inputs("AAA", None, None, "rth", now=T0)
    assert out["meta"]["options_flow"]["age_s"] == 100 and out["meta"]["key_levels"]["age_s"] == 100
