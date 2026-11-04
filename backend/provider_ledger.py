"""A day-by-day ledger of the calls this application makes to each data provider: how many, how they ended, how long they took, and what they cost.

Why it exists: Unusual Whales already counts its own calls in memory (reset by every restart); Finnhub and Finviz counted nothing, and `provider_usage.py` is a hand-written map of what each provider powers, not a counter. So
nobody could answer "how many requests did each provider get today, how many failed and how, and what does a request cost us" without reading logs.

What it does and does not do
  * `record()` is called from a provider's single HTTP door with the outcome of one real request. It never raises, never blocks on disk (the file is written at most once a minute, atomically) and never changes what the caller gets.
  * Unusual Whales is NOT hooked in its client (the most sensitive file in the application). Its numbers are read from its own `_spent` day counter and `_metrics` at snapshot time.
  * Costs are never invented. A provider has a cost only if the owner set its plan price in PROVIDER_PLAN_USD_MONTH (e.g. "UNUSUAL_WHALES=250,FINNHUB=0"); then the ledger shows the plan price and the effective price per 1,000 requests so far this month.
    A provider limit is shown only when it is known (Unusual Whales: no daily cap, three simultaneous requests per token); otherwise it says "not configured".
  * Days are New York dates, like every other limit in the application. The ledger keeps 62 days.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001
    ET = timezone(timedelta(hours=-5))

OUTCOMES = ("OK", "RATE_LIMITED", "SERVER_ERROR", "TIMEOUT", "AUTH", "NO_DATA", "OTHER")
KEEP_DAYS = 62
FLUSH_EVERY_S = 60.0
KNOWN_LIMITS = {"UNUSUAL_WHALES": "no daily cap on the current plan; at most 3 simultaneous requests per token (shared by every consumer of the token)"}
_BOOT_ID = __import__("uuid").uuid4().hex                      # one value per process: tells a restart from a continuing process
_uw = {"day": None, "base": 0, "last": 0, "boot": None}         # Unusual Whales day total = banked (earlier processes) + the current process's own counter

_lock = threading.Lock()
_days: dict = {}                      # {"YYYY-MM-DD": {provider: {"requests", "outcomes": {...}, "latency_ms_sum", "latency_n"}}}
_loaded = False
_last_flush = 0.0
_dirty = False
_clock = time.time                    # replaced by tests


def _dir() -> Path:
    return Path(os.environ.get("PROVIDER_LEDGER_DIR") or (Path(__file__).resolve().parent / ".provider_ledger"))


def _file() -> Path:
    return _dir() / "provider_ledger.json"


def today() -> str:
    return datetime.fromtimestamp(_clock(), ET).date().isoformat()


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
        for d, provs in (data.get("days") or {}).items():
            _days.setdefault(d, provs)
        u = data.get("uw") or {}
        _uw.update(day=u.get("day"), base=int(u.get("base") or 0), last=int(u.get("last") or 0), boot=u.get("boot"))
    except Exception:  # noqa: BLE001 - a missing or damaged file just starts the ledger empty
        pass


def classify_http(code: Optional[int] = None, exc: Optional[BaseException] = None) -> str:
    """Map a failed request to one of OUTCOMES."""
    if code is not None:
        if code == 429:
            return "RATE_LIMITED"
        if code in (401, 403):
            return "AUTH"
        if code == 404:
            return "NO_DATA"
        if code >= 500:
            return "SERVER_ERROR"
        return "OTHER"
    if exc is not None and ("timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower()):
        return "TIMEOUT"
    return "OTHER"


def record(provider: str, outcome: str = "OK", latency_ms: Optional[float] = None) -> None:
    """One real request to `provider` ended as `outcome`. Never raises."""
    global _dirty
    try:
        out = outcome if outcome in OUTCOMES else "OTHER"
        with _lock:
            _load()
            row = _days.setdefault(today(), {}).setdefault(provider, {"requests": 0, "outcomes": {}, "latency_ms_sum": 0.0, "latency_n": 0})
            row["requests"] += 1
            row["outcomes"][out] = row["outcomes"].get(out, 0) + 1
            if latency_ms is not None:
                row["latency_ms_sum"] += float(latency_ms)
                row["latency_n"] += 1
            _dirty = True
        _maybe_flush()
    except Exception:  # noqa: BLE001 - the ledger must never be the reason a provider call fails
        pass


def record_ok(provider: str, started: Optional[float] = None) -> None:
    record(provider, "OK", (_clock() - started) * 1000.0 if started else None)


def record_exception(provider: str, exc: BaseException, started: Optional[float] = None) -> None:
    code = getattr(exc, "code", None)
    record(provider, classify_http(code=code if isinstance(code, int) else None, exc=exc), (_clock() - started) * 1000.0 if started else None)


def _maybe_flush() -> None:
    if _dirty and _clock() - _last_flush >= FLUSH_EVERY_S:
        flush()


def flush() -> bool:
    """Write the ledger atomically (temp file + replace). Returns False when the disk write failed; the in-memory ledger is unaffected."""
    global _last_flush, _dirty
    try:
        with _lock:
            _load()
            keep = sorted(_days)[-KEEP_DAYS:]
            for d in [d for d in _days if d not in keep]:
                del _days[d]
            payload = json.dumps({"saved_at": datetime.fromtimestamp(_clock(), timezone.utc).isoformat(), "days": {d: _days[d] for d in keep}, "uw": dict(_uw)}, indent=1, sort_keys=True)
            _last_flush, _dirty = _clock(), False
        _dir().mkdir(parents=True, exist_ok=True)
        tmp = _file().with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, _file())
        return True
    except Exception:  # noqa: BLE001
        return False


def _prices() -> dict:
    out = {}
    for part in (os.environ.get("PROVIDER_PLAN_USD_MONTH") or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                out[k.strip().upper()] = float(v)
            except ValueError:
                pass
    return out


def _uw_total(counter: int, day: str) -> int:
    """Unusual Whales' request count for `day` WITHOUT double counting. Its own counter lives in process memory, so a restart resets it to zero. We keep `base` (what earlier processes had counted today) and `last` (the
    counter value at our previous look). A new process (different boot id) or a counter that went DOWN means a restart: bank `last`, then follow the new counter. The total can lag the truth by up to one flush interval
    (it is never ahead of it): the ledger errs low, never high."""
    with _lock:
        _load()
        if _uw["day"] != day:
            _uw.update(day=day, base=0, last=0)
        if _uw["boot"] != _BOOT_ID:
            if _uw["boot"] is not None or counter < _uw["last"]:
                _uw["base"] += _uw["last"]
            _uw.update(boot=_BOOT_ID, last=0)
        elif counter < _uw["last"]:
            _uw["base"] += _uw["last"]
        _uw["last"] = counter
        return _uw["base"] + counter


def _uw_today() -> Optional[dict]:
    """Unusual Whales' own counters (read-only): today's request count and the error classes seen since the process started."""
    try:
        import unusualwhales_service as uw
        spent = uw._spent
        m = uw._metrics
        by = dict(m.get("errors_by") or {})
        outcomes = {"RATE_LIMITED": by.get("429", 0), "NO_DATA": m.get("no_data", 0), "TIMEOUT": by.get("net", 0),
                    "SERVER_ERROR": sum(v for k, v in by.items() if k.isdigit() and int(k) >= 500), "AUTH": by.get("401", 0) + by.get("403", 0)}
        counter = int(spent.get("count") or 0) if spent.get("day") == uw._today() else 0
        return {"requests": _uw_total(counter, today()), "counter_since_restart": counter, "outcomes": {k: v for k, v in outcomes.items() if v},
                "outcomes_since_restart": True, "avg_latency_ms": round(sum(m["lat"]) / len(m["lat"])) if m.get("lat") else None, "source": "unusualwhales_service counters (not hooked)"}
    except Exception:  # noqa: BLE001
        return None


def _limits(p: str) -> dict:
    """What is known about a provider's limits, with the source of each figure. Configured by the owner in PROVIDER_LIMITS (JSON, e.g. {"FINNHUB": {"per_minute": 60, "per_day": null}}); nothing is assumed."""
    cfg = {}
    try:
        cfg = (json.loads(os.environ.get("PROVIDER_LIMITS") or "{}") or {}).get(p) or {}
    except ValueError:
        pass
    out = {"per_minute": cfg.get("per_minute", "UNKNOWN"), "per_day": cfg.get("per_day", "UNKNOWN"), "concurrent": cfg.get("concurrent", "UNKNOWN"), "source": "owner configuration (PROVIDER_LIMITS)" if cfg else "UNKNOWN: nothing configured"}
    if p == "UNUSUAL_WHALES":
        out.update({"per_day": cfg.get("per_day", "none (unlimited plan)"), "concurrent": cfg.get("concurrent", 3), "source": "owner-confirmed plan: no daily cap, 3 simultaneous requests per token" + (" (owner configuration overrides)" if cfg else "")})
    if p == "FINNHUB" and "per_minute" not in cfg:
        out.update({"per_minute": "pacing %s/min (application setting FINNHUB_PER_MINUTE, not a verified provider quota)" % (os.environ.get("FINNHUB_PER_MINUTE") or 55)})
    return out


