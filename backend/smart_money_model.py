"""Smart Money -- research/signals tab (behind SMART_MONEY_TAB=on, local only).

A 100-point, all-signed model over six "who's-really-buying" parameters:

  1 Insider News (10)      news sentiment / insider-purchase filings (last 3d)
  2 Insider Buy/Sell (20)  Form-4 open-market trades (code P/S), 10b5-1 excluded
  3 Unusual Options (25)   flow alerts: sweeps/blocks, ask/bid side, net premium
  4 Options Disparity (20) call-premium share + call/put ratio vs 20-day norm
  5 Fund Buy/Sell (15)     latest 13F vs prior quarter (increasers/decreasers)
  6 Positions in Funds(10) institutional ownership trend over recent quarters

Net = sum of signed values (-100..+100); Smart Money Score = 50 + Net/2. A
parameter with no data returns has_data=False, contributes 0 and does NOT count
toward coverage -- never faked. Bias BULLISH >= 65 / BEARISH <= 35 with group
agreement; coverage < 60 -> "Insufficient data", no bias.

Read-only. Reuses unusualwhales_service; all "today"/dates are the New York
session (the server runs on Dubai time). Real-time (options) is kept separate
from delayed (Form 4) and quarterly (13F) data, each with its own cache + a
per-fetch timeout so one slow symbol can't block the scan.
"""
from __future__ import annotations

import concurrent.futures as _cf
from datetime import datetime, date, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001
    _ET = timezone.utc

MODEL = "SMART-MONEY-v1"

MAX = {
    "insider_news": 10, "insider_trades": 20, "unusual": 25,
    "disparity": 20, "fund_flow": 15, "fund_positions": 10,
}  # sum = 100
GROUPS = {
    "insider": ("insider_news", "insider_trades"),   # max 30
    "unusual": ("unusual",),                          # max 25
    "disparity": ("disparity",),                      # max 20
    "funds": ("fund_flow", "fund_positions"),         # max 25
}
COVERAGE_MIN = 60
BUY_CUT, SELL_CUT = 65, 35          # bias (with group agreement)
CALL_BUY, CALL_SELL = 55, 45        # simple score-based call: BUY >= 55 / SELL <= 45

# Cache TTLs (seconds) per the spec: options 60s, insider/news 15m, 13F 24h.
TTL_OPTIONS = 60.0
TTL_INSIDER = 900.0
TTL_13F = 86400.0
_FETCH_TIMEOUT = 8.0


# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #
def enabled() -> bool:
    import os
    return (os.getenv("SMART_MONEY_TAB") or "").strip().lower() in ("on", "1", "true", "yes")


def _f(v) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def today_et() -> date:
    return datetime.now(_ET).date()


def now_et_iso() -> str:
    return datetime.now(_ET).isoformat()


def _et_date(ts) -> Optional[date]:
    if ts is None:
        return None
    try:
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts, timezone.utc)
        else:
            s = str(ts).replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_ET).date()
    except Exception:  # noqa: BLE001
        # plain date strings like '2026-10-15'
        try:
            return date.fromisoformat(str(ts)[:10])
        except Exception:  # noqa: BLE001
            return None


def _timed(fn, timeout: float = _FETCH_TIMEOUT):
    """Run a fetch with a hard timeout so one slow symbol can't block the scan."""
    try:
        with _cf.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(fn).result(timeout=timeout)
    except Exception:  # noqa: BLE001
        return None


def _p(name, value, has_data, detail="", extra=None) -> dict:
    m = MAX[name]
    val = round(max(-m, min(m, value)), 2) if (has_data and value is not None) else 0.0
    return {"name": name, "max": m, "value": val, "has_data": bool(has_data),
            "detail": detail, "extra": extra or {}}


def _clamp(v, lo=-1.0, hi=1.0):
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------- #
#  data fetchers (cached per the spec; reuse unusualwhales_service)
# --------------------------------------------------------------------------- #
def _insider_txns(symbol: str) -> Optional[list]:
    import swr
    import unusualwhales_service as uw

    def _fetch():
        return _timed(lambda: uw._rows(uw.get("/api/insider/transactions",
                                              {"ticker_symbol": symbol, "limit": 200})))

    return swr.serve(f"sm:insider:{symbol}", _fetch, TTL_INSIDER)


