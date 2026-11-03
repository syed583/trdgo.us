"""Production adapters. READ paths use only in-memory / database caches (swr.peek never builds or refreshes anything), so they cannot start a provider request. The provider paths (calendar refresh with a second
vendor, event-evidence refresh, outcome verification) are reached only through ProviderAccess, which needs the owner's approval for each purpose. Modules of the live app are imported lazily, so importing this
file loads no provider code.

What the eight "model" sources really are (read from their code): formula models. None of them calls an external AI service; each scores `earn:equity:{SYM}` and `earn:trade:default:{SYM}`, which are built by
earnings_equity_service.get_analysis and earnings_trade_service.get_summary from Unusual Whales (and, for a few inputs, Finnhub) data. So a genuine stored prediction exists whenever those caches are warm, and
what needs separate provider permission is refreshing that DATA, never running a model."""
from __future__ import annotations

import time
from datetime import date, datetime, timezone
from typing import Callable, Optional

from .service import Adapters, HORIZON_DAYS
from .smart_move import SmartMoveSource

UTC = timezone.utc
MODEL_KEYS = {"claude": "v3:scan:v2:{d}", "claude_pro": "claudepro:scan:{d}", "gemini": "gemini:scan:{d}", "grok": "grok:scan:{d}", "chatgpt": "chatgpt:scan:{d}", "meta": "meta:scan:{d}",
              "kimi": "kimi:scan:{d}", "ped": "ped:scan:{d}"}
STORED_ID = {"claude": "claude_new"}                       # the daily snapshot table keeps Claude under its older id
LABELS = {"claude": "Claude", "claude_pro": "Claude Pro", "gemini": "Gemini", "grok": "Grok", "chatgpt": "ChatGPT", "meta": "Meta", "kimi": "Kimi", "ped": "Pre-Earnings Direction",
          "smart_move": "Smart Move score40-v1"}
MODEL_META = {
    "claude": {"module": "claude_last_service", "what": "v3 two-sided pre-earnings direction model", "extra_inputs": []},
    "claude_pro": {"module": "claude_pro_service", "what": "six-block refined direction model", "extra_inputs": []},
    "gemini": {"module": "gemini_service", "what": "Anas Engine v2, 11 parameters", "extra_inputs": []},
    "grok": {"module": "grok_service", "what": "9 research-strength factors", "extra_inputs": ["Finnhub analyst dispersion (detail page only)"]},
    "chatgpt": {"module": "chatgpt_service", "what": "16-feature direction model V1", "extra_inputs": ["Finnhub analyst dispersion (detail page only)"]},
    "meta": {"module": "meta_service", "what": "11-factor / 100-point direction model", "extra_inputs": []},
    "kimi": {"module": "kimi_service", "what": "12-factor direction model", "extra_inputs": ["Finnhub price-target changes (detail page only)", "SPY trend + VIX once per scan"]},
    "ped": {"module": "pre_earnings_direction_service", "what": "v2 nine-parameter direction model", "extra_inputs": []},
}
SHARED_INPUTS = ["earn:equity:{SYM}  <- earnings_equity_service.get_analysis (Unusual Whales, some Finnhub)", "earn:trade:default:{SYM}  <- earnings_trade_service.get_summary (Unusual Whales)"]


def _session(row: dict) -> str:
    raw = str(row.get("reporting_time") or row.get("report_timing") or row.get("time_label") or row.get("timing_label") or "").lower()
    if raw in ("bmo",) or "before" in raw or "pre" in raw:
        return "BMO"
    if raw in ("amc",) or "after" in raw or "post" in raw:
        return "AMC"
    return "UNKNOWN"


