"""Kit v6.7 (owner decision 2026-11-13): calendar SESSION resolution and single-vendor acceptance, in the kit layer only.

The frozen engine (smvshadow.calendar_confirm.evaluate) is untouched and still runs first. This module only changes what the
collector does with its answer:

  1. SESSION (BMO / AMC) of the chosen event date, in this order, the first that answers wins:
        finnhub-hour     the Finnhub row's hour ('bmo' / 'amc')
        uw-report-time   the Unusual Whales row's report_time ('premarket' / 'afterhours' ...)
        vendor-clock     any other vendor's clock time (Finviz '4:30 PM')
        history-last4    the SYMBOL'S OWN EARLIER CALENDAR ROWS in the store (its last reports as the vendors labelled them at
                         the time): the session of at least 3 of the last 4 earlier report dates (or of all of them when only
                         1 or 2 are known). Mixed history answers nothing.
     Vendors that DISAGREE on the session stay a timing conflict (not scanned, listed as such); history never overrides a vendor.
  2. A SINGLE-VENDOR date is scanned: when the engine's only objections are "one vendor / no second vendor / the Unusual Whales
     flag is not 'company' / the pair's independence is undocumented", the event is accepted and LABELLED single-vendor
     (confirmation = 'single-vendor'; two-vendor agreement keeps 'two-vendor', direct company evidence 'company-verified').
     Date conflicts, stale observations, moved dates, label/clock contradictions and company-evidence contradictions still refuse.
  3. No session from any source -> session_unknown = True: the event stays on the dashboard as SESSION_UNKNOWN, never skipped.
Calendar ingestion only: scoring, factors, thresholds, the liquidity gate, windows, budgets and limits are not touched. Dashboard
only: nothing here reaches MT5 or any alert channel.
"""
from __future__ import annotations

import collections
import dataclasses
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "docs" / "smart_move" / "shadow_impl"))
from smvshadow import calendar_confirm as cc  # noqa: E402

KIT = "kit-v6.7"
SOURCE_FINNHUB, SOURCE_UW, SOURCE_CLOCK, SOURCE_HISTORY = "finnhub-hour", "uw-report-time", "vendor-clock", "history-last4"
CONF_TWO, CONF_SINGLE, CONF_COMPANY, CONF_NONE, CONF_UNKNOWN = "two-vendor", "single-vendor", "company-verified", "not-eligible", "session-unknown"
CONF_TWO_DATE = "two-vendor-date"      # both vendors give the date, only one gives the session (or the Unusual Whales flag is not 'company')
# the engine's objections that single-vendor acceptance is allowed to override (every other no-trade reason still refuses)
ACCEPTABLE = frozenset({"SINGLE_VENDOR", "UW_FLAG_MISSING", "NO_UW_OBSERVATION", "DEPENDENCY_UNDOCUMENTED", "SHARED_PAYLOAD", "PARTNER_OBSERVATION_STALE",
                        "UNKNOWN_TIMING"})   # UNKNOWN_TIMING only ever reaches the acceptance step when the event's own session IS known (the partner vendor just has no hour).
# SOURCE_ESTIMATION is deliberately NOT here: a date the vendor itself marks as its own estimate is not a reported date, and the owner approved
# scanning single-vendor dates, not estimated ones. Such an event stays NOT_ELIGIBLE exactly as under kit v6.6.
REFUSING_REASONS = ("DATE_CONFLICT_OVER_1_DAY", "DATE_DISAGREEMENT_1_DAY", "MOVED", "LABEL_CLOCK_CONTRADICTION", "COMPANY_EVIDENCE_CONTRADICTS_VENDORS", "CALENDAR_OBSERVATION_STALE")
HISTORY_LAST = 4


# ------------------------------------------------------------------ history index
def history_index(rows) -> dict:
    """{symbol: [(date, 'BMO'|'AMC'), ...] newest first} from calendar rows (cal_obs.jsonl dicts or CalObs) whose label or clock
    gives a session: one session per report date (a date whose rows disagree is dropped). Built once per pass from the SAME rows
    the collector works from (after the market-cap floor), so nothing is read that the collector does not already read."""
    per: dict = collections.defaultdict(dict)
    for r in rows:
        o = r if isinstance(r, cc.CalObs) else _calobs(r)
        if o is None:
            continue
        s = o.session()
        if s not in ("BMO", "AMC"):
            continue
        d = per[o.symbol].setdefault(o.date, set())
        d.add(s)
    out = {}
    for sym, dd in per.items():
        out[sym] = [(d, next(iter(s))) for d, s in sorted(dd.items(), reverse=True) if len(s) == 1]
    return out


def _calobs(r: dict):
    try:
        return cc.CalObs(r["source"], r["symbol"], date.fromisoformat(str(r["date"])[:10]), r.get("label"), r.get("clock"), r.get("vendor_flag"), r.get("raw_sha256") or "", _dt(r["observed_at"]))
    except (KeyError, ValueError, TypeError):
        return None


