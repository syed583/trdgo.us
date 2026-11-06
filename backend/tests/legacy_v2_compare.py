"""Deterministic Legacy vs V2 replay.

Both engines are driven by the SAME recorded-in-memory snapshots (boards, quotes, 5m bars, 15m readings) built from a seeded price
model. No network, no production data, no real money. NOTE: no recorded historical market-data snapshots exist in the repository
or on the dev database, so every price/P&L number produced here is SIMULATED and says nothing about real performance.

Legacy = the released tick() of 277f30f (tradable() and the 15-minute gate are emulated from the same 15m snapshot, because the
real gate reads live caches). V2 = intraday_workflow.run() in mode "v2". Scoring, weights and the 55/45 cutoffs are not touched:
the synthetic score only decides which of BUY / SELL / NEUTRAL a board shows.

Run:  .venv/Scripts/python.exe tests/legacy_v2_compare.py [out.json]
"""
import json
import math
import random
import sys
import zlib
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import demo_trade_service as dts  # noqa: E402
import intraday_execution as ex  # noqa: E402
import intraday_safety  # noqa: E402
import intraday_workflow as wf  # noqa: E402
import market_calendar as mc  # noqa: E402
import models_demo_trades as m  # noqa: E402
import tab_settings as cfg  # noqa: E402
from wf_harness import STRATEGIES, bar, et, make_book, quote, row  # noqa: E402

DAYS = [date(2026, 10, 27), date(2026, 10, 28), date(2026, 11, 27)]        # Tue, Wed, Fri after Thanksgiving (13:00 close)
STEP_MIN = 5
SYMBOLS = ["AAPL", "NVDA", "AMD", "MSFT", "TSLA", "META", "AMZN", "NFLX", "SOFI", "ILLQ1", "WIDE1", "PENNY"]
KIND = {"ILLQ1": "illiquid", "WIDE1": "wide", "PENNY": "penny"}
UNIVERSE = {s: SYMBOLS[(i * 3) % 4: (i * 3) % 4 + 9] for i, s in enumerate(STRATEGIES)}     # each strategy watches 9 of the 12
UTC = timezone.utc


def _h(*parts) -> int:
    return zlib.crc32("|".join(str(p) for p in parts).encode())


def _base_price(sym):
    return {"PENNY": 4.2, "SOFI": 14.0, "ILLQ1": 60.0}.get(sym, 80.0 + (_h(sym) % 120))


