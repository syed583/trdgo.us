"""
Dark-pool prints, and where the market's insiders are trading.

Two readings this app has never had, both from Unusual Whales.

**Dark pool.** Roughly a third to a half of US share volume prints away from
the lit exchanges, on venues that report the trade after it happens. Those
prints are the size that does not want to move the price -- so a large block
crossing well above the day's average, at a price away from the last, says
something the lit tape does not. It is a record of what was done, never a
forecast: a print has no side attached, so nothing here labels one "buying"
or "selling". Size, price and where it sat against the spread are the
honest columns.

**Insiders across the market.** The insider panel elsewhere in this app
answers "who traded this company". This answers "what are insiders doing
generally, and in which sectors" -- a breadth reading, in the way the
options tide is a breadth reading for the option market.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import unusualwhales_service as uw

SOURCE = "Unusual Whales"

# A print worth calling large. Below this a dark-pool cross is ordinary
# retail-sized flow being internalised, which says nothing about intent.
BLOCK_PREMIUM = 1_000_000.0


def configured() -> bool:
    return uw.configured()


def _f(value) -> Optional[float]:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _ago(stamp) -> Optional[str]:
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    minutes = (datetime.now(timezone.utc) - when).total_seconds() / 60
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{int(minutes)}m ago"
    if minutes < 1440:
        return f"{int(minutes / 60)}h ago"
    return f"{int(minutes / 1440)}d ago"


def _print_row(r: dict) -> dict:
    size = _f(r.get("size")) or 0.0
    price = _f(r.get("price"))
    bid, ask = _f(r.get("nbbo_bid")), _f(r.get("nbbo_ask"))
    premium = _f(r.get("premium")) or (size * (price or 0.0))

    # Where the print sat against the quote at the time. Not a side -- a
    # dark-pool print carries no buyer/seller flag, and inferring one from
    # the price is the guess this app refuses to make elsewhere. It is
    # reported as a position in the spread, which is what it is.
    at = None
    if price is not None and bid is not None and ask is not None and ask > bid:
        share = (price - bid) / (ask - bid)
        at = ("at the ask" if share >= 0.95
              else "above mid" if share > 0.55
              else "at the bid" if share <= 0.05
              else "below mid" if share < 0.45 else "at mid")

    return {
        "symbol": r.get("ticker"),
        "size": size,
        "price": price,
        "premium": round(premium, 2),
        "bid": bid,
        "ask": ask,
        "spread_position": at,
        "market_center": r.get("market_center"),
        "day_volume": _f(r.get("volume")),
        "share_of_day": (round(size / (_f(r.get("volume")) or 1) * 100, 3)
                         if r.get("volume") else None),
        "block": premium >= BLOCK_PREMIUM,
        "time": r.get("executed_at"),
        "time_label": _ago(r.get("executed_at")),
        "canceled": bool(r.get("canceled")),
    }


def recent_prints(limit: int = 50, min_premium: float = 0.0) -> dict:
    """The market's latest dark-pool prints, largest first."""
    out = uw.get("/api/darkpool/recent", {"limit": max(limit * 3, 100)})
    if out["status"] != "OK":
        return {"status": out["status"], "rows": [],
                "detail": out.get("detail"), "source": SOURCE}

    rows = [_print_row(r) for r in uw._rows(out)]
    rows = [r for r in rows
            if not r["canceled"] and (r["premium"] or 0) >= min_premium]
    rows.sort(key=lambda r: -(r["premium"] or 0))
    blocks = [r for r in rows if r["block"]]

    return {
        "status": "OK" if rows else "NO_DATA",
        "rows": rows[:limit],
        "count": len(rows[:limit]),
        "blocks": len(blocks),
        "block_premium": round(sum(r["premium"] or 0 for r in blocks), 2),
        "total_premium": round(sum(r["premium"] or 0 for r in rows), 2),
        "detail": ("Trades printed away from the lit exchanges, reported "
                   "after execution. A print carries no side, so none is "
                   "shown -- only its size and where it sat in the spread."),
        "source": SOURCE,
    }


