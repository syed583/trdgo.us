"""Table 2 -- Unusual Activity & Call/Put Disparity (its own tab).

Universe = the IV Crush universe (next session + the next ~10 trading days,
tab_b_service.universe_symbols). All thresholds live in tab_settings (UA_*).

Part A -- Unusual Activity, one row per contract that meets ALL of:
    today's volume >= UA_VOL_AVG_MULT x its 20-day average volume,
    volume >= open interest, premium >= UA_MIN_PREMIUM.
  Columns: ticker, expiry, strike, call/put, volume (x avg), vol/oi, premium,
  side (BUY/SELL/MIXED from the ask/bid-side split), ask% / bid%.
  Sorted by premium desc, max UA_MAX_ROWS.

Part B -- Call/Put Disparity, one row per stock (only CALL HEAVY / PUT HEAVY):
  call premium today, put premium today, call/put ratio today, the normal ratio
  (avg of that ratio over the last UA_NORMAL_SESSIONS sessions), the disparity
  (today / normal) and the leaning. Sorted by distance from 1x.

Read-only. Nothing here places, changes or cancels an order.

Every field comes straight from Unusual Whales:
  Part A contracts  -> /api/option-activity/unusual (market-wide), filtered to
                       the earnings universe; the 20-day average volume is the
                       mean of prior-session volumes from the per-contract
                       historic endpoint.
  Part B premiums   -> /api/stock/{sym}/options-volume (a daily series: today's
                       call/put premium plus the prior sessions for the normal
                       ratio).
Both the per-contract average and the per-stock series are cached per item and
filled progressively (like the IV Crush tab), so a scan request never blocks.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import tab_settings as cfg
import tab_b_service as tb
import unusualwhales_service as uw

SOURCE = "Table 2 — Unusual Activity"

_f = uw._f


def _today() -> str:
    return date.today().isoformat()


# ---- Part A: per-contract 20-day average volume (cached per OCC) -----------

def _avg_volume(occ: str) -> Optional[float]:
    """Mean of the last UA_AVG_SESSIONS prior-session volumes for one contract,
    from its historic tape (today excluded). None if there is no prior history."""
    n = int(getattr(cfg, "UA_AVG_SESSIONS", 20))
    payload = uw.get(f"/api/option-contract/{occ}/historic", {"limit": n + 5})
    today = _today()
    vols = []
    for r in uw._rows(payload):
        if str(r.get("date"))[:10] == today:
            continue                       # today is the numerator, never the average
        v = _f(r.get("volume"))
        if v is not None:
            vols.append(v)
    vols = vols[:n]
    return (sum(vols) / len(vols)) if vols else None


def _avg_volume_cached(occ: str) -> Optional[float]:
    import swr
    return swr.peek(f"ua:avgvol:{occ}")


def _avg_volume_peek_many(occs: list[str]) -> dict:
    """One batched cache read for many contracts' 20-day averages."""
    import swr
    got = swr.peek_many([f"ua:avgvol:{o}" for o in occs])
    return {o: got.get(f"ua:avgvol:{o}") for o in occs}


def _avg_volume_warm(occ: str) -> Optional[float]:
    import swr
    # Prior-session volumes don't change intraday -> a long TTL is right.
    return swr.serve(f"ua:avgvol:{occ}", lambda: _avg_volume(occ), 6 * 3600.0)


def _side(ask: float, bid: float, vol: float) -> tuple[str, float, float]:
    """(label, ask%, bid%) from the directional volume split. BUY if >=
    UA_SIDE_PCT filled at the ask, SELL if >= UA_SIDE_PCT at the bid, else MIXED."""
    denom = vol if (vol and vol > 0) else (ask + bid)
    if not denom or denom <= 0:
        return "MIXED", 0.0, 0.0
    ask_pct = ask / denom
    bid_pct = bid / denom
    thr = float(getattr(cfg, "UA_SIDE_PCT", 0.60))
    label = "BUY" if ask_pct >= thr else "SELL" if bid_pct >= thr else "MIXED"
    return label, ask_pct, bid_pct


def _part_a(universe: set[str]) -> tuple[list[dict], list[str]]:
    """(rows, missing_occs). Rows for candidates whose 20-day average is cached;
    candidates still missing their average are returned to be warmed."""
    min_prem = float(getattr(cfg, "UA_MIN_PREMIUM", 100_000.0))
    vol_oi_min = float(getattr(cfg, "UA_VOL_OI_MIN", 1.0))
    mult_min = float(getattr(cfg, "UA_VOL_AVG_MULT", 3.0))
    max_rows = int(getattr(cfg, "UA_MAX_ROWS", 40))
    floor = int(getattr(cfg, "UA_SCAN_PREMIUM_FLOOR", 90_000))
    lim = int(getattr(cfg, "UA_SCAN_LIMIT", 500))

    pull = uw.unusual_activity("", min_premium=floor, limit=lim)

    # Pre-filter to the three hard gates we can check without the average, inside
    # our universe, then rank by premium so we only ever need the average for the
    # strongest candidates.
    cands: list[dict] = []
    for r in uw._rows(pull):
        tkr = (r.get("ticker_symbol") or "").upper()
        if tkr not in universe:
            continue
        prem = _f(r.get("premium"))
        vol = _f(r.get("volume"))
        oi = _f(r.get("open_interest"))
        if prem is None or vol is None or prem < min_prem:
            continue
        if oi is not None and oi > 0 and vol < oi * vol_oi_min:
            continue
        if not r.get("option_symbol"):
            continue
        cands.append(r)
    cands.sort(key=lambda r: _f(r.get("premium")) or 0.0, reverse=True)
    # Only ever resolve the average for a bounded set of the top candidates.
    cands = cands[: max_rows * 4]
    avgs = _avg_volume_peek_many([r["option_symbol"] for r in cands])

    rows: list[dict] = []
    missing: list[str] = []
    for r in cands:
        if len(rows) >= max_rows:
            break
        occ = r["option_symbol"]
        avg = avgs.get(occ)
        if avg is None:
            missing.append(occ)
            continue
        if avg <= 0:
            continue
        vol = _f(r.get("volume")) or 0.0
        mult = vol / avg
        if mult < mult_min:
            continue
        ask = _f(r.get("ask_side_volume")) or 0.0
        bid = _f(r.get("bid_side_volume")) or 0.0
        label, ask_pct, bid_pct = _side(ask, bid, vol)
        rows.append({
            "symbol": r.get("ticker_symbol"),
            "expiry": r.get("expiry"),
            "strike": _f(r.get("strike")),
            "right": (r.get("option_type") or "").upper()[:1] or None,  # C / P
            "volume": vol,
            "avg_volume": avg,
            "vol_mult": mult,
            "open_interest": _f(r.get("open_interest")),
            "vol_oi": (vol / _f(r.get("open_interest")))
            if _f(r.get("open_interest")) else None,
            "premium": _f(r.get("premium")),
            "side": label,
            "ask_pct": ask_pct,
            "bid_pct": bid_pct,
        })
    rows.sort(key=lambda x: x.get("premium") or 0.0, reverse=True)
    return rows, missing


