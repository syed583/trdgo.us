"""Phase 7 — Post-Earnings AI FORWARD recorder + shadow evaluation (ISOLATED).

Development-only, production-independent. It observes real earnings events going
FORWARD, records verified data with honest timestamps, generates experimental
BUY/SELL/WAIT signals from a SEPARATE versioned engine, stores IMMUTABLE
prediction snapshots, and shadow-simulates execution. It changes nothing in the
100-point model, thresholds, consensus or production, sends no orders, and is not
auto-scheduled (scheduling needs explicit confirmation).

Honesty rules enforced here:
  * Actual publication timestamp is only VERIFIED when the provider gives one;
    otherwise TIMESTAMP_UNVERIFIED — we never invent 08:00/16:00 ET as a real
    release time.
  * A positive EPS surprise does NOT auto-BUY; the engine needs reaction + intraday
    confirmation. Missing critical data or conflicting timeframes -> WAIT.
  * Predictions are insert-only (immutable); outcomes live in a SEPARATE grade row.
  * Revenue surprise, true 15-minute bars, extended-hours history and verified
    release timestamps are MISSING today and are recorded as such, never fabricated.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Optional

import claude_last_service as cl

SOURCE = "Post-Earnings AI — Forward Validation (SIMULATED · FORWARD-RECORDED)"
PE_MODEL_VERSION = "pe-v1"
_ready = False


# --------------------------------------------------------------------------- #
# Schema (isolated pe_* tables)                                               #
# --------------------------------------------------------------------------- #
def _ensure_tables() -> bool:
    global _ready
    if _ready:
        return True
    try:
        from sqlalchemy import text
        from database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS pe_events ("
                " id BIGSERIAL PRIMARY KEY, symbol VARCHAR(20) NOT NULL,"
                " company VARCHAR(160), scheduled_date DATE NOT NULL,"
                " timing VARCHAR(10), actual_release_ts TIMESTAMPTZ,"
                " timestamp_status VARCHAR(20) NOT NULL DEFAULT 'TIMESTAMP_UNVERIFIED',"
                " source VARCHAR(64), provider_pub_ts TIMESTAMPTZ,"
                " first_seen_ts TIMESTAMPTZ NOT NULL DEFAULT now(),"
                " eps_actual DOUBLE PRECISION, eps_consensus DOUBLE PRECISION,"
                " eps_surprise_pct DOUBLE PRECISION, rev_actual DOUBLE PRECISION,"
                " rev_consensus DOUBLE PRECISION, rev_surprise_pct DOUBLE PRECISION,"
                " guidance TEXT, UNIQUE(symbol, scheduled_date))"))
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS pe_predictions ("
                " id BIGSERIAL PRIMARY KEY, prediction_id VARCHAR(40) UNIQUE NOT NULL,"
                " event_id BIGINT, symbol VARCHAR(20) NOT NULL,"
                " signal_ts TIMESTAMPTZ NOT NULL DEFAULT now(), model_version VARCHAR(16) NOT NULL,"
                " signal VARCHAR(8) NOT NULL, hypothesis VARCHAR(32), confidence DOUBLE PRECISION,"
                " factor_readings TEXT, coverage_pct DOUBLE PRECISION, missing_factors TEXT,"
                " regime VARCHAR(24), entry_rule TEXT, stop_rule TEXT, target_rule TEXT,"
                " exit_horizon VARCHAR(24), risk_alloc DOUBLE PRECISION, reason TEXT,"
                " created_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
                " UNIQUE(event_id, model_version))"))
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS pe_grades ("
                " id BIGSERIAL PRIMARY KEY, prediction_id VARCHAR(40) NOT NULL,"
                " graded_at TIMESTAMPTZ NOT NULL DEFAULT now(), entry_price DOUBLE PRECISION,"
                " exit_price DOUBLE PRECISION, exit_reason VARCHAR(24), net_pct DOUBLE PRECISION,"
                " outcome VARCHAR(8), notes TEXT, UNIQUE(prediction_id))"))
            # Phase 8 — numeric trade levels (for future intraday grading) + scheduler state.
            for col in ("entry_spot", "atr14", "stop_price", "target_price"):
                db.execute(text(f"ALTER TABLE pe_predictions ADD COLUMN IF NOT EXISTS {col} DOUBLE PRECISION"))
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS pe_scheduler_state ("
                " key VARCHAR(40) PRIMARY KEY, status VARCHAR(16), paused SMALLINT DEFAULT 0,"
                " last_run_ts TIMESTAMPTZ, last_success_ts TIMESTAMPTZ, last_error TEXT,"
                " runs INT DEFAULT 0)"))
            db.commit()
            _ready = True
            return True
        except Exception:  # noqa: BLE001
            db.rollback(); return False
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# Experimental signal engine (pure, versioned, isolated)                      #
# --------------------------------------------------------------------------- #
def daily_proxy_net(side: str, entry: float, exit_px: float, cost_pct: float) -> float:
    """Directional net return (%) for the daily-proxy grade. BUY profits when price
    rises, SELL when it falls; costs always subtract."""
    sgn = 1.0 if side == "BUY" else -1.0
    gross = (exit_px - entry) / entry * 100.0 * sgn
    return round(gross - cost_pct, 3)


def _dir(x: Optional[float], thr: float = 0.1) -> Optional[int]:
    if x is None:
        return None
    return 1 if x > thr else -1 if x < -thr else 0


def pe_signal(readings: dict) -> dict:
    """BUY / SELL / WAIT from post-release evidence. A positive surprise never
    auto-buys: reaction + intraday confirmation is required. Missing critical
    data or conflicting 5m/15m -> WAIT. Pure + deterministic for testing."""
    missing = list(readings.get("missing") or [])
    coverage = readings.get("coverage_pct")
    reaction = readings.get("reaction")
    tf5 = readings.get("tf5") or {}
    tf15 = readings.get("tf15")
    eps = readings.get("eps_surprise_pct")

    def _wait(reason, hyp="none", conf=0.0):
        return {"signal": "WAIT", "hypothesis": hyp, "confidence": round(conf, 3),
                "reason": reason}

    # critical-data gate
    if reaction is None or coverage is None or coverage < 40:
        return _wait("Missing critical data (reaction/intraday coverage < 40%).", "missing_data")
    tf5_vals = [v for v in (tf5.get("vwap_pos"), tf5.get("ema"), tf5.get("momentum")) if v is not None]
    if not tf5_vals:
        return _wait("No 5-minute confirmation available.", "missing_data")

    tf5dir = _dir(sum(tf5_vals) / len(tf5_vals))
    tf15dir = _dir(tf15.get("trend")) if isinstance(tf15, dict) else None
    rdir = _dir(reaction)
    sdir = _dir(eps, thr=0.0) if eps is not None else 0

    # conflicting timeframes -> WAIT
    if tf15dir is not None and tf5dir not in (0, None) and tf15dir not in (0, None) and tf5dir == -tf15dir:
        return _wait("5-minute and 15-minute timeframes conflict.", "conflict",
                     conf=(coverage / 100.0) * 0.3)

    agree = 1.0 if (tf15dir in (None, tf5dir)) else 0.5
    conf = round((coverage / 100.0) * (0.5 + 0.5 * agree), 3)

    if rdir and rdir > 0 and tf5dir and tf5dir > 0 and tf15dir in (None, 1):
        return {"signal": "BUY", "hypothesis": "bullish_continuation", "confidence": conf,
                "reason": "Positive reaction confirmed by 5m (and 15m) uptrend."}
    if rdir and rdir < 0 and tf5dir and tf5dir < 0 and tf15dir in (None, -1):
        return {"signal": "SELL", "hypothesis": "bearish_continuation", "confidence": conf,
                "reason": "Negative reaction confirmed by 5m (and 15m) downtrend."}
    if rdir and rdir < 0 and tf5dir and tf5dir > 0 and sdir >= 0:
        return {"signal": "BUY", "hypothesis": "bullish_reversal", "confidence": conf,
                "reason": "Initial selloff reclaimed on 5m with non-negative surprise."}
    if rdir and rdir > 0 and tf5dir and tf5dir < 0 and sdir <= 0:
        return {"signal": "SELL", "hypothesis": "bearish_reversal", "confidence": conf,
                "reason": "Initial pop rejected on 5m with non-positive surprise."}
    return _wait("No hypothesis met (reaction/intraday not aligned).", "none",
                 conf=(coverage / 100.0) * 0.3)


def _read_live(symbol: str, eps_surprise: Optional[float]) -> dict:
    """Build the engine's readings from the LIVE 5-min intraday engine + the
    event's EPS surprise. True 15m bars are not available, so tf15 is a coarse
    EMA/rel-strength proxy, flagged MISSING. Only uses data available now."""
    missing = ["rev_surprise", "true_15m_bars", "verified_release_timestamp"]
    try:
        import intraday_model as im
        s = im.score(symbol) or {}
    except Exception:  # noqa: BLE001
        s = {}
    if (s.get("status") != "OK"):
        return {"coverage_pct": None, "reaction": None, "tf5": {}, "tf15": None,
                "eps_surprise_pct": eps_surprise, "missing": missing + ["intraday"],
                "spot": None, "atr": None, "regime": None}
    pm = {p.get("name"): p for p in (s.get("params") or [])}

    def bias(name):
        p = pm.get(name)
        if not p or not p.get("has_data"):
            return None
        mx, v = p.get("max"), p.get("value")
        return cl._clamp(v / mx) if (mx and v is not None) else None

    tf5 = {"vwap_pos": bias("vwap_pos"), "ema": bias("ema_trend"),
           "momentum": bias("rsi"), "rvol": bias("rvol")}
    tf15 = {"trend": cl._avg([bias("ema_trend"), bias("rel_strength")])}   # proxy (true 15m MISSING)
    return {"coverage_pct": s.get("coverage_pct"), "reaction": bias("gap"),
            "tf5": tf5, "tf15": tf15, "eps_surprise_pct": eps_surprise,
            "market": bias("market"), "missing": missing,
            "spot": cl._f(s.get("spot")), "atr": cl._f(s.get("atr")),
            "regime": "spy_above_vwap" if s.get("spy_above_vwap") else "spy_below_vwap"}


# --------------------------------------------------------------------------- #
# Recorder                                                                     #
# --------------------------------------------------------------------------- #
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def record_events(days: int = 10) -> dict:
    """Upsert earnings events (metadata only — never a prediction) for the recent
    window. Timestamps are UNVERIFIED (the feed has no actual release time)."""
    if not _ensure_tables():
        return {"status": "NO_TABLE"}
    import unusualwhales_service as uw
    import ai_trade_service as ats
    from sqlalchemy import text
    from database import SessionLocal

    syms = [s.upper() for s in getattr(ats, "UNIVERSE", [])]
    cutoff = (date.today().toordinal() - days)
    db = SessionLocal()
    written = 0
    try:
        for sym in syms:
            try:
                rows = uw._rows(uw.earnings_history(sym)) or []
            except Exception:  # noqa: BLE001
                rows = []
            for r in rows:
                ed = str(r.get("report_date"))[:10]
                try:
                    if date.fromisoformat(ed).toordinal() < cutoff:
                        continue
                except Exception:  # noqa: BLE001
                    continue
                timing = (r.get("report_time") or "").lower()
                tk = "AMC" if "post" in timing else "BMO" if "pre" in timing else "UNKNOWN"
                db.execute(text(
                    "INSERT INTO pe_events (symbol, scheduled_date, timing, timestamp_status, "
                    " source, provider_pub_ts, eps_actual, eps_consensus, eps_surprise_pct) "
                    "VALUES (:s,:d,:tk,'TIMESTAMP_UNVERIFIED','unusual_whales',:pub,:ea,:ec,:es) "
                    "ON CONFLICT (symbol, scheduled_date) DO UPDATE SET "
                    " timing=:tk, eps_actual=:ea, eps_consensus=:ec, eps_surprise_pct=:es"),
                    {"s": sym, "d": ed, "tk": tk,
                     "pub": r.get("updated_at"), "ea": cl._f(r.get("reported_eps")),
                     "ec": cl._f(r.get("estimated_eps")), "es": cl._f(r.get("surprise_percentage"))})
                written += 1
            db.commit()
        return {"status": "OK", "events_seen": written, "universe": len(syms),
                "note": "Metadata only. Revenue surprise MISSING; release timestamps UNVERIFIED."}
    except Exception as e:  # noqa: BLE001
        db.rollback(); return {"status": "ERROR", "detail": str(e)[:160]}
    finally:
        db.close()


def capture_signals() -> dict:
    """For reported events with EPS data and a live intraday read, generate an
    experimental signal and store an IMMUTABLE snapshot (one per event+version).
    Runs forward only; past events without live intraday produce no snapshot."""
    if not _ensure_tables():
        return {"status": "NO_TABLE"}
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    made = 0
    try:
        today = date.today()
        evs = db.execute(text(
            "SELECT e.id, e.symbol, e.eps_surprise_pct FROM pe_events e "
            "LEFT JOIN pe_predictions p ON p.event_id=e.id AND p.model_version=:v "
            "WHERE p.id IS NULL AND e.scheduled_date <= :t AND e.eps_actual IS NOT NULL "
            "ORDER BY e.scheduled_date DESC LIMIT 60"),
            {"v": PE_MODEL_VERSION, "t": today}).all()
        for eid, sym, eps in evs:
            readings = _read_live(sym, eps)
            sig = pe_signal(readings)
            pid = uuid.uuid4().hex[:16]
            atr, spot = readings.get("atr"), readings.get("spot")
            stop_px = tgt_px = None
            if spot and atr:
                s = 1 if sig["signal"] == "BUY" else -1 if sig["signal"] == "SELL" else 0
                stop_px = round(spot - s * 1.5 * atr, 2) if s else None
                tgt_px = round(spot + s * 2.0 * atr, 2) if s else None
            db.execute(text(
                "INSERT INTO pe_predictions (prediction_id,event_id,symbol,model_version,signal,"
                " hypothesis,confidence,factor_readings,coverage_pct,missing_factors,regime,"
                " entry_rule,stop_rule,target_rule,exit_horizon,risk_alloc,reason,"
                " entry_spot,atr14,stop_price,target_price) "
                "VALUES (:pid,:eid,:sym,:v,:sig,:hyp,:conf,:fr,:cov,:miss,:reg,"
                " :entry,:stop,:tgt,:hz,:alloc,:reason,:spot,:atr,:sp,:tp) "
                "ON CONFLICT (event_id, model_version) DO NOTHING"),
                {"pid": pid, "eid": eid, "sym": sym, "v": PE_MODEL_VERSION,
                 "sig": sig["signal"], "hyp": sig["hypothesis"], "conf": sig["confidence"],
                 "fr": json.dumps({k: readings.get(k) for k in ("tf5", "tf15", "reaction",
                       "eps_surprise_pct", "market", "regime")}, default=str),
                 "cov": readings.get("coverage_pct"), "miss": ",".join(readings.get("missing") or []),
                 "reg": readings.get("regime"),
                 "entry": "Next 5m close after signal (hypothetical).",
                 "stop": f"1.5 x ATR14 ({stop_px})", "tgt": f"2.0 x ATR14 ({tgt_px})",
                 "hz": "session_close", "alloc": 0.05, "reason": sig["reason"],
                 "spot": spot, "atr": atr, "sp": stop_px, "tp": tgt_px})
            made += 1
            db.commit()
        return {"status": "OK", "snapshots_made": made,
                "note": "Forward-only. WAIT is expected when no live intraday/reaction exists."}
    except Exception as e:  # noqa: BLE001
        db.rollback(); return {"status": "ERROR", "detail": str(e)[:160]}
    finally:
        db.close()


def grade() -> dict:
    """Auto-grade matured BUY/SELL predictions with a DAILY-PRICE proxy (entry at
    the reaction-session close, exit next session close, net of costs). WAIT is
    never graded. Intrabar stop/target are NOT evaluated — no historical intraday
    exists — so exit_reason is DAILY_PROXY. Insert-only into pe_grades."""
    if not _ensure_tables():
        return {"status": "NO_TABLE"}
    import historical_backtest_service as htb
    import forward_scoreboard_service as fsb
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    graded = 0
    try:
        today = date.today().isoformat()
        rows = db.execute(text(
            "SELECT p.prediction_id, p.symbol, p.signal, e.scheduled_date, e.timing "
            "FROM pe_predictions p JOIN pe_events e ON e.id=p.event_id "
            "LEFT JOIN pe_grades g ON g.prediction_id=p.prediction_id "
            "WHERE g.id IS NULL AND p.signal IN ('BUY','SELL')")).all()
        for pid, sym, side, sched, timing in rows:
            bars = htb._bars2y(sym)
            if not bars:
                continue
            dates = [str(b.get("date"))[:10] for b in bars]
            closes = [cl._f(b.get("close")) for b in bars]
            ed = str(sched)[:10]
            eidx = fsb._idx_on_or_before(dates, ed)
            if eidx is None:
                continue
            tk = fsb._timing_key(timing)
            entry_i = eidx if tk == "bmo" else eidx + 1          # reaction session
            exit_i = entry_i + 1
            if exit_i >= len(bars):
                continue                                          # not matured yet
            entry, exit_px = closes[entry_i], closes[exit_i]
            if not entry or not exit_px:
                continue
            net = daily_proxy_net(side, entry, exit_px, htb._cost_pct(side, entry, 1))
            db.execute(text(
                "INSERT INTO pe_grades (prediction_id, entry_price, exit_price, exit_reason, "
                " net_pct, outcome, notes) VALUES (:pid,:en,:ex,'DAILY_PROXY',:net,:out,:n) "
                "ON CONFLICT (prediction_id) DO NOTHING"),
                {"pid": pid, "en": round(entry, 4), "ex": round(exit_px, 4), "net": net,
                 "out": "WIN" if net > 0 else "LOSS",
                 "n": "Daily proxy (no historical intraday; intrabar stop/target not evaluated)."})
            graded += 1
            db.commit()
        return {"status": "OK", "graded": graded,
                "note": "DAILY-PROXY grading; intrabar stop/target not evaluable (no intraday history)."}
    except Exception as e:  # noqa: BLE001
        db.rollback(); return {"status": "ERROR", "detail": str(e)[:160]}
    finally:
        db.close()


def scheduler_status() -> dict:
    if not _ensure_tables():
        return {"status": "NO_TABLE"}
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        r = db.execute(text("SELECT status, paused, last_run_ts, last_success_ts, last_error, runs "
                            "FROM pe_scheduler_state WHERE key='forward'")).first()
        if not r:
            return {"activated": False, "status": "NOT_ACTIVATED", "paused": False,
                    "last_run": None, "last_success": None, "runs": 0,
                    "note": "Scheduler is built but NOT activated (awaiting approval)."}
        return {"activated": True, "status": r[0], "paused": bool(r[1]),
                "last_run": r[2].isoformat() if r[2] else None,
                "last_success": r[3].isoformat() if r[3] else None,
                "last_error": r[4], "runs": r[5]}
    finally:
        db.close()


def run_cycle(days: int = 7) -> dict:
    """One MANUAL capture cycle: record -> capture -> grade, with an in-DB lock so
    concurrent/overlapping runs do not double-ingest. Updates scheduler state. This
    is invoked on demand only; it is NOT auto-scheduled (that needs approval)."""
    if not _ensure_tables():
        return {"status": "NO_TABLE"}
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        # idempotency lock: skip if a run started < 60s ago and is RUNNING
        row = db.execute(text("SELECT status, last_run_ts, paused FROM pe_scheduler_state "
                              "WHERE key='forward'")).first()
        if row and row[2]:
            return {"status": "PAUSED"}
        db.execute(text(
            "INSERT INTO pe_scheduler_state (key,status,last_run_ts,runs) "
            "VALUES ('forward','RUNNING',now(),1) "
            "ON CONFLICT (key) DO UPDATE SET status='RUNNING', last_run_ts=now(), "
            " runs=pe_scheduler_state.runs+1"))
        db.commit()
    finally:
        db.close()
    err = None
    try:
        rec = record_events(days=days)
        cap = capture_signals()
        grd = grade()
        ok = all(x.get("status") == "OK" for x in (rec, cap, grd))
    except Exception as e:  # noqa: BLE001
        rec = cap = grd = {"status": "ERROR"}; ok = False; err = str(e)[:200]
    db = SessionLocal()
    try:
        db.execute(text("UPDATE pe_scheduler_state SET status=:s, last_error=:e, "
                        "last_success_ts=CASE WHEN :ok THEN now() ELSE last_success_ts END "
                        "WHERE key='forward'"),
                   {"s": "OK" if ok else "ERROR", "e": err, "ok": ok})
        db.commit()
    finally:
        db.close()
    return {"status": "OK" if ok else "ERROR", "record": rec, "capture": cap, "grade": grd}


def dashboard() -> dict:
    """Dev-only forward-validation summary. Pending predictions are never shown as
    successful trades; small-sample caveats are explicit."""
    if not _ensure_tables():
        return {"status": "NO_TABLE", "source": SOURCE}
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        ev = db.execute(text("SELECT COUNT(*), COUNT(*) FILTER (WHERE timestamp_status='VERIFIED'), "
                             "COUNT(*) FILTER (WHERE eps_actual IS NOT NULL) FROM pe_events")).first()
        sig = db.execute(text("SELECT signal, COUNT(*) FROM pe_predictions WHERE model_version=:v "
                              "GROUP BY signal"), {"v": PE_MODEL_VERSION}).all()
        graded = db.execute(text("SELECT COUNT(*), AVG(net_pct), "
                                 "COUNT(*) FILTER (WHERE net_pct>0) FROM pe_grades")).first()
        # BUY/SELL graded performance (join predictions)
        side_perf = db.execute(text(
            "SELECT p.signal, COUNT(*), AVG(g.net_pct), COUNT(*) FILTER (WHERE g.net_pct>0) "
            "FROM pe_grades g JOIN pe_predictions p ON p.prediction_id=g.prediction_id "
            "GROUP BY p.signal")).all()
        sigc = {s: n for s, n in sig}
        gn = graded[0] or 0
        sp = {s: {"n": n, "avg_net": round(float(a), 3) if a is not None else None,
                  "win_rate": round(w / n * 100, 1) if n else None} for s, n, a, w in side_perf}
        return {"status": "OK", "source": SOURCE, "label": "FORWARD-RECORDED · SIMULATED · NO LIVE TRADING",
                "model_version": PE_MODEL_VERSION, "as_of": _utcnow().isoformat(),
                "events_observed": ev[0] or 0, "verified_timestamps": ev[1] or 0,
                "verified_pct": round((ev[1] or 0) / ev[0] * 100, 1) if ev[0] else 0.0,
                "events_with_results": ev[2] or 0,
                "buy": sigc.get("BUY", 0), "sell": sigc.get("SELL", 0), "wait": sigc.get("WAIT", 0),
                "pending_outcomes": (sum(sigc.get(x, 0) for x in ("BUY", "SELL")) - gn),
                "completed_sim_trades": gn,
                "win_rate": round(graded[2] / gn * 100, 1) if gn else None,
                "avg_net_pct": round(float(graded[1]), 3) if graded[1] is not None else None,
                "buy_performance": sp.get("BUY"), "sell_performance": sp.get("SELL"),
                "scheduler": scheduler_status(),
                "missing": ["revenue surprise", "true 15m bars", "verified release timestamps",
                            "extended-hours history", "historical options/IV"],
                "note": ("Forward pipeline — starts near-empty and accrues as earnings report. "
                         "Pending predictions are NOT trades. Grading is a DAILY proxy (no intraday "
                         "history). No statistically supported conclusion until enough independent "
                         "events grade out.")}
    finally:
        db.close()