def symbol_prints(symbol: str, limit: int = 50) -> dict:
    """One symbol's dark-pool prints, with the day's off-exchange share."""
    symbol = (symbol or "").upper().strip()
    out = uw.darkpool(symbol, limit=max(limit * 2, 100))
    if out["status"] != "OK":
        return {"symbol": symbol, "status": out["status"], "rows": [],
                "detail": out.get("detail"), "source": SOURCE}

    rows = [_print_row(r) for r in uw._rows(out) if not r.get("canceled")]
    rows.sort(key=lambda r: -(r["premium"] or 0))
    printed = sum(r["size"] or 0 for r in rows)
    day_volume = max((r["day_volume"] or 0 for r in rows), default=0)

    return {
        "symbol": symbol,
        "status": "OK" if rows else "NO_DATA",
        "rows": rows[:limit],
        "count": len(rows[:limit]),
        "prints_seen": len(rows),
        "shares_printed": printed,
        "day_volume": day_volume or None,
        # What share of today's volume these prints account for. Their feed
        # returns the most recent prints rather than the whole session, so
        # this is a floor, not the day's true off-exchange percentage.
        "share_of_day_volume": (round(printed / day_volume * 100, 2)
                                if day_volume else None),
        "largest": rows[0] if rows else None,
        "blocks": len([r for r in rows if r["block"]]),
        "detail": ("The most recent off-exchange prints for this symbol. "
                   "The share of volume is a floor: this is the latest "
                   "window of prints, not the whole session."),
        "source": SOURCE,
    }


def price_levels(symbol: str, top: int = 12) -> dict:
    """
    Where the off-exchange volume actually traded, by price.

    The levels with the most off-exchange volume are where size has been
    accumulating or distributing, which is the reason to look at this feed
    rather than at a list of prints.
    """
    symbol = (symbol or "").upper().strip()
    out = uw.get(f"/api/stock/{symbol}/stock-volume-price-levels")
    if out["status"] != "OK":
        return {"symbol": symbol, "status": out["status"], "rows": [],
                "detail": out.get("detail"), "source": SOURCE}

    rows = []
    for r in uw._rows(out):
        off = _f(r.get("off_vol")) or 0.0
        lit = _f(r.get("lit_vol")) or 0.0
        if not off and not lit:
            continue
        rows.append({
            "price": _f(r.get("price")),
            "off_exchange_volume": off,
            "lit_volume": lit,
            "total_volume": off + lit,
            "off_share": round(off / (off + lit) * 100, 1) if (off + lit) else None,
        })

    rows.sort(key=lambda r: -(r["off_exchange_volume"] or 0))
    top_rows = rows[:top]
    return {
        "symbol": symbol,
        "status": "OK" if top_rows else "NO_DATA",
        "rows": sorted(top_rows, key=lambda r: -(r["price"] or 0)),
        "levels_seen": len(rows),
        "heaviest": top_rows[0] if top_rows else None,
        "detail": ("Price levels with the most off-exchange volume today -- "
                   "where size has been changing hands away from the lit "
                   "book."),
        "source": SOURCE,
    }


# ---------------------------------------------------------------------------
# insiders, across the market
# ---------------------------------------------------------------------------


