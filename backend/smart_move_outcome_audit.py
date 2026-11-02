"""Earnings-outcome validation audit (Phase 13). OFFLINE and READ-ONLY: it applies the EXISTING outcome rules (smart_move_outcomes.compute, the frozen grader's FLAT band) to SAVED price bars and the saved event
records and reports what can and cannot be validated. It fetches nothing, invents no price or outcome, writes no outcome file, and does not touch the grader, the outcome store or any prediction.

Two parts:
  rule_audit()  checks the session rules (BMO/AMC base and reaction sessions, weekends, holidays, early closes, the 0.5 % inclusive FLAT band, split quarantine) against an INDEPENDENT oracle holiday table.
  audit_events() classifies every event: why an outcome can or cannot be computed from the saved bars, and whether a prediction exists that was made at or before the event's cutoff."""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "shadow_impl"))
import smart_move_outcomes as O  # noqa: E402
from smvshadow import adapters as ad  # noqa: E402
from smvshadow import cutoff as co  # noqa: E402
from smvshadow import grader as gr  # noqa: E402

UTC = timezone.utc
EXPECTED_FLAT_BAND = 0.5
# Independent oracle (NYSE full-day closures and 13:00 early closes, 2026-2027). Deliberately NOT read from market_calendar.py, so a wrong table there is detectable.
ORACLE_HOLIDAYS = {date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
                   date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24)}
ORACLE_EARLY = {date(2026, 11, 27), date(2026, 12, 24), date(2027, 11, 26)}


def _dt(x) -> datetime:
    d = x if isinstance(x, datetime) else datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _open(d: date) -> bool:
    return d.weekday() < 5 and d not in ORACLE_HOLIDAYS


def _next(d: date) -> date:
    d += timedelta(days=1)
    while not _open(d):
        d += timedelta(days=1)
    return d


def _prev(d: date) -> date:
    d -= timedelta(days=1)
    while not _open(d):
        d -= timedelta(days=1)
    return d


def oracle_sessions(event_date: date, kind: str) -> tuple:
    """(base, reaction). BMO: the close before the event day -> the event day's close (or the next session when the date itself is closed). AMC: the event day's close (or the last session before it) -> the next session."""
    if kind == "BMO":
        react = event_date if _open(event_date) else _next(event_date)
        return _prev(react), react
    base = event_date if _open(event_date) else _prev(event_date)
    return base, _next(base if _open(event_date) else event_date)


def rule_audit(start: date = date(2026, 10, 1), end: date = date(2027, 1, 31)) -> dict:
    """Compare the shipped session rules with the oracle for every calendar day in [start, end] and both timings; plus early-close times, the FLAT band and the split quarantine."""
    mism, checked = [], 0
    d = start
    while d <= end:
        for kind in ("BMO", "AMC"):
            checked += 1
            exp = oracle_sessions(d, kind)
            got = (co.base_session(d, kind), co.reaction_session(d, kind))
            if exp != got:
                mism.append({"event_date": d.isoformat(), "kind": kind, "oracle": [x.isoformat() for x in exp], "shipped": [x.isoformat() for x in got]})
        d += timedelta(days=1)
    early = []
    for e in sorted(ORACLE_EARLY):
        if start <= e <= end:
            close = O.co._mc().session_close_utc(e)
            early.append({"date": e.isoformat(), "close_utc": close.isoformat(), "is_13_00_ET": close.astimezone(co.ET).strftime("%H:%M") == "13:00"})
    band = {"grader_flat_band": gr.FLAT_BAND, "expected": EXPECTED_FLAT_BAND, "matches": gr.FLAT_BAND == EXPECTED_FLAT_BAND,
            "boundary": {str(m): gr.outcome_class(m) for m in (0.5, -0.5, 0.5001, -0.5001, 0.0)}, "inclusive_flat_at_exactly_0_5": gr.outcome_class(0.5) == "FLAT" and gr.outcome_class(-0.5) == "FLAT"}
    split = {"2_for_1": O.corporate_action_suspect(100.0, 50.0), "1_for_10_reverse": O.corporate_action_suspect(10.0, 100.0), "ordinary_move": O.corporate_action_suspect(100.0, 104.0)}
    return {"days_checked": checked, "session_mismatches": mism, "early_closes": early, "flat_band": band, "split_quarantine_examples": split,
            "oracle_holidays": len(ORACLE_HOLIDAYS), "passed": not mism and band["matches"] and band["inclusive_flat_at_exactly_0_5"] and all(x["is_13_00_ET"] for x in early) and split["2_for_1"] is not None and split["ordinary_move"] is None}


