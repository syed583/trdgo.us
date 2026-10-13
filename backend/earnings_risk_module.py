"""Earnings Exit & Risk Protection -- Confidence + Exit.

Implements two of the modules from the Earnings Exit & AI Risk Protection plan,
shown as two inner tabs on the Earnings Trade detail page:

  CONFIDENCE (Step 3) -- a 0-100 points-weighted trust score for the position,
    with a Green / Yellow / Red band and, when Red, an ATM hedge suggestion
    (Step 4): long -> protective PUT, short -> protective CALL.

  EXIT (Steps 5 + 6) -- exit levels fixed at entry from the implied move
    (hard stop, target, trailing, time stop) and a direction-aware Exit Urgency
    score (guidance / EPS+rev / price action / volume) reading EXIT NOW /
    TIGHTEN / HOLD. The hard stop always overrides the urgency score.

Pure functions off data already computed in get_summary -- no extra I/O. All
points, thresholds and multipliers are starting values and easy to tune.
"""
from __future__ import annotations

from typing import Optional


def _f(v) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _lean_sign(leaning: Optional[str]) -> Optional[float]:
    if not leaning:
        return None
    s = leaning.lower()
    if "bull" in s:
        return 1.0
    if "bear" in s:
        return -1.0
    return 0.0


def _params_by_name(equity: dict) -> dict:
    return {p.get("name"): p for p in (equity.get("params") or []) if p.get("name")}


# ---------------------------------------------------------------- Confidence
def _confidence(side: int, equity: dict, em_pct: Optional[float],
                hist: list) -> dict:
    """side: +1 long (BUY), -1 short (SELL)."""
    by = _params_by_name(equity)
    rows: list[dict] = []

    def row(key, label, pts, maxpts, note, available=True):
        rows.append({"key": key, "label": label, "points": round(pts, 1),
                     "max": maxpts, "note": note, "available": available})

    # 1) Model score strength (25): how far the earnings score sits above the
    #    decision floor (~40) toward a strong read (~78).
    score = _f(equity.get("score"))
    if score is None:
        row("model", "Model score strength", 0, 25, "No score.", False)
    else:
        frac = _clamp01((score - 40) / 38.0)
        row("model", "Model score strength", 25 * frac, 25,
            f"Earnings score {score:.0f}/100.")

    # 2) Historical earnings behaviour (20): share of the last reports that
    #    moved in the trade's direction.
    moves = [m for m in (hist or []) if _f(m.get("move_pct")) is not None]
    if moves and side != 0:
        agree = sum(1 for m in moves if (m["move_pct"] > 0) == (side > 0))
        cons = agree / len(moves)
        row("history", "Historical earnings behaviour", 20 * cons, 20,
            f"{agree}/{len(moves)} past reports moved your way.")
    else:
        row("history", "Historical earnings behaviour", 0, 20,
            "No post-earnings history.", False)

    # 3) Implied move vs actual move (20): options pricing a much bigger move
    #    than the stock usually makes = more uncertainty = fewer points.
    avg_abs = (sum(abs(m["move_pct"]) for m in moves) / len(moves)) if moves else None
    if em_pct and avg_abs is not None:
        ratio = _clamp01(avg_abs / em_pct)
        row("impvact", "Implied move vs actual move", 20 * ratio, 20,
            f"Implied ±{em_pct:.1f}% vs avg actual {avg_abs:.1f}%.")
    else:
        row("impvact", "Implied move vs actual move", 10, 20,
            "Not enough data to compare.", False)

    # 4) Volatility / beta (15): proxy off the implied move -- a jumpier stock
    #    (wider expected move) earns fewer points.
    if em_pct is not None:
        calm = _clamp01((15.0 - em_pct) / 12.0)
        row("vol", "Volatility / beta", 15 * calm, 15,
            f"Expected move ±{em_pct:.1f}%.")
    else:
        row("vol", "Volatility / beta", 7.5, 15, "No expected move.", False)

    # 5) Analyst revision trend (10): EPS + revenue leanings aligned with side.
    eps, rev = by.get("eps_estimates"), by.get("revenue_estimates")
    signs = [s for s in (_lean_sign(eps and eps.get("leaning")),
                         _lean_sign(rev and rev.get("leaning"))) if s is not None]
    if signs and side != 0:
        align = _clamp01(sum(signs) / len(signs) * side * 0.5 + 0.5)
        row("revision", "Analyst revision trend", 10 * align, 10,
            "Estimate revisions " + ("support" if align >= 0.5 else "fight")
            + " the trade.")
    else:
        row("revision", "Analyst revision trend", 5, 10, "No revision data.", False)

    # 6) Market / sector trend (10): sector_market leaning aligned with side.
    sm = by.get("sector_market")
    smsign = _lean_sign(sm and sm.get("leaning"))
    if smsign is not None and side != 0:
        align = _clamp01(smsign * side * 0.5 + 0.5)
        row("sector", "Market / sector trend", 10 * align, 10,
            "Sector & market " + ("with" if align >= 0.5 else "against") + " you.")
    else:
        row("sector", "Market / sector trend", 5, 10, "No regime read.", False)

    total = round(sum(r["points"] for r in rows), 1)
    if total >= 75:
        band, color, action = "GREEN", "green", "Full size, no hedge."
    elif total >= 50:
        band, color, action = "YELLOW", "yellow", "Reduce size (50-70%) or partial hedge."
    else:
        band, color, action = "RED", "red", "Add protection (see hedge) or skip the trade."

    hedge = None
    if band == "RED" and side != 0:
        leg = "PUT" if side > 0 else "CALL"
        hedge = {
            "leg": leg,
            "strike": "ATM (slightly ITM if the result looks very risky)",
            "expiry": "First weekly expiry right after earnings",
            "note": f"Protect the {'long' if side > 0 else 'short'} with an ATM {leg}. "
                    f"If premium > 4% of position value, reduce size instead.",
        }

    return {"total": total, "band": band, "color": color, "action": action,
            "rows": rows, "hedge": hedge}


