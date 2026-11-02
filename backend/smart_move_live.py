"""Smart Move live layer: current-evidence scoring and the governed refresh service. NOTHING here runs a provider request unless it is explicitly enabled AND authorized.

Safe by default (all of these must be changed on purpose, by configuration, to send a single request):
  * the kill switch is ENGAGED (SMART_MOVE_REFRESH_ENABLED must be "1" to release it; a KILL file or the API can engage it at any time and only a local config change releases it);
  * the daily request authorization is ZERO (SMART_MOVE_AUTHORIZED_DAILY); the 1,700/day figure is only the planning ceiling above any grant, never a grant;
  * automatic refresh is OFF (SMART_MOVE_AUTO_REFRESH=1 starts the loop; nothing starts it by default);
  * the live fetcher exists but is only constructed when mode is LIVE; tests and the simulation use a fake provider.
Requests go through smvshadow.governor (authorization, daily ceiling, shared hourly caps with the warmer, 2 s spacing, at most one retry, 429/401 stop for the day, core-first
priority, append-only ledger). Every datum is stored with its retrieval time and SHA-256; scoring reads only what was retrieved at or before the scoring time and never uses a
historical replay or a saved pack as current evidence. The frozen scorer is untouched: gates around it can only turn a signal into NO TRADE, never create one.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from smart_move_routes import ENGINE_DIR, SECTOR_ETF  # noqa: F401  (also puts the engine on sys.path)

from smvshadow import adapters as ad  # noqa: E402
from smvshadow import calendar_confirm as cc  # noqa: E402
from smvshadow import calendar_validation as cv  # noqa: E402
from smvshadow import cutoff as co  # noqa: E402
from smvshadow import freshness as fr  # noqa: E402
from smvshadow import governor as gv  # noqa: E402
from smvshadow import scorer as sc  # noqa: E402
from smvshadow.freshness import missing  # noqa: E402
from smvshadow.store import PayloadStore  # noqa: E402

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
ETFS = sorted(set(SECTOR_ETF.values()))


# ----------------------------------------------------------------------------- configuration
@dataclass
class LiveConfig:
    mode: str = "LIVE"                       # LIVE | SIMULATION (a fake provider; every row says so)
    store_dir: Path = field(default_factory=lambda: Path(os.getenv("SMART_MOVE_LIVE_DIR") or Path(__file__).resolve().parent / ".smart_move_live"))
    refresh_enabled: bool = field(default_factory=lambda: os.getenv("SMART_MOVE_REFRESH_ENABLED") == "1")
    authorized_daily: int = field(default_factory=lambda: int(os.getenv("SMART_MOVE_AUTHORIZED_DAILY") or 0))
    planning_ceiling: int = 1700
    spacing_s: float = 2.0
    max_retries: int = 1
    calendar_interval_h: float = field(default_factory=lambda: float(os.getenv("SMART_MOVE_CALENDAR_INTERVAL_H") or 6.0))      # the calendar limit for events within 4 days (6 h); the env var exists only to demo staleness quickly
    history_max_age_days: int = 7
    vol_max_age_h: float = 24.0
    calendar_days_ahead: int = 3
    calendar_max_pages: int = 5                # UW returns at most 100 rows per page; a side/day that still fills the last page is recorded as TRUNCATED (it used to stop silently after 2 pages)
    tracking_days_ahead: int = 14              # upcoming-earnings TRACKING horizon (analyst Snapshot A); scoring still needs the strict freshness rule
    finviz_tickers: int = 100
    manual_min_interval_s: float = 60.0
    manual_max_requests: int = 50
    auto_interval_s: float = 300.0
    reserve_existing_hourly: int = 300
    reserve_existing_offhours: int = 1000


class SystemClock:
    def now(self):
        return datetime.now(UTC)

    def sleep(self, s):
        time.sleep(s)


class OffsetClock(SystemClock):
    """A clock that starts at a chosen instant and then runs in real time. Used only to demonstrate the SIMULATED provider on a day when no real event window is open."""

    def __init__(self, start: datetime):
        self.offset = start - datetime.now(UTC)

    def now(self):
        return datetime.now(UTC) + self.offset


# ----------------------------------------------------------------------------- store
class LiveStore:
    """Append-only JSONL files plus a hashed payload store, all local. Nothing here is a database and nothing leaves the machine."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.payloads = PayloadStore(self.root)
        self._lock = threading.Lock()

    def append(self, name: str, obj: dict):
        with self._lock, open(self.root / (name + ".jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, sort_keys=True, default=str) + "\n")

    def read(self, name: str) -> list:
        p = self.root / (name + ".jsonl")
        if not p.exists():
            return []
        with self._lock:
            return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]

    def put(self, raw: bytes) -> str:
        return self.payloads.put(raw)[0]

    def get(self, sha: str) -> bytes:
        return self.payloads.get(sha)

    def state(self) -> dict:
        p = self.root / "state.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def save_state(self, st: dict):
        tmp = self.root / "state.json.tmp"
        tmp.write_text(json.dumps(st, indent=1, sort_keys=True, default=str), encoding="utf-8")
        os.replace(tmp, self.root / "state.json")

    @property
    def kill_file(self) -> Path:
        return self.root / "KILL"


# ----------------------------------------------------------------------------- parsing of the vendor responses (formats seen in the real P1 data)
def parse_calendar(kind: str, body: bytes, retrieved_at: datetime, sha: str, request_id: str, mode: str) -> list:
    out = []
    at = retrieved_at.isoformat()
    if kind == "uw_calendar":
        for r in json.loads(body).get("data", []):
            out.append({"source": "UW_CALENDAR", "symbol": r["symbol"], "date": r["report_date"], "label": r.get("report_time"), "clock": None, "vendor_flag": r.get("source"), "company": r.get("full_name"),
                        "sector": r.get("sector"), "marketcap": r.get("marketcap"), "observed_at": at, "raw_sha256": sha, "request_id": request_id, "mode": mode})
    elif kind == "finviz_calendar":
        for line in list(csv.reader(io.StringIO(body.decode("utf-8", errors="replace"))))[1:]:
            o = cc.from_finviz(line[0], line[1], retrieved_at, sha)
            if o:
                out.append({"source": "FINVIZ", "symbol": o.symbol, "date": str(o.date), "label": None, "clock": o.clock, "vendor_flag": None, "company": None, "sector": None, "marketcap": None,
                            "observed_at": at, "raw_sha256": sha, "request_id": request_id, "mode": mode})
    elif kind == "finnhub_calendar":
        for r in json.loads(body).get("earningsCalendar", []):
            if r.get("symbol") and r.get("date"):
                out.append({"source": "FINNHUB", "symbol": str(r["symbol"]).upper(), "date": r["date"], "label": r.get("hour") or None, "clock": None, "vendor_flag": None, "company": None,
                            "sector": None, "marketcap": None, "observed_at": at, "raw_sha256": sha, "request_id": request_id, "mode": mode})
    return out


def pick_tickers(rows: list, n: int) -> list:
    best = {}
    for r in rows:
        s = str(r.get("symbol") or "").upper()
        if re.match(r"^[A-Z]{1,5}$", s):
            try:
                best[s] = max(float(r.get("marketcap")), best.get(s, 0.0))
            except (TypeError, ValueError):
                continue
    return [s for s, _ in sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))][:n]