def _news(symbol: str) -> Optional[list]:
    import swr
    import unusualwhales_service as uw
    return swr.serve(f"sm:news:{symbol}",
                     lambda: _timed(lambda: uw._rows(uw.headlines(symbol, limit=40))),
                     TTL_INSIDER)


def _flow(symbol: str) -> Optional[list]:
    import swr
    import unusualwhales_service as uw
    # Per-stock flow alerts carry has_sweep / has_floor / total_ask_side_prem /
    # total_bid_side_prem / has_multileg / delta / volume_oi_ratio.
    return swr.serve(f"sm:flow:{symbol}",
                     lambda: _timed(lambda: uw._rows(uw.flow_alerts(symbol, limit=50))),
                     TTL_OPTIONS)


def _opt_vol_series(symbol: str) -> Optional[list]:
    import swr
    import unusualwhales_service as uw
    return swr.serve(f"sm:ovser:{symbol}",
                     lambda: _timed(lambda: uw._rows(uw.get(f"/api/stock/{symbol}/options-volume", {"limit": 25}))),
                     TTL_OPTIONS)


def _ownership(symbol: str) -> Optional[list]:
    import swr
    import unusualwhales_service as uw
    return swr.serve(f"sm:own:{symbol}",
                     lambda: _timed(lambda: uw._rows(uw.institutional_ownership(symbol, limit=100))),
                     TTL_13F)


def _price_change_pct(symbol: str) -> Optional[float]:
    import unusualwhales_service as uw
    q = _timed(lambda: uw.get_quote(symbol))
    if isinstance(q, dict):
        for k in ("change_percent", "changePercent", "percent_change"):
            v = _f(q.get(k))
            if v is not None:
                return v
    return None


# --------------------------------------------------------------------------- #
#  1) Insider News (10) -- news sentiment + insider-purchase filings, last 3d
# --------------------------------------------------------------------------- #
_BULL_KW = ("buyback", "repurchase", "raises guidance", "raised guidance", "beats",
            "contract", "partnership", "approval", "upgrade")
_BEAR_KW = ("offering", "dilut", "resign", "steps down", "guidance cut", "cuts guidance",
            "investigation", "lawsuit", "probe", "downgrade", "auditor")


def _news_eval(news_rows, insider_rows, today) -> tuple:
    if news_rows is None and insider_rows is None:
        return None, "", {}
    headlines = []
    score = 0.0
    n = 0
    for r in (news_rows or []):
        d = _et_date(r.get("created_at"))
        if d is None or (today - d).days > 3 or (today - d).days < 0:
            continue
        text = (r.get("headline") or "").lower()
        sent = (r.get("sentiment") or "").lower()
        lean = 0
        if any(k in text for k in _BEAR_KW) or "neg" in sent or "bear" in sent:
            lean = -1
        elif any(k in text for k in _BULL_KW) or "pos" in sent or "bull" in sent:
            lean = 1
        if lean:
            score += lean
            n += 1
        headlines.append({"headline": r.get("headline"), "date": r.get("created_at"),
                          "source": r.get("source"), "sentiment": r.get("sentiment"),
                          "lean": "bullish" if lean > 0 else "bearish" if lean < 0 else "neutral"})
    # Insider PURCHASE filed in the last 3 days is a bullish news-type event.
    buys = 0
    for r in (insider_rows or []):
        if r.get("transaction_code") == "P" and not r.get("is_10b5_1"):
            fd = _et_date(r.get("filing_date"))
            if fd and 0 <= (today - fd).days <= 3:
                buys += 1
    if buys:
        score += min(buys, 2)
        n += buys
    val = _clamp(score / 2.0) * MAX["insider_news"] if n else 0.0
    detail = f"{len(headlines)} headline(s) 3d" + (f", {buys} insider buy filing(s)" if buys else "")
    return val, detail, {"headlines": headlines[:12], "insider_buy_filings": buys}