def load_bars(store_dirs: Iterable[str]) -> dict:
    """{symbol: [{retrieved_at, raw_sha256, rows}]} from saved `ohlc` evidence (evidence.jsonl + payloads). Read only."""
    from smvshadow.store import PayloadStore
    out: dict = {}
    seen = set()
    for d in store_dirs:
        st = PayloadStore(d)
        for fname, kinds in (("evidence.jsonl", ("ohlc",)), ("v2_evidence.jsonl", ("uw_ohlc",))):                  # V1 bars, and the Phase 14 collector's bars
            f = Path(d) / fname
            if not f.exists():
                continue
            for ln in f.read_text(encoding="utf-8-sig").splitlines():
                if not ln.strip():
                    continue
                r = json.loads(ln)
                if r.get("kind") not in kinds or r["raw_sha256"] in seen or r.get("status", "OK") != "OK":
                    continue
                seen.add(r["raw_sha256"])
                try:
                    rows = json.loads(st.get(r["raw_sha256"])).get("data") or []
                except Exception:  # noqa: BLE001
                    continue
                if r["kind"] == "uw_ohlc":                                                                      # quarantined bars never reach an outcome
                    import smart_move_price_bars as PB
                    rows = PB.validate_rows([x for x in rows if isinstance(x, dict)], r["retrieved_at"])["accepted"]
                out.setdefault(r["symbol"], []).append({"retrieved_at": r["retrieved_at"], "raw_sha256": r["raw_sha256"], "rows": rows})
    return out


def load_predictions(store_dirs: Iterable[str]) -> dict:
    """{(symbol, earnings_date): [prediction rows]} from saved predictions.jsonl. Read only."""
    out: dict = {}
    for d in store_dirs:
        f = Path(d) / "predictions.jsonl"
        if f.exists():
            for ln in f.read_text(encoding="utf-8-sig").splitlines():
                if ln.strip():
                    p = json.loads(ln)
                    out.setdefault((p["symbol"], str(p["earnings_date"])[:10]), []).append(p)
    return out


def _signal(p: dict) -> Optional[str]:
    sig = {k: (v or {}).get("signal") for k, v in (p.get("engine") or {}).items()}
    return ",".join("%s=%s" % kv for kv in sorted(sig.items())) if sig else None


def vendor_session_hint(vendors: list) -> Optional[str]:
    """BMO/AMC when every saved vendor row that states a timing agrees (premarket/bmo/clock before 12:00 -> BMO; postmarket/amc/clock from 16:00 -> AMC); otherwise None. Read from saved rows, never guessed."""
    votes = set()
    for v in vendors:
        lab, clk = str(v.get("label") or "").lower(), str(v.get("clock") or "")
        if lab in ("premarket", "bmo"):
            votes.add("BMO")
        elif lab in ("postmarket", "amc"):
            votes.add("AMC")
        elif clk:
            try:
                h, m = clk.split(":")[0], clk.split(":")[1][:2]
                hour = int(h) % 12 + (12 if "PM" in clk.upper() else 0)
                votes.add("BMO" if hour < 12 else "AMC" if hour >= 16 else "UNKNOWN")
            except (ValueError, IndexError):
                votes.add("UNKNOWN")
    return next(iter(votes)) if len(votes) == 1 and "UNKNOWN" not in votes else None


