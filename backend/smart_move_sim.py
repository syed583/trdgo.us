"""A SIMULATED provider for the Smart Move refresh service: invented companies and prices in the real vendor response formats. It exists so the whole live path (calendar refresh,
governed evidence capture, current scoring, feed status) can be exercised and shown without a single real request. Everything it produces is labelled SIMULATION by the store,
the API and the page; nothing here is market data."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from smart_move_live import Fetcher
from smart_move_routes import ENGINE_DIR  # noqa: F401  (puts the engine on sys.path)
import sys

sys.path.insert(0, str(ENGINE_DIR / "tests"))
from fixtures import context_payload, iv_rank_payload, make_history, make_series, rows_from  # noqa: E402
from smvshadow import cutoff as co  # noqa: E402

UTC = timezone.utc
SECTORS = ["Technology", "Healthcare", "Industrials", "Financial Services", "Consumer Defensive", "Energy"]
HIST = [[5, 4, 6, 5, 4, 5, 6, 5], [-5, -4, -6, -5, -4, -5, -6, -5], [5, -5, 4, -4, 5, -5, 4, -4], [0.3, -0.2, 0.4, -0.3, 0.2, -0.4, 0.3, -0.2]]
TREND = [0.35, -0.35, 0.0]
SKEW = ["call_rich", "put_rich", "flat"]


class SimulatedProvider(Fetcher):
    mode = "SIMULATION"

    def __init__(self, clock, n=16):
        self.clock = clock
        self.n = n
        self.calls = []
        self.slots = self._slots(clock.now())
        self.syms = {}
        for i in range(n):
            d, kind = self.slots[i % len(self.slots)]
            s = "SIM%02d" % i
            self.syms[s] = {"date": d, "kind": kind, "sector": SECTORS[i % len(SECTORS)], "marketcap": 5e11 - i * 1e10, "hist": HIST[(i * 5) % 4], "trend": TREND[(i * 7) % 3], "skew": SKEW[(i * 11) % 3]}

    @staticmethod
    def _slots(now):
        mc = co._mc()
        out = []
        for k in range(0, 10):
            d = now.astimezone(co.ET).date() + timedelta(days=k)
            if not mc.is_trading_day(d):
                continue
            for kind in ("BMO", "AMC"):
                release, _ = co.assumed_release(d, kind)
                if timedelta(hours=25) <= release - now <= timedelta(hours=46):
                    out.append((d, kind))
        return out or [(now.astimezone(co.ET).date() + timedelta(days=2), "AMC")]

    def fetch(self, kind, symbol, params):
        self.calls.append((kind, symbol))
        now = self.clock.now()
        exp = co.last_completed_session_at(now)
        if kind == "uw_calendar":
            rows = []
            for s, m in self.syms.items():
                if str(m["date"]) == params["date"] and ("premarket" if m["kind"] == "BMO" else "afterhours") == params["side"] and params.get("page", 0) == 0:
                    rows.append({"symbol": s, "source": "company", "country_code": "US", "full_name": "SIMULATED " + s, "sector": m["sector"], "is_s_p_500": False, "has_options": True, "marketcap": str(int(m["marketcap"])),
                                 "report_date": str(m["date"]), "report_time": "premarket" if m["kind"] == "BMO" else "postmarket", "street_mean_est": None, "actual_eps": None})
            return json.dumps({"data": rows}).encode()
        if kind == "finviz_calendar":
            lines = ['"Ticker","Earnings Date"']
            for s in params["tickers"]:
                m = self.syms.get(s)
                if m:
                    lines.append('"%s",%d/%d/%d %s' % (s, m["date"].month, m["date"].day, m["date"].year, "8:30:00 AM" if m["kind"] == "BMO" else "4:30:00 PM"))
            return ("\n".join(lines) + "\n").encode()
        if kind == "finnhub_calendar":
            lo, hi = date.fromisoformat(params["from"]), date.fromisoformat(params["to"])
            rows = [{"symbol": s, "date": str(m["date"]), "hour": "bmo" if m["kind"] == "BMO" else "amc", "quarter": 3, "year": 2026, "epsEstimate": 1.0, "epsActual": None} for s, m in self.syms.items() if lo <= m["date"] <= hi]
            return json.dumps({"earningsCalendar": rows}).encode()
        if kind == "ohlc":
            m = self.syms.get(symbol)
            start = exp - timedelta(days=1095)
            if m:
                _, react = make_history(m["date"], m["kind"], m["hist"])
                ser = make_series(symbol, start, exp, reactions=react, trend_from=exp - timedelta(days=30), trend_pct_per_day=m["trend"])
            else:
                ser = make_series(symbol, start, exp, trend_from=exp - timedelta(days=30), trend_pct_per_day=0.1 if symbol == "SPY" else 0.0)
            return json.dumps({"data": rows_from(ser)}).encode()
        if kind == "earnings":
            m = self.syms[symbol]
            rows, _ = make_history(m["date"], m["kind"], m["hist"])
            return json.dumps({"data": rows}).encode()
        if kind == "vol_context":
            payload = context_payload(exp, self.syms[symbol]["skew"])
            payload["data"]["date"] = params["date"]
            return json.dumps(payload).encode()
        if kind == "iv_rank":
            stamp = datetime.combine(exp, datetime.min.time(), UTC).replace(hour=22, minute=30)
            return json.dumps(iv_rank_payload(exp, stamp)).encode()
        raise ValueError(kind)