def p_insider_news(symbol: str) -> dict:
    news = _news(symbol)
    ins = _insider_txns(symbol)
    val, detail, extra = _news_eval(news, ins, today_et())
    has = not (news is None and ins is None)
    return _p("insider_news", val, has, detail, extra)


# --------------------------------------------------------------------------- #
#  2) Insider Buy/Sell (20) -- Form-4 open-market P/S, 10b5-1 & M excluded
# --------------------------------------------------------------------------- #
def _role_weight(r) -> float:
    title = (r.get("officer_title") or "").lower()
    if any(k in title for k in ("chief executive", "ceo", "chief financial", "cfo")):
        return 2.0
    if r.get("is_officer") or r.get("is_director"):
        return 1.0
    if r.get("is_ten_percent_owner"):
        return 0.5
    return 1.0


def _insider_eval(rows, today) -> tuple:
    if rows is None:
        return None, "", {}
    trades = []
    buyers30 = set()
    ceo_cfo_buy_30 = 0.0
    single_buy_max_30 = 0.0
    disc_sellers_30 = set()
    net_sell_dollar_30 = 0.0
    for r in rows:
        code = r.get("transaction_code")
        if code not in ("P", "S"):             # exclude M (exercise), G (gift), etc.
            continue
        is_plan = bool(r.get("is_10b5_1"))
        td = _et_date(r.get("transaction_date"))
        if td is None:
            continue
        days = (today - td).days
        if days < 0 or days > 90:
            continue
        shares = _f(r.get("amount")) or 0.0            # signed: + buy, - sell
        price = _f(r.get("price")) or _f(r.get("stock_price")) or 0.0
        value = abs(shares) * price
        owner = r.get("owner_name")
        role_w = _role_weight(r)
        trades.append({"owner": owner, "title": r.get("officer_title"),
                       "code": code, "is_10b5_1": is_plan, "shares": shares,
                       "price": price, "value": round(value), "transaction_date": r.get("transaction_date"),
                       "filing_date": r.get("filing_date"),
                       "role": "CEO/CFO" if role_w >= 2 else "officer/director" if role_w >= 1 else "10% owner"})
        if days <= 30:
            if code == "P":                            # open-market purchase
                buyers30.add(owner)
                single_buy_max_30 = max(single_buy_max_30, value)
                if role_w >= 2:
                    ceo_cfo_buy_30 += value
            elif code == "S" and not is_plan:          # discretionary sale only
                disc_sellers_30.add(owner)
                net_sell_dollar_30 += value
    # scoring per spec
    val = 0.0
    if len(buyers30) >= 2 or ceo_cfo_buy_30 >= 100_000:
        val = 20.0
    elif single_buy_max_30 >= 50_000:
        val = 10.0
    if len(disc_sellers_30) >= 3 and net_sell_dollar_30 > 1_000_000:
        val = min(val, 0.0) - 20.0 if net_sell_dollar_30 > 3_000_000 else min(val, 0.0) - 10.0
    detail = (f"{len(buyers30)} buyer(s) 30d, ${ceo_cfo_buy_30/1e3:.0f}k exec buys; "
              f"{len(disc_sellers_30)} discretionary seller(s)")
    return _clamp_val(val), detail, {"trades": trades[:20], "buyers_30d": len(buyers30),
                                     "disc_sellers_30d": len(disc_sellers_30),
                                     "exec_buy_usd_30d": round(ceo_cfo_buy_30),
                                     "net_sell_usd_30d": round(net_sell_dollar_30)}


def _clamp_val(v):
    return max(-MAX["insider_trades"], min(MAX["insider_trades"], v))


def p_insider_trades(symbol: str) -> dict:
    rows = _insider_txns(symbol)
    val, detail, extra = _insider_eval(rows, today_et())
    return _p("insider_trades", val, rows is not None, detail, extra)


