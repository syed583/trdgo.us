"""Intraday indicator engine (v2) -- pure math over 5-minute bars, in ET.

Everything the Intraday Model v2 needs that can be derived from price/volume:
the current US session's VWAP (and its slope), 5-min EMA9/EMA21, RSI(14),
relative volume vs the same time of day, the opening range (first 30 min),
today's % change, gap vs prior close, and daily ATR(14). No scoring or gates
here -- just the numbers, so intraday_model.py and both screens read one source.

Session = the current America/New_York trading day (09:30-16:00 ET). Bars carry
a UTC timestamp 't'; we convert to ET and bucket by ET calendar day.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001
    _ET = timezone.utc

_OPEN = time(9, 30)
_CLOSE = time(16, 0)
_OR_END = time(10, 0)             # opening range = 09:30-10:00 ET (first 30 min)


def _f(v) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _et(ts) -> Optional[datetime]:
    """Parse a bar timestamp ('2026-10-14T18:55:00Z' or epoch) to ET-aware dt."""
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
        return dt.astimezone(_ET)
    except Exception:  # noqa: BLE001
        return None


def now_et() -> datetime:
    return datetime.now(_ET)


def _norm(bars: list[dict]) -> list[dict]:
    """Attach an ET datetime to each bar and sort oldest->newest."""
    out = []
    for b in bars or []:
        dt = _et(b.get("t") or b.get("date") or b.get("time"))
        c = _f(b.get("close"))
        if dt is None or c is None:
            continue
        out.append({"dt": dt, "o": _f(b.get("open")), "h": _f(b.get("high")),
                    "l": _f(b.get("low")), "c": c, "v": _f(b.get("volume")) or 0.0})
    out.sort(key=lambda x: x["dt"])
    return out


def _is_rth(dt: datetime) -> bool:
    return _OPEN <= dt.time() < _CLOSE


def session_bars(bars: list[dict], day=None) -> list[dict]:
    """Regular-hours bars for one ET session (default: the latest session present)."""
    norm = _norm(bars)
    rth = [b for b in norm if _is_rth(b["dt"])]
    if not rth:
        return []
    # TODAY's ET session only -- never the last session present. Before the open
    # there are no RTH bars for today, so this is empty (the model then withholds
    # rather than scoring yesterday's session as if it were live).
    sess = day or now_et().date()
    return [b for b in rth if b["dt"].date() == sess]


def market_phase(now: Optional[datetime] = None) -> str:
    """'pre' (before 09:30 ET), 'rth' (09:30-16:00), 'post' (16:00-20:00),
    or 'closed' (weekend / overnight). Holidays are not detected here."""
    now = now or now_et()
    if now.weekday() >= 5:
        return "closed"
    t = now.time()
    if t < _OPEN:
        return "pre"
    if t < _CLOSE:
        return "rth"
    if t < time(20, 0):
        return "post"
    return "closed"


def _ema(vals: list[float], span: int) -> Optional[float]:
    if not vals:
        return None
    k = 2.0 / (span + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def ema_pair(bars: list[dict]) -> tuple[Optional[float], Optional[float]]:
    """(EMA9, EMA21) of 5-min closes over the recent bars (uses up to ~100)."""
    norm = _norm(bars)
    closes = [b["c"] for b in norm][-120:]
    if len(closes) < 21:
        return None, None
    return _ema(closes[-60:], 9), _ema(closes[-100:], 21)


def rsi(bars: list[dict], period: int = 14) -> Optional[float]:
    norm = _norm(bars)
    closes = [b["c"] for b in norm]
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    # Wilder smoothing
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    rs = ag / al
    return round(100 - 100 / (1 + rs), 1)


def vwap(sess: list[dict]) -> Optional[float]:
    num = den = 0.0
    for b in sess:
        if b["h"] is None or b["l"] is None:
            tp = b["c"]
        else:
            tp = (b["h"] + b["l"] + b["c"]) / 3
        num += tp * b["v"]
        den += b["v"]
    return (num / den) if den > 0 else None


def vwap_slope(sess: list[dict]) -> Optional[float]:
    """Sign/size of VWAP change over the last ~6 bars (30 min): + rising."""
    if len(sess) < 8:
        return None
    recent = vwap(sess)
    earlier = vwap(sess[:-6])
    if recent is None or earlier is None or earlier == 0:
        return None
    return (recent - earlier) / earlier


def opening_range(sess: list[dict]) -> tuple[Optional[float], Optional[float]]:
    """(OR high, OR low) for 09:30-10:00 ET."""
    orb = [b for b in sess if b["dt"].time() < _OR_END]
    if not orb:
        return None, None
    highs = [b["h"] for b in orb if b["h"] is not None]
    lows = [b["l"] for b in orb if b["l"] is not None]
    return (max(highs) if highs else None, min(lows) if lows else None)


def rvol_tod(bars: list[dict]) -> Optional[float]:
    """Relative volume vs the same time of day: today's cumulative session volume
    so far divided by the average cumulative-to-this-time over prior sessions."""
    norm = _norm(bars)
    rth = [b for b in norm if _is_rth(b["dt"])]
    if not rth:
        return None
    today = rth[-1]["dt"].date()
    cutoff = rth[-1]["dt"].time()
    by_day: dict = {}
    for b in rth:
        by_day.setdefault(b["dt"].date(), []).append(b)
    today_cum = sum(b["v"] for b in by_day.get(today, []))
    prior = []
    for d, bs in by_day.items():
        if d == today:
            continue
        prior.append(sum(b["v"] for b in bs if b["dt"].time() <= cutoff))
    prior = [p for p in prior if p > 0]
    if not prior or today_cum <= 0:
        return None
    avg = sum(prior) / len(prior)
    return round(today_cum / avg, 2) if avg > 0 else None


def pct_change_today(bars: list[dict]) -> Optional[float]:
    """Today's % change vs the prior session close (regular hours)."""
    norm = _norm(bars)
    rth = [b for b in norm if _is_rth(b["dt"])]
    if len(rth) < 2:
        return None
    today = rth[-1]["dt"].date()
    today_bars = [b for b in rth if b["dt"].date() == today]
    prior_bars = [b for b in rth if b["dt"].date() < today]
    if not today_bars or not prior_bars:
        return None
    prior_close = prior_bars[-1]["c"]
    if not prior_close:
        return None
    return (today_bars[-1]["c"] - prior_close) / prior_close * 100