def _iso(epoch: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(epoch, UTC).isoformat() if epoch else None


def _db_session():
    from database import SessionLocal
    return SessionLocal()


class ProdAdapters(Adapters):
    model_labels = LABELS

    def __init__(self, days: int = HORIZON_DAYS, calendar_days: int = 30, smart_move: Optional[SmartMoveSource] = None, session_factory: Optional[Callable] = None, clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.days, self.calendar_days, self.smart = days, calendar_days, smart_move if smart_move is not None else SmartMoveSource.from_env()
        self._session, self.clock = session_factory or _db_session, clock
        self._moves: dict = {}                                    # symbol -> {report date: move_pct} from the last verify_outcome in this process

    # ------------------------------------------------------------ cache reads
    def _scans(self) -> dict:
        import swr
        out = {}
        for m, tmpl in MODEL_KEYS.items():
            key = tmpl.format(d=self.days)
            res = swr.peek(key)                                   # cache read only: never builds, never refreshes
            if isinstance(res, dict):
                out[m] = (res, swr.held_at(key))
        return out

    def _stored_snapshots(self) -> dict:
        """The latest stored daily snapshot per model / symbol for events still ahead (the existing capture loop writes them). A fallback for a cold cache, labelled as such."""
        try:
            from sqlalchemy import text
            db = self._session()
            try:
                rows = db.execute(text("SELECT model, symbol, earnings_date, signal, score, predicted_at FROM model_predictions WHERE earnings_date >= :d ORDER BY predicted_at"), {"d": date.today().isoformat()}).fetchall()
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            return {}
        back = {v: k for k, v in STORED_ID.items()}
        out: dict = {}
        for model, sym, ed, sig, score, at in rows:
            m = back.get(model, model)
            if m in LABELS and sig in ("BUY", "SELL", "NEUTRAL"):
                at_iso = at.isoformat() if hasattr(at, "isoformat") else str(at)
                if at_iso and "+" not in at_iso[10:] and not at_iso.endswith("Z"):
                    at_iso += "+00:00"
                out.setdefault(m, {})[str(sym).upper()] = {"event_date": str(ed)[:10], "signal": sig, "score": score, "coverage": None, "tier": None, "as_of": at_iso, "evidence_as_of": None, "source": "model_predictions_snapshot"}
        return out

    def models(self) -> dict:
        out = {}
        cached = self._scans()
        stored = None
        for m, (res, held) in cached.items():
            rows = {}
            for r in res.get("rows") or []:
                s = (r.get("symbol") or "").upper()
                if s:
                    cov = r.get("coverage")                       # the eight services report coverage as a PERCENT (0-100); store it as a fraction so 0.5 can never be misread
                    rows[s] = {"event_date": str(r.get("date") or "")[:10] or None, "signal": r.get("signal"), "score": r.get("score") if isinstance(r.get("score"), (int, float)) else None,
                               "coverage": round(cov / 100.0, 4) if isinstance(cov, (int, float)) and not isinstance(cov, bool) else None, "tier": r.get("tier"),
                               "as_of": _iso(held), "evidence_as_of": None, "source": "swr_cache"}
            out[m] = rows
        if len(cached) < len(MODEL_KEYS):                         # at least one model's cache is cold: fill ITS gaps from the stored daily snapshots
            stored = self._stored_snapshots()
            for m in MODEL_KEYS:
                if m not in cached:
                    out[m] = stored.get(m, {})
        out["smart_move"] = self.smart.models(self.clock()) if self.smart else {}
        return out

    def _uw_calendar_cache(self) -> list:
        """The 30-day calendar from Unusual Whales' per-date responses the application has already cached (uwcal:<path>:<date>). Only entries that exist and are not older than two future-TTLs are used; a date that was never
        fetched contributes nothing (a gap is a gap, never an invented event). Nothing is requested."""
        try:
            import uw_earnings_calendar as cal
            import unusualwhales_service as uw
            data = self._cache_snapshot()
        except Exception:  # noqa: BLE001
            return []
        horizon = self.clock().date()
        out, t_now = [], time.time()
        for off in range(0, self.calendar_days + 1):
            d = horizon.toordinal() + off
            when = date.fromordinal(d)
            if when.weekday() >= 5:
                continue
            w = when.isoformat()
            for path in ("/api/earnings/premarket", "/api/earnings/afterhours"):
                hit = data.get("uwcal:%s:%s" % (path, w))
                if not (isinstance(hit, tuple) and isinstance(hit[1], dict) and hit[1].get("status") == "OK") or t_now - hit[0] > 2 * getattr(cal, "TTL_FUTURE", 6 * 3600.0):
                    continue
                for r in uw._rows(hit[1]):
                    if cal._us_listed(r):
                        row = cal._row(r, w)
                        if cal.MIN_PRICE <= 0 or row.get("prior_close") is None or row["prior_close"] >= cal.MIN_PRICE:
                            out.append(row)
        return out

    def _finviz_map(self) -> dict:
        """Finviz's {SYMBOL: {date, time}} read ONCE per calendar read. Its cache lives in the shared database tier as well as in memory: reading it per symbol cost one database round trip and one JSON parse per event."""
        try:
            import swr
            m = swr.peek("fviz:earn")
            return m if isinstance(m, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _finviz_source(self, sym: str, fmap: Optional[dict] = None) -> list:
        fv = (fmap if fmap is not None else self._finviz_map()).get(sym)
        if not (fv and fv.get("date")):
            return []
        t = str(fv.get("time") or "").lower()
        return [{"vendor": "FINVIZ", "date": str(fv["date"])[:10], "session": "BMO" if "pre" in t or "bmo" in t else "AMC" if "after" in t or "amc" in t else "UNKNOWN"}]

    def calendar(self) -> list:
        seen = {}
        fmap = self._finviz_map()
        for r in self._uw_calendar_cache():
            s = (r.get("symbol") or "").upper()
            if s and s not in seen:                                # the earliest date of a symbol wins
                sess = r.get("reporting_time") or "UNKNOWN"
                seen[s] = {"symbol": s, "company": r.get("company"), "date": r["date"], "session": sess, "source": "uw_calendar_cache",
                           "sources": [{"vendor": "UW", "date": r["date"], "session": sess}] + self._finviz_source(s, fmap)}
        for m, (res, _) in self._scans().items():
            for r in res.get("rows") or []:
                s = (r.get("symbol") or "").upper()
                d = str(r.get("date") or "")[:10]
                if s and d and s not in seen:
                    seen[s] = {"symbol": s, "company": r.get("company"), "date": d, "session": _session(r), "source": "model_scan_cache"}
        return list(seen.values())

    def outcome(self, symbol: str, d: str) -> Optional[dict]:
        """The real post-earnings move: what verify_outcome fetched in this process, else what the existing scoreboard already graded. Both come from daily bars (prior close -> reaction close)."""
        mv = (self._moves.get(symbol) or {}).get(d)
        if mv is not None:
            ok = abs(mv) <= 100
            return {"move_pct": mv, "direction": "BUY" if mv > 0.5 else "SELL" if mv < -0.5 else "FLAT", "available_at": self.clock().isoformat(), "verified": ok, "source": "daily_bars"}
        try:
            from sqlalchemy import text
            db = self._session()
            try:
                r = db.execute(text("SELECT actual_move_pct, actual_direction, evaluated_at FROM model_predictions WHERE symbol=:s AND earnings_date=:d AND actual_move_pct IS NOT NULL "
                                    "ORDER BY evaluated_at DESC LIMIT 1"), {"s": symbol, "d": d}).fetchone()
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            return None
        if not r:
            return None
        return {"move_pct": float(r[0]), "direction": r[1], "available_at": r[2].isoformat() if hasattr(r[2], "isoformat") else str(r[2]), "verified": abs(float(r[0])) <= 100, "source": "scoreboard_graded"}

    def smart_move_plan(self, events: list, now: datetime) -> Optional[dict]:
        return self.smart.plan(events, now) if self.smart else None

    def smart_move_coverage(self) -> Optional[dict]:
        return self.smart.coverage() if self.smart and hasattr(self.smart, "coverage") else None

    def smart_move_windows(self) -> Optional[dict]:
        return self.smart.window_report() if self.smart and hasattr(self.smart, "window_report") else None

    def app_requests_today(self) -> Optional[int]:
        try:
            import unusualwhales_service as uw
            sp = uw._spent
            return int(sp["count"]) if sp.get("day") == uw._today() else 0
        except Exception:  # noqa: BLE001
            return None

    def smart_move_details(self, symbol: str, d: str) -> Optional[dict]:
        return self.smart.details(symbol, d) if self.smart and hasattr(self.smart, "details") else None

    # ------------------------------------------------------------ evidence (cache reads only)
    def _cache_snapshot(self) -> dict:
        """One shallow copy of the in-memory response cache, reused for 20 s: an evidence scan over hundreds of events must not copy a cache of thousands of entries per event. Retries if the app mutates it meanwhile."""
        t = time.time()
        if getattr(self, "_snap", None) is not None and t - self._snap[0] < 20:
            return self._snap[1]
        import live_market_service as lms
        for _ in range(5):
            try:
                data = dict(lms.cache._data)
                break
            except RuntimeError:                                  # dictionary changed size during iteration
                continue
        else:
            data = {}
        self._snap = (t, data)
        return data

    def _uw_cached(self, prefix: str) -> list:
        """Every cached Unusual Whales response whose key starts with prefix, freshest first. Reads the in-memory cache; a miss is a miss and never a request."""
        try:
            data = self._cache_snapshot()
        except Exception:  # noqa: BLE001
            return []
        hits = [(v[0], v[1]) for k, v in data.items() if k.startswith(prefix) and isinstance(v, tuple) and isinstance(v[1], dict) and v[1].get("status") == "OK"]
        return [p for _, p in sorted(hits, key=lambda x: -x[0])]

    def evidence(self, symbol: str, d: str) -> list:
        import hashlib
        sym = symbol.upper()
        items: list = []
        # 1. the scored inputs the formula models read: earn:equity:{SYM}.params, each with its own bias in -1..+1
        try:
            import swr
            from . import evidence as EV
            batch = getattr(self, "_eq_batch", None)
            eq = batch.get("earn:equity:%s" % sym) if batch is not None else swr.peek("earn:equity:%s" % sym)
            for p in (eq or {}).get("params") or []:
                name = str(p.get("name") or p.get("key") or "")
                kind = EV.kind_of_param(name)
                b = p.get("bias")
                if kind and isinstance(b, (int, float)) and not isinstance(b, bool):
                    items.append({"kind": kind, "provider": "earn:equity", "ext_id": name, "occurred_at": None, "title": p.get("label") or name, "summary": p.get("detail") or p.get("why"),
                                  "data": {"bias": round(float(b), 4), "weight": p.get("weight")}, "provider_structured": True})
        except Exception:  # noqa: BLE001
            pass
        # 2. headlines already cached for this symbol
        try:
            import unusualwhales_service as uw
            seen = set()
            for payload in self._uw_cached("uw:news:%s:" % sym):
                for r in uw._rows(payload)[:30]:
                    head = str(r.get("headline") or r.get("title") or "").strip()
                    if not head:
                        continue
                    ext = str(r.get("id") or hashlib.sha1((head + str(r.get("created_at"))).encode()).hexdigest()[:16])
                    if ext in seen:
                        continue
                    seen.add(ext)
                    low = head.lower()
                    guide = any(w in low for w in ("guidance", "outlook", "raises forecast", "lowers forecast", "cuts forecast", "preannounce", "pre-announce", "restat", "withdraws"))
                    items.append({"kind": "NEWS", "provider": "UW", "ext_id": ext, "occurred_at": str(r.get("created_at") or "") or None, "title": head[:300], "summary": None,
                                  "data": {"sentiment": r.get("sentiment"), "source": r.get("source"), "is_major": bool(r.get("is_major")), "guidance_keyword": guide}, "provider_structured": False})
            # 3. the options tape already cached for this symbol, summarised (a lean of -1..+1 plus the numbers behind it)
            import uw_flow_service as fs
            prints = []
            for payload in self._uw_cached("uw:flowrecent:%s:" % sym)[:1]:
                prints = [fs._print_row(r) for r in uw._rows(payload)]
            if prints:
                total = sum(p.get("premium") or 0 for p in prints)
                bull = sum(p.get("premium") or 0 for p in prints if p.get("sentiment") == "BULLISH")
                share = round(100 * bull / total, 1) if total else None
                items.append({"kind": "OPTIONS_FLOW", "provider": "UW", "ext_id": "flow-recent", "occurred_at": max((p.get("time") or "" for p in prints), default=None) or None,
                              "title": "Options tape summary", "summary": None,
                              "data": {"total_premium": round(total, 0), "bullish_share": share, "prints": len(prints), "sweeps": sum(1 for p in prints if p.get("kind") == "SWEEP"),
                                       "bias": None if share is None else round((share - 50) / 50, 2)}, "provider_structured": True})
        except Exception:  # noqa: BLE001
            pass
        return items

    # ------------------------------------------------------------ read-only compatibility probe
    STALE_H = {"model_scan_rows": 36.0, "equity_params": 36.0, "uw_news_rows": 12.0, "uw_flow_rows": 12.0, "uw_calendar_rows": 12.0, "finviz_rows": 24.0}      # freshness limit of each reader, in hours
    NEEDS = {"model_scan_rows": ("symbol", "signal", "score", "coverage", "date"), "uw_news_rows": ("headline", "created_at", "sentiment"),
             "uw_flow_rows": ("premium", "tags", "option_type"), "uw_calendar_rows": ("symbol", "report_time", "full_name"), "finviz_rows": ("date", "time"), "equity_params": ("name", "bias")}

    def cache_probe(self) -> dict:
        """Shapes only (field names and types, counts), never values: whether the live caches hold what each reader needs. Cache reads only; makes no request."""
        out: dict = {}

        def fields(rows):
            seen: dict = {}
            for r in rows[:25]:
                if isinstance(r, dict):
                    for k, v in r.items():
                        seen.setdefault(k, type(v).__name__)
            return seen

        def check(name, rows, ages=None):
            """Four distinct answers: COMPATIBLE, INCOMPATIBLE (samples exist and a field is missing), NO_SAMPLES (nothing to judge: never reported as missing fields) and STALE (samples are compatible but all older than the
            reader's freshness limit). `ages` are the ages in hours of the cache entries the sample came from."""
            fs = fields(rows)
            if not rows:                                           # an empty cache cannot be judged: after a restart the in-process caches are empty until the warmers refill them
                return {"rows_sampled": 0, "fields": [], "missing_fields": [], "compatible": None, "status": "NO_SAMPLES"}
            missing = [k for k in self.NEEDS[name] if k not in fs]
            res = {"rows_sampled": min(len(rows), 25), "fields": sorted(fs), "missing_fields": missing, "compatible": not missing, "status": "COMPATIBLE" if not missing else "INCOMPATIBLE"}
            ages = [a for a in (ages or []) if a is not None]
            if ages:
                res["newest_age_h"], res["oldest_age_h"] = round(min(ages), 2), round(max(ages), 2)
                if not missing and min(ages) > self.STALE_H[name]:
                    res["status"] = "STALE"
                    res["stale_after_h"] = self.STALE_H[name]
            return res
        _now = time.time()
        _age = lambda ts: None if ts is None else max(0.0, (_now - float(ts)) / 3600.0)
        scans = {}
        try:
            for m, (res, held) in self._scans().items():
                scans[m] = dict(check("model_scan_rows", res.get("rows") or [], [_age(held)]), rows=len(res.get("rows") or []), built_at=_iso(held))
        except Exception as e:  # noqa: BLE001
            scans["error"] = type(e).__name__
        out["model_scan_caches"] = {"present": sorted(k for k in scans if k != "error"), "absent": sorted(m for m in MODEL_KEYS if m not in scans), "detail": scans}
        try:
            import swr
            syms = [r["symbol"] for r in self.calendar()][:40]                       # a sample of the symbols the monitor tracks
            got = swr.peek_many(["earn:equity:%s" % s for s in syms]) or {}          # in-process first, then ONE read of the shared database tier (what the eight models see)
            eq = [(k, v) for k, v in got.items() if isinstance(v, dict)]
            params = [p for _, v in eq[:25] for p in ((v or {}).get("params") or [])]
            in_memory = sum(1 for k, _ in eq if k in getattr(swr, "_values", {}))
            from . import evidence as EV
            mapped = sorted({EV.kind_of_param(str(p.get("name") or p.get("key") or "")) or "UNMAPPED:" + str(p.get("name") or p.get("key")) for p in params})
            eq_ages = [_age(swr.held_at(k)) for k, _ in eq[:25]]
            out["earn_equity"] = dict(check("equity_params", [dict(p, name=p.get("name") or p.get("key")) for p in params], eq_ages), symbols_sampled=len(syms), symbols_cached=len(eq), symbols_in_process_memory=in_memory,
                                      symbols_from_database_tier=len(eq) - in_memory, evidence_kinds_mapped=mapped)
            fv = swr.peek("fviz:earn")
            out["finviz_cache"] = dict(check("finviz_rows", list(fv.values()) if isinstance(fv, dict) else [], [_age(swr.held_at("fviz:earn"))]), symbols=len(fv) if isinstance(fv, dict) else 0, present=isinstance(fv, dict))
        except Exception as e:  # noqa: BLE001
            out["earn_equity"] = {"error": type(e).__name__}
        try:
            import unusualwhales_service as uw
            data = self._cache_snapshot()
            okv = lambda v: isinstance(v, tuple) and isinstance(v[1], dict) and v[1].get("status") == "OK"
            news = [uw._rows(v[1]) for k, v in data.items() if k.startswith("uw:news:") and okv(v)]
            flow = [uw._rows(v[1]) for k, v in data.items() if k.startswith("uw:flowrecent:") and okv(v)]
            news_ages = [_age(v[0]) for k, v in data.items() if k.startswith("uw:news:") and okv(v)]
            flow_ages = [_age(v[0]) for k, v in data.items() if k.startswith("uw:flowrecent:") and okv(v)]
            cal_ages = [_age(data[k][0]) for k in data if k.startswith("uwcal:") and isinstance(data[k], tuple)]
            cal_keys = sorted(k for k in data if k.startswith("uwcal:"))
            cal_rows = [r for k in cal_keys for r in (uw._rows(data[k][1]) if isinstance(data[k], tuple) and isinstance(data[k][1], dict) else [])]
            out["uw_news"] = dict(check("uw_news_rows", [r for rows in news for r in rows], news_ages), symbols_cached=len(news))
            out["uw_flow"] = dict(check("uw_flow_rows", [r for rows in flow for r in rows], flow_ages), symbols_cached=len(flow))
            wanted = []
            d0 = self.clock().date()
            for off in range(0, self.calendar_days + 1):
                d = date.fromordinal(d0.toordinal() + off)
                if d.weekday() < 5:
                    wanted.append(d.isoformat())
            have = {k.rsplit(":", 1)[1] for k in cal_keys}
            out["uw_calendar"] = dict(check("uw_calendar_rows", cal_rows, cal_ages), dates_cached=len(have & set(wanted)), dates_wanted=len(wanted), dates_missing=sorted(set(wanted) - have)[:40])
        except Exception as e:  # noqa: BLE001
            out["uw_cache"] = {"error": type(e).__name__}
        out["smart_move"] = self.smart.status() if self.smart else {"engine_installed": False}
        out["all_readers_compatible"] = not any(v.get("compatible") is False for v in out.values() if isinstance(v, dict) and "compatible" in v)
        out["stale_now"] = sorted(k for k, v in out.items() if isinstance(v, dict) and v.get("status") == "STALE")
        out["unverifiable_now"] = sorted(k for k, v in out.items() if isinstance(v, dict) and v.get("compatible") is None)             # no samples in the cache at this moment
        return out

    def prepare_evidence(self, symbols: list) -> None:
        """Called once at the start of an evidence scan: reads every symbol's equity inputs in ONE batch (the in-process cache first, then a single read of the shared database tier, exactly as the eight models do), so a scan never pays one
        database round trip per symbol and still sees what survived a restart."""
        try:
            import swr
            self._eq_batch = swr.peek_many(["earn:equity:%s" % s for s in symbols]) or {}
        except Exception:  # noqa: BLE001
            self._eq_batch = None

    def model_sources(self) -> dict:
        if getattr(self, "_msrc", None) is None:
            import importlib
            import inspect
            out = {}
            for m, meta in MODEL_META.items():
                try:
                    out[m] = inspect.getsource(importlib.import_module(meta["module"]))
                except Exception:  # noqa: BLE001
                    out[m] = ""
            self._msrc = out
        return self._msrc

    # ------------------------------------------------------------ provider paths (only through ProviderAccess)
    def refresh_calendar(self) -> list:
        import uw_earnings_calendar as cal                         # has its own UW gate and per-date cache
        res = cal.calendar(days=self.calendar_days)
        if res.get("status") != "OK":
            raise RuntimeError("calendar refresh returned %s" % res.get("status"))
        fmap = {}
        try:
            import swr                                             # a second, independent vendor, read from its cache ONLY: earnings_map() would fetch one page per symbol on a cold cache
            fmap = swr.peek("fviz:earn") or {}
        except Exception:  # noqa: BLE001
            fmap = {}
        rows = []
        for r in res.get("rows") or []:
            sym = (r.get("symbol") or "").upper()
            d = str(r.get("date"))[:10]
            sess = r.get("reporting_time") or "UNKNOWN"
            sources = [{"vendor": "UW", "date": d, "session": sess}]
            fvi = fmap.get(sym)
            if fvi and fvi.get("date"):
                t = str(fvi.get("time") or "").lower()
                sources.append({"vendor": "FINVIZ", "date": str(fvi["date"])[:10], "session": "BMO" if "pre" in t or "bmo" in t else "AMC" if "after" in t or "amc" in t else "UNKNOWN"})
            rows.append({"symbol": sym, "company": r.get("company"), "date": d, "session": sess, "source": "uw_calendar", "sources": sources})
        return rows

    def refresh_event(self, symbol: str) -> None:
        """Rebuild the evidence caches the eight models read for ONE symbol, exactly as the app's own detail routes do (earn:equity and earn:trade). The models re-score from them on their normal cycle."""
        import earnings_equity_service as eq_svc
        import earnings_trade_service as ets
        import swr
        sym = symbol.upper()
        fresh = eq_svc.get_analysis(sym)
        if fresh and fresh.get("params"):
            swr._values[f"earn:equity:{sym}"] = (time.time(), fresh)
        trade = ets.get_summary(sym, "default")
        if trade:
            swr._values[f"earn:trade:default:{sym}"] = (time.time(), trade)

    def verify_outcome(self, symbol: str, d: str) -> None:
        import earnings_trade_service as ets
        moves = ets._historical_moves(symbol)                      # earnings history + daily candles: the real prior-close -> reaction-close move
        self._moves[symbol] = {m["date"]: m["move_pct"] for m in moves if m.get("date") and m.get("move_pct") is not None}

    # ------------------------------------------------------------ what is connected, honestly
    def source_report(self) -> dict:
        cached = {}
        try:
            for m, (res, held) in self._scans().items():
                cached[m] = {"rows": len(res.get("rows") or []), "built_at": _iso(held)}
        except Exception:  # noqa: BLE001
            cached = {}
        stored = self._stored_snapshots()
        models = []
        for m, label in LABELS.items():
            if m == "smart_move":
                st = self.smart.status() if self.smart else {"engine_installed": False, "snapshot_dir_configured": False, "how_predictions_arise": "none: not installed"}
                models.append({"id": m, "label": label, "kind": "frozen 40-point engine (score40-v1)", "external_ai_service": False, "stored_predictions_now": bool(self.smart and self.smart.models()), **st,
                               "needs_authorised_execution": "collecting its evidence (3-year bars, earnings history, SPY, sector ETFs) is a separate provider run with its own attestation and 50-attempt cap"})
                continue
            meta = MODEL_META[m]
            models.append({"id": m, "label": label, "kind": "formula model over warmed caches", "what": meta["what"], "external_ai_service": False, "reads": SHARED_INPUTS + meta["extra_inputs"],
                           "scan_cache": cached.get(m), "stored_snapshots": len(stored.get(m, {})), "stored_predictions_now": bool(cached.get(m, {}).get("rows") or stored.get(m)),
                           "needs_authorised_execution": "only to refresh the data it reads (Unusual Whales / Finnhub requests through the shared UW gate); no model run is billed or external"})
        return {"models": models, "note": "Determined from the services' code and the live caches: no model calls an external AI service."}
