"""Intraday paper-trading workflow v2: sessions, validated entries, evidence, and the end-of-day close.

Switched on by ``tab_settings.INTRADAY_WORKFLOW["mode"] == "v2"`` (default "legacy" = the release behaviour, untouched). It is
called from ``demo_trade_service.tick`` and replaces only the tracker's decisions; the six strategies' scores, weights,
formulas and their BUY >= 55 / SELL <= 45 calls are read as they are and never altered.

One tick (every 60 s, and at the closing bell):

  1. EXITS first, in their own transaction: stop / target / score exit (the existing rules, unchanged) and, at the official
     close of the position's trading day, MARKET_CLOSE. A position never survives its trading day.
  2. RECONCILE closes that had no valid price (status PENDING_RECON, never a fabricated price).
  3. ENTRIES, only in PREMARKET (04:00-09:30 ET) and REGULAR (09:30-close) and strictly before the official close:
     every BUY / SELL becomes a SIGNAL EVENT (one per strategy, symbol, trading day, session, direction); it is validated
     (data, persistence, duplicate, risk, entry lock, quote, liquidity, candles, levels) and either traded or REJECTED with a
     stored reason. Every observed piece of evidence is stored as a snapshot.

Nothing here connects to a broker. These are paper trades.
"""
from __future__ import annotations

import json
import logging
import time as _time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

import intraday_execution as ex
import market_calendar as mc
import tab_settings as cfg

log = logging.getLogger(__name__)
ET = mc.ET
MARKET_CLOSE, PENDING_RECON = "MARKET_CLOSE", "PENDING_RECON"
_RECON_RETRY_S = 600.0
_last_recon_try: dict = {}


def active() -> bool:
    return str(cfg.INTRADAY_WORKFLOW.get("mode", "legacy")).lower() == "v2"


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))


def _js(obj: Any) -> str:
    return json.dumps(obj, default=str, sort_keys=True)


def last_close_before(now: datetime) -> datetime:
    """The most recent regular-session close at or before ``now``."""
    et = now.astimezone(ET)
    d = et.date()
    if mc.is_trading_day(d) and et >= mc.close_datetime(d):
        return mc.close_datetime(d)
    d -= timedelta(days=1)
    while not mc.is_trading_day(d):
        d -= timedelta(days=1)
    return mc.close_datetime(d)


# ------------------------------------------------------------------------------------------------------ schema ---------
_NEW_COLUMNS = {      # DemoTrade columns added by this workflow: name -> SQL type (nullable, so history is untouched)
    "entry_session": "VARCHAR(12)", "signal_event_id": "INTEGER", "snapshot_id": "VARCHAR(40)", "entry_quote": "TEXT",
    "risk_checks": "TEXT", "validation": "TEXT", "exit_price_source": "VARCHAR(24)", "fill_assumption": "VARCHAR(160)",
}


def migrate(engine=None) -> dict:
    """ADDITIVE, idempotent schema change. Never deletes or rewrites a row.

      * the three new tables are created if missing;
      * the nullable evidence columns are added to demo_trades;
      * the old one-per-day unique constraint is replaced by 'one per day and entry session' (old rows count as session '')
        and a partial unique index allows at most one OPEN position per strategy and symbol.

    Returns what it did. Safe to run on every start."""
    from sqlalchemy import inspect, text
    import models_demo_trades as m
    if engine is None:
        from database import engine as eng
        engine = eng
    done: dict = {"columns": [], "tables": [], "indexes": [], "constraint_dropped": False, "skipped": []}
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    for t in (m.DemoTrade, m.IntradaySnapshot, m.IntradaySignalEvent, m.IntradayDataException):
        if t.__tablename__ not in existing:
            try:
                t.__table__.create(bind=engine, checkfirst=True)
                done["tables"].append(t.__tablename__)
            except Exception as exc:  # noqa: BLE001 - e.g. the development table-isolation twin already exists
                done["skipped"].append(f"{t.__tablename__}: {type(exc).__name__}")
    insp = inspect(engine)
    have = {c["name"] for c in insp.get_columns("demo_trades")} if "demo_trades" in set(insp.get_table_names()) else set()
    dialect = engine.dialect.name
    with engine.begin() as conn:
        for col, typ in _NEW_COLUMNS.items():
            if have and col not in have:
                conn.execute(text(f"ALTER TABLE demo_trades ADD COLUMN {col} {typ}"))
                done["columns"].append(col)
    if dialect == "postgresql":
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE demo_trades DROP CONSTRAINT IF EXISTS uq_demo_strategy_symbol_day"))
            done["constraint_dropped"] = True
    for name, ddl in (
        ("uq_demo_strategy_symbol_day_session",
         "CREATE UNIQUE INDEX IF NOT EXISTS uq_demo_strategy_symbol_day_session ON demo_trades "
         "(strategy, symbol, session_date, COALESCE(entry_session, ''))"),
        ("uq_demo_one_open",
         "CREATE UNIQUE INDEX IF NOT EXISTS uq_demo_one_open ON demo_trades (strategy, symbol) WHERE status = 'OPEN'"),
    ):
        try:
            with engine.begin() as conn:
                conn.execute(text(ddl))
            done["indexes"].append(name)
        except Exception as exc:  # noqa: BLE001 - e.g. historical duplicates among OPEN rows: report, never force
            done["skipped"].append(f"{name}: {type(exc).__name__}")
    return done


