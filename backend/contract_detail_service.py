"""
The per-contract detail behind a tape row, from Unusual Whales.

Clicking a print on the flow screen opens one option contract. Unusual Whales
answers that with four reads, and this gathers the same four into one payload:

* **Summary** -- the contract's intraday price with its volume split, from
  ``/intraday``.
* **Time & Sales** -- every print on the contract, from ``/flow`` (mapped by
  the same ``_print_row`` the tape uses, so a row reads identically here).
* **Volume** -- volume by fill and by source (sweep / floor / cross / multi),
  from ``/volume-profile``.
* **History** -- daily volume and open interest, from ``/historic``.

The **Analysis** block is computed here rather than fetched: a Black-Scholes
probability of finishing past breakeven, the breakeven itself, and a Kelly
fraction derived from that probability. It is a model estimate, labelled as
descriptive rather than advice -- this app analyses, it does not advise.
"""

from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Optional

import black_scholes as bs
import unusualwhales_service as uw
import uw_flow_service as flow

SOURCE = "Unusual Whales"

# NVDA271217C00250000 -> ("NVDA", "2027-12-17", "C", 250.0)
_OCC = re.compile(r"^([A-Z]+)(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")


def _f(value) -> Optional[float]:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def parse_occ(occ: str) -> Optional[dict]:
    """Pull the symbol, expiry, right and strike out of an OCC symbol."""
    m = _OCC.match((occ or "").strip().upper())
    if not m:
        return None
    sym, yy, mm, dd, right, strike = m.groups()
    return {
        "symbol": sym,
        "expiry": f"20{yy}-{mm}-{dd}",
        "right": right,
        "strike": int(strike) / 1000.0,
    }


# Cash-settled index options print without an underlying price attached, so
# the model has to source the index level itself. These roots map to the cards
# the market bar already carries (S&P 500 via SPY, etc.).
_INDEX_ROOTS = {
    "SPX": "S&P 500", "SPXW": "S&P 500",
    "NDX": "Nasdaq 100", "NDXP": "Nasdaq 100",
    "RUT": "Russell 2000", "RUTW": "Russell 2000",
    "DJX": "Dow Jones",
    "VIX": "VIX", "VIXW": "VIX",
}


def _underlying_spot(symbol: str) -> Optional[float]:
    """
    The underlying price for a contract whose prints did not carry one.

    Index options (SPX, SPXW, NDX, RUT, VIX ...) are cash-settled and arrive
    with no underlying attached, so the level is read from the same index cards
    the market bar shows. Everything else is a regular quote.
    """
    sym = (symbol or "").upper()
    label = _INDEX_ROOTS.get(sym)
    if label:
        try:
            import market_pulse_service as mp
            for i in mp.get_topbar().get("indices", []):
                if i.get("label") == label and i.get("price"):
                    return float(i["price"])
        except Exception:  # noqa: BLE001 - fall through to a quote
            pass
    try:
        import live_market_service as market
        price = (market.get_quote(sym) or {}).get("price")
        return float(price) if price is not None else None
    except Exception:  # noqa: BLE001
        return None


def _dte(expiry: Optional[str]) -> Optional[int]:
    if not expiry:
        return None
    try:
        return (date.fromisoformat(str(expiry)[:10])
                - datetime.now(timezone.utc).date()).days
    except (TypeError, ValueError):
        return None


def _intraday(occ: str) -> dict:
    """The contract's intraday bars with the bid/ask/mid volume split."""
    out = uw.get(f"/api/option-contract/{occ}/intraday")
    if out.get("status") != "OK":
        return {"status": out.get("status", "NO_DATA"), "bars": []}
    bars = []
    for r in uw._rows(out):
        bars.append({
            "time": r.get("start_time"),
            "close": _f(r.get("close")),
            "high": _f(r.get("high")),
            "low": _f(r.get("low")),
            "open": _f(r.get("open")),
            "avg_price": _f(r.get("avg_price")),
            "volume_ask": _f(r.get("volume_ask_side")) or 0.0,
            "volume_bid": _f(r.get("volume_bid_side")) or 0.0,
            "volume_mid": _f(r.get("volume_mid_side")) or 0.0,
            "iv_high": _f(r.get("iv_high")),
            "iv_low": _f(r.get("iv_low")),
        })
    bars.sort(key=lambda b: str(b["time"]))
    return {"status": "OK" if bars else "NO_DATA", "bars": bars}


def _time_sales(occ: str, limit: int = 100) -> dict:
    """Every print on the contract, in the same shape the tape renders."""
    out = uw.get(f"/api/option-contract/{occ}/flow", {"limit": limit})
    if out.get("status") != "OK":
        return {"status": out.get("status", "NO_DATA"), "trades": []}
    trades = [flow._print_row(r) for r in uw._rows(out)]
    trades.sort(key=lambda t: -(t.get("epoch") or 0))
    return {"status": "OK" if trades else "NO_DATA", "trades": trades}