class Market:
    """One-minute price paths 04:00-20:00 for every symbol and day, from a seeded model (the 'recorded' snapshots)."""

    def __init__(self, seed=7):
        self.seed, self.paths = seed, {}
        for sym in SYMBOLS:
            px = _base_price(sym)
            for d in DAYS:
                rng = random.Random(_h(seed, sym, d))
                px *= 1 + rng.gauss(0, 0.004)                                        # overnight gap
                drift = rng.gauss(0, 0.00012)
                path = {}
                for k in range(16 * 60 + 1):
                    t = et(d.year, d.month, d.day, 4, 0) + timedelta(minutes=k)
                    px *= 1 + drift + rng.gauss(0, 0.0007 if t.hour >= 9 else 0.0004)
                    path[t] = round(px, 4)
                self.paths[(sym, d)] = path

    def price(self, sym, now):
        now = now.replace(second=0, microsecond=0)
        d = now.date()
        p = self.paths.get((sym, d), {})
        return p.get(now) or (p[max(p)] if p and now > max(p) else None) or (p[min(p)] if p else None)

    def bars(self, sym, now, bar_s):
        """Closed-or-forming regular-session bars of today and the previous simulated day (forming ones are dropped by the rules)."""
        out = []
        for d in [x for x in DAYS if x <= now.date()][-2:]:
            path = self.paths[(sym, d)]
            b = mc.session_bounds(d)
            start, end = b["regular_open"], b["close"]
            t = start
            while t < end and t <= now:
                minutes = [path[t + timedelta(minutes=i)] for i in range(bar_s // 60) if (t + timedelta(minutes=i)) in path]
                if len(minutes) == bar_s // 60:
                    out.append(bar(t, minutes[0], max(minutes) * 1.0002, min(minutes) * 0.9998, minutes[-1]))
                t += timedelta(seconds=bar_s)
        return out


def _signal(mkt, strat, sym, now):
    """Synthetic strategy output: momentum plus a strategy-specific tilt and noise, mapped through the REAL cutoffs."""
    p0, p1 = mkt.price(sym, now - timedelta(minutes=30)), mkt.price(sym, now)
    if not p0 or not p1:
        return None
    mom = (p1 / p0 - 1) / 0.004
    rng = random.Random(_h("sig", strat, sym, now.date(), now.hour, now.minute // 30))
    z = max(-1.2, min(1.2, mom * 0.9 + rng.gauss(0, 0.2)))
    score = round(50 + 22 * z, 1)
    sig = "BUY" if score >= cfg.V3_CUTOFFS["buy"] else "SELL" if score <= cfg.V3_CUTOFFS["sell"] else "NEUTRAL"
    return sig, score, p1


def _fault(strat, sym, now):
    """Deterministic data faults per 30-minute block: 'stale' (a source is hours old) or 'thin' (few sources reported)."""
    r = _h("fault", strat, sym, now.date(), now.hour, now.minute // 30) % 100
    return "stale" if r < 4 else "thin" if r < 7 else None


def _spread(sym):
    return 0.5 if KIND.get(sym) == "wide" else 0.04


class Feed:
    """Everything an engine may read at time ``now`` (identical for both engines)."""

    def __init__(self, mkt):
        self.mkt = mkt
        self.now = None
        self.cache = {}

    def at(self, now):
        self.now = now
        self.cache = {}
        return self

    def boards(self):
        key = "b"
        if key in self.cache:
            return self.cache[key]
        out = {}
        for strat in STRATEGIES:
            rows = {}
            for sym in UNIVERSE[strat]:
                s = _signal(self.mkt, strat, sym, self.now)
                if s is None:
                    continue
                sig, score, px = s
                f = _fault(strat, sym, self.now)
                kw = {}
                if f == "stale":
                    kw["meta_age"] = 4000.0
                elif f == "thin":                                   # most parameters had no reading at all
                    names = list(ex.registry_for(strat, "PREMARKET" if mc.session_at(self.now)["session"] == "PREMARKET" else "REGULAR"))
                    kw["overrides"] = {n: None for i, n in enumerate(names) if i % 4 != 0}
                r = row(strat, sym, self.now, signal=sig, session="PREMARKET" if mc.session_at(self.now)["session"] == "PREMARKET" else "REGULAR", price=round(px, 2), score=score, **kw,
                        sl=round(px * (0.995 if sig != "SELL" else 1.005), 2), tp=round(px * (1.01 if sig != "SELL" else 0.99), 2))
                r["strategy"], r["fault"] = strat, f
                rows[sym] = r
            out[strat] = rows
        self.cache[key] = out
        return out

    def quote(self, sym):
        px = self.mkt.price(sym, self.now)
        return quote(px, _spread(sym)) if px else None

    def bars5(self, sym):
        return self.mkt.bars(sym, self.now, 300)

    def reading15(self, sym):
        if self.now.date() not in DAYS:
            return None
        return ex.candle_reading(self.mkt.bars(sym, self.now, 900), 900, self.now, self.now.date())

    def avg_volume(self, sym):
        return 200_000.0 if KIND.get(sym) == "illiquid" else 5_000_000.0

    def final_close(self, sym, d):
        b = [x for x in self.mkt.bars(sym, mc.close_datetime(d), 300) if x["start_time"]]
        return b[-1]["close"] if b else None


def _grid(d):
    t, end = et(d.year, d.month, d.day, 4, 0), et(d.year, d.month, d.day, 20, 0)
    while t <= end:
        yield t
        t += timedelta(minutes=STEP_MIN)


def _first_signals(feed_by_time):
    """Distinct (strategy, symbol, day, direction) actionable signals, counted from the boards themselves (engine independent)."""
    seen = {}
    for now, boards in feed_by_time:
        for strat, rows in boards.items():
            for sym, r in rows.items():
                if r["signal"] in ("BUY", "SELL") and mc.entries_allowed(now):
                    seen.setdefault((strat, sym, str(now.date()), r["signal"]), mc.session_at(now)["session"])
    return seen


def _emulated_entry_check(feed, sym, sig, now):
    """The released 15-minute gate, evaluated from the same 15m snapshot: regular session only, newest closed 15m candle fresh and agreeing."""
    if mc.session_at(now)["session"] != "REGULAR" or sig not in ("BUY", "SELL"):
        return False, "not regular session"
    r15 = feed.reading15(sym)
    if not r15 or r15.get("status") != "OK":
        return False, "15m warm-up / unavailable"
    if now.timestamp() - r15["last_bar_end_epoch"] > cfg.INTRADAY_15M["max_close_lag_s"]:
        return False, "15m stale"
    if r15["bias"] != (1 if sig == "BUY" else -1):
        return False, "15m not agreeing"
    return True, None


def run_engine(name: str, mkt: Market, exec_overrides: dict | None = None) -> dict:
    """Replay every day through one engine. Returns trades, per-candidate outcomes and rejection counters."""
    mp = pytest.MonkeyPatch()
    book = make_book(mp)
    feed = Feed(mkt)
    clock = {"now": None}
    legacy_reasons = defaultdict(Counter)
    mp.setitem(cfg.INTRADAY_WORKFLOW, "mode", name)
    # The simulated market holds ONE day of bars, so the real 22-bar warm-up of the 15-minute reading would only be
    # satisfied at 15:00 ET -- after the pure-intraday tracker's entry window (10:00-15:00) has shut. Production reads
    # ~4 sessions of candles. Relax the warm-up for BOTH engines equally; the comparison is about the two engines, not
    # about the warm-up rule (pinned in test_intraday_15m).
    mp.setitem(cfg.INTRADAY_15M, "min_bars", 4)
    for k, v in (exec_overrides or {}).items():
        mp.setitem(cfg.INTRADAY_EXEC, k, v)
    mp.setattr(wf, "get_boards", lambda models: feed.boards())
    mp.setattr(wf, "get_quote", feed.quote)
    mp.setattr(wf, "get_bars5", feed.bars5)
    mp.setattr(wf, "get_reading15", feed.reading15)
    mp.setattr(wf, "get_avg_volume", lambda sym, d: feed.avg_volume(sym))
    mp.setattr(wf, "get_final_regular_close", feed.final_close)
    mp.setattr(dts, "_et_now", lambda: clock["now"])
    mp.setattr(dts, "MODELS", {k: (k, k) for k in STRATEGIES})
    mp.setattr(dts, "_board", lambda spec: _legacy_board(feed.boards()[spec]) if spec in feed.boards() else None)
    mp.setattr(dts, "_quote_price", lambda sym: (feed.quote(sym) or {}).get("price"))
    if hasattr(dts, "_fresh_quote"):                  # the pure-intraday tracker's live-quote gate, fed from the same recorded quotes

        def fresh_quote(sym, now=None):
            q = feed.quote(sym) or {}
            if q.get("price") is None:
                return None, "no live quote"
            return {"price": q["price"], "bid": q.get("bid"), "ask": q.get("ask"), "spread": None, "spread_pct": None,
                    "quote_age_s": float(q.get("age_s") or 0.0), "quote_epoch": 1.0}, ""
        mp.setattr(dts, "_fresh_quote", fresh_quote)

    def legacy_tradable(r):
        sym, sig, now = r.get("symbol"), r.get("signal"), clock["now"]
        if r.get("wait") or sig not in ("BUY", "SELL"):
            return False
        ok, why = _emulated_entry_check(feed, sym, sig, now)
        if not ok:
            legacy_reasons[(r["strategy"], sym, str(now.date()), sig)][f"15m gate: {why}"] += 1
        return ok

    mp.setattr(intraday_safety, "tradable", legacy_tradable)
    mp.setattr(dts, "_entry_allowed", lambda sym, sig, et_: _emulated_entry_check(feed, sym, sig, et_)[0])
    seen_signals, last_id = [], 0

    for d in DAYS:
        for now in _grid(d):
            clock["now"] = now
            feed.at(now)
            seen_signals.append((now, feed.boards()))
            if name in ("legacy", "shadow"):
                dts.tick(force=True)
                with book.Session() as s:                                # the released code stamps wall-clock time; restamp with simulated time
                    for t in s.query(m.DemoTrade).filter(m.DemoTrade.id > last_id).all():
                        t.entry_time = now.astimezone(UTC).replace(tzinfo=None)
                        t.updated_at = t.entry_time
                        t.entry_session = None
                        last_id = max(last_id, t.id)
                    s.commit()
            else:
                book.tick(now)
    sigs = _first_signals(seen_signals)
    res = {"name": name, "signals": sigs, "book": book, "legacy_reasons": legacy_reasons, "feed": feed, "mp": mp}
    return res


def _legacy_board(rows):
    out = {"buyers": [], "sellers": [], "neutral": [], "wait": []}
    for r in rows.values():
        r = dict(r)
        faulty = r.get("fault") is not None
        if faulty:                                                       # the released freshness gate withholds stale / incomplete rows
            r["wait"], r["signal"] = True, "WAIT"
            out["wait"].append(r)
        else:
            out[{"BUY": "buyers", "SELL": "sellers"}.get(r["signal"], "neutral")].append(r)
    return out


def summarise(res: dict) -> dict:
    book, name = res["book"], res["name"]
    trades = book.trades()
    per = defaultdict(lambda: {"signals": 0, "trades": 0, "wins": 0, "pnl": 0.0, "rejects": Counter()})
    for (strat, sym, day, direction), sess in res["signals"].items():
        per[strat]["signals"] += 1
    for t in trades:
        p = per[t.strategy]
        p["trades"] += 1
        p["pnl"] += t.profit or 0.0
        p["wins"] += 1 if (t.profit or 0) > 0 else 0
    if name == "v2":
        reasons = defaultdict(Counter)
        for e in book.events():
            if e.eligibility_status != "TRADED":
                reasons[e.strategy][e.validation_status or "PENDING"] += 1
        for strat, c in reasons.items():
            per[strat]["rejects"] = c
        per_evt = {(e.strategy, e.symbol, str(e.trading_date), e.direction): e.eligibility_status for e in book.events()}
    else:
        traded = {(t.strategy, t.symbol, str(t.session_date)) for t in trades}
        for key, c in res["legacy_reasons"].items():
            strat, sym, day, dirn = key
            if (strat, sym, day) not in traded:
                per[strat]["rejects"].update({k: 1 for k in c})
        per_evt = {}
    closed = [t for t in trades if t.profit is not None and t.status != "OPEN"]
    # drawdown of cumulative realised P&L by exit time
    cum, peak, mdd = 0.0, 0.0, 0.0
    for t in sorted(closed, key=lambda x: x.exit_time or datetime.min):
        cum += t.profit
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    statuses = Counter(t.status for t in trades)
    return {"engine": name, "total_signals": len(res["signals"]), "trades": len(trades), "statuses": dict(statuses),
            "net_pnl": round(sum(t.profit or 0 for t in closed), 2), "win_rate": round(100 * sum(1 for t in closed if t.profit > 0) / len(closed), 1) if closed else None,
            "max_drawdown": round(mdd, 2), "per_strategy": {k: {**v, "pnl": round(v["pnl"], 2), "rejects": dict(v["rejects"])} for k, v in per.items()},
            "still_open_at_end": sum(1 for t in trades if t.status == "OPEN"), "pending_recon": statuses.get("PENDING_RECON", 0),
            "overnight_carried": sum(1 for t in trades if t.exit_time and t.entry_time and
                                     t.exit_time.astimezone(wf.ET).date() > t.entry_time.astimezone(wf.ET).date()) if hasattr(wf, "ET") else None,
            "sessions": dict(Counter(t.entry_session or "n/a" for t in trades)), "events": len(per_evt)}


def book_fingerprint(book):
    return [(t.strategy, t.symbol, t.side, t.entry_price, t.status, t.exit_price, t.profit, t.stop, t.target, str(t.entry_time)) for t in book.trades()]


def main(out=None):
    mkt = Market()
    out_all, prints = {}, {}
    opening = {"opening_rule": {"enabled": True, "no_entry_start": "09:30", "no_entry_end": "10:00", "require_both_after_end": True}}
    for label, name, ov in (("legacy", "legacy", None), ("shadow", "shadow", None), ("v2", "v2", None),
                            ("v2_tight_risk_sensitivity", "v2", {"max_open_total": 12, "max_open_per_strategy": 3}),
                            ("v2_opening_rule_sensitivity", "v2", opening)):
        res = run_engine(name, mkt, ov)
        out_all[label] = summarise(res)
        prints[label] = book_fingerprint(res["book"])
        if name == "shadow":
            sh_trades = res["book"].shadow_trades()
            closed = [t for t in sh_trades if t.profit is not None and t.status not in ("OPEN", "PENDING_RECON")]
            out_all[label]["shadow_book"] = {"trades": len(sh_trades), "open_at_end": sum(1 for t in sh_trades if t.status == "OPEN"),
                                             "pending_recon": sum(1 for t in sh_trades if t.status == "PENDING_RECON"),
                                             "net_pnl": round(sum(t.profit for t in closed), 2),
                                             "log_rows": len(res["book"].shadow_log()),
                                             "log_by_engine_decision": dict(Counter(f"{x.engine}/{x.decision}" for x in res["book"].shadow_log()))}
        res["mp"].undo()
    out_all["shadow"]["legacy_book_identical_to_legacy_mode"] = prints["shadow"] == prints["legacy"]
    if out:
        Path(out).write_text(json.dumps(out_all, indent=2, default=str))
    return out_all


if __name__ == "__main__":
    r = main(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps(r, indent=2, default=str))