# ------------------------------------------------------------------------------------------------- provider hooks ------
# These are the ONLY places the workflow touches a provider; tests replace them.
def get_boards(models: dict) -> dict:
    """{strategy: {SYMBOL: row}} from the six boards: every bucket, the strategy's RAW signal and score, its parameters."""
    import demo_trade_service as dts
    out: dict = {}
    for strat, (_label, spec) in models.items():
        b = dts._board(spec)
        if not b:
            continue
        rows: dict = {}
        for bucket in ("buyers", "sellers", "neutral", "wait"):
            for r in b.get(bucket) or []:
                sym = (r.get("symbol") or "").upper()
                if not sym:
                    continue
                score = r.get("score") if r.get("score") is not None else r.get("score_raw")
                sig = r.get("signal_raw") or r.get("signal")
                rows[sym] = {"symbol": sym, "signal": sig, "score": score, "entry": r.get("entry"), "now": r.get("now"),
                             "sl": r.get("sl"), "tp": r.get("tp"), "price_age_s": (r.get("data") or {}).get("price_age_s"),
                             "wait": False, "params": r.get("params") or [], "low_confidence": r.get("low_confidence"),
                             "signal_meta": r.get("signal_meta"), "data": r.get("data") or {}, "gate_ts": r.get("gate_ts"),
                             "model_status": r.get("model_status"), "coverage_legacy": r.get("coverage")}
        out[strat] = rows
    return out


def get_quote(symbol: str) -> Optional[dict]:
    """{"price", "bid", "ask", "age_s"} from the provider quote (cached <= 30 s); None when there is none."""
    try:
        import unusualwhales_service as uw
        q = uw.get_quote(symbol)
        if not q:
            return None
        age = None
        try:
            age = uw.cache.age(f"uw:quote:{symbol.upper()}")
        except Exception:  # noqa: BLE001
            age = None
        return {"price": q.get("price"), "bid": q.get("bid"), "ask": q.get("ask"), "age_s": age, "source": "unusualwhales"}
    except Exception:  # noqa: BLE001
        return None


def get_bars5(symbol: str) -> list:
    try:
        import intraday_model as im
        return im.bars_meta(symbol)[0]
    except Exception:  # noqa: BLE001
        return []


def get_reading15(symbol: str) -> Optional[dict]:
    try:
        import intraday_15m
        return intraday_15m.read(symbol)
    except Exception:  # noqa: BLE001
        return None


def get_avg_volume(symbol: str, today_iso: str) -> Optional[float]:
    try:
        import intraday_model as im
        return ex.average_volume(im.daily_candles(symbol), today_iso)
    except Exception:  # noqa: BLE001
        return None