# ---- Part B: per-stock call/put premium series (cached per symbol) ---------

def _ov_series(sym: str) -> list[dict]:
    n = int(getattr(cfg, "UA_NORMAL_SESSIONS", 20))
    payload = uw.get(f"/api/stock/{sym}/options-volume", {"limit": n + 5})
    return uw._rows(payload)


def _ov_series_cached(sym: str) -> Optional[list]:
    import swr
    return swr.peek(f"ua:ov:{sym}")


def _ov_series_peek_many(syms: list[str]) -> dict:
    """One batched cache read for many stocks' options-volume series."""
    import swr
    got = swr.peek_many([f"ua:ov:{s}" for s in syms])
    return {s: got.get(f"ua:ov:{s}") for s in syms}


def _ov_series_warm(sym: str) -> list[dict]:
    import swr
    # Today's row moves intraday; refresh on the tab's cadence.
    return swr.serve(f"ua:ov:{sym}", lambda: _ov_series(sym),
                     float(getattr(cfg, "UA_REFRESH_SEC", 300.0)))


def _disparity_row(sym: str, rows: list[dict]) -> Optional[dict]:
    if not rows:
        return None
    n = int(getattr(cfg, "UA_NORMAL_SESSIONS", 20))
    today = rows[0]
    cp = _f(today.get("call_premium"))
    pp = _f(today.get("put_premium"))
    if not cp or not pp or pp <= 0:
        return None
    floor = float(getattr(cfg, "UA_PARTB_MIN_PREMIUM", 0) or 0)
    if floor and (cp + pp) < floor:
        return None                      # too little premium today -> noisy ratio, skip
    ratio = cp / pp
    hist = []
    for d in rows[1: 1 + n]:
        c = _f(d.get("call_premium"))
        p = _f(d.get("put_premium"))
        if c and p and p > 0:
            hist.append(c / p)
    if not hist:
        return None
    normal = sum(hist) / len(hist)
    if normal <= 0:
        return None
    disp = ratio / normal
    hi = float(getattr(cfg, "UA_CALL_HEAVY", 2.0))
    lo = float(getattr(cfg, "UA_PUT_HEAVY", 0.5))
    lean = "CALL HEAVY" if disp >= hi else "PUT HEAVY" if disp <= lo else ""
    return {
        "symbol": sym,
        "call_premium": cp,
        "put_premium": pp,
        "ratio": ratio,
        "normal_ratio": normal,
        "disparity": disp,
        "leaning": lean,
        "hist_n": len(hist),
    }


def _part_b(universe: list[str]) -> tuple[list[dict], list[str]]:
    """(rows, missing_symbols). Only CALL HEAVY / PUT HEAVY rows, sorted by how
    far the disparity is from 1x. Symbols without a cached series are warmed."""
    rows: list[dict] = []
    missing: list[str] = []
    cached = _ov_series_peek_many(universe)
    for sym in universe:
        series = cached.get(sym)
        if series is None:
            missing.append(sym)
            continue
        row = _disparity_row(sym, series or [])
        if row and row["leaning"]:
            rows.append(row)
    rows.sort(key=lambda x: abs((x.get("disparity") or 1.0) - 1.0), reverse=True)
    return rows, missing


def scan(days: int = 15) -> dict:
    """Assemble both tables from cached per-item data, listing what still needs
    warming. The endpoint fills the warm queue a batch at a time."""
    import swr
    # The calendar lookup is the slow part; cache the universe so a recompute
    # doesn't re-pull it every cycle.
    universe = swr.serve("ua:universe", lambda: tb.universe_symbols(days), 600.0)
    uset = set(universe)
    part_a, miss_a = _part_a(uset)
    part_b, miss_b = _part_b(universe)
    return {
        "status": "OK",
        "source": SOURCE,
        "asof": _today(),
        "universe": len(universe),
        "part_a": part_a,
        "part_b": part_b,
        "warm_occs": miss_a[:120],
        "warm_syms": miss_b[:200],
    }


def warm(occs: list[str], syms: list[str]) -> None:
    """Fill a batch of per-item caches (called off the request thread)."""
    for occ in occs:
        try:
            _avg_volume_warm(occ)
        except Exception:  # noqa: BLE001
            pass
    for sym in syms:
        try:
            _ov_series_warm(sym)
        except Exception:  # noqa: BLE001
            pass