# ----------------------------------------------------------------------------- fetchers
class Fetcher:
    """fetch(kind, symbol, params) -> bytes, or raise smvshadow.governor.ProviderError(kind in transient|rate_limited|auth)."""

    mode = "SIMULATION"

    def fetch(self, kind: str, symbol: Optional[str], params: dict) -> bytes:
        raise NotImplementedError


class InfraFetcher(Fetcher):
    """The existing TRDGO-US provider clients (unusualwhales_service, finviz_service, finnhub_service), used as BACKGROUND work so they yield to live requests. Constructing and
    calling this sends real requests; it is only built in LIVE mode and only called after the governor has granted a request. It is never called in the tests or the simulation."""

    mode = "LIVE"

    def __init__(self):
        import smart_move_shared_key_gate as _gate
        _gate.require_live_allowed()                                  # every live Smart Move script and route builds its client here: no valid owner attestation for THIS environment, no client
        import finnhub_service as fh
        import finviz_service as fz
        import unusualwhales_service as uw
        self.uw, self.fz, self.fh = uw, fz, fh

    def _uw(self, path, params):
        self.uw.set_background(True)
        try:
            out = self.uw.get(path, params)
        finally:
            self.uw.set_background(False)
        st = out.get("status")
        if st == "OK" and out.get("data") is not None:
            return json.dumps({"data": out["data"]}, default=str).encode("utf-8")
        if st in ("RATE_LIMITED", "BUDGET_EXHAUSTED"):
            raise gv.ProviderError("rate_limited", 429)
        if st in ("AUTH_ERROR", "PROVIDER_NOT_CONFIGURED", "UNAUTHORIZED"):
            raise gv.ProviderError("auth", 401)
        raise gv.ProviderError("transient", st)

    def fetch(self, kind, symbol, params):
        if kind == "uw_calendar":
            return self._uw("/api/earnings/" + params["side"], {"date": params["date"], "limit": 100, "page": params.get("page", 0)})
        if kind == "ohlc":
            return self._uw("/api/stock/%s/ohlc/1d" % symbol, {"timeframe": params.get("timeframe", "3Y"), "limit": 2500})
        if kind == "earnings":
            return self._uw("/api/earnings/%s" % symbol, {"limit": 24})
        if kind == "vol_context":
            return self._uw("/api/stock/%s/volatility/context" % symbol, {"date": params["date"]})
        if kind == "iv_rank":
            return self._uw("/api/stock/%s/iv-rank" % symbol, {"date": params["date"]})
        # V2 evidence kinds (additive; V1 never requests these). Endpoints are documented in api_spec.yaml.
        if kind == "uw_analysts":
            return self._uw("/api/screener/analysts", {"ticker": symbol, "limit": params.get("limit", 50)})
        if kind == "uw_estimates":
            return self._uw("/api/companies/%s/earnings-estimates" % symbol, {})
        if kind == "uw_flow_alerts":
            return self._uw("/api/stock/%s/flow-alerts" % symbol, {"limit": params.get("limit", 100)})
        if kind == "uw_short_float":
            return self._uw("/api/shorts/%s/interest-float/v2" % symbol, {})
        if kind == "uw_inst_own":
            return self._uw("/api/institution/%s/ownership" % symbol, {"limit": params.get("limit", 100)})
        if kind == "uw_news":
            q = {"ticker": symbol, "limit": params.get("limit", 25)}
            if params.get("page"):
                q["page"] = params["page"]                                   # documented paging parameter (starts on page 0, so page 0 is simply omitted)
            return self._uw("/api/news/headlines", q)
        if kind == "finviz_calendar":
            data = self.fz._get({"v": "152", "t": ",".join(params["tickers"]), "c": "1,68"})
            if data is None:
                raise gv.ProviderError("transient", "FINVIZ_NONE")
            return data.encode("utf-8")
        if kind == "finnhub_calendar":
            data = self.fh._get("/calendar/earnings?from=%s&to=%s" % (params["from"], params["to"]), 0)
            if data is None:
                raise gv.ProviderError("transient", "FINNHUB_NONE")
            return json.dumps(data).encode("utf-8")
        raise ValueError("unknown request kind " + kind)


# ----------------------------------------------------------------------------- freshness rules (shared by the planner, the scorer and the feed status)
def expected_session(now: datetime) -> date:
    return co.last_completed_session_at(now)


