"""Synthetic fixtures for the local tests: candles with controlled reactions, earnings history, volatility context, and a fake provider. No real data, no network."""
import hashlib
import json
import random
from datetime import date, datetime, timedelta, timezone

from smvshadow import cutoff as co

UTC = timezone.utc
MC = co._mc()


def trading_days(start, end):
    d, out = start, []
    while d <= end:
        if MC.is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def weekday_on_or_before(d):
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def make_history(target, kind, pcts):
    """Quarterly events before `target` (most recent first in `pcts`): list of provider-style rows plus {reaction_session: pct}."""
    rows, react = [], {}
    label = "premarket" if kind == "BMO" else "postmarket"
    for i, pct in enumerate(pcts, 1):
        d = weekday_on_or_before(target - timedelta(days=91 * i))
        while not MC.is_trading_day(d):
            d -= timedelta(days=1)
        rows.append({"report_date": d.isoformat(), "report_time": label, "post_earnings_move_1d": "9.99", "street_mean_est": "1.0"})   # provider fields must be ignored
        react[co.reaction_session(d, kind)] = pct
    return rows, react


def make_series(symbol, start, end, reactions=None, jumps=None, drift=0.0004, base=100.0, vol=2_000_000, trend_from=None, trend_pct_per_day=0.0):
    """date -> close. Ordinary days follow a seeded walk; reaction sessions get exactly the given percent; `jumps` {date: pct} are non-earnings moves."""
    rnd = random.Random(int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16))
    reactions, jumps = reactions or {}, jumps or {}
    close, out = base, {}
    for d in trading_days(start, end):
        if d in reactions:
            r = reactions[d] / 100.0
        elif d in jumps:
            r = jumps[d] / 100.0
        else:
            r = drift + rnd.uniform(-0.002, 0.002)
            if trend_from and d >= trend_from:
                r += trend_pct_per_day / 100.0
        close *= 1 + r
        out[d] = round(close, 4)
    return out


def rows_from(ser, vol=2_000_000, extra_after=None):
    """provider-style daily rows: regular 'r' rows plus noisy pre/post-market rows that must be ignored."""
    rows = []
    for d, c in sorted(ser.items()):
        rows.append({"date": d.isoformat(), "market_time": "pr", "open": c * 0.9, "high": c * 0.9, "low": c * 0.9, "close": c * 0.9, "volume": 10})
        rows.append({"date": d.isoformat(), "market_time": "r", "open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": vol})
        rows.append({"date": d.isoformat(), "market_time": "po", "open": c * 1.1, "high": c * 1.1, "low": c * 1.1, "close": c * 1.1, "volume": 10})
    return rows


def context_payload(expected_session, label="flat", iv_rank=30.0):
    return {"data": {"date": expected_session.isoformat(), "ticker": "X", "skew": {"label": label}, "iv_rank": iv_rank, "iv_percentile": 40.0,
                     "expiry": {"implied_move_perc": 0.05, "earnings_in_expiry": True}}}


def iv_rank_payload(expected_session, stamp_utc):
    return {"data": [{"date": expected_session.isoformat(), "iv_rank_1y": 30.0, "updated_at": stamp_utc.isoformat().replace("+00:00", "Z")}]}


class FakeProvider:
    """fetch(endpoint, symbol, params) -> bytes. Records every call so tests can count requests."""

    def __init__(self, worlds, now_fn):
        self.worlds, self.now_fn, self.calls = worlds, now_fn, []

    def __call__(self, endpoint, symbol, params):
        self.calls.append((endpoint, symbol))
        w = self.worlds.get(symbol)
        if w is None:
            w = self.worlds["__default__"]
        now = self.now_fn()
        exp = co.last_completed_session_at(now)
        if endpoint == "ohlc":
            body = {"data": w["rows"] if symbol not in ("SPY",) else w["rows"]}
        elif endpoint == "earnings":
            body = {"data": w["history"]}
        elif endpoint == "vol_context":
            body = context_payload(exp, w.get("skew", "flat"))
        elif endpoint == "iv_rank":
            body = iv_rank_payload(exp, datetime.combine(exp, datetime.min.time(), UTC).replace(hour=22, minute=35))
        else:
            body = {"data": [], "note": "capture-only payload for " + endpoint}
        return json.dumps(body).encode("utf-8")