# --------------------------------------------------------------------- Exit
def _exit(side: int, spot: Optional[float], em_pct: Optional[float],
          em_dollars: Optional[float], change_pct: Optional[float],
          equity: dict,
          stop_mult: float = 0.5, target_mult: float = 1.0,
          exit_mode: str = "score") -> dict:
    by = _params_by_name(equity)
    levels: list[dict] = []
    im = _f(em_dollars)
    if im is None and spot is not None and em_pct is not None:
        im = spot * em_pct / 100.0

    if exit_mode == "implied" and spot is not None and im is not None and side != 0:
        # Old "Claude +-" exit: implied-move hard stop + target.
        hard = spot - side * stop_mult * im
        tgt = spot + side * target_mult * im
        levels = [
            {"key": "stop", "label": "Hard Stop Loss",
             "value": round(hard, 2),
             "formula": f"entry {'-' if side > 0 else '+'} {stop_mult}× implied move",
             "action": "EXIT immediately, no questions"},
            {"key": "target", "label": "Target",
             "value": round(tgt, 2),
             "formula": f"entry {'+' if side > 0 else '-'} {target_mult}× implied move",
             "action": "Book half the position"},
            {"key": "trail", "label": "Trailing Stop", "value": None,
             "formula": "On a favourable move, lift stop to entry, then trail price",
             "action": "EXIT the rest when hit"},
            {"key": "time", "label": "Time Stop", "value": None,
             "formula": "No meaningful move 1-2 trading days after the result",
             "action": "EXIT -- don't leave money stuck"},
        ]
    elif spot is not None and side != 0:
        # The exit is a score cross: a long exits once the score falls back
        # below 50, a short once it rises above 50 -- at the live price.
        rule = ("Exit if the score falls below 50" if side > 0
                else "Exit if the score rises above 50")
        levels = [
            {"key": "stop", "label": "Score Exit",
             "value": round(spot, 2),
             "formula": rule + " — exit at the live price",
             "action": "EXIT at market"},
        ]
        if im is not None:
            tgt = spot + side * target_mult * im
            levels.append(
                {"key": "target", "label": "Target",
                 "value": round(tgt, 2),
                 "formula": f"entry {'+' if side > 0 else '-'} {target_mult}× implied move",
                 "action": "Book half the position"})
        levels += [
            {"key": "trail", "label": "Trailing Stop", "value": None,
             "formula": "On a favourable move, lift stop to entry, then trail price",
             "action": "EXIT the rest when hit"},
            {"key": "time", "label": "Time Stop", "value": None,
             "formula": "No meaningful move 1-2 trading days after the result",
             "action": "EXIT -- don't leave money stuck"},
        ]

    # Exit Urgency Score (provisional pre-result; finalises when actuals land).
    rows: list[dict] = []

    def urow(key, label, pts, maxpts, note, available=True):
        rows.append({"key": key, "label": label, "points": round(pts, 1),
                     "max": maxpts, "note": note, "available": available})

    # Guidance vs expectation (35): guidance leaning against the position.
    g = by.get("guidance")
    gs = _lean_sign(g and g.get("leaning"))
    if gs is not None and side != 0:
        against = _clamp01(-gs * side * 0.5 + 0.5)
        urow("guidance", "Guidance vs expectation", 35 * against, 35,
             "Guidance " + ("against" if against >= 0.5 else "supports") + " you.")
    else:
        urow("guidance", "Guidance vs expectation", 0, 35, "Awaiting guidance.", False)

    # EPS + revenue vs estimate (20): leanings against the position.
    eps, rev = by.get("eps_estimates"), by.get("revenue_estimates")
    signs = [s for s in (_lean_sign(eps and eps.get("leaning")),
                         _lean_sign(rev and rev.get("leaning"))) if s is not None]
    if signs and side != 0:
        avg = sum(signs) / len(signs)
        against = _clamp01(-avg * side * 0.5 + 0.5)
        urow("epsrev", "EPS + revenue vs estimate", 20 * against, 20,
             "Estimates " + ("against" if against >= 0.5 else "support") + " you.")
    else:
        urow("epsrev", "EPS + revenue vs estimate", 0, 20, "Awaiting actuals.", False)

    # Price action vs implied (25): move against you relative to the implied move.
    cp = _f(change_pct)
    if cp is not None and em_pct:
        adverse = max(0.0, -cp * side) / em_pct  # fraction of implied, against you
        urow("price", "Price action vs implied", 25 * _clamp01(adverse), 25,
             f"{cp:+.1f}% move ({'against' if cp * side < 0 else 'with'} you).")
    else:
        urow("price", "Price action vs implied", 0, 25, "No price move yet.", False)

    # Volume / liquidity (20): conviction behind the move -- proxy off price
    #    action param strength when available.
    pa = by.get("price_action")
    if pa is not None and _f(pa.get("points")) is not None and side != 0:
        conv = _clamp01(-(_f(pa.get("points")) or 0) * side / (abs(_f(pa.get("weight")) or 14) or 14) + 0.0)
        urow("volume", "Volume / liquidity", 20 * conv, 20,
             "Flow " + ("confirms the adverse move." if conv >= 0.5 else "not against you."))
    else:
        urow("volume", "Volume / liquidity", 0, 20, "No volume read.", False)

    urg = round(sum(r["points"] for r in rows), 1)
    if urg >= 70:
        u_band, u_color, u_signal = "EXIT NOW", "red", "Exit immediately (close the hedge too)."
    elif urg >= 40:
        u_band, u_color, u_signal = "TIGHTEN", "yellow", "Tighten the stop, cut half, watch 15 min."
    else:
        u_band, u_color, u_signal = "HOLD / TRAIL", "green", "Result favours you -- trail the stop."

    # Pre-earnings reminder (Step 2).
    pre = ("Options straddle: BOOK PROFIT near +25%; DEFAULT EXIT 1 day before earnings. "
           "Equity: book half when 50% of target is reached before the result.")

    return {
        "side": "LONG" if side > 0 else "SHORT" if side < 0 else "FLAT",
        "levels": levels,
        "urgency": {"total": urg, "band": u_band, "color": u_color,
                    "signal": u_signal, "rows": rows,
                    "note": "Provisional pre-result — finalises when the result is out. "
                            "The score exit always overrides this score."},
        "pre_earnings": pre,
    }


def build(*, decision: Optional[str], equity: Optional[dict],
          spot: Optional[float], change_pct: Optional[float],
          em_pct: Optional[float], em_dollars: Optional[float],
          historical_moves: Optional[list],
          exit_mode: str = "score") -> Optional[dict]:
    """Return {confidence, exit} for a directional equity earnings trade."""
    d = (decision or "").upper()
    # Accept the STRONG tier too ("STRONG BUY" / "STRONG SELL").
    side = 1 if "BUY" in d else -1 if "SELL" in d else 0
    if side == 0:
        return None
    eq = equity or {}
    return {
        "side": "LONG" if side > 0 else "SHORT",
        "confidence": _confidence(side, eq, _f(em_pct), historical_moves or []),
        "exit": _exit(side, _f(spot), _f(em_pct), _f(em_dollars),
                      _f(change_pct), eq, exit_mode=exit_mode),
    }