# --------------------------------------------------------------------------- #
#  3) Unusual Options (25) -- sweeps/blocks, ask/bid side, net premium
# --------------------------------------------------------------------------- #
def _flow_norm_20d(series) -> Optional[float]:
    if not series:
        return None
    tstr = today_et().isoformat()
    prem = []
    for r in series:
        if str(r.get("date"))[:10] == tstr:
            continue
        c, p = _f(r.get("call_premium")), _f(r.get("put_premium"))
        if c is not None and p is not None:
            prem.append(c + p)
        if len(prem) >= 20:
            break
    if not prem:
        return None
    avg = sum(prem) / len(prem)
    return avg if avg > 0 else None


def _unusual_eval(alerts, norm_20d, today) -> tuple:
    if alerts is None:
        return None, "", {}
    if not norm_20d:
        return None, "normalizer unavailable", {}    # can't normalise -> no data
    prints = []
    net = 0.0
    for a in alerts:
        if not isinstance(a, dict):
            continue
        prem = _f(a.get("total_premium")) or 0.0
        if prem < 100_000:
            continue
        if not (a.get("has_sweep") or a.get("has_floor")):   # sweeps or blocks only
            continue
        if a.get("has_multileg"):                            # exclude spreads
            continue
        voi = _f(a.get("volume_oi_ratio"))
        if voi is not None and voi <= 1:
            continue
        try:
            dte = (date.fromisoformat(str(a.get("expiry"))[:10]) - today).days
        except Exception:  # noqa: BLE001
            continue
        if dte < 0 or dte > 45:
            continue
        typ = (a.get("type") or "").lower()
        delta = abs(_f(a.get("delta")) or 0.0)
        if typ.startswith("p") and delta > 0.85:             # deep-ITM put = likely hedge
            continue
        ask = _f(a.get("total_ask_side_prem")) or 0.0
        bid = _f(a.get("total_bid_side_prem")) or 0.0
        d = _et_date(a.get("created_at"))
        if d is None:
            continue
        age = (today - d).days
        if age < 0 or age > 2:
            continue
        w = 1.0 if age == 0 else 0.5                         # today 100%, prev 2 sessions 50%
        if typ.startswith("c"):                              # call bought at ask = bullish
            contrib = (ask - bid)
        else:                                                # put bought at ask = bearish
            contrib = -(ask - bid)
        net += contrib * w
        prints.append({"time": a.get("created_at"), "type": "CALL" if typ.startswith("c") else "PUT",
                       "strike": _f(a.get("strike")), "expiry": a.get("expiry"),
                       "premium": round(prem), "sweep": bool(a.get("has_sweep")),
                       "block": bool(a.get("has_floor")), "ask_prem": round(ask), "bid_prem": round(bid),
                       "vol_oi": voi, "side": "bull" if (typ.startswith("c")) == (ask >= bid) else "bear"})
    val = _clamp(net / norm_20d) * MAX["unusual"]
    prints.sort(key=lambda x: x["premium"], reverse=True)
    detail = f"net ${net/1e6:.2f}M vs 20d avg ${norm_20d/1e6:.1f}M"
    return val, detail, {"prints": prints[:25], "net_premium": round(net)}


def p_unusual(symbol: str) -> dict:
    alerts = _flow(symbol)
    norm = _flow_norm_20d(_opt_vol_series(symbol))
    val, detail, extra = _unusual_eval(alerts, norm, today_et())
    return _p("unusual", val, alerts is not None and norm is not None, detail, extra)


