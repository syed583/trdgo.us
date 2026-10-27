"""Shared harness for the intraday workflow v2 tests: a SQLite book, fake providers and board rows.

No network, no production data: every provider the workflow reads is replaced by a plain dict the test controls."""
import json
import types
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import chatgpt_intraday_service as chatgpt
import claude_pro_intraday_service as claude_pro
import demo_trade_service as dts
import intraday_execution as ex
import intraday_workflow as wf
import market_calendar as mc
import models_demo_trades as m
import tab_settings as cfg

ET = ZoneInfo("America/New_York")
STRATEGIES = ("chatgpt", "grok", "meta", "kimi", "gemini", "claude_pro")
WEIGHTS = {
    "grok": dict(cfg.INTRADAY_GROK_WEIGHTS), "kimi": dict(cfg.INTRADAY_KIMI_WEIGHTS), "meta": dict(cfg.META_INTRADAY_WEIGHTS),
    "gemini": dict(cfg.GEMINI_INTRADAY_WEIGHTS),
    "chatgpt": {k: w for k, _l, w, _s in chatgpt.PARAMS}, "claude_pro": {k: v[1] for k, v in claude_pro.BLOCKS.items()},
}


def et(y, mo, d, hh=10, mm=0, ss=0):
    return datetime(y, mo, d, hh, mm, ss, tzinfo=ET)


TUE = (2026, 10, 27)                    # an ordinary trading Tuesday (EDT)


def make_params(strategy: str, session: str, bias=1.0, overrides=None) -> list:
    """The strategy's own parameter rows for the data path of ``session`` (what its board would return)."""
    spec = ex.registry_for(strategy, session)
    out = []
    for name, weight in WEIGHTS[strategy].items():
        s = spec.get(name)
        b = (overrides or {}).get(name, bias)
        if s in (ex.UNIMPLEMENTED, ex.NOT_APPLICABLE):
            b = None
        pts = weight * 0.5 if b is None else weight * (0.5 + 0.5 * b)
        out.append({"name": name, "label": name, "max": weight, "bias": b, "points": round(pts, 2)})
    return out


def fresh_meta(strategy: str, session: str, now: datetime, age=120.0) -> dict:
    members = set()
    for spec in ex.registry_for(strategy, session).values():
        if isinstance(spec, list):
            members |= {x for x in spec if not x.startswith("@")}
    return {k: {"age_s": age, "status": "LIVE", "source": "intraday feed"} for k in members}


def row(strategy, symbol, now, signal="BUY", session=None, price=100.0, bias=None, overrides=None, meta_age=120.0, sl=None, tp=None,
        score=None, params=None, meta=None, gate_age=0.0, engine_age=30.0, model_status=None, low_confidence=False):
    session = session or mc.session_at(now)["session"]
    bias = (1.0 if signal == "BUY" else -1.0) if bias is None else bias
    params = params if params is not None else make_params(strategy, session, bias, overrides)
    if score is None:
        score = round(sum(p["points"] for p in params), 1)
    if sl is None:
        sl = price - 1.0 if signal == "BUY" else price + 1.0
    if tp is None:
        tp = price + 2.0 if signal == "BUY" else price - 2.0
    return {"symbol": symbol, "signal": signal, "score": score, "entry": price, "now": price, "sl": sl, "tp": tp,
            "price_age_s": 10.0, "wait": False, "params": params, "low_confidence": low_confidence,
            "signal_meta": meta if meta is not None else fresh_meta(strategy, session, now, meta_age),
            "data": {"price_age_s": 10, "signals_age_s": engine_age, "phase": "rth" if session == "REGULAR" else "pre"},
            "gate_ts": now.timestamp() - gate_age, "model_status": model_status, "coverage_legacy": None}


def quote(price=100.0, spread_pct=0.04, age=10.0):
    half = price * spread_pct / 200.0
    return {"price": price, "bid": round(price - half, 4), "ask": round(price + half, 4), "age_s": age, "source": "test"}


def bar(dt, o, h, l, c, v=100000):
    return {"start_time": dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), "open": o, "high": h, "low": l, "close": c, "volume": v}