def get_final_regular_close(symbol: str, d: date) -> Optional[float]:
    """The close of the LAST regular-session 5-minute candle of ``d`` (the one ending at the official close), or None.

    Not the closing-auction print, and the daily bar is NOT used: the provider merges the extended-hours row over the regular
    one, so a daily 'close' can be an after-hours price."""
    try:
        import intraday_indicators as ind
        import unusualwhales_service as uw
        bars = uw.get_intraday_bars(symbol, "5m", days=5) or []
        end = mc.close_datetime(d)
        best = None
        for b in bars:
            dt = ind._et(b.get("t") or b.get("start_time") or b.get("date"))
            c = ex._num(b.get("close") if b.get("close") is not None else b.get("c"))
            if dt is not None and c and dt.date() == d and dt + timedelta(minutes=5) == end:
                best = c
        return best
    except Exception:  # noqa: BLE001
        return None


def entry_locked() -> bool:
    """The operator's entry lock. FAIL CLOSED: an unreadable lock is a locked lock."""
    try:
        import entry_lock
        return bool(entry_lock.is_locked(fresh=True))
    except Exception:  # noqa: BLE001
        return True


# ------------------------------------------------------------------------------------------------------ the tick ------
def run(now: datetime, models: dict) -> dict:
    """One workflow tick at ``now`` (aware ET). Returns a summary (used by tests and diagnostics)."""
    import database
    summary = {"closed": [], "exits": [], "reconciled": [], "pending": [], "opened": [], "rejected": [], "signals": 0}
    boards = get_boards(models)
    s = database.SessionLocal()
    try:
        try:
            _manage(s, now, boards, summary)
            _reconcile(s, now, summary)
            s.commit()
        except Exception:
            s.rollback()
            raise
        sess = mc.session_at(now)
        if sess["session"] in (ex.PREMARKET, ex.REGULAR):
            _entries(s, now, sess, boards, summary)
    finally:
        s.close()
    return summary


def should_tick(now: datetime, has_open: Callable[[], bool]) -> bool:
    """Tick through the whole entry window, around the closing bell, and for as long as anything is open or pending."""
    if mc.entries_allowed(now):
        return True
    return bool(has_open())


def sleep_seconds(now: datetime, default: float = 60.0) -> float:
    """Wake exactly at the official close (the bell is a boundary the 60-second cadence must not blur)."""
    to_close = mc.seconds_to_close(now)
    if to_close is not None and 0 < to_close < default:
        return max(1.0, to_close + 1.0)
    return default


# --------------------------------------------------------------------------------------------------- exits ------------
def _manage(s, now: datetime, boards: dict, summary: dict) -> None:
    import demo_trade_service as dts
    import paper_fills as pf
    from sqlalchemy import update
    from models_demo_trades import DemoTrade
    for t in s.query(DemoTrade).filter_by(status="OPEN").order_by(DemoTrade.id.asc()).all():
        info = boards.get(t.strategy, {}).get(t.symbol) or {}
        if mc.close_due(t.session_date, now):
            _market_close(s, t, now, info, boards, summary)
            continue
        stop, target = t.stop, t.target
        if stop is None or target is None:
            stop, target = dts._with_levels(t.side, t.entry_price, stop, target)
        price = dts._priced(t, info, boards)
        if not price.reliable:
            continue                                        # no executable price: nothing closed, nothing refreshed
        last = price.value
        profit, pct = dts._pnl(t.side, t.entry_price, last, t.shares)
        reason = dts._exit_reason(dts._Level(t, stop, target), last, info.get("score"), False)
        values = {"stop": stop, "target": target, "last_price": last, "profit": profit, "profit_pct": pct}
        if reason:
            gap = pf.observation_gap(_aware(t.updated_at) or _aware(t.entry_time), now) if reason in ("STOP", "TARGET") else False
            exit_px = pf.fill_price(t.side, reason, stop, target, last, gap)
            profit, pct = dts._pnl(t.side, t.entry_price, exit_px, t.shares)
            values.update(exit_price=exit_px, last_price=exit_px, profit=profit, profit_pct=pct, status=reason,
                          exit_time=_utc(now), exit_price_source=price.source,
                          fill_assumption=("booked at the stop/target level" if not gap and reason in ("STOP", "TARGET")
                                           else "booked at the first reliable price after a market gap" if gap
                                           else "booked at the observed reliable price"))
            summary["exits"].append((t.id, reason))
        s.execute(update(DemoTrade).where(DemoTrade.id == t.id, DemoTrade.status == "OPEN").values(**values))


