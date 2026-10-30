"""Factor adapters F3 (historical reaction), F5 (price and technical), F7 (implied volatility) and F8 (sector and market) (component 5).

Pure functions on already-fetched provider payloads: they never fetch, never read a clock of their own and never look at anything dated after the capture time. A
payload that cannot be trusted becomes MISSING with a reason code; it is never replaced by a neutral value. Provider move fields (post_earnings_move_*) are never used:
reactions are recomputed from regular-session candles.
"""
import statistics
from datetime import date, datetime, timedelta, timezone

from . import cutoff as co
from . import freshness as fr
from .freshness import FactorEvidence, missing

UTC = timezone.utc
FLAT_BAND = 0.5
SUSPECT_PCT = 25.0


def regular_series(rows):
    """date -> (open, high, low, close, volume) from market_time 'r' rows only; the last regular row of a date wins."""
    ser = {}
    for r in rows or []:
        if str(r.get("market_time", "")).lower() != "r":
            continue
        try:
            d = date.fromisoformat(str(r["date"])[:10])
            vol = float(r["volume"]) if r.get("volume") not in (None, "") else None
            ser[d] = (float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), vol)
        except (KeyError, ValueError, TypeError):
            continue
    return ser


def find_suspects(ser, reaction_sessions=frozenset(), threshold_pct=SUSPECT_PCT):
    days = sorted(ser)
    out = set()
    for p, q in zip(days, days[1:]):
        if ser[p][3] and abs(ser[q][3] / ser[p][3] - 1) * 100 > threshold_pct and q not in reaction_sessions:
            out.add(q)
    return out


def _ret(ser, last, n, suspects):
    ds = [d for d in sorted(ser) if d <= last]
    if len(ds) < n + 1 or ds[-1] != last:
        return None, "INSUFFICIENT_HISTORY"
    if any(q in suspects for q in ds[-n:]):
        return None, "SUSPECT_WINDOW"
    return ser[ds[-1]][3] / ser[ds[-1 - n]][3] - 1, None


def _usable_last(capture_ts, plan):
    """Last session whose data may be used: complete at the capture time AND not after the cutoff-based session (point in time)."""
    last = co.last_completed_session_at(capture_ts)
    fr.assert_point_in_time(capture_ts, plan["cutoff_utc"], "capture time")
    return min(last, plan["last_usable_session"]) if plan["last_usable_session"] else last


# ------------------------------------------------------------------ F5
def f5_price_technical(rows, spy_rows, plan, capture_ts, reaction_sessions=frozenset(), payload_sha=None):
    ser, spy = regular_series(rows), regular_series(spy_rows)
    if not ser:
        return missing(5, "uw.ohlc.1d", "PROVIDER_EMPTY", capture_ts)
    last = _usable_last(capture_ts, plan)
    if last not in ser:
        return missing(5, "uw.ohlc.1d", "NOT_AVAILABLE_AS_OF", capture_ts, session_reflected=max(d for d in ser if d <= last) if any(d <= last for d in ser) else None)
    klass = fr.classify(capture_ts, last, None)
    if klass == fr.STALE:
        return missing(5, "uw.ohlc.1d", "STALE_AGE", capture_ts, session_reflected=last, freshness_class=klass)
    sus = find_suspects({d: v for d, v in ser.items() if d <= last}, reaction_sessions)
    spy_sus = find_suspects({d: v for d, v in spy.items() if d <= last})
    why, x = {}, {"last_session": last.isoformat()}
    x["ret20"], w = _ret(ser, last, 20, sus)
    why["ret20"] = w
    s20, ws = _ret(spy, last, 20, spy_sus)
    x["rel20"] = x["ret20"] - s20 if x["ret20"] is not None and s20 is not None else None
    why["rel20"] = w or ws
    ds = [d for d in sorted(ser) if d <= last]
    if len(ds) >= 50 and not any(q in sus for q in ds[-50:]):
        x["ext50"] = ser[last][3] / statistics.mean(ser[d][3] for d in ds[-50:]) - 1
    else:
        x["ext50"], why["ext50"] = None, "SUSPECT_WINDOW" if any(q in sus for q in ds[-50:]) else "INSUFFICIENT_HISTORY"
    if len(ds) >= 20 and not any(q in sus for q in ds[-20:]) and all(ser[d][4] for d in ds[-20:]):
        x["adv20"] = statistics.mean(ser[d][3] * ser[d][4] for d in ds[-20:])
    else:
        x["adv20"], why["adv20"] = None, "SUSPECT_WINDOW" if any(q in sus for q in ds[-20:]) else "INSUFFICIENT_HISTORY"
    x["missing_why"] = {k: v for k, v in why.items() if v and x.get(k) is None}
    if all(x[k] is None for k in ("ret20", "rel20", "ext50")):
        reason = "SUSPECT_WINDOW" if "SUSPECT_WINDOW" in x["missing_why"].values() else "INSUFFICIENT_HISTORY"
        return missing(5, "uw.ohlc.1d", reason, capture_ts, session_reflected=last, freshness_class=klass, extracted=x)
    return FactorEvidence(5, "uw.ohlc.1d", "OK", None, x, None, capture_ts, last, "DATED_NOT_STAMPED", klass, payload_sha)


