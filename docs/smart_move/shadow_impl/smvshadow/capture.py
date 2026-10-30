"""Capture orchestration for one scheduled event (components 3, 4, 5, 6 working together). `fetch` is injected: in tests a fake provider, never the network.

Per core event: ohlc (symbol), earnings history, volatility context, iv-rank = 4 requests; the shared daily candles (SPY, sector ETF) are fetched once per day and cached.
Full-tier events add capture-only requests (F1, F2, F4, F6 payloads stored raw, never scored). Every request goes through the governor; a denied or failed request produces
a MISSING snapshot with a reason code, never a default value. Raw bytes are stored in the hashed payload store BEFORE any metadata row is written.
"""
import hashlib
import json
from datetime import timezone

from . import adapters as ad
from . import cutoff as co
from .freshness import FactorEvidence, missing

CORE_ENDPOINTS = ("ohlc", "earnings", "vol_context", "iv_rank")
FULL_EXTRA_ENDPOINTS = ("flow_alerts", "net_prem_ticks", "greek_flow", "options_pulse", "unusual", "darkpool_levels", "darkpool_window", "ownership", "estimates", "analysts", "headlines",
                        "oi_change", "stock_info")        # 13 capture-only requests per full event
FACTOR_OF_EXTRA = {"flow_alerts": 1, "net_prem_ticks": 1, "greek_flow": 1, "options_pulse": 1, "unusual": 1, "oi_change": 1, "darkpool_levels": 2, "darkpool_window": 2, "ownership": 2,
                   "estimates": 4, "analysts": 4, "headlines": 6, "stock_info": 8}


def params_hash(endpoint, params):
    return hashlib.sha256(json.dumps([endpoint, params], sort_keys=True).encode("ascii")).hexdigest()


class Capturer:
    def __init__(self, governor, store, fetch, db=None, run_id=None):
        self.g, self.store, self.fetch, self.db, self.run_id = governor, store, fetch, db, run_id
        self.shared = {}
        self.payload_meta = []

    def _get(self, endpoint, symbol, params, priority, capture_ts):
        """-> (parsed json or None, sha or None, reason or None)."""
        res = self.g.execute(lambda: self.fetch(endpoint, symbol, params), purpose=endpoint, priority=priority, symbol=symbol)
        if res["status"] != "OK":
            return None, None, {"DENIED": "BUDGET_STOP", "FAILED": "HTTP_ERROR"}[res["status"]]
        raw = res["value"]
        sha, rel, created = self.store.put(raw)
        self.payload_meta.append({"sha": sha, "rel": rel, "endpoint": endpoint, "params_hash": params_hash(endpoint, params), "raw_len": len(raw), "symbol": symbol,
                                  "captured_at": capture_ts})
        try:
            return json.loads(raw.decode("utf-8")), sha, None
        except ValueError:
            return None, sha, "UNINFORMATIVE_FIELD"

    def _shared(self, endpoint, symbol, capture_ts):
        key = (endpoint, symbol, capture_ts.astimezone(co.ET).date())
        if key not in self.shared:
            self.shared[key] = self._get(endpoint, symbol, {"timeframe": "3Y"}, "core", capture_ts)
        return self.shared[key]

    def capture_event(self, a, plan, kind, capture_ts, sector_etf=None):
        """a: assignment dict from the scheduler. Returns dict factor -> FactorEvidence (scored factors) and extra payload evidences."""
        sym = a["symbol"]
        from datetime import date as _d
        edate = _d.fromisoformat(a["event_date"])
        ev = {}
        cand, sha_c, why = self._get("ohlc", sym, {"timeframe": "3Y"}, "core", capture_ts)
        hist, sha_h, why_h = self._get("earnings", sym, {"limit": 24}, "core", capture_ts)
        ctx, sha_x, why_x = self._get("vol_context", sym, {"date": co.last_completed_session_at(capture_ts).isoformat()}, "core", capture_ts)
        ivr, sha_i, why_i = self._get("iv_rank", sym, {"date": co.last_completed_session_at(capture_ts).isoformat()}, "core", capture_ts)
        spy, sha_s, why_s = self._shared("ohlc", "SPY", capture_ts)
        sec, sha_e, why_e = self._shared("ohlc", sector_etf, capture_ts) if sector_etf else (None, None, "NO_SOURCE")
        rows = lambda o: (o or {}).get("data") if isinstance(o, dict) else None
        hist_rows = rows(hist)
        react_sessions = set()
        for r in hist_rows or []:
            k = ad._kind(r.get("report_time"))
            try:
                if k:
                    react_sessions.add(co.reaction_session(_d.fromisoformat(str(r["report_date"])[:10]), k))
            except (KeyError, ValueError):
                pass
        ev[5] = ad.f5_price_technical(rows(cand), rows(spy), plan, capture_ts, react_sessions, sha_c) if cand else missing(5, "uw.ohlc.1d", why or "NO_SOURCE", capture_ts)
        ev[3] = ad.f3_reaction_history(hist_rows, rows(cand), edate, plan, capture_ts, sha_h) if (hist and cand) else missing(3, "uw.earnings+ohlc", why_h or why or "NO_SOURCE", capture_ts)
        ev[7] = ad.f7_volatility_context(ctx, rows(ivr), plan, capture_ts, sha_x) if ctx else missing(7, "uw.volatility.context", why_x or "NO_SOURCE", capture_ts)
        ev[8] = ad.f8_sector_market(rows(spy), rows(sec), plan, capture_ts, sha_s) if spy else missing(8, "uw.ohlc.1d:SPY", why_s or "NO_SOURCE", capture_ts)
        extras = []
        if a["tier"] == "FULL":
            for ep in FULL_EXTRA_ENDPOINTS:
                obj, sha, why_x2 = self._get(ep, sym, {}, "full_extra", capture_ts)
                f = FACTOR_OF_EXTRA[ep]
                extras.append(FactorEvidence(f, "uw." + ep, "OK", None, {"captured_only": True}, None, capture_ts, None, "FETCH_TIME_ONLY", "UNSTAMPED", sha) if sha and not why_x2
                              else missing(f, "uw." + ep, why_x2 or "BUDGET_STOP", capture_ts))
        return ev, extras