def _market_close(s, t, now: datetime, info: dict, boards: dict, summary: dict) -> None:
    """Close one position at its trading day's official close, or record why it could not be priced."""
    import demo_trade_service as dts
    from sqlalchemy import update
    from models_demo_trades import DemoTrade, IntradayDataException
    grace = timedelta(seconds=float(cfg.INTRADAY_EXEC["close_grace_s"]))
    close_dt = mc.close_datetime(t.session_date) if mc.is_trading_day(t.session_date) else now
    price, source, assumption, when = None, None, None, now
    if now <= close_dt + grace:
        p = dts._priced(t, info, boards)
        if p.reliable:
            price, source = p.value, p.source
            assumption = (f"last reliable regular-session price observed within {grace.total_seconds():.0f}s of the "
                          f"official close ({close_dt.strftime('%H:%M')} ET)")
        else:
            return                                            # inside the grace window: wait for a price, stay OPEN
    if price is None:
        px = get_final_regular_close(t.symbol, t.session_date)
        if px:
            price, source, when = px, "regular_close_candle", close_dt
            assumption = "close of the final regular-session 5-minute candle (the one ending at the official close)"
    if price is not None:
        profit, pct = dts._pnl(t.side, t.entry_price, price, t.shares)
        s.execute(update(DemoTrade).where(DemoTrade.id == t.id, DemoTrade.status == "OPEN").values(
            status=MARKET_CLOSE, exit_price=price, last_price=price, profit=profit, profit_pct=pct, exit_time=_utc(when),
            exit_price_source=source, fill_assumption=assumption))
        summary["closed"].append((t.id, MARKET_CLOSE, price))
        return
    # no valid closing evidence: record it, never invent a price. The position is no longer active (no further exits,
    # no P&L, never in a win rate); it waits for reconciliation.
    s.execute(update(DemoTrade).where(DemoTrade.id == t.id, DemoTrade.status == "OPEN").values(
        status=PENDING_RECON, exit_price=None, profit=None, profit_pct=None, exit_time=_utc(close_dt),
        exit_price_source="none", fill_assumption="no valid closing price: pending reconciliation"))
    s.add(IntradayDataException(kind="CLOSE_PRICE_MISSING", trade_id=t.id, strategy=t.strategy, symbol=t.symbol,
                                trading_date=t.session_date, created_at=_utc(now),
                                detail=f"no reliable price within {grace.total_seconds():.0f}s of the close and no final "
                                       f"regular-session candle; position closed administratively, price pending"))
    summary["pending"].append(t.id)


def _reconcile(s, now: datetime, summary: dict) -> None:
    """Try again to price closes that had no valid price (not more often than every 10 minutes per trade)."""
    import demo_trade_service as dts
    from sqlalchemy import update
    from models_demo_trades import DemoTrade, IntradayDataException
    for t in s.query(DemoTrade).filter_by(status=PENDING_RECON).all():
        if _time.time() - _last_recon_try.get(t.id, 0.0) < _RECON_RETRY_S:
            continue
        _last_recon_try[t.id] = _time.time()
        px = get_final_regular_close(t.symbol, t.session_date)
        if not px:
            continue
        profit, pct = dts._pnl(t.side, t.entry_price, px, t.shares)
        s.execute(update(DemoTrade).where(DemoTrade.id == t.id, DemoTrade.status == PENDING_RECON).values(
            status=MARKET_CLOSE, exit_price=px, last_price=px, profit=profit, profit_pct=pct,
            exit_price_source="regular_close_candle",
            fill_assumption="reconciled: close of the final regular-session 5-minute candle"))
        for e in s.query(IntradayDataException).filter_by(trade_id=t.id, kind="CLOSE_PRICE_MISSING", resolved_at=None).all():
            e.resolved_at, e.resolution = _utc(now), f"reconciled at {px}"
        summary["reconciled"].append(t.id)