def _remaining(p: str, used_today: int):
    """Requests left today where a daily quota is known; otherwise the word UNKNOWN (never a guess)."""
    lim = _limits(p).get("per_day")
    return max(0, int(lim) - int(used_today)) if isinstance(lim, (int, float)) and not isinstance(lim, bool) else "UNKNOWN"


def snapshot(history_days: int = 7) -> dict:
    """Today, the last days, the month to date and the cost picture for every provider seen."""
    with _lock:
        _load()
        days = {d: {p: dict(r, outcomes=dict(r["outcomes"])) for p, r in provs.items()} for d, provs in _days.items()}
    t = today()
    uw = _uw_today()
    if uw:
        row = days.setdefault(t, {}).setdefault("UNUSUAL_WHALES", {"requests": 0, "outcomes": {}, "latency_ms_sum": 0.0, "latency_n": 0})
        row["requests"] = uw["requests"]                                   # Unusual Whales is never recorded by record(): its total comes only from the reconciled counter, so nothing is counted twice
        row["outcomes"] = {**row["outcomes"], **uw["outcomes"]}
        row["note"] = uw["source"]
    month = t[:7]
    prices = _prices()
    providers = {}
    names = sorted({p for provs in days.values() for p in provs} | set(prices))
    for p in names:
        td = (days.get(t) or {}).get(p) or {"requests": 0, "outcomes": {}, "latency_ms_sum": 0.0, "latency_n": 0}
        mtd = sum(r.get("requests", 0) for d, provs in days.items() if d.startswith(month) for q, r in provs.items() if q == p)
        errs = {k: v for k, v in td["outcomes"].items() if k not in ("OK", "NO_DATA")}
        usd = prices.get(p)
        providers[p] = {"today_requests": td["requests"], "today_outcomes": td["outcomes"], "today_error_requests": sum(errs.values()),
                        "today_error_rate": round(sum(errs.values()) / td["requests"], 4) if td["requests"] else None,
                        "avg_latency_ms": round(td["latency_ms_sum"] / td["latency_n"]) if td.get("latency_n") else (uw or {}).get("avg_latency_ms") if p == "UNUSUAL_WHALES" else None,
                        "month_to_date_requests": mtd, "limit": KNOWN_LIMITS.get(p, "UNKNOWN"), "limits": _limits(p), "remaining_today": _remaining(p, td["requests"]),
                        "plan_usd_month": usd, "effective_usd_per_1000_requests_month_to_date": round(usd / mtd * 1000, 4) if usd is not None and mtd else None,
                        "cost": "plan price set by the owner (PROVIDER_PLAN_USD_MONTH)" if usd is not None else "UNKNOWN: no plan price was provided, so no cost is shown",
                        "note": (days.get(t) or {}).get(p, {}).get("note")}
    recent = sorted(days)[-max(1, history_days):]
    return {"status": "OK", "day": t, "day_timezone": "America/New_York", "providers": providers,
            "history": {d: {p: {"requests": r["requests"], "outcomes": r["outcomes"]} for p, r in days[d].items()} for d in recent},
            "persisted_to": str(_file()), "kept_days": KEEP_DAYS,
            "note": "Requests counted are real HTTP requests made by this application. Unusual Whales' error classes cover the time since the last restart; its request count is its own day counter."}


def reset_for_tests() -> None:
    global _days, _loaded, _last_flush, _dirty
    with _lock:
        _days, _loaded, _last_flush, _dirty = {}, False, 0.0, False
