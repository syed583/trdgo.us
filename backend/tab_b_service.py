"""Tab B -- Short Straddle Scanner.

Ranks upcoming-earnings names by how inflated their option premium / IV is
before the report (the bigger the expected IV crush, the better the sell). Four
banded filters (100 pts): IV Rank 40, Implied Move % 25, Implied-vs-historical
20, Liquidity 15. Action SELL >= 70 / WATCH 50-69 / SKIP, plus a bullish/
bearish/neutral tilt and hard exclusions. It does NOT predict direction.

Reuses the warmed earn:trade:default entry for the implied move %, the average
historical earnings move and the tilt; reads IV rank (uw_volatility_service)
and the ATM straddle spread (earnings_options_service) fresh, cached per symbol
as tabb:{SYM}.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import tab_settings as cfg

SOURCE = "Tab B — Straddle Scanner"

_log_ready = False


def _f(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _ensure_log_table() -> bool:
    """One-time create of the daily straddle-score log (spec: log every day's
    scores so results can be compared with the actual move later)."""
    global _log_ready
    if _log_ready:
        return True
    try:
        from sqlalchemy import text
        from database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS straddle_scan_log ("
                "  symbol        VARCHAR(20) NOT NULL,"
                "  snapshot_date DATE        NOT NULL,"
                "  rank          INTEGER,"
                "  atm_iv        DOUBLE PRECISION,"
                "  premium_pct   DOUBLE PRECISION,"
                "  status        VARCHAR(16),"
                "  PRIMARY KEY (symbol, snapshot_date)"
                ")"))
            db.commit()
            _log_ready = True
            return True
        except Exception:  # noqa: BLE001
            db.rollback()
            return False
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return False


def log_scores(rows: list[dict]) -> int:
    """Upsert today's rank/IV/premium for each computed name (idempotent per day)."""
    scored = [r for r in (rows or []) if r.get("atm_iv") is not None]
    if not scored or not _ensure_log_table():
        return 0
    try:
        from sqlalchemy import text
        from database import SessionLocal
        db = SessionLocal()
        try:
            today = date.today()
            for r in scored:
                db.execute(text(
                    "INSERT INTO straddle_scan_log "
                    "(symbol, snapshot_date, rank, atm_iv, premium_pct, status) "
                    "VALUES (:s, :d, :rk, :iv, :pp, :st) "
                    "ON CONFLICT (symbol, snapshot_date) DO UPDATE SET "
                    "rank = :rk, atm_iv = :iv, premium_pct = :pp, status = :st"),
                    {"s": r["symbol"].upper(), "d": today, "rk": r.get("rank"),
                     "iv": r.get("atm_iv"), "pp": r.get("premium_pct"),
                     "st": r.get("status")})
            db.commit()
            return len(scored)
        except Exception:  # noqa: BLE001
            db.rollback()
            return 0
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return 0


def _next_session(report_date, reporting_time) -> bool:
    """True if the result is due the NEXT US session: today after close (AMC) or
    the next trading day before open (BMO). This is the only window a short
    straddle qualifies in -- never hold one over an unknown later session."""
    if not report_date:
        return False
    try:
        rd = datetime.fromisoformat(str(report_date)[:10]).date()
    except Exception:  # noqa: BLE001
        return False
    today = date.today()
    nd = today + timedelta(days=1)
    while nd.weekday() >= 5:            # skip Sat/Sun to the next trading day
        nd += timedelta(days=1)
    rt = (reporting_time or "").upper()
    if rd == today and rt == "AMC":
        return True
    if rd == nd and rt in ("BMO", ""):  # next-day before open (unknown time -> allow)
        return True
    return False