# ------------------------------------------------------------------------------------------------------ entries -------
def _open_positions(s) -> list:
    from models_demo_trades import DemoTrade
    return [{"strategy": t.strategy, "symbol": t.symbol, "side": t.side, "shares": t.shares,
             "price": t.last_price or t.entry_price} for t in s.query(DemoTrade).filter_by(status="OPEN").all()]


def _day_pnl(s, today: date) -> dict:
    from models_demo_trades import DemoTrade
    out: dict = {"total": 0.0}
    for t in s.query(DemoTrade).filter(DemoTrade.session_date == today).all():
        if t.profit is None or t.status == PENDING_RECON:
            continue
        out[t.strategy] = out.get(t.strategy, 0.0) + t.profit
        out["total"] += t.profit
    return out


def _snapshot_row(strategy: str, sym: str, direction: str, session: str, row: dict, now: datetime, dv: dict, fp: str,
                  signal_meta: dict):
    from models_demo_trades import IntradaySnapshot
    cov = dv["coverage"]
    return IntradaySnapshot(
        id=uuid.uuid4().hex, strategy=strategy, symbol=sym, trading_date=now.astimezone(ET).date(), session_type=session,
        observed_at=_utc(now), signal=direction, score=row.get("score"), coverage_total_pct=cov["total_pct"],
        coverage_eligible_pct=cov["eligible_pct"], agreement_pct=None if dv["agreement"] is None else round(dv["agreement"] * 100, 1),
        low_confidence=row.get("low_confidence"), fingerprint=fp,
        params=_js([{k: r[k] for k in ("name", "label", "weight", "bias", "points", "category", "reason")} for r in dv["classified"]]),
        sources=_js(signal_meta), validation=_js({"data": {k: dv[k] for k in ("ok", "code", "reasons")},
                                                  "coverage": cov, "legacy_low_confidence": row.get("low_confidence")}))


def _entries(s, now: datetime, sess: dict, boards: dict, summary: dict) -> None:
    from models_demo_trades import DemoTrade, IntradaySignalEvent
    import demo_trade_service as dts
    session, today = sess["session"], sess["trading_date"]
    prev_close = last_close_before(now)
    for strat, rows in boards.items():
        for sym, row in rows.items():
            direction = row.get("signal")
            if direction not in ("BUY", "SELL"):
                continue
            summary["signals"] += 1
            try:
                _one_candidate(s, now, sess, strat, sym, row, direction, prev_close, summary)
                s.commit()
            except Exception:  # noqa: BLE001 - one candidate must never stop the others, nor undo a committed exit
                s.rollback()
                log.exception("intraday workflow: candidate %s/%s failed", strat, sym)


def _age_adjusted_meta(row: dict, now: datetime) -> dict:
    """The per-signal ages measured when the board was built, aged forward to ``now``."""
    meta = row.get("signal_meta") or {}
    gate_ts = row.get("gate_ts")
    drift = max(0.0, now.timestamp() - float(gate_ts)) if gate_ts else 0.0
    out = {}
    for k, v in meta.items():
        v = dict(v or {})
        if ex._num(v.get("age_s")) is not None:
            v["age_s"] = float(v["age_s"]) + drift
        out[k] = v
    return out