def market_insiders(days: int = 30) -> dict:
    """
    What company insiders are doing across the market, day by day.

    Insiders sell for many reasons -- taxes, diversification, a plan adopted
    months ago -- and buy for essentially one. So the buy side is the part
    worth reading, and the ratio is reported rather than a net dollar figure
    that one large sale would dominate.
    """
    out = uw.get("/api/market/insider-buy-sells", {"limit": max(days, 1)})
    if out["status"] != "OK":
        return {"status": out["status"], "rows": [],
                "detail": out.get("detail"), "source": SOURCE}

    rows = []
    for r in uw._rows(out)[:days]:
        buys = _f(r.get("purchases")) or 0.0
        sells = _f(r.get("sells")) or 0.0
        buy_value = abs(_f(r.get("purchases_notional")) or 0.0)
        sell_value = abs(_f(r.get("sells_notional")) or 0.0)
        rows.append({
            "date": str(r.get("filing_date") or "")[:10],
            "buys": buys,
            "sells": sells,
            "buy_value": buy_value,
            "sell_value": sell_value,
            "buy_share": (round(buys / (buys + sells) * 100, 1)
                          if (buys + sells) else None),
            "lean": ("BUYING" if buys > sells else "SELLING"
                     if sells > buys else "BALANCED"),
        })

    rows.sort(key=lambda r: r["date"], reverse=True)
    buys = sum(r["buys"] for r in rows)
    sells = sum(r["sells"] for r in rows)
    buy_value = sum(r["buy_value"] for r in rows)
    sell_value = sum(r["sell_value"] for r in rows)

    return {
        "status": "OK" if rows else "NO_DATA",
        "rows": rows,
        "days": len(rows),
        "total_buys": buys,
        "total_sells": sells,
        "buy_value": round(buy_value, 2),
        "sell_value": round(sell_value, 2),
        "buy_share": round(buys / (buys + sells) * 100, 1) if (buys + sells) else None,
        "lean": ("BUYING" if buys > sells else "SELLING" if sells > buys
                 else "BALANCED"),
        "detail": ("Insider filings across the market. Selling is routine -- "
                   "taxes, diversification, pre-adopted plans -- so the buy "
                   "side is the side that carries information."),
        "source": SOURCE,
    }


def sector_insiders(sector: str, limit: int = 30) -> dict:
    """Insider buying and selling within one sector."""
    import urllib.parse

    out = uw.get(f"/api/insider/{urllib.parse.quote(sector)}/sector-flow",
                 {"limit": limit})
    if out["status"] != "OK":
        return {"sector": sector, "status": out["status"], "rows": [],
                "detail": out.get("detail"), "source": SOURCE}

    rows = [{
        "date": str(r.get("date") or "")[:10],
        "sector": r.get("sector"),
        "side": str(r.get("buy_sell") or "").upper() or None,
        "transactions": _f(r.get("transactions")),
        "shares": _f(r.get("volume")),
        "premium": _f(r.get("premium")),
        "insiders": _f(r.get("uniq_insiders")),
        "tickers": _f(r.get("uniq_tickers")),
        # A trade running off a plan adopted months ago is not a decision
        # taken this week, and is counted apart for that reason.
        "planned_transactions": _f(r.get("transactions_10b5")),
    } for r in uw._rows(out)]

    rows.sort(key=lambda r: r["date"] or "", reverse=True)
    return {"sector": sector, "status": "OK" if rows else "NO_DATA",
            "rows": rows, "count": len(rows),
            "detail": "Insider activity within this sector, newest first.",
            "source": SOURCE}


SECTORS = ("Technology", "Financial Services", "Healthcare",
           "Consumer Cyclical", "Consumer Defensive", "Energy",
           "Industrials", "Communication Services", "Basic Materials",
           "Real Estate", "Utilities")


def insider_sector_board(limit: int = 10) -> dict:
    """Every sector's insider lean, so one screen shows where the buying is."""
    rows = []
    for sector in SECTORS:
        out = sector_insiders(sector, limit)
        if out["status"] != "OK":
            continue
        buys = [r for r in out["rows"] if r["side"] == "BUY"]
        sells = [r for r in out["rows"] if r["side"] == "SELL"]
        buy_tx = sum(r["transactions"] or 0 for r in buys)
        sell_tx = sum(r["transactions"] or 0 for r in sells)
        rows.append({
            "sector": sector,
            "buy_transactions": buy_tx,
            "sell_transactions": sell_tx,
            "buy_share": (round(buy_tx / (buy_tx + sell_tx) * 100, 1)
                          if (buy_tx + sell_tx) else None),
            "insiders": sum(r["insiders"] or 0 for r in out["rows"]),
            "lean": ("BUYING" if buy_tx > sell_tx else "SELLING"
                     if sell_tx > buy_tx else "BALANCED"),
        })

    rows.sort(key=lambda r: -(r["buy_share"] or 0))
    return {"status": "OK" if rows else "NO_DATA", "rows": rows,
            "count": len(rows),
            "detail": ("Insider lean by sector over the last few filing "
                       "days. Buying is the rarer signal of the two."),
            "source": SOURCE}
