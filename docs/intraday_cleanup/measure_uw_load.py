"""Count Unusual Whales requests per feature in ONE local run, with every network call stubbed.

Run as:  python -I -X utf8 -c "import sys; sys.path.insert(0, sys.argv[1]); exec(open(sys.argv[2], encoding='utf-8').read())" <backend dir> <this file> <label> <out.json>

The UW client's wire function (_get_impl) is replaced by a counter that returns synthetic payloads; urllib.request.urlopen is
replaced so NO provider (UW, Finnhub, SEC, Finviz) is ever reached. The request memo, the module caches and the priority /
budget gate stay real, so what is counted is what would have gone to the wire. Each feature's unit of work is run cold
(empty caches) and the count is then scaled by its production cadence to requests per minute in the regular session.
"""
import json
import os
import sys
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

LABEL, OUT = sys.argv[3], sys.argv[4]
os.environ["PYTEST_CURRENT_TEST"] = "measure"            # no state files, no log files, no board saves
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///" + os.path.join(os.environ.get("TEMP", "."), f"uw_measure_{LABEL}.db").replace("\\", "/")
os.environ["DISABLE_WARMERS"] = "1"
os.environ.pop("APP_ENV", None)

import urllib.request


def _no_network(*a, **k):
    raise OSError("network blocked by the measurement harness")


urllib.request.urlopen = _no_network

import unusualwhales_service as uw
import uw_priority as pri
import uw_budget as bud
import swr
import tab_settings as cfg

NOW_MS = int(time.time() * 1000)
COUNTS: dict = {}
_lock = threading.Lock()


def _template(path: str) -> str:
    parts = path.split("/")
    # /api/stock/AAPL/quote -> /api/stock/{t}/quote
    if len(parts) > 3 and parts[2] == "stock":
        parts[3] = "{t}"
    return "/".join(parts)


def _payload(path: str):
    if path.endswith("/quote"):
        return {"last_trade": {"price": 100.0, "time": NOW_MS, "vol": 1000},
                "quote": {"bid": {"price": 99.98}, "ask": {"price": 100.02}}, "market_time": "market"}
    return []


def counting_get_impl(path, params=None):
    prio, module, _tracker = pri.current()
    key = (f"P{prio}", module or "?", _template(path))
    with _lock:
        COUNTS[key] = COUNTS.get(key, 0) + 1
    return {"status": "OK", "data": _payload(path), "source": "stub"}


uw._get_impl = counting_get_impl


def reset():
    with _lock:
        COUNTS.clear()
    swr.clear()
    try:
        uw._memo.clear()
    except Exception:
        pass
    try:
        uw.cache.clear()
    except Exception:
        try:
            uw.cache._data.clear()
        except Exception:
            pass
    bud.reset_for_tests(bud.Controller(persist=False))


def snapshot():
    with _lock:
        return {k: v for k, v in COUNTS.items()}


def total(c):
    return sum(c.values())


ET = ZoneInfo("America/New_York")
import intraday_indicators as ind
ind.market_phase = lambda *a, **k: "rth"
import ai_trade_service as ats
UNIVERSE = list(ats.UNIVERSE)

# the equity snapshots the boards score from, seeded for every name (the earnings warmer's job, not the boards')
names = sorted(set(cfg.SIGNAL_DATA_TYPE) | {"oi_positioning"})
EQ = {"params": [{"name": n, "bias": 0.6} for n in ("eps_estimates", "revenue_estimates", "guidance", "price_action",
                                                      "options_positioning", "unusual_options", "disparity", "dark_pool",
                                                      "peer_readthrough", "sector_market")],
      "signals": {n: 0.6 for n in names}}
try:
    import intraday_feed as feed
    FAST = {n: 0.6 for n in feed.FAST_SIGNALS}
except Exception:
    feed, FAST = None, {}


def seed_inputs():
    now = time.time()
    for s in UNIVERSE:
        swr._values[f"earn:equity:{s}"] = (now, dict(EQ))
        if feed is not None:
            swr._values[feed.KEY.format(s)] = (now, {"symbol": s, "signals": dict(FAST), "ts": now - 30})


results = {"label": LABEL, "universe": len(UNIVERSE), "features": {}}