def compute(symbol: str) -> dict:
    """Per-symbol metrics for the short-straddle scanner (cached as tabb:{SYM}).
    No score -- just absolute ATM IV, straddle premium, liquidity and exclusions;
    the relative rank is assigned across the qualifying set in scan()."""
    import swr
    import uw_volatility_service as volsvc
    import earnings_options_service as opt

    symbol = symbol.upper()
    d = swr.peek(f"earn:trade:default:{symbol}") or {}
    em = d.get("expected_move") or {}
    spot = _f(em.get("current")) or _f(d.get("price"))

    # Absolute ATM implied volatility, as a %.
    atm_iv = None
    iv_15d_ago = iv_change = None
    iv_spark: list = []
    try:
        v = volsvc.get_volatility(symbol)
        iv = _f((v.get("stats") or {}).get("iv"))
        if iv is not None:
            atm_iv = round(iv * 100, 1) if iv <= 5 else round(iv, 1)
        # Last ~15 trading days of IV, so you can see whether it has been rising
        # into earnings (a rising run-up = more IV left to crush after the result).
        ser = [s for s in (v.get("iv_rv_series") or []) if _f(s.get("iv")) is not None]
        tail = ser[-16:]
        iv_spark = [round(_f(s["iv"]), 1) for s in tail]
        if len(tail) >= 2:
            iv_15d_ago = round(_f(tail[0]["iv"]), 1)
            if atm_iv is not None and iv_15d_ago is not None:
                iv_change = round(atm_iv - iv_15d_ago, 1)
    except Exception:  # noqa: BLE001
        atm_iv = None

    strike = expiry = premium = prem_pct = leg_spread = oi = None
    try:
        st = opt._atm_straddle(symbol, spot) or {}
        premium = st.get("premium")
        strike = st.get("call_strike")
        expiry = st.get("expiry")
        oi = st.get("oi")
        leg_spread = st.get("leg_spread_pct")
        prem_pct = round(premium / spot * 100, 2) if (premium and spot) else None
    except Exception:  # noqa: BLE001
        pass

    # Ex-dividend before expiry -> early-assignment risk on the short call.
    ex_div = None
    try:
        import dividends_service as divsvc
        nx = (divsvc.get_dividends(symbol) or {}).get("next_ex_date")
        if nx and expiry and str(nx)[:10] <= str(expiry)[:10]:
            ex_div = str(nx)[:10]
    except Exception:  # noqa: BLE001
        ex_div = None

    excl = None
    if premium is None or leg_spread is None or atm_iv is None:
        excl = "No option-chain / IV data"
    elif leg_spread > cfg.TAB_B_MAX_LEG_SPREAD_PCT:
        excl = f"Illiquid — leg spread {leg_spread:.0f}% (> {cfg.TAB_B_MAX_LEG_SPREAD_PCT:.0f}%)"
    elif oi is not None and oi < cfg.TAB_B_MIN_OI:
        excl = "Illiquid — very low open interest"
    elif ex_div:
        excl = f"Ex-dividend {ex_div} before expiry"

    return {"symbol": symbol, "spot": spot, "atm_iv": atm_iv,
            "iv_15d_ago": iv_15d_ago, "iv_change": iv_change, "iv_spark": iv_spark,
            "premium": premium, "premium_pct": prem_pct,
            "leg_spread_pct": leg_spread, "oi": oi,
            "atm_strike": strike, "expiry": expiry,
            "ex_div": ex_div, "exclusion": excl}


def _cached(symbol: str) -> Optional[dict]:
    import swr
    return swr.serve(f"tabb:{symbol.upper()}", lambda: compute(symbol), 21600.0)


def scan(days: int = 5, warm: int = 0) -> dict:
    """Rank the watchlist names reporting the NEXT session by how inflated their
    options are right now -- the average of the ATM-IV rank and the premium-%
    rank (1 = most inflated). `days` only bounds the calendar lookahead."""
    import swr
    import uw_earnings_calendar as cal

    rows = (cal.calendar(days=max(days, 3)) or {}).get("rows") or []
    wl = set(cfg.TAB_B_WATCHLIST)
    seen: set[str] = set()
    uniq: list[dict] = []
    for r in rows:
        s = (r.get("symbol") or "").upper()
        if (s and s in wl and s not in seen
                and _next_session(r.get("date"), r.get("reporting_time"))):
            seen.add(s)
            uniq.append(r)
    cached = swr.peek_many([f"tabb:{s}" for s in seen])

    out: list[dict] = []
    missing: list[str] = []
    for r in uniq:
        s = r["symbol"].upper()
        meta = {"company": r.get("company"), "date": r.get("date"),
                "date_label": r.get("date_label"), "result_time": r.get("time_label"),
                "result_code": r.get("reporting_time")}
        t = cached.get(f"tabb:{s}")
        if t:
            out.append({**t, **meta})
        else:
            missing.append(s)
            out.append({"symbol": s, "status": "PENDING", "atm_iv": None,
                        "iv_15d_ago": None, "iv_change": None, "iv_spark": [],
                        "premium": None, "premium_pct": None, "leg_spread_pct": None,
                        "oi": None, "atm_strike": None, "expiry": None, "spot": None,
                        "ex_div": None, "exclusion": None, "rank": None, **meta})

    for s in missing[:max(0, warm)]:
        try:
            _cached(s)
        except Exception:  # noqa: BLE001
            pass

    # Rank the qualifying names (computed, not excluded) by absolute ATM IV and
    # by premium %, then by the average of the two ranks. Most inflated first.
    live = [r for r in out if r.get("status") != "PENDING" and not r.get("exclusion")
            and r.get("atm_iv") is not None and r.get("premium_pct") is not None]
    for i, r in enumerate(sorted(live, key=lambda x: -x["atm_iv"]), 1):
        r["rank_iv"] = i
    for i, r in enumerate(sorted(live, key=lambda x: -x["premium_pct"]), 1):
        r["rank_prem"] = i
    for r in live:
        r["combined"] = (r["rank_iv"] + r["rank_prem"]) / 2.0
        r["status"] = "SELL STRADDLE"
    live.sort(key=lambda x: x["combined"])
    for i, r in enumerate(live, 1):
        r["rank"] = i
    for r in out:
        if r.get("exclusion") and r.get("status") != "PENDING":
            r["status"] = "SKIP"
            r["rank"] = None

    def _order(r):
        if r.get("rank"):
            return (0, r["rank"])
        return (2, 0) if r.get("status") == "PENDING" else (1, r.get("symbol") or "")

    out.sort(key=_order)
    try:
        log_scores(out)
    except Exception:  # noqa: BLE001
        pass
    return {"status": "OK", "source": SOURCE, "rows": out,
            "universe": len(seen), "scored": len(live), "missing": missing[:60]}