def _volume_profile(occ: str) -> dict:
    """
    Volume by fill and by source, for the two charts the Volume tab draws:
    a by-price histogram split bid/ask/mid, and the by-source totals
    (normal / sweep / floor / cross / multi-leg).
    """
    out = uw.get(f"/api/option-contract/{occ}/volume-profile")
    if out.get("status") != "OK":
        return {"status": out.get("status", "NO_DATA"), "by_price": [],
                "by_source": {}}

    by_price = []
    acc = {"sweep": 0.0, "floor": 0.0, "cross": 0.0, "multi": 0.0,
           "ask": 0.0, "bid": 0.0, "mid": 0.0, "total": 0.0}
    for r in uw._rows(out):
        vol = _f(r.get("volume")) or 0.0
        by_price.append({
            "price": _f(r.get("price")),
            "volume": vol,
            "ask_vol": _f(r.get("ask_vol")) or 0.0,
            "bid_vol": _f(r.get("bid_vol")) or 0.0,
            "mid_vol": _f(r.get("mid_vol")) or 0.0,
        })
        acc["ask"] += _f(r.get("ask_vol")) or 0.0
        acc["bid"] += _f(r.get("bid_vol")) or 0.0
        acc["mid"] += _f(r.get("mid_vol")) or 0.0
        acc["sweep"] += _f(r.get("sweep_vol")) or 0.0
        acc["floor"] += _f(r.get("floor_vol")) or 0.0
        acc["cross"] += _f(r.get("cross_vol")) or 0.0
        acc["multi"] += _f(r.get("multi_vol")) or 0.0
        acc["total"] += vol
    by_price.sort(key=lambda b: (b["price"] or 0))

    # Two different questions, kept apart. ``by_side`` is where in the spread
    # the volume traded (bid/ask/mid) and partitions the total. ``by_source``
    # is the trade mechanism (sweep/floor/cross vs an ordinary print) -- these
    # are exclusive, so "normal" is whatever was none of them; ``multi`` (a leg
    # count, which overlaps the others) is reported alongside, not inside the
    # partition. Mixing the two in one chart double-counted the volume.
    total = acc["total"]
    typed = acc["sweep"] + acc["floor"] + acc["cross"]
    normal = max(0.0, total - typed)
    return {
        "status": "OK" if by_price else "NO_DATA",
        "by_price": by_price,
        "by_side": {"ask": round(acc["ask"]), "bid": round(acc["bid"]),
                    "mid": round(acc["mid"])},
        "by_source": {"normal": round(normal), "sweep": round(acc["sweep"]),
                      "floor": round(acc["floor"]), "cross": round(acc["cross"])},
        "multi": round(acc["multi"]),
        "total": round(total),
    }


def _history(occ: str, limit: int = 30) -> dict:
    """Daily volume, open interest and settle for the historical table."""
    out = uw.get(f"/api/option-contract/{occ}/historic", {"limit": limit})
    if out.get("status") != "OK":
        return {"status": out.get("status", "NO_DATA"), "rows": []}
    rows = []
    for r in uw._rows(out):
        rows.append({
            "date": r.get("date"),
            "volume": _f(r.get("volume")),
            "open_interest": _f(r.get("open_interest")),
            "last_price": _f(r.get("last_price")),
            "high_price": _f(r.get("high_price")),
            "low_price": _f(r.get("low_price")),
            "iv": (round(_f(r.get("implied_volatility")) * 100, 1)
                   if _f(r.get("implied_volatility")) is not None else None),
            "total_premium": _f(r.get("total_premium")),
        })
    rows.sort(key=lambda x: str(x["date"]), reverse=True)
    return {"status": "OK" if rows else "NO_DATA", "rows": rows}


