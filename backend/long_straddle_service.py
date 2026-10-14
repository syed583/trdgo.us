"""Long Straddle (IV run-up) -- Strategy A of the Earnings Options Module.

Buy the ATM call + ATM put ~15 days before a watchlist name's earnings, when
implied volatility still has room to rise into the event, and exit the day
BEFORE the result (never hold through it). Profit comes from IV / premium rising,
not from the move itself. Risk is limited to the premium paid.

This tab plans and monitors the trade across the 67-name watchlist:
  * ENTER  -- ~15 days out, IV rank still low, options liquid: open the straddle.
  * HOLD   -- a position is live; ride the IV run-up.
  * EXIT   -- the day before the result: square up per the direction view.
  * WATCH  -- too early (more than ~18 days out).
  * SKIP   -- IV already high (edge gone) or options illiquid.

At exit the direction view (the app's BUY / SELL / NEUTRAL stock signal) decides
which leg to keep: bullish keeps the call, bearish keeps the put, no view closes
both and banks the IV gain.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import tab_settings as cfg

SOURCE = "Long Straddle — IV run-up"


def _f(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _direction(decision: Optional[str]) -> tuple[str, str]:
    """(view, exit-plan) from the stock's BUY / SELL / NEUTRAL earnings signal."""
    d = (decision or "").upper()
    if "BUY" in d:
        return "bullish", "keep the CALL, close the PUT"
    if "SELL" in d:
        return "bearish", "keep the PUT, close the CALL"
    return "no view", "close BOTH legs — bank the IV gain"


def compute(symbol: str) -> dict:
    """Per-symbol long-straddle entry/monitor metrics (cached as lstr:{SYM})."""
    import swr
    import uw_volatility_service as volsvc
    import earnings_options_service as opt

    symbol = symbol.upper()
    d = swr.peek(f"earn:trade:default:{symbol}") or {}
    em = d.get("expected_move") or {}
    spot = _f(em.get("current")) or _f(d.get("price"))
    dec = d.get("overall_decision") or (d.get("equity") or {}).get("decision")
    view, exit_plan = _direction(dec)

    atm_iv = iv_rank = None
    try:
        v = volsvc.get_volatility(symbol)
        stats = v.get("stats") or {}
        iv = _f(stats.get("iv"))
        if iv is not None:
            atm_iv = round(iv * 100, 1) if iv <= 5 else round(iv, 1)
        ivr = _f(stats.get("iv_rank"))
        if ivr is not None:
            iv_rank = round(ivr * 100, 1) if ivr <= 1.5 else round(ivr, 1)
    except Exception:  # noqa: BLE001
        pass

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

    liquid = (leg_spread is not None and leg_spread <= cfg.LS_MAX_LEG_SPREAD_PCT
              and (oi is None or oi >= cfg.LS_MIN_OI))
    iv_has_room = iv_rank is not None and iv_rank <= cfg.LS_MAX_IV_RANK

    return {"symbol": symbol, "spot": spot, "atm_iv": atm_iv, "iv_rank": iv_rank,
            "premium": premium, "premium_pct": prem_pct, "leg_spread_pct": leg_spread,
            "oi": oi, "atm_strike": strike, "expiry": expiry,
            "view": view, "exit_plan": exit_plan,
            "liquid": liquid, "iv_has_room": iv_has_room,
            "no_data": premium is None or leg_spread is None or iv_rank is None}


def _cached(symbol: str) -> Optional[dict]:
    import swr
    return swr.serve(f"lstr:{symbol.upper()}", lambda: compute(symbol), 21600.0)


def _status(days_to: Optional[int], m: dict) -> tuple[str, Optional[str]]:
    """(status, note) from the days-to-earnings and the entry gates."""
    lo = cfg.LS_ENTRY_DAYS - cfg.LS_ENTRY_TOL
    hi = cfg.LS_ENTRY_DAYS + cfg.LS_ENTRY_TOL
    if days_to is None:
        return "WATCH", "no confirmed date"
    if days_to <= 1:
        return "EXIT", "the session before the result"
    if days_to > hi:
        return "WATCH", f"too early — enter ~{cfg.LS_ENTRY_DAYS}d out"
    if days_to < lo:
        return "HOLD", "position should be open — ride the IV run-up"
    # Inside the entry window: it's an ENTER only if IV still has room and liquid.
    if m.get("no_data"):
        return "SKIP", "no option / IV data"
    if not m.get("liquid"):
        return "SKIP", "illiquid — spread too wide / thin OI"
    if not m.get("iv_has_room"):
        return "SKIP", f"IV already high (rank > {cfg.LS_MAX_IV_RANK:.0f}) — edge gone"
    return "ENTER", "IV has room — buy the ATM call + put"


def _days_to(d) -> Optional[int]:
    if not d:
        return None
    try:
        return (datetime.fromisoformat(str(d)[:10]).date() - date.today()).days
    except Exception:  # noqa: BLE001
        return None


# Entry candidates first, then watch/hold, then exit, then skip.
_ORDER = {"ENTER": 0, "HOLD": 1, "EXIT": 2, "WATCH": 3, "SKIP": 4, "PENDING": 5}


def scan(days: int = 21, warm: int = 0) -> dict:
    """Plan the long straddle across the watchlist names reporting within `days`."""
    import swr
    import uw_earnings_calendar as cal

    rows = (cal.calendar(days=days) or {}).get("rows") or []
    wl = set(cfg.TAB_B_WATCHLIST)   # same 67-name universe as the short straddle
    seen: set[str] = set()
    uniq: list[dict] = []
    for r in rows:
        s = (r.get("symbol") or "").upper()
        if s and s in wl and s not in seen:
            seen.add(s)
            uniq.append(r)
    cached = swr.peek_many([f"lstr:{s}" for s in seen])

    out: list[dict] = []
    missing: list[str] = []
    for r in uniq:
        s = r["symbol"].upper()
        dt = _days_to(r.get("date"))
        meta = {"company": r.get("company"), "date": r.get("date"),
                "date_label": r.get("date_label"), "result_time": r.get("time_label"),
                "result_code": r.get("reporting_time"), "days_to": dt}
        t = cached.get(f"lstr:{s}")
        if t:
            status, note = _status(dt, t)
            out.append({**t, **meta, "status": status, "note": note})
        else:
            missing.append(s)
            out.append({"symbol": s, "status": "PENDING", "note": None, "spot": None,
                        "atm_iv": None, "iv_rank": None, "premium": None,
                        "premium_pct": None, "leg_spread_pct": None, "oi": None,
                        "atm_strike": None, "expiry": None, "view": None,
                        "exit_plan": None, **meta})

    for s in missing[:max(0, warm)]:
        try:
            _cached(s)
        except Exception:  # noqa: BLE001
            pass

    # ENTER names first, lowest IV rank (most room) at the top; then by days-to.
    out.sort(key=lambda r: (_ORDER.get(r["status"], 9),
                            r.get("iv_rank") if (r["status"] == "ENTER"
                                                 and r.get("iv_rank") is not None) else 999,
                            r.get("days_to") if r.get("days_to") is not None else 999))
    enter = sum(1 for r in out if r["status"] == "ENTER")
    return {"status": "OK", "source": SOURCE, "rows": out,
            "universe": len(seen), "enter": enter, "missing": missing[:60]}