def _prior(bar_s, start, step, sign):
    """The previous session's closing bars (EMA / RSI warm-up history), in the same direction as the test's trend."""
    mins = bar_s // 60
    first = et(2026, 10, 26, 16, 0) - timedelta(minutes=mins * 30)
    out, px = [], start - sign * step * 30
    for i in range(30):
        s = first + timedelta(minutes=mins * i)
        out.append(bar(s, px, px + step * (1.2 if sign > 0 else 0.2), px - step * (0.2 if sign > 0 else 1.2), px + sign * step))
        px += sign * step
    return out


def rising_bars(now, n=24, bar_s=300, start=100.0, step=0.05, warmup=False):
    """``n`` closed bars ending at the last bar boundary before ``now`` (rising: price > VWAP, EMA9 > EMA21, RSI high)."""
    mins = bar_s // 60
    end = now.replace(second=0, microsecond=0)
    end = end - timedelta(minutes=end.minute % mins)
    out, px = [], start
    for i in range(n, 0, -1):
        s = end - timedelta(minutes=mins * i)
        out.append(bar(s, px, px + step * 1.2, px - step * 0.2, px + step))
        px += step
    return _prior(bar_s, start, step, +1) + out if warmup else out


def falling_bars(now, n=24, bar_s=300, start=100.0, step=0.05, warmup=False):
    mins = bar_s // 60
    end = now.replace(second=0, microsecond=0)
    end = end - timedelta(minutes=end.minute % mins)
    out, px = [], start
    for i in range(n, 0, -1):
        s = end - timedelta(minutes=mins * i)
        out.append(bar(s, px, px + step * 0.2, px - step * 1.2, px - step))
        px -= step
    return _prior(bar_s, start, step, -1) + out if warmup else out


class Book:
    def __init__(self):
        self.engine = create_engine("sqlite://")
        m.Base.metadata.create_all(self.engine, tables=[getattr(m, n).__table__ for n in (
            "DemoTrade", "IntradaySnapshot", "IntradaySignalEvent", "IntradayDataException", "ShadowTrade", "IntradayShadowLog", "IntradaySignalAudit")])
        self.Session = sessionmaker(bind=self.engine)
        self.boards: dict = {}
        self.quotes: dict = {}
        self.bars5: dict = {}
        self.r15: dict = {}
        self.avgvol: dict = {}
        self.final_close: dict = {}
        self.locked = False

    def tick(self, now):
        return wf.run(now, dts.MODELS)

    def set_board(self, strategy, *rows):
        self.boards.setdefault(strategy, {})
        for r in rows:
            self.boards[strategy][r["symbol"]] = r

    def trades(self, **kw):
        with self.Session() as s:
            q = s.query(m.DemoTrade)
            for k, v in kw.items():
                q = q.filter(getattr(m.DemoTrade, k) == v)
            rows = q.order_by(m.DemoTrade.id).all()
            s.expunge_all()
            return rows

    def events(self, **kw):
        with self.Session() as s:
            q = s.query(m.IntradaySignalEvent)
            for k, v in kw.items():
                q = q.filter(getattr(m.IntradaySignalEvent, k) == v)
            rows = q.order_by(m.IntradaySignalEvent.id).all()
            s.expunge_all()
            return rows

    def shadow_trades(self, **kw):
        with self.Session() as s:
            q = s.query(m.ShadowTrade)
            for k, v in kw.items():
                q = q.filter(getattr(m.ShadowTrade, k) == v)
            rows = q.order_by(m.ShadowTrade.id).all()
            s.expunge_all()
            return rows

    def shadow_log(self, **kw):
        with self.Session() as s:
            q = s.query(m.IntradayShadowLog)
            for k, v in kw.items():
                q = q.filter(getattr(m.IntradayShadowLog, k) == v)
            rows = q.order_by(m.IntradayShadowLog.id).all()
            s.expunge_all()
            return rows

    def full_tick(self, now):
        """The real dts.tick() (Legacy + whatever the mode adds), with the clock and the Legacy gates emulated from this book."""
        self.now = now
        dts.tick(force=True)
        with self.Session() as s:                     # the released code stamps wall-clock time: restamp new rows with the simulated clock
            for t in s.query(m.DemoTrade).filter(m.DemoTrade.id > getattr(self, "_stamped", 0)).all():
                t.entry_time = t.updated_at = now.astimezone(timezone.utc).replace(tzinfo=None)
                self._stamped = max(getattr(self, "_stamped", 0), t.id)
            s.commit()

    def snapshots(self):
        with self.Session() as s:
            rows = s.query(m.IntradaySnapshot).all()
            s.expunge_all()
            return rows

    def exceptions(self):
        with self.Session() as s:
            rows = s.query(m.IntradayDataException).all()
            s.expunge_all()
            return rows