def _one_candidate(s, now: datetime, sess: dict, strat: str, sym: str, row: dict, direction: str, prev_close: datetime,
                   summary: dict) -> None:
    from models_demo_trades import DemoTrade, IntradaySignalEvent, IntradaySnapshot
    session, today, bounds = sess["session"], sess["trading_date"], sess["bounds"]
    ev = (s.query(IntradaySignalEvent).filter_by(strategy=strat, symbol=sym, trading_date=today, session_type=session,
                                                 direction=direction).first())
    if ev and ev.eligibility_status == "TRADED":
        return
    meta_now = _age_adjusted_meta(row, now)
    eng_age = (row.get("data") or {}).get("signals_age_s")
    engine_ok = (eng_age is not None and float(eng_age) <= float(cfg.INTRADAY_FRESHNESS["engine_score"][1])) \
        if ex.data_path(session) == "live" else True
    dv = ex.evaluate_data(strat, direction, session, dict(row, signal_meta=meta_now), now=now, bounds=bounds,
                          prev_close=prev_close, engine_ok=engine_ok)
    meta_fp = dict(meta_now)                                    # the live engine's own build time is evidence too (it has no per-signal ages)
    if eng_age is not None:
        gts = row.get("gate_ts")
        meta_fp["@engine"] = {"age_s": float(eng_age) + (max(0.0, now.timestamp() - float(gts)) if gts else 0.0)}
    fp = ex.fingerprint(dv["classified"], meta_fp, now)

    # ---- the signal event and its evidence (always recorded, whatever the verdict) ----
    new_evidence = False
    if ev is None:
        snap = _snapshot_row(strat, sym, direction, session, row, now, dv, fp, meta_now)
        s.add(snap)
        s.flush()
        ev = IntradaySignalEvent(strategy=strat, symbol=sym, trading_date=today, session_type=session, direction=direction,
                                 score=row.get("score"), signal_ts=_utc(now), last_seen_ts=_utc(now), snapshot_id=snap.id,
                                 last_snapshot_id=snap.id, observations=1, evidence_ids=_js([{"ts": now.timestamp(), "ev": fp}]),
                                 eligibility_status="PENDING")
        s.add(ev)
        s.flush()
        new_evidence = True
    else:
        evid = json.loads(ev.evidence_ids or "[]")
        ev.observations += 1
        ev.last_seen_ts = _utc(now)
        if not evid or evid[-1]["ev"] != fp:
            snap = _snapshot_row(strat, sym, direction, session, row, now, dv, fp, meta_now)
            s.add(snap)
            s.flush()
            ev.last_snapshot_id = snap.id
            evid.append({"ts": now.timestamp(), "ev": fp})
            ev.evidence_ids = _js(evid[-20:])
            new_evidence = True
    snapshot_id = ev.last_snapshot_id

    def reject(code: str, reasons: list, stage: dict) -> None:
        hist = json.loads(ev.reject_reasons or "[]")
        entry = {"code": code, "reasons": reasons}
        if not hist or {k: hist[-1].get(k) for k in ("code", "reasons")} != entry:
            hist.append(dict(entry, ts=now.timestamp(), stage=stage))
        ev.reject_reasons = _js(hist[-10:])
        ev.validation_status, ev.eligibility_status = code, "REJECTED"
        summary["rejected"].append((strat, sym, session, direction, code))

    stages: dict = {"data": {k: dv[k] for k in ("ok", "code", "reasons")}, "coverage": dv["coverage"], "agreement": dv["agreement"]}

    # 1. instrument
    import market_calendar
    if market_calendar.instrument_kind(sym) != "equity":
        return reject(ex.WAIT_INSTRUMENT, [f"{sym} is not a US stock / ETF"], stages)
    # 2. duplicate protection (cheap, database only, before any provider call): a signal never closes, flips or doubles a position
    positions = _open_positions(s)
    from models_demo_trades import DemoTrade
    session_exists = s.query(DemoTrade.id).filter_by(strategy=strat, symbol=sym, session_date=today,
                                                     entry_session=session).first() is not None
    held = [p for p in positions if p["strategy"] == strat and p["symbol"] == sym]
    if held or session_exists:
        why = (f"this strategy already holds {sym} {held[0]['side']}; the {direction} signal is recorded and the position is left "
               f"unchanged" if held else f"this strategy already traded {sym} in the {session.lower()} session today")
        return reject(ex.WAIT_DUPLICATE, [why], stages)
    # 2b. data validity (freshness, eligible coverage, valid agreement)
    if not dv["ok"]:
        return reject(dv["code"] or ex.WAIT_INSUFFICIENT, dv["reasons"], stages)
    # 3. persistence on distinct evidence (both directions of this strategy/symbol/session, newest evidence decides)
    obs = []
    for e2 in s.query(IntradaySignalEvent).filter_by(strategy=strat, symbol=sym, trading_date=today, session_type=session).all():
        for x in json.loads(e2.evidence_ids or "[]"):
            obs.append({"ts": x["ts"], "evidence": x["ev"], "direction": e2.direction})
    obs.sort(key=lambda o: o["ts"])
    pv = ex.persistence(obs, direction)
    stages["persistence"] = pv
    if not pv["ok"]:
        return reject(ex.WAIT_UNCONFIRMED, pv["reasons"], stages)
    # 4. position and exposure limits (database only; before any provider call)
    ref_price = row.get("entry") or row.get("now")
    rk = ex.check_risk(strategy=strat, symbol=sym, direction=direction, price=float(ref_price or 0), open_positions=positions,
                       day_pnl=_day_pnl(s, today), session_entry_exists=session_exists)
    stages["risk_pre"] = rk
    if not rk["ok"]:
        return reject(rk["code"], rk["reasons"], stages)
    # 5. the operator's entry lock (fail closed)
    if entry_locked():
        return reject(ex.WAIT_LOCKED, ["new entries are locked by the operator, or the lock could not be read"], stages)
    # 6. an executable quote
    q = get_quote(sym)
    qv = ex.validate_quote(q, session=session, side=direction, reference_price=float(ref_price) if ref_price else None)
    stages["quote"] = {"raw": q, **{k: qv[k] for k in ("ok", "reasons", "fill_price", "spread_pct", "assumption")}}
    if not qv["ok"]:
        return reject(ex.WAIT_PRICE, qv["reasons"], stages)
    # 7. liquidity
    lv = ex.validate_liquidity(qv["fill_price"], get_avg_volume(sym, today.isoformat()))
    stages["liquidity"] = lv
    if not lv["ok"]:
        return reject(ex.WAIT_LIQUIDITY, lv["reasons"], stages)
    # 8. closed-candle confirmation (regular session only)
    r5 = r15 = None
    if session == ex.REGULAR:
        r5 = ex.candle_reading(get_bars5(sym), 300, now, today)
        r15 = get_reading15(sym)
        if r15 is None and now < bounds["regular_open"] + timedelta(minutes=30):
            r15 = {"status": "WARMUP", "detail": "first 15-minute candles not closed yet"}
    cv = ex.confirm_candles(direction, session, r5, r15, now)
    stages["confirmation"] = cv
    if not cv["ok"]:
        return reject(ex.WAIT_UNCONFIRMED, cv["reasons"], stages)
    # 9. levels must bracket the executable entry (the strategy's own stop / target, unchanged)
    import demo_trade_service as dts
    side = "LONG" if direction == "BUY" else "SHORT"
    fill = qv["fill_price"]
    sl, tp = dts._with_levels(side, fill, row.get("sl"), row.get("tp"))
    bracket = (sl < fill < tp) if side == "LONG" else (tp < fill < sl)
    stages["levels"] = {"stop": sl, "target": tp, "entry": fill, "bracket": bracket}
    if not bracket:
        return reject(ex.WAIT_PRICE, [f"stop {sl} / target {tp} do not bracket the executable entry {fill}"], stages)
    # 10. final risk check with the executable price (sizes the quantity)
    rk2 = ex.check_risk(strategy=strat, symbol=sym, direction=direction, price=fill, open_positions=positions,
                        day_pnl=_day_pnl(s, today), session_entry_exists=session_exists)
    stages["risk"] = rk2
    if not rk2["ok"]:
        return reject(rk2["code"], rk2["reasons"], stages)

    # ---- the paper trade, with its complete evidence ----
    from sqlalchemy.exc import IntegrityError
    trade = DemoTrade(strategy=strat, symbol=sym, side=side, shares=rk2["quantity"], session_date=today,
                      entry_time=_utc(now), entry_price=fill, stop=sl, target=tp, score_at_entry=row.get("score"),
                      last_price=fill, profit=0.0, profit_pct=0.0, status="OPEN", entry_session=session,
                      signal_event_id=ev.id, snapshot_id=snapshot_id, entry_quote=_js(stages["quote"]),
                      risk_checks=_js({"pre": rk, "final": rk2}), validation=_js(stages))
    try:
        with s.begin_nested():
            s.add(trade)
            s.flush()
    except IntegrityError:
        return reject(ex.WAIT_DUPLICATE, ["the database already holds this strategy / symbol / session position"], stages)
    ev.trade_id, ev.eligibility_status, ev.validation_status = trade.id, "TRADED", "PASS"
    summary["opened"].append((strat, sym, session, side, trade.id))