# ------------------------------------------------------------------ F3
def _kind(label):
    s = str(label or "").strip().lower()
    return "BMO" if s.startswith(("pre", "before", "bmo")) else "AMC" if s.startswith(("post", "after", "amc")) else None


def f3_reaction_history(history_rows, candle_rows, event_date, plan, capture_ts, payload_sha=None):
    """Last up to 8 earlier reactions recomputed from candles; each must have closed (plus the availability buffer) before the capture, which is before the cutoff."""
    ser = regular_series(candle_rows)
    if not history_rows or not ser:
        return missing(3, "uw.earnings+ohlc", "PROVIDER_EMPTY", capture_ts)
    fr.assert_point_in_time(capture_ts, plan["cutoff_utc"], "capture time")
    mc = co._mc()
    react_sessions = set()
    parsed = []
    for r in history_rows:
        try:
            ed = date.fromisoformat(str(r.get("report_date"))[:10])
        except ValueError:
            continue
        kind = _kind(r.get("report_time"))
        if ed >= event_date or kind is None:
            continue
        parsed.append((ed, kind, co.base_session(ed, kind), co.reaction_session(ed, kind)))
        react_sessions.add(parsed[-1][3])
    sus = find_suspects(ser, react_sessions)
    got, dropped = [], {"not_closed_before_capture": 0, "missing_close": 0, "suspect_window": 0}
    for ed, kind, base, react in sorted(parsed):
        if mc.session_close_utc(react) + timedelta(minutes=co.BUFFER_MIN) > capture_ts:
            dropped["not_closed_before_capture"] += 1
            continue
        if base not in ser or react not in ser:
            dropped["missing_close"] += 1
            continue
        if any(base < q <= react for q in sus):
            dropped["suspect_window"] += 1
            continue
        got.append((ed, kind, round((ser[react][3] / ser[base][3] - 1) * 100, 2)))
    got = got[-8:]
    if len(got) < 4:
        return missing(3, "uw.earnings+ohlc", "INSUFFICIENT_HISTORY", capture_ts, extracted={"n": len(got), "dropped": dropped})
    rets = [g[2] for g in got]
    up, down = sum(1 for r in rets if r > FLAT_BAND), sum(1 for r in rets if r < -FLAT_BAND)
    x = {"n": len(got), "mean_pct": round(statistics.mean(rets), 3), "up_share": up / len(got), "down_share": down / len(got), "reactions": [[g[0].isoformat(), g[1], g[2]] for g in got], "dropped": dropped}
    last_react = max(co.reaction_session(g[0], g[1]) for g in got)
    return FactorEvidence(3, "uw.earnings+ohlc", "OK", None, x, None, capture_ts, last_react, "DERIVED", fr.classify(capture_ts, co.last_completed_session_at(capture_ts), None), payload_sha)