def _analysis(meta: dict, latest: dict) -> dict:
    """
    A model read of the contract, held to expiry: the probability of finishing
    past breakeven, the breakeven, and the Kelly fraction that probability
    implies. Descriptive, not advice.
    """
    spot = _f(latest.get("underlying_price"))
    iv = _f(latest.get("iv"))
    price = _f(latest.get("price")) or _f(latest.get("ask"))
    strike = _f(meta.get("strike"))
    right = meta.get("right")
    dte = meta.get("dte")

    if not (spot and iv and price and strike and dte and dte > 0 and iv > 0):
        return {"status": "NO_DATA",
                "detail": "Not enough live data to model this contract."}

    is_call = right == "C"
    t = dte / 365.0
    sigma_rt = iv * math.sqrt(t)
    breakeven = strike + price if is_call else strike - price
    if breakeven <= 0:
        return {"status": "NO_DATA",
                "detail": "Not enough live data to model this contract."}

    # Risk-neutral probability of expiring past breakeven (r = q = 0).
    d = (math.log(spot / breakeven) - 0.5 * iv * iv * t) / sigma_rt
    prob_above = bs._norm_cdf(d)
    pop = prob_above if is_call else 1.0 - prob_above
    pop = max(0.0, min(1.0, pop))

    # A payoff ratio from a one-sigma favourable move, so Kelly has a b to
    # work with. The move is the market's own expected move (spot * IV * sqrt t).
    move = spot * sigma_rt
    target = spot + move if is_call else spot - move
    gain = max(0.0, (target - breakeven) if is_call else (breakeven - target))
    b = gain / price if price else 0.0

    q = 1.0 - pop
    full_kelly = (b * pop - q) / b if b > 0 else 0.0
    full_kelly = max(0.0, full_kelly)

    # The move the underlying must make to reach breakeven, in percent -- the
    # first thing a trader wants to know, ahead of any probability.
    move_needed = ((breakeven - spot) / spot * 100.0) if is_call \
        else ((spot - breakeven) / spot * 100.0)
    direction = "rise" if is_call else "fall"
    # Reward-to-risk measured at one expected move in the trade's favour:
    # the dollars won per dollar risked (the premium) on a typical-sized move.
    reward_to_risk = round(b, 2)

    pop_pct = round(pop * 100)
    sym = meta.get("symbol")
    if move_needed <= 0:
        move_phrase = (f"{sym} is already past breakeven "
                       f"(${breakeven:,.2f}), so this is intrinsic-heavy")
    else:
        move_phrase = (f"{sym} needs to {direction} {move_needed:.1f}% to "
                       f"${breakeven:,.2f}")
    if full_kelly > 0.02:
        edge_phrase = (f"the model sees an edge here (Kelly suggests up to "
                       f"{round(full_kelly * 100, 1)}% of the stack)")
    elif full_kelly > 0:
        edge_phrase = "the model sees only a razor-thin edge"
    else:
        edge_phrase = "the model sees no mathematical edge at this price"
    verdict = (f"{move_phrase} by {meta.get('expiry')} ({dte}d). About "
               f"{pop_pct}% chance of profit if held to expiry, roughly "
               f"{reward_to_risk}:1 reward-to-risk on a one-move swing, and "
               f"{edge_phrase}.")

    return {
        "status": "OK",
        "verdict": verdict,
        "move_needed_percent": round(move_needed, 2),
        "move_direction": direction,
        "reward_to_risk": reward_to_risk,
        "probability_of_profit": round(pop * 100, 2),
        "probability_of_loss": round(q * 100, 2),
        "breakeven": round(breakeven, 2),
        "odds_ratio": round(pop / q, 2) if q > 0 else None,
        "payoff_ratio": round(b, 2),
        "max_loss_per_contract": round(price * 100, 2),
        "expected_move": round(move, 2),
        "full_kelly": round(full_kelly * 100, 2),
        "half_kelly": round(full_kelly * 50, 2),
        "quarter_kelly": round(full_kelly * 25, 2),
        "delta": _f(latest.get("delta")),
        "iv_percent": round(iv * 100, 1),
        "dte": dte,
        "detail": ("A Black-Scholes estimate of finishing past breakeven, held "
                   "to expiry, with the Kelly fraction it implies. Descriptive, "
                   "not advice."),
    }


def get_contract(occ: str) -> dict:
    """Everything the contract-detail popup shows, in one payload."""
    occ = (occ or "").strip().upper()
    meta = parse_occ(occ)
    if not meta:
        return {"status": "INVALID_CONTRACT", "occ": occ, "source": SOURCE}
    meta["dte"] = _dte(meta.get("expiry"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_ts = pool.submit(_time_sales, occ)
        f_intraday = pool.submit(_intraday, occ)
        f_vprof = pool.submit(_volume_profile, occ)
        f_hist = pool.submit(_history, occ)
        time_sales = f_ts.result()
        intraday = f_intraday.result()
        vprof = f_vprof.result()
        history = f_hist.result()

    latest = (time_sales.get("trades") or [{}])[0]
    # Cash-settled index prints carry no underlying; resolve it so the header
    # and the model both have a spot to work with.
    spot = latest.get("underlying_price")
    if spot is None:
        spot = _underlying_spot(meta["symbol"])
        latest["underlying_price"] = spot

    # Moneyness, so a trader sees at a glance whether the strike is in or out of
    # the money and by how much.
    strike = _f(meta.get("strike"))
    moneyness = None
    moneyness_pct = None
    if spot and strike:
        itm = (spot > strike) if meta["right"] == "C" else (spot < strike)
        moneyness = "ITM" if itm else "OTM"
        moneyness_pct = round((spot - strike) / strike * 100, 2)
    # Carry the live greeks / spot / quote onto the meta so the header reads.
    meta.update({
        "spot": spot,
        "iv_percent": (round(latest["iv"] * 100, 1)
                       if latest.get("iv") is not None else None),
        "bid": latest.get("bid"),
        "ask": latest.get("ask"),
        "last_price": latest.get("price"),
        "delta": latest.get("delta"),
        "gamma": latest.get("gamma"),
        "theta": latest.get("theta"),
        "vega": latest.get("vega"),
        "rho": latest.get("rho"),
        "open_interest": latest.get("open_interest"),
        "volume": latest.get("volume"),
        "moneyness": moneyness,
        "moneyness_pct": moneyness_pct,
    })

    return {
        "status": "OK",
        "occ": occ,
        "meta": meta,
        "summary": intraday,
        "time_sales": time_sales,
        "volume_profile": vprof,
        "history": history,
        "analysis": _analysis(meta, latest),
        "source": SOURCE,
    }