# --------------------------------------------------------------------------- #
#  4) Options Disparity (20) -- call-premium share + call/put vs 20-day
# --------------------------------------------------------------------------- #
def _disparity_eval(series, price_chg, today) -> tuple:
    if not series:
        return None, "", {}
    tstr = today_et().isoformat()
    today_row = next((r for r in series if str(r.get("date"))[:10] == tstr), None)
    if today_row is None:
        return None, "no today options-volume row", {}
    cp, pp = _f(today_row.get("call_premium")), _f(today_row.get("put_premium"))
    cv, pv = _f(today_row.get("call_volume")), _f(today_row.get("put_volume"))
    if cp is None or pp is None or (cp + pp) <= 0:
        return None, "", {}
    share = cp / (cp + pp)
    # 20-day average call-premium share (exclude today)
    shares = []
    for r in series:
        if str(r.get("date"))[:10] == tstr:
            continue
        c, p = _f(r.get("call_premium")), _f(r.get("put_premium"))
        if c is not None and p is not None and (c + p) > 0:
            shares.append(c / (c + p))
        if len(shares) >= 20:
            break
    norm = sum(shares) / len(shares) if shares else None
    pc_ratio = (cv / pv) if (cv and pv) else None
    # bullish: call share >= 65% and above norm; bearish mirror; scale between.
    v = 0.0
    if norm is not None:
        if share >= 0.65 and share > norm:
            v = _clamp((share - 0.5) / 0.3) * MAX["disparity"]
        elif share <= 0.35 and share < norm:
            v = -_clamp((0.5 - share) / 0.3) * MAX["disparity"]
        else:
            v = _clamp((share - norm) / 0.15) * MAX["disparity"]
    else:
        v = _clamp((share - 0.5) / 0.15) * MAX["disparity"]
    # divergence flag (no points): flow bullish but price down, or vice-versa.
    divergence = None
    if price_chg is not None:
        if v > 0 and price_chg < -0.2:
            divergence = "flow bullish but price down"
        elif v < 0 and price_chg > 0.2:
            divergence = "flow bearish but price up"
    detail = f"call prem {share*100:.0f}%" + (f" vs 20d {norm*100:.0f}%" if norm is not None else "")
    return v, detail, {"call_share": round(share, 3), "norm_share": round(norm, 3) if norm is not None else None,
                       "call_put_vol": round(pc_ratio, 2) if pc_ratio else None,
                       "net_delta": None,                       # UW has no aggregate -> no data
                       "divergence": divergence, "price_chg": price_chg}


def p_disparity(symbol: str) -> dict:
    series = _opt_vol_series(symbol)
    val, detail, extra = _disparity_eval(series, _price_change_pct(symbol), today_et())
    return _p("disparity", val, series is not None and (extra or {}).get("call_share") is not None,
              detail, extra)


# --------------------------------------------------------------------------- #
#  5) Fund Buy/Sell (15) -- latest 13F vs prior quarter
# --------------------------------------------------------------------------- #
def _fund_flow_eval(rows) -> tuple:
    if rows is None:
        return None, "", {}
    if not rows:
        return 0.0, "no 13F holders", {"quarter_end": None, "filed": None}
    inc = dec = new = 0
    net_shares = 0.0
    shares_out = None
    report_date = filing_date = None
    for r in rows:
        ch = _f(r.get("units_changed"))
        if ch is None:
            continue
        net_shares += ch
        if ch > 0:
            inc += 1
        elif ch < 0:
            dec += 1
        if r.get("first_buy") and str(r.get("first_buy"))[:7] == str(r.get("report_date"))[:7]:
            new += 1
        shares_out = shares_out or _f(r.get("shares_outstanding"))
        report_date = report_date or r.get("report_date")
        filing_date = filing_date or r.get("filing_date")
    float_pct = (net_shares / shares_out * 100) if (shares_out and shares_out > 0) else None
    ratio = (inc / dec) if dec else (inc if inc else 0)
    v = 0.0
    if dec and inc / dec >= 1.5 and net_shares > 0:
        v = MAX["fund_flow"] * _clamp(((inc / dec) - 1.0) / 1.5)
    elif inc and dec / max(inc, 1) >= 1.5 and net_shares < 0:
        v = -MAX["fund_flow"] * _clamp(((dec / max(inc, 1)) - 1.0) / 1.5)
    elif net_shares != 0:
        v = _clamp(net_shares / (abs(shares_out) * 0.01) if shares_out else 0) * MAX["fund_flow"]
    detail = f"{inc} increasing / {dec} decreasing, net {net_shares:+,.0f} sh"
    return v, detail, {"increasers": inc, "decreasers": dec, "new_positions": new,
                       "closed_positions": None,            # exited funds absent -> no data
                       "net_shares": round(net_shares), "pct_float": round(float_pct, 3) if float_pct is not None else None,
                       "quarter_end": str(report_date)[:10] if report_date else None,
                       "filed": str(filing_date)[:10] if filing_date else None}