def _dt(x):
    from datetime import datetime, timezone
    if isinstance(x, datetime):
        return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
    d = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def history_session(symbol: str, event_date: date, index: dict) -> tuple:
    """(session | None, detail) from the symbol's earlier report dates (strictly before event_date), newest first, last 4."""
    if not isinstance(index, dict):          # a caller that passes the wrong shape must not take a live pass down
        return None, "no history index"
    past = [s for d, s in (index.get(symbol) or []) if d < event_date][:HISTORY_LAST]
    if not past:
        return None, "no earlier calendar observation with a session"
    c = collections.Counter(past)
    top, n = c.most_common(1)[0]
    if n >= 3 or (len(past) <= 2 and n == len(past)):
        return top, "last %d reports: %s" % (len(past), dict(sorted(c.items())))
    return None, "history mixed: %s" % dict(sorted(c.items()))


# ------------------------------------------------------------------ session resolution
def resolve_session(on: list) -> tuple:
    """(session, source) from the latest observation per source on the chosen date, vendor order Finnhub -> Unusual Whales -> clock.
    Returns ('CONFLICT', None) when two vendors give different sessions, (None, None) when no vendor gives one."""
    latest = {}
    for o in on:
        if o.source not in latest or o.observed_at > latest[o.source].observed_at:
            latest[o.source] = o
    sess = {src: o.session() for src, o in latest.items()}
    known = {src: s for src, s in sess.items() if s in ("BMO", "AMC")}
    if "CONTRADICTED" in sess.values() or len(set(known.values())) > 1:
        return "CONFLICT", None
    if known.get("FINNHUB"):
        return known["FINNHUB"], SOURCE_FINNHUB
    for src in ("UW_CALENDAR", "UW_ROW"):
        if known.get(src):
            return known[src], SOURCE_UW
    for src, o in latest.items():
        if known.get(src) and o.clock:
            return known[src], SOURCE_CLOCK
    if known:
        return next(iter(known.values())), SOURCE_CLOCK
    return None, None


# ------------------------------------------------------------------ the kit-level evaluation
def evaluate_event(symbol: str, obs: list, now, today, history: dict | None = None, **kw) -> dict:
    """cc.evaluate + kit v6.7 rules. The returned dict is the engine's dict plus: confirmation, session_source, session_unknown,
    engine_no_trade_reasons (the engine's own objections, kept verbatim for the record even when overridden)."""
    ev = cc.evaluate(symbol, obs, now, today, **kw)
    ev["engine_no_trade_reasons"] = list(ev["no_trade_reasons"])
    ev["session_source"], ev["session_unknown"], ev["confirmation"] = None, False, CONF_NONE
    if ev["event_date"] is None:
        return ev
    on = [o for o in obs if o.symbol == symbol and o.observed_at <= now and o.date == ev["event_date"]]
    sess, src = resolve_session(on)
    if sess == "CONFLICT":
        return ev                                                       # vendors disagree on the session: the engine already refuses (TIMING_CONTRADICTED)
    if sess is None and history is not None:
        hs, _why = history_session(symbol, ev["event_date"], history)
        if hs:
            # inject the historical session as the label of every observation on the date that has none, then let the frozen engine judge again
            patched = [dataclasses.replace(o, label=hs) if (o in on and o.session() == "UNKNOWN") else o for o in obs]
            ev2 = cc.evaluate(symbol, patched, now, today, **kw)
            ev2["engine_no_trade_reasons"] = list(ev2["no_trade_reasons"])
            ev2["session_unknown"], ev2["confirmation"] = False, CONF_NONE
            ev2["session_source"] = SOURCE_HISTORY
            ev2["reasons"] = list(ev2["reasons"]) + ["SESSION_FROM_HISTORY"]
            ev = ev2
            sess, src = hs, SOURCE_HISTORY
    if src and ev["session_source"] is None:
        ev["session_source"] = src
    if ev["kind"] not in ("BMO", "AMC"):
        if sess is None:
            ev["session_unknown"], ev["confirmation"] = True, CONF_UNKNOWN
        return ev
    if ev["status"] == cc.COMPANY_VERIFIED:
        ev["confirmation"] = CONF_COMPANY
    elif ev["status"] == cc.VENDOR_CONFIRMED:
        ev["confirmation"] = CONF_TWO
    elif ev["status"] == cc.ESTIMATED and _single_vendor_acceptable(ev):
        ev["confirmation"] = CONF_SINGLE if len(ev.get("vendors") or []) < 2 else CONF_TWO_DATE
        ev["no_trade_reasons"] = []
        ev["eligible_for_shadow"] = True
        ev["reasons"] = list(ev["reasons"]) + ["SINGLE_VENDOR_ACCEPTED_KIT_V6_7"]
    return ev


def _single_vendor_acceptable(ev: dict) -> bool:
    if ev.get("moved_from") or any(r in REFUSING_REASONS for r in ev["reasons"]):
        return False
    return bool(ev["no_trade_reasons"]) and set(ev["no_trade_reasons"]) <= ACCEPTABLE


def calendar_fields(ev: dict) -> dict:
    """The extra keys every published/scored row carries under row['calendar'] (kit v6.7)."""
    return {"confirmation": ev.get("confirmation"), "session_source": ev.get("session_source"), "session_unknown": bool(ev.get("session_unknown")),
            "engine_no_trade_reasons": list(ev.get("engine_no_trade_reasons") or [])}