# ------------------------------------------------------------------ F7
def f7_volatility_context(context, ivrank_rows, plan, capture_ts, payload_sha=None):
    """Counts only if: the context date is the expected (latest completed) session, no iv-rank stamp is after the cutoff, the capture is inside the window."""
    fr.assert_point_in_time(capture_ts, plan["cutoff_utc"], "capture time")
    if not (plan["window_start_utc"] <= capture_ts <= plan["cutoff_utc"]):
        return missing(7, "uw.volatility.context", "NOT_AVAILABLE_AS_OF", capture_ts, notes=["capture outside the prediction window"])
    data = (context or {}).get("data") if isinstance(context, dict) else None
    if not data or not isinstance(data, dict):
        return missing(7, "uw.volatility.context", "PROVIDER_EMPTY", capture_ts)
    expected = co.last_completed_session_at(capture_ts)
    try:
        got = date.fromisoformat(str(data.get("date"))[:10])
    except ValueError:
        return missing(7, "uw.volatility.context", "NOT_AVAILABLE_AS_OF", capture_ts)
    if got != expected:
        return missing(7, "uw.volatility.context", "STALE_AGE" if got < expected else "NOT_AVAILABLE_AS_OF", capture_ts, session_reflected=got)
    stamps = []
    for r in ivrank_rows or []:
        u = r.get("updated_at")
        if u:
            t = datetime.fromisoformat(str(u).replace("Z", "+00:00"))
            if t > plan["cutoff_utc"]:
                return missing(7, "uw.volatility.context", "NOT_AVAILABLE_AS_OF", capture_ts, notes=["iv-rank stamp after the cutoff"])
            stamps.append(t)
    label = ((data.get("skew") or {}).get("label")) if isinstance(data.get("skew"), dict) else None
    if label not in ("call_rich", "put_rich", "flat"):
        return missing(7, "uw.volatility.context", "UNINFORMATIVE_FIELD", capture_ts, session_reflected=got)
    exp = data.get("expiry") or {}
    x = {"skew_label": label, "iv_rank": data.get("iv_rank"), "iv_percentile": data.get("iv_percentile"), "implied_move_perc": exp.get("implied_move_perc"), "context_date": got.isoformat()}
    return FactorEvidence(7, "uw.volatility.context", "OK", None, x, max(stamps) if stamps else None, capture_ts, got, "PER_ROW_STAMPED" if stamps else "DATED_NOT_STAMPED",
                          fr.classify(capture_ts, got, None), payload_sha)


# ------------------------------------------------------------------ F8
def f8_sector_market(spy_rows, sector_rows, plan, capture_ts, payload_sha=None):
    spy = regular_series(spy_rows)
    if not spy:
        return missing(8, "uw.ohlc.1d:SPY", "PROVIDER_EMPTY", capture_ts)
    last = _usable_last(capture_ts, plan)
    if last not in spy:
        return missing(8, "uw.ohlc.1d:SPY", "NOT_AVAILABLE_AS_OF", capture_ts)
    s20, w = _ret(spy, last, 20, find_suspects({d: v for d, v in spy.items() if d <= last}))
    if s20 is None:
        return missing(8, "uw.ohlc.1d:SPY", w, capture_ts)
    x = {"spy_ret20": s20, "sector_rel20": None}
    sec = regular_series(sector_rows) if sector_rows else {}
    if sec:
        r20, _ = _ret(sec, last, 20, find_suspects({d: v for d, v in sec.items() if d <= last}))
        x["sector_rel20"] = r20 - s20 if r20 is not None else None
    return FactorEvidence(8, "uw.ohlc.1d:SPY+sector", "OK", None, x, None, capture_ts, last, "DATED_NOT_STAMPED", fr.classify(capture_ts, last, None), payload_sha)
