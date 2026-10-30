"""Offline replay of the frozen 40-point engines on SAVED historical data only (no request, no database).

Scope guard: only development-period events are loaded (the baseline's sealed loader drops validation/test rows before anything else is read) and no candle later than the
baseline truncation date is read. Outcomes (moves, directions) are never loaded into the replay, so nothing here can be tuned on them and no accuracy statement is made.

For every event the evidence is rebuilt as it could have been known at the capture time (window start + 5 min): candles only up to the last completed session, earlier
events only if their date is before the event. Each datum is classified:
  PIT_BY_DATE            price values dated on or before the cutoff (retrieval was later, adjustment of old closes is not documented by the provider)
  HINDSIGHT              the event date and BMO/AMC label as stored after the fact (no calendar snapshot of the time exists), and anything taken from filings after the release
  MISSING_NOT_CAPTURED   F7 volatility context for these symbols was never requested; sector mapping per symbol is not saved
The replay therefore shows how the frozen engine behaves on dated price evidence; it is NOT a shadow prediction, because the calendar status of the time is unknowable.
"""
import csv
import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import adapters as ad
from . import calendar_validation as cv
from . import cutoff as co
from . import scorer as sc
from .freshness import missing

UTC = timezone.utc


def load_history(events_csv, until: date, dev_start: date):
    """symbol -> [(date, label)] from the saved events file, restricted to dates inside [dev_start, until] BEFORE any other column is used."""
    hist = {}
    with open(events_csv, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            d = date.fromisoformat(r["earnings_date"])
            if not (dev_start <= d <= until):
                continue
            hist.setdefault(r["symbol"], []).append((d, r["provider_label"]))
    return hist


def load_sec_history(sec_json, until: date, cluster_days=45):
    """Prior-event dates and sides from the saved SEC item 2.02 filings (immutable, dated by acceptance time; nothing after `until` is used).
    Acceptance before 09:30 ET -> premarket (a release cannot be later than its filing). Acceptance at or after 16:00 ET -> postmarket (NOT contradicted only: an earlier
    release is possible). Acceptance during regular hours -> the event is dropped. Within `cluster_days` only the first filing counts (later ones are re-furnishings)."""
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    raw = json.loads(Path(sec_json).read_text(encoding="utf-8"))
    out = {}
    for sym, rec in raw.items():
        ev = []
        for f in rec.get("item_2_02_filings", []):
            acc = datetime.fromisoformat(f["acceptanceDateTime"].replace("Z", "+00:00")).astimezone(et)
            if acc.date() > until:
                continue
            t = acc.time()
            lab = "premarket" if t < datetime.strptime("09:30", "%H:%M").time() else "postmarket" if t >= datetime.strptime("16:00", "%H:%M").time() else None
            if lab:
                ev.append((acc.date(), lab))
        ev.sort()
        kept = []
        for d, lab in ev:
            if not kept or (d - kept[-1][0]).days > cluster_days:
                kept.append((d, lab))
        out[sym] = kept
    return out


def read_candles(path, until: date):
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    return [r for r in rows if date.fromisoformat(str(r["date"])[:10]) <= until]


def classify_event(row):
    """Hindsight flags kept apart from the engine's inputs (they never change a score)."""
    flags = []
    if row.get("label_conflict") in ("True", True):
        flags.append("LABEL_CONTRADICTED_BY_FILING_AFTER_THE_FACT")
    if row.get("provider_kind") not in ("BMO", "AMC"):
        flags.append("TIMING_UNKNOWN")
    return flags


def replay_event(pre, pre_sha, sym, edate, kind, hist_rows, cand_rows, spy_rows, sha_cand, sha_spy, hindsight_flags):
    plan = co.plan_event(edate, kind if kind in ("BMO", "AMC") else "UNKNOWN") if kind in ("BMO", "AMC") else None
    if plan is None:
        return {"symbol": sym, "earnings_date": str(edate), "kind": kind, "signal_continuation": "NO_TRADE", "signal_contrarian": "NO_TRADE", "reason_codes": ["UNKNOWN_TIMING"],
                "hindsight_flags": hindsight_flags, "evidence": {}, "weight_available": 0, "replayed": False}
    capture_ts = plan["window_start_utc"] + timedelta(minutes=5)
    last = co.last_completed_session_at(capture_ts)
    cand = [r for r in cand_rows if date.fromisoformat(str(r["date"])[:10]) <= last]
    spy = [r for r in spy_rows if date.fromisoformat(str(r["date"])[:10]) <= last]
    prior = [{"report_date": d.isoformat(), "report_time": lab} for d, lab in hist_rows if d < edate]
    react = set()
    for r in prior:
        k = ad._kind(r["report_time"])
        if k:
            react.add(co.reaction_session(date.fromisoformat(r["report_date"]), k))
    ev = {5: ad.f5_price_technical(cand, spy, plan, capture_ts, react, sha_cand) if cand else missing(5, "saved.ohlc", "NO_SOURCE", capture_ts),
          3: ad.f3_reaction_history(prior, cand, edate, plan, capture_ts, sha_cand) if (prior and cand) else missing(3, "saved.earnings+ohlc", "INSUFFICIENT_HISTORY" if cand else "NO_SOURCE", capture_ts),
          7: missing(7, "uw.volatility.context", "NOT_CAPTURED", capture_ts),
          8: ad.f8_sector_market(spy, None, plan, capture_ts, sha_spy) if spy else missing(8, "saved.ohlc:SPY", "NO_SOURCE", capture_ts)}
    timing_status = cv.T_CONFLICT if "LABEL_CONTRADICTED_BY_FILING_AFTER_THE_FACT" in hindsight_flags else cv.T_SINGLE
    out = {"symbol": sym, "earnings_date": str(edate), "kind": kind, "capture_ts_utc": capture_ts.isoformat(), "last_session": last.isoformat(), "hindsight_flags": hindsight_flags, "replayed": True}
    for v in ("continuation", "contrarian"):
        p = sc.score(pre, pre_sha, ev, symbol=sym, event_date=edate, event_version=1, kind=kind, timing_status=timing_status, plan=plan, capture_ts=capture_ts, variant=v)
        out["signal_" + v] = p["signal"]
        out["bull_" + v], out["bear_" + v] = p["bull_points"], p["bear_points"]
        out["codes_" + v] = p["reason_codes"]
        out["weight_available"] = p["weight_available"]
    out["reason_codes"] = sorted(set(out["codes_continuation"]) | set(out["codes_contrarian"]))
    out["evidence"] = {str(f): {"status": e.status, "missing_reason": e.missing_reason, "freshness": e.freshness_class, "as_of_quality": e.as_of_quality, "usable": e.usable} for f, e in ev.items()}
    return out


def summarise(rows):
    rep = [r for r in rows if r["replayed"]]
    s = {"events_in_dev_scope": len(rows), "replayed": len(rep), "not_replayed_timing_unknown": len(rows) - len(rep)}
    for v in ("continuation", "contrarian"):
        s["signals_" + v] = dict(Counter(r["signal_" + v] for r in rows))
    s["coverage_weight_available"] = dict(Counter(r["weight_available"] for r in rep))
    ev_missing = Counter()
    for r in rep:
        for f, e in r["evidence"].items():
            if not e["usable"]:
                ev_missing["F%s:%s" % (f, e["missing_reason"])] += 1
    s["missing_evidence"] = dict(ev_missing.most_common())
    s["reason_codes"] = dict(Counter(c for r in rows for v in ("continuation", "contrarian") for c in r.get("codes_" + v, r["reason_codes"])).most_common())
    s["hindsight_flags"] = dict(Counter(f for r in rows for f in r["hindsight_flags"]))
    return s
