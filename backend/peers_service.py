"""
Peer comparison: a stock against the others in its sector.

Analysts read in relatives -- "cheap vs peers", "leading its sector" -- not in
isolation. This lines a symbol up against its sector peers on the metrics the
app already computes (the directional score, today's move, IV rank) plus
relative strength vs SPY, so a name's reading has a peer group to stand against.

Everything is drawn from data the app already caches (scores, quotes, IV rank,
daily bars), so a peer table is cheap: no new provider, and most cells are warm
from the board and strip.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import live_market_service as market
import unusualwhales_service as uw

# The scored universe is the peer pool: these are the names the app already
# warms, so their metrics are in cache.
from ai_trade_service import UNIVERSE

MAX_PEERS = 8
RS_SESSIONS = 21  # ~1 month of trading days for relative strength

# The company table often has no sector for these names, so peers would fall
# back to the whole universe. This curated map covers the scored universe so a
# name is compared against its real peer group (a chip vs other chips).
SECTOR_MAP = {
    "NVDA": "Semiconductors", "AMD": "Semiconductors", "AVGO": "Semiconductors",
    "INTC": "Semiconductors", "MU": "Semiconductors", "QCOM": "Semiconductors",
    "AAPL": "Technology", "MSFT": "Technology", "GOOGL": "Technology",
    "META": "Technology", "CRM": "Technology", "ORCL": "Technology",
    "ADBE": "Technology",
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
    "HD": "Consumer Discretionary", "NKE": "Consumer Discretionary",
    "NFLX": "Consumer Discretionary", "DIS": "Consumer Discretionary",
    "JPM": "Financials", "BAC": "Financials", "GS": "Financials",
    "V": "Financials", "MA": "Financials",
    "XOM": "Energy", "CVX": "Energy", "COP": "Energy",
    "UNH": "Health Care", "LLY": "Health Care", "JNJ": "Health Care",
    "PFE": "Health Care",
    "WMT": "Consumer Staples", "COST": "Consumer Staples",
    "CAT": "Industrials", "BA": "Industrials", "GE": "Industrials",
    "SPY": "Index ETF", "QQQ": "Index ETF",
}


def _sector(symbol: str) -> Optional[str]:
    if symbol in SECTOR_MAP:
        return SECTOR_MAP[symbol]
    from database import SessionLocal
    from models import Company
    db = SessionLocal()
    try:
        c = db.query(Company).filter(Company.symbol == symbol).first()
        return (c.sector or None) if c else None
    finally:
        db.close()


def _name(symbol: str) -> Optional[str]:
    from database import SessionLocal
    from models import Company
    db = SessionLocal()
    try:
        c = db.query(Company).filter(Company.symbol == symbol).first()
        return (c.company_name or None) if c else None
    finally:
        db.close()


def _sectors_for(symbols: list[str]) -> dict[str, Optional[str]]:
    from database import SessionLocal
    from models import Company
    db = SessionLocal()
    try:
        rows = db.query(Company).filter(Company.symbol.in_(symbols)).all()
        return {r.symbol: r.sector for r in rows}
    finally:
        db.close()


def _iv_rank(symbol: str) -> Optional[float]:
    try:
        rows = uw._rows(uw.iv_rank(symbol))
        vals = [uw._f(r.get("iv_rank_1y")) for r in rows
                if r.get("iv_rank_1y") is not None]
        vals = [v for v in vals if v is not None]
        # The provider often appends an unfilled "0" for the current day; drop a
        # trailing zero when a real recent value precedes it so IV rank does not
        # read 0 for a name whose vol is simply not yet computed today.
        while len(vals) > 1 and vals[-1] == 0:
            vals.pop()
        return round(vals[-1], 1) if vals else None
    except Exception:  # noqa: BLE001
        return None


def _score(symbol: str) -> Optional[float]:
    """The directional score (0-100) from cache, else the composite's."""
    cached = market.cache.get(f"directional:{symbol}", 3600.0)
    if cached and cached.get("direction_score") is not None:
        return round(cached["direction_score"], 1)
    import live_score_service as scoring
    final = scoring.get_cached_score(symbol)
    if final and final.get("direction_score") is not None:
        return round(final["direction_score"], 1)
    return None


def _period_return(bars: list, sessions: int) -> Optional[float]:
    closes = [b.get("close") for b in bars if b.get("close")]
    if len(closes) <= sessions:
        return None
    now, then = closes[-1], closes[-1 - sessions]
    if not then:
        return None
    return (now - then) / then * 100.0


def get_peers(symbol: str) -> dict:
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"status": "INVALID_SYMBOL"}

    sector = _sector(symbol)
    # Peer pool: universe names sharing the sector. Fall back to the whole
    # universe when the sector is unknown or too thin to compare against.
    pool = [s for s in UNIVERSE if s != symbol]
    if sector:
        db_sec = _sectors_for(pool)
        same = [s for s in pool
                if (SECTOR_MAP.get(s) or db_sec.get(s)) == sector]
        peers = same[:MAX_PEERS] if len(same) >= 2 else pool[:MAX_PEERS]
    else:
        peers = pool[:MAX_PEERS]

    all_syms = [symbol] + peers

    # SPY baseline for relative strength, fetched once.
    try:
        spy_bars = market.get_chart("SPY", "3M").get("bars") or []
        spy_bars = [{"close": b.get("close")} for b in spy_bars]
    except Exception:  # noqa: BLE001
        spy_bars = []
    spy_ret = _period_return(spy_bars, RS_SESSIONS)

    def row(sym: str) -> dict:
        q = {}
        try:
            q = market.get_quote(sym) or {}
        except Exception:  # noqa: BLE001
            q = {}
        rs = None
        try:
            bars = market.get_chart(sym, "3M").get("bars") or []
            r = _period_return([{"close": b.get("close")} for b in bars], RS_SESSIONS)
            if r is not None and spy_ret is not None:
                rs = round(r - spy_ret, 2)
        except Exception:  # noqa: BLE001
            rs = None
        return {
            "symbol": sym,
            "name": _name(sym) or sym,
            "is_subject": sym == symbol,
            "score": _score(sym),
            "price": q.get("price"),
            "change_percent": q.get("change_percent"),
            "iv_rank": _iv_rank(sym),
            "rs_1m": rs,  # relative strength vs SPY over ~1 month, in points
        }

    with ThreadPoolExecutor(max_workers=min(6, len(all_syms)),
                            thread_name_prefix="peers") as pool_ex:
        rows = list(pool_ex.map(row, all_syms))

    # Rank by score (highest first); unscored sink to the bottom.
    rows.sort(key=lambda r: (r["score"] if r["score"] is not None else -1e9),
              reverse=True)
    subject = next((r for r in rows if r["is_subject"]), None)
    rank = next((i + 1 for i, r in enumerate(rows) if r["is_subject"]), None)

    return {
        "status": "OK",
        "symbol": symbol,
        "sector": sector or "Unclassified",
        "peer_count": len(peers),
        "subject_rank": rank,
        "of": len(rows),
        "spy_return_1m_pct": round(spy_ret, 2) if spy_ret is not None else None,
        "rows": rows,
        "detail": ("Peers are the scored-universe names in the same sector. "
                   "Score is the directional read (0-100); RS is relative "
                   "strength vs SPY over ~1 month; IV rank is where implied "
                   "vol sits in its own year."),
    }
