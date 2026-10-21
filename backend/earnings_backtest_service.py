"""Historical earnings BACKTEST (Part 3) -- SIMULATED, clearly separated.

What this does, and does NOT do:

* It reconstructs, point-in-time (only data dated strictly BEFORE each past
  earnings announcement), the subset of model inputs that Unusual Whales
  actually retains historically: daily price action (EMA/RSI/trend from candles
  up to T-1), the company's prior earnings-beat history and past reactions, and
  options flow / OI / unusual activity queried at T-1 via the date-parameter
  endpoints.
* It then runs the EXISTING model scorers UNMODIFIED on those partial inputs.
  The scorers already treat an absent input as neutral and report a coverage %.
* Inputs that UW does NOT retain point-in-time -- IV rank (only ~5 days of
  history), the option-chain structure (expected move, disparity, key levels,
  max pain), peer read-through, guidance, dark pool, news -- are left ABSENT
  (never back-filled with today's values), and listed as "missing factors".

Because coverage is partial, these results are SIMULATED and are stored in a
separate table (`model_backtests`). They are NEVER mixed with the genuine
recorded-prediction accuracy (`model_predictions`). No model formula, weight or
parameter is touched; the input-signal builders in earnings_equity_service are
not used (they are current-only), so nothing live is affected.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import claude_last_service as cl

SOURCE = "Earnings Backtest (SIMULATED, partial inputs)"

# The 10 live earnings models, run exactly as on the All Models page.
import grok_service as gk
import meta_service as mt
import chatgpt_service as cg
import kimi_service as ki
import gemini_service as gm
import claude_pro_service as cp

_SCORERS = {
    "claude_new": lambda s, eq, tr: cl.score_one(s, eq, tr, {}),
    "claude_new_pz": lambda s, eq, tr: cl.score_one(s, eq, tr, {}),
    "grok": lambda s, eq, tr: gk.score_one(s, eq, tr, {}, with_analyst=False),
    "meta": lambda s, eq, tr: mt.score_one(s, eq, tr, {}),
    "chatgpt": lambda s, eq, tr: cg.score_one(s, eq, tr, {}, with_analyst=False),
    "kimi": lambda s, eq, tr: ki.score_one(s, eq, tr, {}, with_analyst=False, mkt=None),
    "gemini": lambda s, eq, tr: gm.score_one(s, eq, tr, {}),
    "claude_pro": lambda s, eq, tr: cp.score_one(s, eq, tr, {}, with_analyst=False, mkt=None),
}
# Claude +0 / Claude +- derive from the equity score/decision, which we do not
# reconstruct here, so they are not backtested (shown as "no backtest").

# Inputs we can reconstruct point-in-time, and the ones we cannot.
_PRESENT_FACTORS = ["price_action", "ema_trend", "rsi", "earnings_results",
                    "hist_reaction", "options_flow", "unusual_activity",
                    "oi_positioning"]
_MISSING_FACTORS = ["implied_volatility (IV rank)", "expected_move", "disparity",
                    "key_levels", "oi_concentration/gamma", "peer_readthrough",
                    "guidance", "dark_pool", "event_radar/news",
                    "eps/revenue revisions", "short_interest"]

_FLAT_PCT = 0.5
_ready = False


def _ensure_table() -> bool:
    global _ready
    if _ready:
        return True
    try:
        from sqlalchemy import text
        from database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS model_backtests ("
                "  id            BIGSERIAL PRIMARY KEY,"
                "  model         VARCHAR(24) NOT NULL,"
                "  symbol        VARCHAR(20) NOT NULL,"
                "  earnings_date DATE        NOT NULL,"
                "  signal        VARCHAR(10),"
                "  score         DOUBLE PRECISION,"
                "  coverage_pct  DOUBLE PRECISION,"
                "  actual_move_pct  DOUBLE PRECISION,"
                "  actual_direction VARCHAR(10),"
                "  correct       SMALLINT,"
                "  tested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),"
                "  UNIQUE (model, symbol, earnings_date)"
                ")"))
            db.execute(text("CREATE INDEX IF NOT EXISTS ix_backtest_model "
                            "ON model_backtests(model)"))
            db.commit()
            _ready = True
            return True
        except Exception:  # noqa: BLE001
            db.rollback(); return False
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return False


def _direction(mv) -> Optional[str]:
    try:
        v = float(mv)
    except (TypeError, ValueError):
        return None
    return "BUY" if v > _FLAT_PCT else "SELL" if v < -_FLAT_PCT else "NEUTRAL"


def _ema(vals: list[float], span: int) -> Optional[float]:
    if len(vals) < span:
        return None
    k = 2 / (span + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def _rsi(closes: list[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(-period, 0):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0.0); losses += max(-ch, 0.0)
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100 - 100 / (1 + rs)


def _price_biases(closes: list[float]) -> dict:
    """point-in-time price_action / ema_trend / rsi biases from closes up to T-1."""
    out = {"price_action": None, "ema_trend": None, "rsi": None}
    if len(closes) < 20:
        return out
    last = closes[-1]
    e20, e50, e200 = _ema(closes, 20), _ema(closes, 50), _ema(closes, 200)
    aligned = [1 if (e is not None and last > e) else -1 if e is not None else 0
               for e in (e20, e50, e200)]
    nz = [a for a in aligned if a != 0]
    out["ema_trend"] = cl._clamp(sum(nz) / len(nz)) if nz else None
    r = _rsi(closes)
    if r is not None:
        out["rsi"] = cl._clamp((r - 50) / 30.0)
    if len(closes) >= 6:
        chg = (last - closes[-6]) / closes[-6] if closes[-6] else 0.0
        out["price_action"] = cl._clamp(chg / 0.05)   # ~+/-5% over a week -> +/-1
    return out


def _options_lean(symbol: str, asof: str) -> dict:
    """Bullish/bearish options lean from UW data dated AT `asof` (T-1).
    Uses unusual activity (call vs put premium) + OI change build."""
    out = {"options_flow": None, "unusual_activity": None, "oi_positioning": None}
    try:
        import unusualwhales_service as uw
        rows = uw._rows(uw.get("/api/option-activity/unusual",
                               {"limit": 100, "ticker_symbol": symbol,
                                "date": asof, "unusual": "true"}))
        callp = putp = 0.0
        for r in rows:
            prem = cl._f(r.get("total_premium")) or cl._f(r.get("premium")) or 0.0
            typ = (r.get("type") or r.get("option_type") or "").lower()
            if "call" in typ:
                callp += prem
            elif "put" in typ:
                putp += prem
        if callp + putp > 0:
            lean = cl._clamp((callp - putp) / (callp + putp))
            out["options_flow"] = lean
            out["unusual_activity"] = lean
    except Exception:  # noqa: BLE001
        pass
    try:
        import unusualwhales_service as uw
        oi = uw._rows(uw.oi_change(symbol, limit=50, date=asof))
        cc = pc = 0
        for r in oi:
            osym = (r.get("option_symbol") or "")
            # OCC symbol: ...C/P before strike. crude call/put detect.
            if "C" in osym[-9:-8] or "call" in str(r).lower():
                cc += 1
            elif "P" in osym[-9:-8]:
                pc += 1
        if cc + pc > 0:
            out["oi_positioning"] = cl._clamp((cc - pc) / (cc + pc))
    except Exception:  # noqa: BLE001
        pass
    return out


def _build_hist_eq(symbol: str, edate: date) -> Optional[tuple[dict, dict, float]]:
    """Reconstruct a point-in-time eq/trade for `symbol` as of the day before
    `edate`. Returns (eq_hist, trade_hist, actual_move_pct) or None if the real
    outcome cannot be verified. Only data dated < edate is used."""
    import earnings_trade_service as ets
    import unusualwhales_service as uw

    # Real post-earnings move for THIS event (the outcome we grade against).
    moves = ets._historical_moves(symbol) or []
    by_date = {str(m.get("date"))[:10]: m.get("move_pct") for m in moves if m.get("date")}
    key = edate.isoformat()
    mv = by_date.get(key)
    if mv is None:
        best, gap0 = None, 6
        for dd in by_date:
            try:
                g = abs((date.fromisoformat(dd) - edate).days)
            except Exception:  # noqa: BLE001
                continue
            if g <= 5 and g < gap0:
                best, gap0 = dd, g
        if best:
            mv = by_date[best]
    if mv is None:
        return None   # cannot verify outcome -> skip (never guess)

    # Past reactions available BEFORE this event (look-ahead safe).
    past = [m for m in moves if str(m.get("date"))[:10] < key]

    # Point-in-time daily closes up to the day before the announcement.
    closes: list[float] = []
    try:
        bars = uw._rows(uw.candles(symbol, "1d", limit=2500))
        rows = sorted(((str(b.get("date"))[:10], cl._f(b.get("close")))
                       for b in bars if b.get("date") and cl._f(b.get("close")) is not None),
                      key=lambda x: x[0])
        closes = [c for d, c in rows if d < key]
    except Exception:  # noqa: BLE001
        closes = []
    px = _price_biases(closes)

    # Earnings beat history available before this event.
    er_bias = None
    try:
        eh = uw._rows(uw.earnings_history(symbol))
        prior = [r for r in eh if str(r.get("report_date"))[:10] < key
                 and r.get("reported_eps") not in (None, "")]
        beats = 0; tot = 0
        for r in prior[:8]:
            est = cl._f(r.get("estimated_eps")); act = cl._f(r.get("reported_eps"))
            if est is not None and act is not None:
                tot += 1; beats += 1 if act >= est else 0
        if tot:
            er_bias = cl._clamp((beats / tot - 0.5) * 2)
    except Exception:  # noqa: BLE001
        pass

    asof = (edate - timedelta(days=1)).isoformat()
    opt = _options_lean(symbol, asof)

    signals = {
        "price_action": px["price_action"], "ema_trend": px["ema_trend"],
        "rsi": px["rsi"], "options_flow": opt["options_flow"],
        "unusual_activity": opt["unusual_activity"],
        "oi_positioning": opt["oi_positioning"],
        "earnings_results": er_bias,
    }
    params = [
        {"name": "price_action", "bias": px["price_action"]},
        {"name": "options_positioning", "bias": opt["options_flow"]},
        {"name": "unusual_options", "bias": opt["unusual_activity"]},
        {"name": "eps_estimates", "bias": None},
        {"name": "revenue_estimates", "bias": None},
        {"name": "guidance", "bias": er_bias},
        {"name": "peer_readthrough", "bias": None},
        {"name": "dark_pool", "bias": None},
        {"name": "disparity", "bias": None},
    ]
    eq_hist = {"params": params, "signals": signals,
               "short_interest": None, "score": None, "decision": None}
    trade_hist = {"historical_moves": past, "spark": None,
                  "expected_move": {"percent": None}}
    return eq_hist, trade_hist, float(mv)


def run(symbols: Optional[list[str]] = None, months: int = 12,
        max_events: int = 400) -> dict:
    """Backtest the reconstructable models over the last `months` of earnings for
    `symbols` (default: the liquid universe). SIMULATED; writes model_backtests."""
    if not _ensure_table():
        return {"status": "NO_TABLE"}
    if not symbols:
        try:
            import ai_trade_service as ats
            symbols = list(getattr(ats, "UNIVERSE", []))
        except Exception:  # noqa: BLE001
            symbols = []
    syms = [s.upper() for s in symbols if s]
    cutoff = date.today() - timedelta(days=months * 31)
    today = date.today()

    import unusualwhales_service as uw
    from sqlalchemy import text
    from database import SessionLocal

    events = 0
    written = 0
    db = SessionLocal()
    try:
        for sym in syms:
            if events >= max_events:
                break
            # Past earnings dates in the window (dated, already reported).
            try:
                eh = uw._rows(uw.earnings_history(sym))
            except Exception:  # noqa: BLE001
                eh = []
            edates = sorted({str(r.get("report_date"))[:10] for r in eh
                             if r.get("report_date")
                             and cutoff.isoformat() <= str(r.get("report_date"))[:10] < today.isoformat()})
            for ed in edates:
                if events >= max_events:
                    break
                try:
                    edate = date.fromisoformat(ed)
                except Exception:  # noqa: BLE001
                    continue
                built = _build_hist_eq(sym, edate)
                if not built:
                    continue
                eq_hist, trade_hist, mv = built
                adir = _direction(mv)
                events += 1
                for model, scorer in _SCORERS.items():
                    try:
                        res = scorer(sym, eq_hist, trade_hist)
                        sig = res.get("raw_signal") or res.get("signal")
                        score = res.get("score")
                        cov = res.get("coverage")
                    except Exception:  # noqa: BLE001
                        sig = score = cov = None
                    correct = 1 if (sig in ("BUY", "SELL") and sig == adir) else (
                        0 if sig in ("BUY", "SELL") else None)
                    db.execute(text(
                        "INSERT INTO model_backtests "
                        "(model, symbol, earnings_date, signal, score, coverage_pct, "
                        " actual_move_pct, actual_direction, correct) "
                        "VALUES (:m,:s,:d,:sig,:sc,:cov,:mv,:ad,:c) "
                        "ON CONFLICT (model, symbol, earnings_date) DO UPDATE SET "
                        "  signal=:sig, score=:sc, coverage_pct=:cov, "
                        "  actual_move_pct=:mv, actual_direction=:ad, correct=:c, "
                        "  tested_at=now()"),
                        {"m": model, "s": sym, "d": edate, "sig": sig,
                         "sc": score, "cov": cov, "mv": mv, "ad": adir, "c": correct})
                    written += 1
                db.commit()
        return {"status": "OK", "events": events, "rows": written,
                "symbols": len(syms), "months": months}
    except Exception as e:  # noqa: BLE001
        db.rollback()
        return {"status": "ERROR", "detail": str(e)[:200], "events": events}
    finally:
        db.close()


def scoreboard() -> dict:
    """Per-model SIMULATED directional accuracy + coverage + missing factors."""
    if not _ensure_table():
        return {"status": "NO_TABLE", "source": SOURCE, "models": []}
    from sqlalchemy import text
    from database import SessionLocal
    try:
        import model_prediction_service as mp
        labels = dict(mp._model_labels())
    except Exception:  # noqa: BLE001
        labels = {}
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT model, "
            "  COUNT(*) AS n_events, "
            "  COUNT(*) FILTER (WHERE signal IN ('BUY','SELL')) AS dir_n, "
            "  COUNT(*) FILTER (WHERE signal IN ('BUY','SELL') AND correct=1) AS dir_ok, "
            "  COUNT(*) FILTER (WHERE signal='NEUTRAL') AS neu_n, "
            "  AVG(actual_move_pct) AS avg_move, "
            "  AVG(coverage_pct) AS avg_cov "
            "FROM model_backtests GROUP BY model")).all()
    except Exception:  # noqa: BLE001
        return {"status": "ERROR", "source": SOURCE, "models": []}
    finally:
        db.close()

    out = []
    tested_events = 0
    for r in rows:
        dir_acc = round(r.dir_ok / r.dir_n * 100, 1) if r.dir_n else None
        out.append({
            "model": r.model, "label": labels.get(r.model, r.model),
            "events_tested": r.n_events,
            "directional_n": r.dir_n, "directional_correct": r.dir_ok,
            "directional_accuracy": dir_acc,
            "neutral_n": r.neu_n,
            "avg_move_pct": round(float(r.avg_move), 2) if r.avg_move is not None else None,
            "coverage_pct": round(float(r.avg_cov), 1) if r.avg_cov is not None else None,
        })
        tested_events = max(tested_events, r.n_events)
    # Faithfulness verdict (per the owner's requirement). A faithful backtest
    # would need EVERY input the live model weights to be reconstructable
    # point-in-time. IV rank (~5 days of history) and the whole option-chain
    # structure (expected move, disparity, key levels, max pain, gamma/OI) are
    # NOT retained historically by UW, so no model can be faithfully reproduced.
    # Every result here is therefore a PARTIAL-FACTOR SIMULATION, never the
    # model's genuine historical accuracy.
    for m in out:
        m["reconstruction"] = "partial-factor"
        m["faithful"] = False
    full_available = False
    return {"status": "OK", "source": SOURCE, "label": "SIMULATED",
            "full_backtest_available": full_available,
            "unavailable_reason": ("IV rank and the historical option chain "
                                   "(expected move, disparity, key levels, max "
                                   "pain, gamma/OI) are not retained point-in-time "
                                   "by Unusual Whales, so the original models "
                                   "cannot be faithfully reproduced."),
            "models": out, "events_tested": tested_events,
            "present_factors": _PRESENT_FACTORS, "missing_factors": _MISSING_FACTORS,
            "note": ("PARTIAL-FACTOR SIMULATION. The unmodified scorers run on a "
                     "separate point-in-time adapter, but key inputs are missing, "
                     "so this is NOT the models' historical accuracy and is not "
                     "comparable to live predictions.")}
