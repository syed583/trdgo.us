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
    """Daily option-chain snapshot per watchlist name, so the `lb`-sessions-ago
    ATM premium and IV for the same strike are available for the inflation
    ranking (the spec's 'store daily snapshots' requirement)."""
    global _log_ready
    if _log_ready:
        return True
    try:
        from sqlalchemy import text
        from database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS straddle_daily_snapshot ("
                "  symbol        VARCHAR(20) NOT NULL,"
                "  snapshot_date DATE        NOT NULL,"
                "  atm_strike    DOUBLE PRECISION,"
                "  atm_iv        DOUBLE PRECISION,"
                "  premium       DOUBLE PRECISION,"
                "  rank          INTEGER,"
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


def _snapshot_prev(symbol: str, lb: int) -> Optional[dict]:
    """The stored snapshot closest to ~`lb` trading sessions ago (approx lb*7/5
    calendar days), if one exists. Returns {strike, atm_iv, premium} or None."""
    if not _ensure_log_table():
        return None
    try:
        from sqlalchemy import text
        from database import SessionLocal
        target = date.today() - timedelta(days=round(lb * 7 / 5))
        db = SessionLocal()
        try:
            row = db.execute(text(
                "SELECT atm_strike, atm_iv, premium FROM straddle_daily_snapshot "
                "WHERE symbol = :s AND snapshot_date <= :d AND premium IS NOT NULL "
                "ORDER BY snapshot_date DESC LIMIT 1"),
                {"s": symbol.upper(), "d": target}).fetchone()
            if row:
                return {"strike": _f(row[0]), "atm_iv": _f(row[1]), "premium": _f(row[2])}
            return None
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return None


def log_scores(rows: list[dict]) -> int:
    """Upsert today's ATM snapshot (strike/IV/premium + rank/status) per name."""
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
                    "INSERT INTO straddle_daily_snapshot "
                    "(symbol, snapshot_date, atm_strike, atm_iv, premium, rank, status) "
                    "VALUES (:s, :d, :k, :iv, :pm, :rk, :st) "
                    "ON CONFLICT (symbol, snapshot_date) DO UPDATE SET "
                    "atm_strike = :k, atm_iv = :iv, premium = :pm, rank = :rk, status = :st"),
                    {"s": r["symbol"].upper(), "d": today, "k": r.get("atm_strike"),
                     "iv": r.get("atm_iv"), "pm": r.get("premium"),
                     "rk": r.get("rank"), "st": r.get("status")})
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

    lb = cfg.TAB_B_LOOKBACK_SESSIONS
    atm_iv = iv_prev = iv_ratio = None
    iv_spark: list = []
    try:
        v = volsvc.get_volatility(symbol)
        iv = _f((v.get("stats") or {}).get("iv"))
        if iv is not None:
            atm_iv = round(iv * 100, 1) if iv <= 5 else round(iv, 1)
        # Daily IV series: today vs `lb` TRADING sessions ago (theta-neutral, so a
        # fair inflation measure without historical option-price snapshots).
        ser = [s for s in (v.get("iv_rv_series") or []) if _f(s.get("iv")) is not None]
        iv_spark = [round(_f(s["iv"]), 1) for s in ser[-(lb + 1):]]
        if len(ser) > lb:
            pv = _f(ser[-(lb + 1)].get("iv"))
            iv_prev = round(pv * 100, 1) if (pv is not None and pv <= 5) else (round(pv, 1) if pv else None)
        if atm_iv and iv_prev:
            iv_ratio = round(atm_iv / iv_prev, 2)
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

    # Premium `lb` sessions ago for the CURRENT ATM strike, from a stored daily
    # snapshot (the spec's requirement). A straddle's value also decays with time
    # to expiry, so a Black-Scholes reconstruction at the old date would be
    # dominated by theta, not by earnings inflation -- hence actual stored
    # snapshots only. The premium ratio joins the ranking once snapshots exist.
    premium_prev = premium_ratio = None
    premium_prev_src = None
    snap = _snapshot_prev(symbol, lb)
    if snap and snap.get("premium"):
        premium_prev = round(_f(snap["premium"]), 2)
        premium_prev_src = "stored"
        if premium and premium_prev:
            premium_ratio = round(premium / premium_prev, 2)

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
    thr = cfg.TAB_B_MIN_INFLATION
    if premium is None or leg_spread is None or atm_iv is None:
        excl = "No option-chain / IV data"
    elif leg_spread > cfg.TAB_B_MAX_LEG_SPREAD_PCT:
        excl = f"Illiquid — leg spread {leg_spread:.0f}% (> {cfg.TAB_B_MAX_LEG_SPREAD_PCT:.0f}%)"
    elif oi is not None and oi < cfg.TAB_B_MIN_OI:
        excl = "Illiquid — very low open interest"
    elif ex_div:
        excl = f"Ex-dividend {ex_div} before expiry"
    elif (iv_ratio is not None and iv_ratio < thr
          and (premium_ratio is None or premium_ratio < thr)):
        excl = f"No real inflation vs {lb} sessions ago"

    return {"symbol": symbol, "spot": spot,
            "atm_iv": atm_iv, "iv_prev": iv_prev, "iv_ratio": iv_ratio, "iv_spark": iv_spark,
            "premium": premium, "premium_prev": premium_prev,
            "premium_ratio": premium_ratio, "premium_prev_src": premium_prev_src,
            "premium_pct": prem_pct, "leg_spread_pct": leg_spread, "oi": oi,
            "atm_strike": strike, "expiry": expiry, "ex_div": ex_div,
            "lookback": lb, "exclusion": excl}


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
                        "iv_prev": None, "iv_ratio": None, "iv_spark": [],
                        "premium": None, "premium_prev": None, "premium_ratio": None,
                        "premium_prev_src": None, "premium_pct": None,
                        "leg_spread_pct": None, "oi": None, "atm_strike": None,
                        "expiry": None, "spot": None, "ex_div": None, "lookback": None,
                        "exclusion": None, "rank": None, **meta})

    for s in missing[:max(0, warm)]:
        try:
            _cached(s)
        except Exception:  # noqa: BLE001
            pass

    # Rank the qualifying names by INFLATION vs `lb` sessions ago: the IV ratio
    # and the premium ratio, then the average of the two ranks. Most inflated top.
    live = [r for r in out if r.get("status") != "PENDING" and not r.get("exclusion")
            and r.get("iv_ratio") is not None]
    for i, r in enumerate(sorted(live, key=lambda x: -x["iv_ratio"]), 1):
        r["rank_iv"] = i
    with_prem = [r for r in live if r.get("premium_ratio") is not None]
    for i, r in enumerate(sorted(with_prem, key=lambda x: -x["premium_ratio"]), 1):
        r["rank_prem"] = i
    for r in live:
        parts = [r["rank_iv"]] + ([r["rank_prem"]] if r.get("rank_prem") is not None else [])
        r["combined"] = sum(parts) / len(parts)
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