def measure(name, fn, cadence_s=None, note=""):
    reset()
    seed_inputs()
    t0 = time.time()
    err = None
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"[:200]
    c = snapshot()
    by_path = {}
    for (p, m, path), n in c.items():
        by_path[f"{p} {m} {path}"] = by_path.get(f"{p} {m} {path}", 0) + n
    per_min = (total(c) * 60.0 / cadence_s) if cadence_s else None
    results["features"][name] = {"calls_per_run": total(c), "cadence_s": cadence_s,
                                 "calls_per_min_rth": round(per_min, 1) if per_min is not None else None,
                                 "seconds": round(time.time() - t0, 1), "error": err, "note": note, "by_path": by_path}
    print(f"{name:<28} calls/run {total(c):>5}  per-min {per_min if per_min is None else round(per_min, 1)}  {('ERR ' + err) if err else ''}")


# ---------------------------------------------------------------- features ---
import demo_trade_service as dts
try:
    import intraday_signal_v2 as eng
    KICKS = {"n": 0}
    eng.scan_once = lambda *a, **k: KICKS.__setitem__("n", KICKS["n"] + 1)     # the boards' "kick" is counted, not run
except Exception:
    eng, KICKS = None, {"n": 0}

BOARDS = {}


def boards_build():
    for key, (label, spec) in dts.MODELS.items():
        b = dts._board(spec)
        BOARDS[key] = dts._row_map(b) if b else {}


measure("boards (all models, 1 build)", boards_build, cadence_s=60,
        note=f"{len(dts.MODELS)} boards rebuilt by the tracker every 60 s; kicks of scan_once: see kicks")
results["features"]["boards (all models, 1 build)"]["kicks_of_engine_scan"] = KICKS["n"]


def tracker_tick():
    dts._board = lambda spec: None              # boards measured above; this isolates the tracker's own requests
    dts._et_now = lambda: datetime.now(ET).replace(hour=10, minute=30, second=0)
    try:
        dts._entry_allowed = lambda *a, **k: True
    except Exception:
        pass
    try:
        dts.ensure_schema()
    except Exception:
        pass
    import intraday_safety
    intraday_safety.tradable = lambda r: r.get("signal") in ("BUY", "SELL") and not r.get("wait")
    saved = {k: v for k, v in BOARDS.items()}
    dts._board = lambda spec: None
    # feed the tracker the boards computed in the previous step without re-building them
    import types
    dts._row_map = lambda b: b
    dts._board = lambda spec: saved.get(next(k for k, v in dts.MODELS.items() if v[1] == spec), {})
    dts.tick(force=True)


measure("tracker tick (quotes only)", tracker_tick, cadence_s=60, note="entries priced at a live quote (after) / board price (before)")


def engine_scan():
    import intraday_model as im
    for s in UNIVERSE:
        try:
            im.score(s)
        except Exception:
            pass


measure("live engine: 38 names cold", engine_scan, cadence_s=300, note="tiered rescore ~5 min per name (unchanged by this change)")


def ai_trade_pass():
    if not hasattr(ats, "get_board"):
        raise RuntimeError("v1 AI-Trade board removed")
    ats.BOARD_BUDGET = 120.0
    ats.get_board(refresh=True)


measure("v1 AI-Trade board pass", ai_trade_pass, cadence_s=45, note="45 s pause between passes 04:00-20:00 ET (removed)")


def premarket():
    import intraday_premarket as pm
    pm.board()


measure("pre-market board", premarket, cadence_s=300, note="pm:score cached 300 s; pre-market only")


def pulse():
    import market_pulse_service as mp
    mp.warm()


measure("market pulse warm", pulse, cadence_s=180, note="every 180 s in session")


def index_quotes():
    for s in ("SPX", "NDX", "VIX", "SPX", "NDX", "VIX"):
        uw.get_quote(s)


measure("index quotes SPX/NDX/VIX x2", index_quotes, cadence_s=60, note="index strip polls once a minute; 404s never cached before")

rth_total = sum((f["calls_per_min_rth"] or 0) for f in results["features"].values()
                if f["calls_per_min_rth"] is not None and "pre-market" not in f["note"])
results["rth_calls_per_min_total_excluding_premarket"] = round(rth_total, 1)
print("TOTAL per-min (RTH, excl. pre-market board):", round(rth_total, 1))
with open(OUT, "w", encoding="utf-8") as fh:
    json.dump(results, fh, indent=1, default=str)
print("written", OUT)