def p_fund_flow(symbol: str) -> dict:
    rows = _ownership(symbol)
    val, detail, extra = _fund_flow_eval(rows)
    return _p("fund_flow", val, rows is not None, detail, extra)


# --------------------------------------------------------------------------- #
#  6) Positions in Funds (10) -- ownership trend over quarters
# --------------------------------------------------------------------------- #
def _fund_positions_eval(rows) -> tuple:
    if rows is None:
        return None, "", {}
    if not rows:
        return 0.0, "no 13F holders", {}
    shares_out = _f(rows[0].get("shares_outstanding"))
    # aggregate institutional units per quarter from each holder's historical_units
    quarters = None
    agg = None
    top = []
    for r in rows:
        hist = r.get("historical_units") or []
        hist = [_f(x) or 0.0 for x in hist]
        if hist:
            if agg is None:
                agg = list(hist)
            else:
                for i in range(min(len(agg), len(hist))):
                    agg[i] += hist[i]
        top.append({"name": r.get("name") or r.get("short_name"),
                    "units": _f(r.get("units")), "value": _f(r.get("value")),
                    "units_changed": _f(r.get("units_changed")),
                    "pct_float": (round((_f(r.get("units")) or 0) / shares_out * 100, 2)
                                  if shares_out else None)})
    trend = []
    if agg and shares_out:
        trend = [round(u / shares_out * 100, 2) for u in agg]     # inst ownership % by quarter (newest first)
    # rising/falling 2+ quarters in a row (trend is newest-first)
    v = 0.0
    direction = "flat"
    if len(trend) >= 3:
        if trend[0] > trend[1] > trend[2]:
            v = MAX["fund_positions"] * 0.7
            direction = "rising 2+ quarters"
        elif trend[0] < trend[1] < trend[2]:
            v = -MAX["fund_positions"] * 0.7
            direction = "falling 2+ quarters"
    top.sort(key=lambda x: (x["units"] or 0), reverse=True)
    detail = (f"inst {trend[0]:.1f}% ({direction})" if trend else "ownership trend n/a")
    return v, detail, {"ownership_trend_pct": trend[:8], "top_holders": top[:10],
                       "fund_type_split": None,              # UW doesn't classify active vs index -> no data
                       "total_inst_pct": trend[0] if trend else None}


def p_fund_positions(symbol: str) -> dict:
    rows = _ownership(symbol)
    val, detail, extra = _fund_positions_eval(rows)
    has = rows is not None and bool((extra or {}).get("ownership_trend_pct"))
    return _p("fund_positions", val, has, detail, extra)


# --------------------------------------------------------------------------- #
#  assembly: score, coverage, bias
# --------------------------------------------------------------------------- #
def _bias(score, params: dict):
    def gnet(g):
        return sum(params[n]["value"] for n in GROUPS[g])

    def ghas(g):
        return any(params[n]["has_data"] for n in GROUPS[g])

    def gmax(g):
        return sum(MAX[n] for n in GROUPS[g])

    bull_groups = sum(1 for g in GROUPS if ghas(g) and gnet(g) > 0)
    strong_bear = any(ghas(g) and gnet(g) <= -0.5 * gmax(g) for g in GROUPS)
    strong_bull = any(ghas(g) and gnet(g) >= 0.5 * gmax(g) for g in GROUPS)
    bear_groups = sum(1 for g in GROUPS if ghas(g) and gnet(g) < 0)
    if score >= BUY_CUT and bull_groups >= 3 and not strong_bear:
        return "BULLISH"
    if score <= SELL_CUT and bear_groups >= 3 and not strong_bull:
        return "BEARISH"
    return "MIXED"