def make_book(monkeypatch) -> Book:
    b = Book()
    import database
    monkeypatch.setattr(database, "SessionLocal", b.Session)
    monkeypatch.setitem(cfg.INTRADAY_WORKFLOW, "mode", "v2")
    monkeypatch.setattr(dts, "ensure_schema", lambda: None)
    monkeypatch.setattr(dts, "MODELS", {k: (k, k) for k in STRATEGIES})
    monkeypatch.setattr(dts, "_quote_price", lambda sym: b.quotes.get(sym, {}).get("price") if b.quotes.get(sym) else None)
    monkeypatch.setattr(wf, "get_boards", lambda models: b.boards)
    monkeypatch.setattr(wf, "get_quote", lambda sym: b.quotes.get(sym))
    monkeypatch.setattr(wf, "get_bars5", lambda sym: b.bars5.get(sym, []))
    monkeypatch.setattr(wf, "get_reading15", lambda sym: b.r15.get(sym))
    monkeypatch.setattr(wf, "get_avg_volume", lambda sym, d: b.avgvol.get(sym, 5_000_000.0))
    monkeypatch.setattr(wf, "get_final_regular_close", lambda sym, d: b.final_close.get((sym, d)))
    monkeypatch.setattr(wf, "entry_locked", lambda: b.locked)
    wf._last_recon_try.clear()
    import intraday_shadow as sh
    sh._last.clear()
    import signal_verification
    signal_verification._last.clear()
    import intraday_safety
    b.now = None
    mp_ = monkeypatch
    mp_.setattr(dts, "_et_now", lambda: b.now)
    mp_.setattr(dts, "_board", lambda spec: legacy_board(b.boards.get(spec, {})) if spec in b.boards else None)

    def legacy_tradable(r):                       # the released gate, emulated: regular session only, 15-minute reading agrees
        sym, sig = r.get("symbol"), r.get("signal")
        if r.get("wait") or sig not in ("BUY", "SELL") or mc.session_at(b.now)["session"] != "REGULAR":
            return False
        r15 = b.r15.get(sym)
        return bool(r15 and r15.get("status") == "OK" and r15.get("bias") == (1 if sig == "BUY" else -1))
    mp_.setattr(intraday_safety, "tradable", legacy_tradable)
    mp_.setattr(dts, "_entry_allowed", lambda sym, sig, et_: legacy_tradable({"symbol": sym, "signal": sig}))
    return b


def legacy_board(rows: dict) -> dict:
    """Rows as the released freshness gate would hand them to the tracker: stale / thin rows become WAIT rows without levels."""
    out = {"buyers": [], "sellers": [], "neutral": [], "wait": []}
    for r in rows.values():
        r = dict(r)
        if r.get("wait_legacy"):
            r.update(signal_raw=r["signal"], score_raw=r["score"], signal="WAIT", score=None, wait=True, entry=None, sl=None, tp=None,
                     raw_levels={"entry": rows[r["symbol"]]["entry"], "sl": rows[r["symbol"]]["sl"], "tp": rows[r["symbol"]]["tp"]})
            out["wait"].append(r)
        else:
            out[{"BUY": "buyers", "SELL": "sellers"}.get(r["signal"], "neutral")].append(r)
    return out


@pytest.fixture
def book(monkeypatch):
    return make_book(monkeypatch)


def open_via_premarket(book, now0=None, strategy="chatgpt", symbol="NVDA", signal="BUY", price=100.0, score=None):
    """Drive two distinct observations 3 minutes apart so the pre-market entry opens. Returns (now_of_entry, trade).
    The entry score defaults to just past the cutoff (56 BUY / 44 SELL), as real entries are."""
    now0 = now0 or et(*TUE, 7, 15)
    score = (56.0 if signal == "BUY" else 44.0) if score is None else score
    book.quotes[symbol] = quote(price)
    for k in range(2):
        now = now0 + timedelta(minutes=3 * k)
        book.set_board(strategy, row(strategy, symbol, now, signal=signal, price=price, score=score))
        book.tick(now)
    t = book.trades(strategy=strategy, symbol=symbol)
    return now0 + timedelta(minutes=3), (t[0] if t else None)