def audit_event(event: dict, bars: list, preds: list, now: datetime) -> dict:
    """event: {event_id, symbol, event_date, session, cutoff_utc}. bars: saved ohlc records of the symbol. preds: saved predictions for (symbol, event date)."""
    sym, ed, kind = event["symbol"], event["event_date"], event["session"]
    out = {"event_id": event["event_id"], "symbol": sym, "event_date": ed, "session": kind, "cutoff_utc": event.get("cutoff_utc")}
    if ed is None:
        return dict(out, state="NO_EVENT_DATE")
    basis = "EVENT_RECORD"
    if kind not in ("BMO", "AMC"):
        hint = vendor_session_hint(event.get("vendors") or [])
        if hint is None:
            return dict(out, state="UNKNOWN_TIMING_NO_OUTCOME_RULE")
        kind, basis = hint, "VENDOR_LABEL_ONLY"                                           # saved vendor labels agree on a session: usable to read the outcome, never to qualify a prediction
        if not event.get("cutoff_utc"):
            event = dict(event, cutoff_utc=co.plan_event(date.fromisoformat(ed), kind)["cutoff_utc"].isoformat())
        out.update(session_hint=kind, cutoff_utc=event["cutoff_utc"])
    out["timing_basis"] = basis
    d = O.due(date.fromisoformat(ed), kind, now)
    exp = oracle_sessions(date.fromisoformat(ed), kind)
    out.update(base_session=d["base_session"].isoformat(), reaction_session=d["reaction_session"].isoformat(), earliest_fetch_utc=d["earliest_fetch_utc"].isoformat(), sessions_match_oracle=(d["base_session"], d["reaction_session"]) == exp)
    # prediction point-in-time validity: a prediction counts only if it was captured at or before the cutoff
    co_ = _dt(event["cutoff_utc"]) if event.get("cutoff_utc") else None
    if not preds:
        pv = "NO_PREDICTION"
    elif co_ is None:
        pv = "NO_CUTOFF"
    else:
        before = [p for p in preds if _dt(p["captured_at"]) <= co_]
        pv = "PREDICTION_AT_OR_BEFORE_CUTOFF" if before else "PREDICTION_CAPTURED_AFTER_CUTOFF"
    out.update(prediction_validity=pv, prediction_signals=sorted({s for s in (_signal(p) for p in preds) if s}))
    if not d["due"]:
        return dict(out, state="PENDING_REACTION_SESSION_NOT_CLOSED", outcome_class=None)
    if not bars:
        return dict(out, state="NO_PRICE_EVIDENCE_SAVED", outcome_class=None)
    close_ok = d["earliest_fetch_utc"]
    best = None
    for b in sorted(bars, key=lambda b: b["retrieved_at"]):
        ser = ad.regular_series(b["rows"])
        if d["base_session"] in ser and d["reaction_session"] in ser:
            if _dt(b["retrieved_at"]) < close_ok:
                best = best or ("BARS_RETRIEVED_BEFORE_REACTION_SESSION_CLOSED", b)
                continue
            best = ("COMPLETE", b)
    if best is None:
        have = sorted({s for b in bars for s in ad.regular_series(b["rows"])})
        miss = [n for n, s in (("base", d["base_session"]), ("reaction", d["reaction_session"])) if s not in have]
        return dict(out, state="MISSING_BARS", missing=miss, last_saved_bar=have[-1].isoformat() if have else None, outcome_class=None)
    if best[0] != "COMPLETE":
        return dict(out, state=best[0], outcome_class=None)
    res = O.compute(sym, ed, kind, best[1]["rows"], now, mode="AUDIT", source="saved:" + best[1]["raw_sha256"][:12])
    st = res["status"]
    base = dict(out, bars_sha256=best[1]["raw_sha256"], bars_retrieved_at=best[1]["retrieved_at"])
    if st == "GRADED":
        return dict(base, state="OUTCOME_COMPUTABLE", move_pct=res["move_pct"], outcome_class=gr.outcome_class(res["move_pct"]), review_flag=res.get("review_flag"))
    return dict(base, state={"UNVERIFIED_CORPORATE_ACTION": "QUARANTINED_CORPORATE_ACTION", "IMPLAUSIBLE": "IMPLAUSIBLE_MOVE"}.get(st, st), raw_move_pct=res.get("raw_move_pct"), outcome_class=None)


def audit(events: Iterable[dict], bars_by_symbol: dict, preds_by_event: dict, now) -> dict:
    now = _dt(now)
    rows = [audit_event(e, bars_by_symbol.get(e["symbol"], []), preds_by_event.get((e["symbol"], str(e["event_date"])[:10]), []), now) for e in events]
    comp = [r for r in rows if r["state"] == "OUTCOME_COMPUTABLE"]
    usable = [r for r in comp if r["prediction_validity"] == "PREDICTION_AT_OR_BEFORE_CUTOFF" and r.get("timing_basis") == "EVENT_RECORD"]
    return {"as_of": now.isoformat(), "events": rows, "summary": {"events": len(rows), "by_state": dict(Counter(r["state"] for r in rows)), "outcome_computable": len(comp),
                                                                  "outcome_computable_by_class": dict(Counter(r["outcome_class"] for r in comp)), "outcome_computable_timing_basis": dict(Counter(r["timing_basis"] for r in comp)),
                                                                  "prediction_validity_among_computable": dict(Counter(r["prediction_validity"] for r in comp)), "usable_for_accuracy_measurement": len(usable),
                                                                  "session_rule_disagreements_with_oracle": sum(1 for r in rows if r.get("sessions_match_oracle") is False),
                                                                  "note": "Nothing is invented: an outcome exists only where saved regular-session bars cover both sessions and were retrieved after the reaction close. No prediction accuracy is measured here."}}