def score_one(symbol: str, *, detail: bool = False) -> dict:
    symbol = symbol.upper()
    params = {
        "insider_news": p_insider_news(symbol),
        "insider_trades": p_insider_trades(symbol),
        "unusual": p_unusual(symbol),
        "disparity": p_disparity(symbol),
        "fund_flow": p_fund_flow(symbol),
        "fund_positions": p_fund_positions(symbol),
    }
    ordered = [params[n] for n in ("insider_news", "insider_trades", "unusual",
                                   "disparity", "fund_flow", "fund_positions")]
    net = round(sum(p["value"] for p in ordered), 2)
    score = round(50 + net / 2, 1)
    covered = sum(p["max"] for p in ordered if p["has_data"])
    insufficient = covered < COVERAGE_MIN
    bias = None if insufficient else _bias(score, params)
    # Simple score-based call on THIS page's score only: BUY >= 55 / SELL <= 45.
    call = ("INSUFFICIENT" if insufficient
            else "BUY" if score >= CALL_BUY
            else "SELL" if score <= CALL_SELL
            else "NEUTRAL")

    import unusualwhales_service as uw
    price = None
    q = _timed(lambda: uw.get_quote(symbol))
    if isinstance(q, dict):
        price = _f(q.get("price")) or _f(q.get("last"))

    out = {
        "symbol": symbol, "model": MODEL, "asof": now_et_iso(),
        "score": None if insufficient else score, "net": net,
        "coverage_pct": round(covered / 100 * 100, 1), "covered": covered,
        "bias": "INSUFFICIENT" if insufficient else bias,
        "call": call,
        "insufficient": insufficient, "price": price,
        # scanner summary cells
        "insider_net_30d": params["insider_trades"]["extra"].get("exec_buy_usd_30d"),
        "insider_sell_30d": params["insider_trades"]["extra"].get("net_sell_usd_30d"),
        "unusual_net": params["unusual"]["extra"].get("net_premium"),
        "call_share": params["disparity"]["extra"].get("call_share"),
        "fund_net_shares": params["fund_flow"]["extra"].get("net_shares"),
    }
    if detail:
        out["params"] = ordered
    else:
        out["params_brief"] = [{"name": p["name"], "max": p["max"], "value": p["value"],
                                "has_data": p["has_data"]} for p in ordered]
    return out


# --------------------------------------------------------------------------- #
#  scanner over the universe
# --------------------------------------------------------------------------- #
def universe() -> list:
    try:
        import ai_trade_service as ats
        return [s.upper() for s in getattr(ats, "UNIVERSE", [])]
    except Exception:  # noqa: BLE001
        return []


def _score_cached(symbol: str) -> dict:
    """Full scanner row for one symbol, cached ~2 min (same score as the detail
    view, which reads the same sub-caches)."""
    import swr
    return swr.serve(f"sm:one:{symbol.upper()}", lambda: score_one(symbol.upper(), detail=False), 120.0)


def scan() -> dict:
    """Light aggregator: return whatever names are already scored (per-symbol
    cache) and list the rest to warm. Scoring the full universe at once is far
    too slow (6 provider calls x ~40 names), so the scanner fills PROGRESSIVELY
    -- the endpoint warms a batch each poll and rows appear as they're ready."""
    import swr
    syms = universe()
    cached = swr.peek_many([f"sm:one:{s}" for s in syms])
    rows, missing = [], []
    for s in syms:
        r = cached.get(f"sm:one:{s}")
        if r:
            rows.append(r)
        else:
            missing.append(s)
    rows.sort(key=lambda r: (r["score"] if r.get("score") is not None else -1), reverse=True)
    counts = {"bullish": 0, "bearish": 0, "mixed": 0, "insufficient": 0}
    for r in rows:
        if r.get("insufficient"):
            counts["insufficient"] += 1
        elif r.get("bias") == "BULLISH":
            counts["bullish"] += 1
        elif r.get("bias") == "BEARISH":
            counts["bearish"] += 1
        else:
            counts["mixed"] += 1
    return {"status": "OK", "model": MODEL, "asof": now_et_iso(), "counts": counts,
            "universe": len(syms), "scored": len(rows), "rows": rows, "missing": missing[:60]}


def warm(syms: list) -> None:
    """Score a batch of names into their per-symbol caches (off the request thread)."""
    for s in syms:
        try:
            _score_cached(s)
        except Exception:  # noqa: BLE001
            pass