def gap_pct(bars: list[dict]) -> Optional[float]:
    """Opening gap: today's first regular-hours open vs prior session close, %."""
    norm = _norm(bars)
    rth = [b for b in norm if _is_rth(b["dt"])]
    if len(rth) < 2:
        return None
    today = rth[-1]["dt"].date()
    today_bars = [b for b in rth if b["dt"].date() == today]
    prior_bars = [b for b in rth if b["dt"].date() < today]
    if not today_bars or not prior_bars:
        return None
    o = today_bars[0]["o"] or today_bars[0]["c"]
    pc = prior_bars[-1]["c"]
    if not pc:
        return None
    return (o - pc) / pc * 100


def premarket_high(bars: list[dict]) -> Optional[float]:
    """Highest price in today's pre-market (before 09:30 ET), if the feed has it."""
    norm = _norm(bars)
    today = None
    rth = [b for b in norm if _is_rth(b["dt"])]
    if rth:
        today = rth[-1]["dt"].date()
    if today is None:
        return None
    pre = [b for b in norm if b["dt"].date() == today and b["dt"].time() < _OPEN]
    highs = [b["h"] for b in pre if b["h"] is not None]
    return max(highs) if highs else None


def last_price(bars: list[dict]) -> Optional[float]:
    norm = _norm(bars)
    return norm[-1]["c"] if norm else None


def atr14_daily(daily_bars: list[dict]) -> Optional[float]:
    """Daily ATR(14) from daily OHLC bars (Wilder). Expects dicts with
    high/low/close (newest or oldest order; we sort by date if present)."""
    rows = []
    for b in daily_bars or []:
        h, l, c = _f(b.get("high")), _f(b.get("low")), _f(b.get("close"))
        d = str(b.get("date") or b.get("t") or "")[:10]
        if h is not None and l is not None and c is not None:
            rows.append((d, h, l, c))
    if len(rows) < 15:
        return None
    if all(r[0] for r in rows):
        rows.sort(key=lambda r: r[0])
    trs = []
    for i in range(1, len(rows)):
        _, h, l, c = rows[i]
        pc = rows[i - 1][3]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < 14:
        return None
    atr = sum(trs[:14]) / 14
    for tr in trs[14:]:
        atr = (atr * 13 + tr) / 14
    return round(atr, 4)