def rec_fresh(rec: Optional[dict], kind: str, now: datetime, cfg: LiveConfig) -> bool:
    if not rec:
        return False
    at = datetime.fromisoformat(rec["retrieved_at"])
    if at > now:
        return False
    if kind == "ohlc":
        lb = rec.get("last_bar_date")
        # the bar of the last completed session must be there AND must have been retrieved after that session was complete (a bar fetched during the session is partial);
        # a bar for a session still in progress is allowed (the adapters cut at the usable session)
        return bool(lb) and lb >= expected_session(now).isoformat() and co.last_completed_session_at(at) >= expected_session(now)
    if kind == "earnings":
        return now - at <= timedelta(days=cfg.history_max_age_days)
    if kind in ("vol_context", "iv_rank"):
        return rec.get("data_date") == expected_session(now).isoformat() and now - at <= timedelta(hours=cfg.vol_max_age_h)
    return False


def _key(kind, symbol, params):
    return "%s|%s|%s" % (kind, symbol or "", json.dumps({k: v for k, v in (params or {}).items() if k != "date"} if kind in ("vol_context", "iv_rank") else params or {}, sort_keys=True))


# ----------------------------------------------------------------------------- the service
class RefreshService:
    def __init__(self, cfg: LiveConfig, fetcher: Optional[Fetcher], clock=None, store: Optional[LiveStore] = None, existing_usage_fn: Optional[Callable] = None):
        self.cfg, self.fetcher, self.clock = cfg, fetcher, clock or SystemClock()
        self.store = store or LiveStore(cfg.store_dir)
        self.existing_usage_fn = existing_usage_fn or (lambda ts: 0)
        self._run_lock = threading.Lock()
        self._last_manual = None
        self._thread = None
        self._stop = threading.Event()
        self.focus = None                       # optional set of symbols: when set, evidence is collected only for these (a supervised run)
        self.gov = self._new_governor()
        self._seed_counts()

    # ---- governor, counts, kill switch
    def _new_governor(self):
        g = gv.Governor(self.clock, authorized_daily=self.cfg.authorized_daily, planning_ceiling=self.cfg.planning_ceiling, reserve_existing_hourly=self.cfg.reserve_existing_hourly,
                        reserve_existing_offhours=self.cfg.reserve_existing_offhours, existing_usage_fn=self.existing_usage_fn, spacing_s=self.cfg.spacing_s, max_retries=self.cfg.max_retries,
                        enabled=self.enabled)
        return g

    @property
    def kill_engaged(self) -> bool:
        return self.store.kill_file.exists() or not self.cfg.refresh_enabled

    @property
    def enabled(self) -> bool:
        return not self.kill_engaged

    def _seed_counts(self):
        """Today's and this hour's request counts survive a restart: they are rebuilt from the ledger."""
        now = self.clock.now()
        day = now.astimezone(gv.ET).strftime("%Y-%m-%d")
        hour = now.astimezone(gv.ET).strftime("%Y-%m-%d %H")
        for r in self.store.read("requests"):
            at = datetime.fromisoformat(r["at"]).astimezone(gv.ET)
            if at.strftime("%Y-%m-%d") == day:
                self.gov.used_day[day] = self.gov.used_day.get(day, 0) + 1
            if at.strftime("%Y-%m-%d %H") == hour:
                self.gov.used_hour[hour] = self.gov.used_hour.get(hour, 0) + 1

    def engage_kill_switch(self, reason: str = "manual"):
        self.store.kill_file.write_text(json.dumps({"at": self.clock.now().isoformat(), "reason": reason}), encoding="utf-8")
        self.gov.enabled = False
        self.log("KILL_SWITCH_ENGAGED", reason=reason)

    def log(self, event: str, **kw):
        self.store.append("log", dict(kw, event=event, at=self.clock.now().isoformat(), mode=self.cfg.mode))

    # ---- one governed request
    def _call(self, kind, symbol, params, request_id, priority="core"):
        self.gov.enabled = self.enabled
        n0 = len(self.gov.ledger)
        res = self.gov.execute(lambda: self.fetcher.fetch(kind, symbol, params), purpose=kind, priority=priority, symbol=symbol)
        at = self.clock.now()
        for e in self.gov.ledger[n0:]:
            self.store.append("ledger", e)
        if res["status"] == "OK":
            self.store.append("requests", {"at": at.isoformat(), "kind": kind, "symbol": symbol, "request_id": request_id, "attempts": res["attempts"], "provider": self._provider(kind)})
            body = res["value"]
            return body, self.store.put(body), at
        self.log("REQUEST_NOT_DONE", kind=kind, symbol=symbol, status=res["status"], reason=res["reason"], attempts=res["attempts"])
        st = self.store.state()
        st["last_error"] = {"at": at.isoformat(), "kind": kind, "symbol": symbol, "reason": res["reason"]}
        self.store.save_state(st)
        return None, None, at

    @staticmethod
    def _provider(kind):
        return {"finviz_calendar": "FINVIZ_ELITE", "finnhub_calendar": "FINNHUB"}.get(kind, "UNUSUAL_WHALES")

    def _ingest_evidence(self, kind, symbol, params, body, sha, at, request_id):
        rec = {"id": request_id, "kind": kind, "symbol": symbol, "params": params, "retrieved_at": at.isoformat(), "raw_sha256": sha, "mode": self.fetcher.mode}
        try:
            obj = json.loads(body)
            rows = obj.get("data") if isinstance(obj, dict) else None
            if kind == "ohlc" and isinstance(rows, list):
                reg = [r["date"][:10] for r in rows if str(r.get("market_time")) == "r"]
                rec["last_bar_date"] = max(reg) if reg else None
            if kind in ("vol_context", "iv_rank"):
                d = rows if isinstance(rows, dict) else (rows[0] if isinstance(rows, list) and rows else {})
                rec["data_date"] = str((d or {}).get("date"))[:10] if d else None
        except ValueError:
            rec["parse_error"] = True
        self.store.append("evidence", rec)

    # ---- calendar refresh
    def calendar_due(self, now) -> bool:
        obs = self.store.read("cal_obs")
        if not obs:
            return True
        newest = max(datetime.fromisoformat(o["observed_at"]) for o in obs)
        return now - newest >= timedelta(hours=self.cfg.calendar_interval_h)

    def _trading_days(self, now, ahead=None):
        today = now.astimezone(co.ET).date()
        mc = co._mc()
        n = self.cfg.calendar_days_ahead if ahead is None else ahead
        return [(today + timedelta(days=k)) for k in range(0, n + 1) if mc.is_trading_day(today + timedelta(days=k))]

    def refresh_calendar(self, budget: list, days_ahead=None) -> int:
        """UW premarket/afterhours for the next trading days (page 0, page 1 only when page 0 is full), one Finviz export for the largest names, one Finnhub window. budget[0] = requests left."""
        now = self.clock.now()
        days = self._trading_days(now, days_ahead)
        uw_rows, done, truncated = [], 0, []
        reserve = 2 if budget[0] > 2 else 0                              # the Finviz and Finnhub confirmations are kept: without a second vendor no event can become eligible
        stamp = now.strftime("%Y%m%dT%H%M%S")
        for d in days:
            for side in ("premarket", "afterhours"):
                page = 0
                while budget[0] > reserve:
                    rid = "uwcal-%s-%s-%s-p%d" % (stamp, d, side, page)
                    body, sha, at = self._call("uw_calendar", None, {"date": str(d), "side": side, "page": page}, rid)
                    budget[0] -= 1
                    if body is None:
                        return done
                    done += 1
                    rows = parse_calendar("uw_calendar", body, at, sha, rid, self.fetcher.mode)
                    for r in rows:
                        self.store.append("cal_obs", r)
                    uw_rows += rows
                    if len(rows) >= 100:
                        if page + 1 < self.cfg.calendar_max_pages:
                            page += 1
                            continue
                        truncated.append({"date": str(d), "side": side, "pages": page + 1})        # still full at the page cap: later rows were never seen
                    break
        tickers = pick_tickers(uw_rows, self.cfg.finviz_tickers)
        if tickers and budget[0] > 0:
            rid = "fzcal-%s" % stamp
            body, sha, at = self._call("finviz_calendar", None, {"tickers": tickers}, rid)
            budget[0] -= 1
            if body is not None:
                done += 1
                for r in parse_calendar("finviz_calendar", body, at, sha, rid, self.fetcher.mode):
                    self.store.append("cal_obs", r)
        if days and budget[0] > 0:
            rid = "fhcal-%s" % stamp
            body, sha, at = self._call("finnhub_calendar", None, {"from": str(days[0]), "to": str(days[-1])}, rid)
            budget[0] -= 1
            if body is not None:
                done += 1
                for r in parse_calendar("finnhub_calendar", body, at, sha, rid, self.fetcher.mode):
                    self.store.append("cal_obs", r)
        st = self.store.state()
        st["calendar_truncated"] = truncated
        st["last_calendar_refresh"] = self.clock.now().isoformat()
        self.store.save_state(st)
        return done

    # ---- evidence planning
    def _calobs(self, symbol=None):
        out = {}
        for r in self.store.read("cal_obs"):
            if symbol and r["symbol"] != symbol:
                continue
            o = cc.CalObs(r["source"], r["symbol"], date.fromisoformat(r["date"]), r["label"], r["clock"], r["vendor_flag"], r["raw_sha256"], datetime.fromisoformat(r["observed_at"]))
            out.setdefault(r["symbol"], []).append((o, r))
        return out

    def eligible_events(self, now, horizon_days=None):
        """Events the vendor rules accept (VENDOR_CONFIRMED / COMPANY_VERIFIED) whose capture window is open now. Deadline-ordered.
        horizon_days (tracking only): instead of 'window open now', every accepted event whose assumed release is between now and now + horizon_days and whose cutoff has not passed."""
        out = []
        for sym, pairs in self._calobs().items():
            ev = cc.evaluate(sym, [o for o, _ in pairs], now, now.astimezone(co.ET).date())
            if not ev["eligible_for_shadow"] or (self.focus is not None and sym not in self.focus):
                continue
            plan = co.plan_event(ev["event_date"], ev["kind"])
            if (plan["window_start_utc"] <= now <= plan["cutoff_utc"]) if horizon_days is None else (now <= plan["cutoff_utc"] and plan["assumed_release_utc"] <= now + timedelta(days=horizon_days)):
                sector = next((r["sector"] for _, r in pairs if r.get("sector")), None)
                try:
                    cap = max(float(r["marketcap"]) for _, r in pairs if r.get("marketcap"))
                except ValueError:
                    cap = 0.0
                out.append({"symbol": sym, "event_date": ev["event_date"], "kind": ev["kind"], "plan": plan, "sector": sector, "etf": SECTOR_ETF.get(sector), "marketcap": cap})
        out.sort(key=lambda e: (e["plan"]["cutoff_utc"], hashlib.sha256(e["symbol"].encode()).hexdigest()))
        return out

    def _derive(self, rec):
        """Freshness fields are derived from the stored payload at read time (the first live run showed that a daily series carries today's in-progress bar and that iv-rank returns a
        week of stamped rows), so the stored summary fields are informational only."""
        out = dict(rec)
        try:
            rows = json.loads(self.store.get(rec["raw_sha256"])).get("data")
            if rec["kind"] == "ohlc" and isinstance(rows, list):
                reg = [r["date"][:10] for r in rows if str(r.get("market_time")) == "r"]
                out["last_bar_date"] = max(reg) if reg else None
            elif rec["kind"] == "vol_context":
                d = rows if isinstance(rows, dict) else (rows[0] if isinstance(rows, list) and rows else None)
                out["data_date"] = str(d.get("date"))[:10] if d else None
            elif rec["kind"] == "iv_rank" and isinstance(rows, list) and rows:
                out["data_date"] = max(str(r.get("date"))[:10] for r in rows)
        except Exception:  # noqa: BLE001
            out["parse_error"] = True
        return out

    def _latest(self, kind, symbol, params=None):
        k = _key(kind, symbol, params)
        best = None
        for r in self.store.read("evidence"):
            if _key(r["kind"], r["symbol"], r["params"]) == k and (best is None or r["retrieved_at"] > best["retrieved_at"]):
                best = r
        return self._derive(best) if best else None

    def plan_evidence(self, now) -> list:
        """What is missing or stale for the open events, in priority order (shared ETF/SPY candles first, then each event by deadline)."""
        events = self.eligible_events(now)
        exp = expected_session(now).isoformat()
        tasks, seen = [], set()

        def add(kind, symbol, params, why):
            key = _key(kind, symbol, params)
            if key in seen:
                return
            seen.add(key)
            if not rec_fresh(self._latest(kind, symbol, params), kind, now, self.cfg):
                tasks.append({"kind": kind, "symbol": symbol, "params": params, "why": why})
        if events:
            add("ohlc", "SPY", {"timeframe": "3Y"}, "market")
        for e in events:
            if e["etf"]:
                add("ohlc", e["etf"], {"timeframe": "3Y"}, "sector of " + e["symbol"])
        for e in events:
            add("ohlc", e["symbol"], {"timeframe": "3Y"}, "price")
            add("earnings", e["symbol"], {"limit": 24}, "history")
            add("vol_context", e["symbol"], {"date": exp}, "F7 live capture")
            add("iv_rank", e["symbol"], {"date": exp}, "F7 live capture")
        return tasks

    def refresh_evidence(self, budget: list) -> int:
        now = self.clock.now()
        tasks = self.plan_evidence(now)
        events = self.eligible_events(now)
        self.gov.reserve_core(sum(1 for t in tasks if t["kind"] != "iv_rank") if tasks else 0)
        done, stamp = 0, now.strftime("%Y%m%dT%H%M%S")
        for t in tasks:
            if budget[0] <= 0:
                break
            rid = "%s-%s-%s" % (t["kind"], t["symbol"], stamp)
            body, sha, at = self._call(t["kind"], t["symbol"], t["params"], rid)
            budget[0] -= 1
            if body is None:
                if self.gov.stopped_until and self.clock.now() < self.gov.stopped_until:
                    break
                continue
            done += 1
            self._ingest_evidence(t["kind"], t["symbol"], t["params"], body, sha, at, rid)
        return done

    # ---- the run
    def run(self, scope: str = "all", max_requests: Optional[int] = None, manual: bool = False) -> dict:
        if not self._run_lock.acquire(blocking=False):
            return {"status": "ALREADY_RUNNING", "requests": 0}
        try:
            now = self.clock.now()
            blockers = self.blockers(now)
            hard = [b for b in blockers if b["code"] in ("KILL_SWITCH", "NOT_AUTHORIZED", "NO_FETCHER")]
            if hard:
                self.log("RUN_BLOCKED", scope=scope, manual=manual, blockers=[b["code"] for b in hard])
                return {"status": hard[0]["code"], "requests": 0, "blockers": hard}
            budget = [min(max_requests if max_requests is not None else 10 ** 6, self.cfg.manual_max_requests if manual else 10 ** 6)]
            start = budget[0]
            self.log("RUN_START", scope=scope, manual=manual, budget=start)
            calls = 0
            if scope in ("all", "calendar") and (manual or self.calendar_due(now)):
                calls += self.refresh_calendar(budget)
            if scope in ("all", "prices"):
                calls += self.refresh_evidence(budget)
            rows = LiveScorer(self).score_all(self.clock.now(), persist=True)
            st = self.store.state()
            st.update(last_run=self.clock.now().isoformat(), last_run_requests=calls, last_run_scope=scope)
            if calls:
                st["last_updated"] = self.clock.now().isoformat()
            self.store.save_state(st)
            self.log("RUN_END", scope=scope, requests=calls, scored=len(rows))
            return {"status": "OK", "requests": calls, "scored": len(rows)}
        finally:
            self._run_lock.release()

    def manual_refresh(self, scope: str = "all", max_requests: Optional[int] = None) -> dict:
        now = self.clock.now()
        if self._last_manual and (now - self._last_manual).total_seconds() < self.cfg.manual_min_interval_s:
            self.log("MANUAL_REFRESH_TOO_SOON")
            return {"status": "TOO_SOON", "requests": 0, "retry_in_s": round(self.cfg.manual_min_interval_s - (now - self._last_manual).total_seconds())}
        out = self.run(scope=scope, max_requests=max_requests, manual=True)
        if out.get("requests") or out["status"] == "OK":
            self._last_manual = now
        return out

    # ---- automatic refresh (OFF unless started; nothing in this repository starts it)
    def start_auto(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            while not self._stop.is_set():
                try:
                    self.run(scope="all")
                except Exception as exc:  # noqa: BLE001
                    self.log("AUTO_REFRESH_ERROR", error=type(exc).__name__)
                self._stop.wait(self.cfg.auto_interval_s)
        self._thread = threading.Thread(target=loop, name="smart-move-auto-refresh", daemon=True)
        self._thread.start()
        self.log("AUTO_REFRESH_STARTED", interval_s=self.cfg.auto_interval_s)

    def stop_auto(self):
        self._stop.set()

    # ---- status
    def blockers(self, now) -> list:
        b = []
        if self.fetcher is None and not self.kill_engaged:
            b.append({"code": "NO_FETCHER", "message": "No provider client is attached to this server."})
        if self.store.kill_file.exists():
            b.append({"code": "KILL_SWITCH", "message": "The kill switch file is present: no request can be sent. Remove it locally to release."})
        elif not self.cfg.refresh_enabled:
            b.append({"code": "KILL_SWITCH", "message": "Refresh is disabled (SMART_MOVE_REFRESH_ENABLED is not 1). No provider request can be sent."})
        if self.cfg.authorized_daily <= 0:
            b.append({"code": "NOT_AUTHORIZED", "message": "The daily request authorization is 0. A written approval and SMART_MOVE_AUTHORIZED_DAILY are required; the 1,700/day ceiling is not a grant."})
        if self.gov.stopped_until and now < self.gov.stopped_until:
            b.append({"code": "PROVIDER_STOP", "message": "A provider 429/401 stopped requests until %s." % self.gov.stopped_until.isoformat()})
        return b

    def freshness(self, now) -> dict:
        obs = self.store.read("cal_obs")
        out = {}
        if obs:
            newest = max(datetime.fromisoformat(o["observed_at"]) for o in obs)
            age = (now - newest).total_seconds() / 3600
            out["calendar"] = {"last": newest.isoformat(), "age_hours": round(age, 1), "limit_hours": self.cfg.calendar_interval_h, "state": "FRESH" if age <= self.cfg.calendar_interval_h else "STALE"}
        else:
            out["calendar"] = {"last": None, "age_hours": None, "limit_hours": self.cfg.calendar_interval_h, "state": "MISSING"}
        ev = self.store.read("evidence")
        for kind, label in (("ohlc", "prices"), ("earnings", "history"), ("vol_context", "volatility")):
            recs = [r for r in ev if r["kind"] == kind]
            if not recs:
                out[label] = {"last": None, "state": "MISSING", "count": 0}
                continue
            newest = max(recs, key=lambda r: r["retrieved_at"])
            fresh = sum(1 for r in {_key(r["kind"], r["symbol"], r["params"]): r for r in sorted(recs, key=lambda r: r["retrieved_at"])}.values() if rec_fresh(r, kind, now, self.cfg))
            total = len({_key(r["kind"], r["symbol"], r["params"]) for r in recs})
            out[label] = {"last": newest["retrieved_at"], "state": "FRESH" if fresh == total else "PARTIAL" if fresh else "STALE", "fresh_series": fresh, "series": total, "count": len(recs)}
        return out

    def feed(self) -> dict:
        now = self.clock.now()
        st = self.store.state()
        blockers = self.blockers(now)
        fr_ = self.freshness(now)
        day = now.astimezone(gv.ET).strftime("%Y-%m-%d")
        hour = now.astimezone(gv.ET).strftime("%Y-%m-%d %H")
        used_day, used_hour = self.gov.used_day.get(day, 0), self.gov.used_hour.get(hour, 0)
        prov = {}
        for r in self.store.read("requests"):
            if datetime.fromisoformat(r["at"]).astimezone(gv.ET).strftime("%Y-%m-%d") == day:
                prov[r["provider"]] = prov.get(r["provider"], 0) + 1
        hard = [b for b in blockers if b["code"] in ("KILL_SWITCH", "NOT_AUTHORIZED", "NO_FETCHER")]
        last_err = st.get("last_error")
        if self.kill_engaged:
            status = "DISABLED_KILL_SWITCH"
        elif any(b["code"] == "NOT_AUTHORIZED" for b in blockers):
            status = "NOT_AUTHORIZED"
        elif any(b["code"] == "PROVIDER_STOP" for b in blockers):
            status = "PROVIDER_STOPPED"
        elif self._run_lock.locked():
            status = "REFRESHING"
        elif any(v.get("state") in ("STALE", "MISSING") for v in fr_.values() if v is fr_.get("calendar")) or fr_["calendar"]["state"] != "FRESH":
            status = "STALE"
        elif last_err and datetime.fromisoformat(last_err["at"]) > now - timedelta(hours=1):
            status = "DEGRADED"
        else:
            status = "OK"
        next_refresh = None
        if not hard and fr_["calendar"]["last"]:
            next_refresh = (datetime.fromisoformat(fr_["calendar"]["last"]) + timedelta(hours=self.cfg.calendar_interval_h)).isoformat()
        elif not hard:
            next_refresh = now.isoformat()
        return {"mode": self.cfg.mode, "status": status, "kill_switch": self.kill_engaged, "refresh_enabled": self.cfg.refresh_enabled, "auto_refresh": bool(self._thread and self._thread.is_alive()),
                "authorized_daily": self.cfg.authorized_daily, "planning_ceiling": self.cfg.planning_ceiling, "requests_today": used_day, "requests_this_hour": used_hour, "requests_today_by_provider": prov,
                "last_updated": st.get("last_updated"), "last_run": st.get("last_run"), "next_refresh": next_refresh,
                "next_refresh_note": ("none scheduled: " + hard[0]["message"]) if hard else "next calendar refresh due then; evidence refreshes whenever it is stale and an event window is open",
                "freshness": fr_, "blockers": blockers, "last_error": last_err, "limits": {"spacing_s": self.cfg.spacing_s, "max_retries": self.cfg.max_retries, "manual_min_interval_s": self.cfg.manual_min_interval_s,
                                                                                     "manual_max_requests": self.cfg.manual_max_requests, "shared_hourly_live": 400, "reserve_existing_hourly": self.cfg.reserve_existing_hourly},
                "recent_log": self.store.read("log")[-15:], "evidence_mode": self.fetcher.mode if self.fetcher else "NONE"}


# ----------------------------------------------------------------------------- live scoring
class LiveScorer:
    """Scores the events in the live store with the frozen engine, using only evidence retrieved at or before `now`. Gates can only turn a signal into NO TRADE."""

    def __init__(self, service: RefreshService):
        self.s = service
        self.cfg = service.cfg

    def _rows_of(self, rec):
        if not rec:
            return None, None
        obj = json.loads(self.s.store.get(rec["raw_sha256"]))
        return obj.get("data"), rec["raw_sha256"]

    @staticmethod
    def _mode_of(raw_obs: list) -> str:
        """LIVE only if every calendar observation of the row came from the live providers; SIMULATION if any came from the simulated one."""
        modes = {r.get("mode") for r in raw_obs}
        return "SIMULATION" if "SIMULATION" in modes else "LIVE" if modes == {"LIVE"} else "NONE"

    def score_all(self, now: datetime, persist: bool = False) -> list:
        pre, sha = sc.load_prereg()
        rows = []
        for sym, pairs in sorted(self.s._calobs().items()):
            obs = [o for o, _ in pairs if o.observed_at <= now]
            if not obs:
                continue
            ev = cc.evaluate(sym, obs, now, now.astimezone(co.ET).date())
            raw = [r for _, r in pairs]
            uw = next((r for r in raw if r["source"] == "UW_CALENDAR"), {})
            sector = uw.get("sector")
            etf = SECTOR_ETF.get(sector)
            row = {"dataset": "LIVE_CURRENT", "symbol": sym, "company": uw.get("company"), "sector": sector,
                   "sector_note": ("from the Unusual Whales row; mapped to " + etf) if etf else ("sector %r has no ETF mapping" % sector if sector else "no sector (not seen by Unusual Whales): F8 sector part unavailable"),
                   "earnings_date": str(ev["event_date"]) if ev["event_date"] else None, "kind": ev["kind"], "evidence_mode": self._mode_of(raw),
                   "calendar": {"status_at_observation": ev["status"], "status_now": ev["status"], "reasons": ev["reasons"], "no_trade_reasons": ev["no_trade_reasons"], "eligible_for_shadow": ev["eligible_for_shadow"],
                                "vendors": ev["vendors"], "calendar_observed_at": ev["calendar_observed_at"].isoformat() if ev["calendar_observed_at"] else None, "uw_flag": uw.get("vendor_flag"),
                                "dependency": ev["dependency"], "stale_vendor_rows": ev.get("stale_vendor_rows", []), "note": "vendor agreement, not direct company confirmation" if ev["status"] == cc.VENDOR_CONFIRMED else None},
                   "observations": [{k: r[k] for k in ("source", "date", "label", "clock", "vendor_flag", "observed_at", "raw_sha256", "request_id")} for r in sorted(raw, key=lambda r: (r["source"], r["date"]))],
                   "exploratory": {"sec_f3": {"available": False, "reason": "SEC_EVIDENCE_NOT_COLLECTED", "note": "experimental and kept out of the primary model"}}}
            if ev["event_date"] is None:
                row["engine"] = {v: {"signal": "NO_TRADE", "bull": 0, "bear": 0, "available": 0, "max": 40, "reason_codes": ev["no_trade_reasons"] or ["NO_OBSERVATION"]} for v in ("continuation", "contrarian")}
                row.update(available_points=0, missing_factors=[{"factor": f, "name": n, "reason": "NO_EVENT_DATE"} for f, n in (("F3", "Earnings-reaction history"), ("F5", "Price / technical"), ("F7", "Volatility (skew)"), ("F8", "Sector / market"))],
                           subscores={}, blocked_by=["NO_EVENT_DATE"], evidence=[])
                rows.append(row)
                continue
            plan = co.plan_event(ev["event_date"], ev["kind"])
            exp = expected_session(now)
            used = []

            early = set()

            def latest(kind, symbol, params):
                """The frozen design says capture AND prediction happen between release - 48 h and the cutoff: evidence retrieved before the window opened does not count."""
                r = self.s._latest(kind, symbol, params)
                if r and r["retrieved_at"] <= now.isoformat():
                    if r["retrieved_at"] < plan["window_start_utc"].isoformat():
                        early.add((kind, symbol))
                        return None
                    return r
                return None

            def why(default, *keys):
                return "RETRIEVED_BEFORE_WINDOW" if any(k in early for k in keys) else default
            cand_rec, hist_rec = latest("ohlc", sym, {"timeframe": "3Y"}), latest("earnings", sym, {"limit": 24})
            spy_rec, sec_rec = latest("ohlc", "SPY", {"timeframe": "3Y"}), (latest("ohlc", etf, {"timeframe": "3Y"}) if etf else None)
            ctx_rec, ivr_rec = latest("vol_context", sym, {"date": exp.isoformat()}), latest("iv_rank", sym, {"date": exp.isoformat()})
            cand, sha_c = self._rows_of(cand_rec) if rec_fresh(cand_rec, "ohlc", now, self.cfg) else (None, None)
            hist, sha_h = self._rows_of(hist_rec) if rec_fresh(hist_rec, "earnings", now, self.cfg) else (None, None)
            spy, sha_s = self._rows_of(spy_rec) if rec_fresh(spy_rec, "ohlc", now, self.cfg) else (None, None)
            sec, _ = self._rows_of(sec_rec) if (sec_rec and rec_fresh(sec_rec, "ohlc", now, self.cfg)) else (None, None)
            ctx, sha_x = self._rows_of(ctx_rec) if rec_fresh(ctx_rec, "vol_context", now, self.cfg) else (None, None)
            ivr, _ = self._rows_of(ivr_rec) if rec_fresh(ivr_rec, "iv_rank", now, self.cfg) else (None, None)
            for r, k in ((cand_rec, "ohlc"), (hist_rec, "earnings"), (spy_rec, "ohlc"), (sec_rec, "ohlc"), (ctx_rec, "vol_context"), (ivr_rec, "iv_rank")):
                if r:
                    used.append({"kind": k, "symbol": r["symbol"], "retrieved_at": r["retrieved_at"], "raw_sha256": r["raw_sha256"], "mode": r["mode"], "fresh": rec_fresh(r, k, now, self.cfg)})
            evid = {}
            react = set()
            for r in (hist or []):
                k = ad._kind(r.get("report_time"))
                try:
                    if k:
                        react.add(co.reaction_session(date.fromisoformat(str(r["report_date"])[:10]), k))
                except (KeyError, ValueError):
                    pass

            def safe(factor, fn, why):
                try:
                    return fn()
                except (fr.LeakageError, Exception) as exc:  # noqa: BLE001
                    return missing(factor, "live", why if isinstance(exc, fr.LeakageError) else type(exc).__name__, now)
            evid[5] = safe(5, lambda: ad.f5_price_technical(cand, spy, plan, now, react, sha_c), "OUT_OF_WINDOW") if cand and spy else missing(5, "live.ohlc", why("NO_FRESH_PRICE_EVIDENCE", ("ohlc", sym)) if not cand else why("NO_FRESH_MARKET_EVIDENCE", ("ohlc", "SPY")), now)
            evid[3] = safe(3, lambda: ad.f3_reaction_history(hist, cand, ev["event_date"], plan, now, sha_h), "OUT_OF_WINDOW") if hist and cand else missing(3, "live.earnings+ohlc", why("NO_FRESH_HISTORY_OR_PRICE", ("earnings", sym), ("ohlc", sym)), now)
            evid[8] = safe(8, lambda: ad.f8_sector_market(spy, sec, plan, now, sha_s), "OUT_OF_WINDOW") if spy else missing(8, "live.ohlc:SPY", why("NO_FRESH_MARKET_EVIDENCE", ("ohlc", "SPY")), now)
            # F7 only from a PROSPECTIVE capture taken in the window; saved or replayed context never enters
            f7_ok = bool(ctx and ctx_rec and plan["window_start_utc"] <= datetime.fromisoformat(ctx_rec["retrieved_at"]) <= plan["cutoff_utc"])
            evid[7] = safe(7, lambda: ad.f7_volatility_context({"data": ctx}, ivr, plan, now, sha_x), "OUT_OF_WINDOW") if f7_ok else missing(7, "uw.volatility.context", "NOT_CAPTURED_LIVE_IN_WINDOW", now)
            kind = ev["kind"]
            preds = {v: sc.score(pre, sha, evid, symbol=sym, event_date=ev["event_date"], event_version=1, kind=kind, timing_status=ev["timing_status"], plan=plan, capture_ts=now, variant=v) for v in ("continuation", "contrarian")}
            blocked = []
            newest_cal = ev["calendar_observed_at"]
            if newest_cal is not None and now - newest_cal > timedelta(hours=self.cfg.calendar_interval_h):
                blocked.append("STALE_CALENDAR")                       # the service's own freshness limit applies on top of the vendor rules
                row["calendar"]["status_now"] = "STALE"
                row["calendar"]["eligible_for_shadow"] = False
            if not ev["eligible_for_shadow"]:
                blocked += ["CALENDAR_" + (ev["status"] if ev["status"] not in cc.ELIGIBLE else "NOT_ELIGIBLE")] + ev["no_trade_reasons"]
            if any(e.missing_reason in ("STALE_AGE",) for e in evid.values() if e is not None):
                blocked.append("STALE_EVIDENCE")
            row["engine"] = {}
            for v, p in preds.items():
                sig, codes = p["signal"], list(p["reason_codes"])
                if blocked and sig in ("BUY", "SELL"):
                    sig, codes = "NO_TRADE", codes + ["BLOCKED_BY_GATE"]
                row["engine"][v] = {"signal": sig, "bull": p["bull_points"], "bear": p["bear_points"], "available": p["weight_available"], "max": 40, "reason_codes": codes,
                                    "score_before_gate": p["signal"]}
            miss = [{"factor": "F%d" % f, "name": {3: "Earnings-reaction history", 5: "Price / technical", 7: "Volatility (skew)", 8: "Sector / market"}[f], "reason": e.missing_reason or e.status}
                    for f, e in sorted(evid.items()) if not e.usable]
            if evid[8].usable and evid[8].extracted.get("sector_rel20") is None:
                miss.append({"factor": "F8", "name": "Sector part of F8", "reason": "NO_SECTOR_MAPPING_OR_ETF_EVIDENCE", "partial": True})
            if any(e["mode"] == "SIMULATION" for e in used):
                row["evidence_mode"] = "SIMULATION"
            row.update(available_points=preds["continuation"]["weight_available"], missing_factors=miss, blocked_by=sorted(set(blocked)), evidence=used,
                       adv20=(getattr(evid[5], "extracted", None) or {}).get("adv20") if evid[5].usable else None,
                       subscores={v: preds[v]["explanation"]["subscores"] for v in preds}, capture_ts=now.isoformat(),
                       plan={"assumed_release_et": plan["assumed_release_et"].isoformat(), "window_start_utc": plan["window_start_utc"].isoformat(), "cutoff_utc": plan["cutoff_utc"].isoformat(), "basis": plan["basis"]})
            rows.append(row)
        if persist:
            last = {}
            for r in self.s.store.read("predictions"):
                last[(r["symbol"], r["earnings_date"])] = r
            for r in rows:
                if r.get("capture_ts") is None:
                    continue
                digest = hashlib.sha256(json.dumps({k: r[k] for k in ("engine", "missing_factors", "blocked_by", "available_points")}, sort_keys=True, default=str).encode()).hexdigest()
                prev = last.get((r["symbol"], r["earnings_date"]))
                if prev is None or prev["digest"] != digest:
                    self.s.store.append("predictions", {"symbol": r["symbol"], "earnings_date": r["earnings_date"], "digest": digest, "version": (prev["version"] + 1) if prev else 1,
                                                        "captured_at": r["capture_ts"], "mode": r["evidence_mode"], "engine": r["engine"], "blocked_by": r["blocked_by"],
                                                        "meta": {"sector": r.get("sector"), "kind": r.get("kind"), "adv20": r.get("adv20"), "coverage": r.get("available_points"), "company": r.get("company")},
                                                        "evidence": [{k: e[k] for k in ("kind", "symbol", "raw_sha256", "retrieved_at")} for e in r["evidence"]]})
        return rows
