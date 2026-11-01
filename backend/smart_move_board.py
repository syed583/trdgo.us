"""Smart Move earnings board: every eligible upcoming earnings event with an honest status, built only from SAVED data. No request, no scoring, no write.

The status is a DISPLAY classification of what the frozen engine already returned. It never creates a BUY or SELL and never turns missing data into NEUTRAL:

  BUY / SELL        the engine returned that signal with its RULE code and no gate code (valid, in window, coverage met, liquid, timing known)
  NO_TRADE          the engine explicitly returned NO TRADE after full evaluation (BELOW_MARGIN or CONFLICT, or a known illiquid stock): neutral, grey
  DATA_INCOMPLETE   the engine could not decide: required data missing, coverage under 24 of 40, stale evidence, unknown timing, or the capture window closed with no capture
  PENDING           not evaluated yet: no in-window capture exists and the window is still ahead or open

Both variants (continuation, contrarian) are classified separately and never merged; the caller picks which one drives the row colour.
Sources, newest evaluation wins per event: a saved one-time run report (audit_report.json), then the live store, then the saved calendar (which can only ever give PENDING or DATA_INCOMPLETE,
because evidence retrieved before the window opens does not count).
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

UTC = timezone.utc
MIN_AVAILABLE = 24                    # frozen minimum coverage 0.60 of 40
STATUSES = ("BUY", "SELL", "NO_TRADE", "DATA_INCOMPLETE", "PENDING")
GATE_CODES = {"INSUFFICIENT_COVERAGE", "UNKNOWN_TIMING", "TIMING_CONTRADICTED", "NOT_TRADABLE", "STALE_EVIDENCE", "SUSPECT_WINDOW", "BLOCKED_BY_GATE"}
FACTORS = (("F3", "Earnings-reaction history"), ("F5", "Price / technical"), ("F7", "Volatility (skew)"), ("F8", "Sector / market"))
REASON_TEXT = {
    "INSUFFICIENT_COVERAGE": "fewer than 24 of 40 scoring points have valid data", "UNKNOWN_TIMING": "release timing (before open / after close) is not known",
    "TIMING_CONTRADICTED": "vendors disagree on the release timing", "NOT_TRADABLE": "the entry rules cannot be met for this event", "STALE_EVIDENCE": "evidence is too old",
    "SUSPECT_WINDOW": "price history has a suspect bar (split or data glitch)", "BLOCKED_BY_GATE": "the calendar or evidence gate blocked the signal",
    "ILLIQUID_OR_UNKNOWN": "liquidity (20-day average dollar volume) is unknown", "ILLIQUID": "20-day average dollar volume is under $20 million",
}


def _dt(x) -> Optional[datetime]:
    if not x:
        return None
    t = datetime.fromisoformat(str(x))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _miss_names(missing) -> dict:
    """{'F3': 'REASON'} from either a list of dicts (live rows) or 'F3:REASON' strings (run reports). A partial F8 (sector only) stays available with a note."""
    out = {}
    for m in missing or []:
        if isinstance(m, str):
            f, _, why = m.partition(":")
            out[f] = {"reason": why or "MISSING", "partial": False}
        else:
            out[m["factor"]] = {"reason": m.get("reason") or "MISSING", "partial": bool(m.get("partial"))}
    return out


def factor_states(missing, evaluated: bool) -> list:
    """Available and missing scoring factors. Before a capture exists nothing is 'available': evidence saved before the window opened does not count."""
    miss = _miss_names(missing)
    out = []
    for f, name in FACTORS:
        if not evaluated:
            out.append({"factor": f, "name": name, "state": "NOT_CAPTURED", "reason": "not captured inside the window yet"})
        elif f in miss and not miss[f]["partial"]:
            out.append({"factor": f, "name": name, "state": "MISSING", "reason": miss[f]["reason"]})
        elif f in miss:
            out.append({"factor": f, "name": name, "state": "PARTIAL", "reason": miss[f]["reason"]})
        else:
            out.append({"factor": f, "name": name, "state": "AVAILABLE", "reason": None})
    return out


def classify(engine: Optional[dict], *, evaluated: bool, missing, plan: Optional[dict], now: datetime, calendar_ok: bool = True) -> dict:
    """Display status for one variant. engine: {signal, bull, bear, available, reason_codes}. Pure; never raises on odd input."""
    codes = list((engine or {}).get("reason_codes") or [])
    avail = int((engine or {}).get("available") or 0)
    if not evaluated:
        start, cutoff = _dt((plan or {}).get("window_start_utc")), _dt((plan or {}).get("cutoff_utc"))
        if plan is None or start is None or cutoff is None:
            return {"status": "DATA_INCOMPLETE", "why": "no usable event plan: release timing unknown"}
        if now > cutoff:
            return {"status": "DATA_INCOMPLETE", "why": "EXPIRED: the capture window closed and nothing was captured for this event"}
        if now < start:
            return {"status": "PENDING", "why": "capture window opens %s UTC" % start.strftime("%Y-%m-%d %H:%M")}
        return {"status": "PENDING", "why": "window is open; not captured or evaluated yet" + ("" if calendar_ok else " (calendar must be refreshed first)")}
    sig = (engine or {}).get("signal")
    miss = _miss_names(missing)
    if sig in ("BUY", "SELL"):
        if "RULE" in codes and not (GATE_CODES & set(codes)) and "OUT_OF_WINDOW" not in codes and "ILLIQUID_OR_UNKNOWN" not in codes and avail >= MIN_AVAILABLE:
            return {"status": sig, "why": "engine rule met (bull %s, bear %s of %s available points)" % (engine.get("bull"), engine.get("bear"), avail)}
        return {"status": "DATA_INCOMPLETE", "why": "a %s without its rule code or with a gate code is not shown as a signal" % sig}
    if "OUT_OF_WINDOW" in codes:
        return {"status": "DATA_INCOMPLETE", "why": "evaluated outside the capture window"}
    gates = sorted(GATE_CODES & set(codes))
    illiquid_unknown = "ILLIQUID_OR_UNKNOWN" in codes
    if illiquid_unknown and "F5" not in miss:
        gates_known_illiquid = True
    else:
        gates_known_illiquid = False
        if illiquid_unknown:
            gates.append("ILLIQUID_OR_UNKNOWN")
    if gates or avail < MIN_AVAILABLE:
        shown = gates or ["INSUFFICIENT_COVERAGE"]
        return {"status": "DATA_INCOMPLETE", "why": "; ".join(REASON_TEXT.get(g, g) for g in shown)}
    if gates_known_illiquid:
        return {"status": "NO_TRADE", "why": REASON_TEXT["ILLIQUID"]}
    return {"status": "NO_TRADE", "why": "engine returned NO TRADE (%s)" % (", ".join(c for c in codes if c in ("BELOW_MARGIN", "CONFLICT")) or "rule not met")}


def _release_label(kind, plan) -> dict:
    rel = (plan or {}).get("assumed_release_et")
    word = {"BMO": "Before open (BMO)", "AMC": "After close (AMC)"}.get(kind, "Timing unknown")
    return {"kind": kind or "UNKNOWN", "label": word, "assumed_release_et": rel, "basis": (plan or {}).get("basis")}


def load_run_results(run_root) -> dict:
    """{(symbol, earnings_date): result row + run info} from saved one-time run reports. Read only; incomplete or unreadable files are skipped; self-test folders have no audit report."""
    out = {}
    root = Path(run_root) if run_root else None
    if not root or not root.is_dir():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith("run_") and not p.is_symlink()):
        f = d / "audit_report.json"
        try:
            rep = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for r in rep.get("results") or []:
            if r.get("capture_ts") and r.get("symbol") and r.get("earnings_date"):
                k = (r["symbol"], str(r["earnings_date"])[:10])
                if k not in out or str(r["capture_ts"]) >= str(out[k]["capture_ts"]):
                    out[k] = dict(r, _run=d.name, _simulation=bool(rep.get("simulation")))
    return out


def load_run_reports(run_root) -> list:
    """[(folder name, report)] oldest first by started_utc. Read only."""
    out = []
    root = Path(run_root) if run_root else None
    if root and root.is_dir():
        for d in (p for p in root.iterdir() if p.is_dir() and p.name.startswith("run_") and not p.is_symlink()):
            try:
                out.append((d.name, json.loads((d / "audit_report.json").read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                continue
    return sorted(out, key=lambda t: str(t[1].get("started_utc") or ""))


def derive_registry(saved_rows: list, reports: list):
    """In-memory event registry: the saved calendar first (at its observation time), then every real run report in order.
    A run that covered the whole open-window universe is a complete snapshot (symbols it no longer lists are REMOVED); smaller runs are partial."""
    from smart_move_event_registry import EventRegistry
    reg = EventRegistry(None)
    saved = [s for s in saved_rows if s.get("earnings_date") and s["calendar"].get("eligible_for_shadow")]
    if saved:
        obs = max((s["calendar"].get("calendar_observed_at") or "") for s in saved) or "1970-01-01T00:00:00+00:00"
        reg.apply_snapshot([{"symbol": s["symbol"], "earnings_date": s["earnings_date"], "kind": s.get("kind"), "confirmed": True} for s in saved], obs, complete=True)
    for name, rep in reports:
        if rep.get("simulation") or not rep.get("results"):
            continue
        evs = [{"symbol": r["symbol"], "earnings_date": str(r["earnings_date"])[:10], "kind": r.get("kind"), "confirmed": r.get("calendar_status") in ("VENDOR_CONFIRMED", "COMPANY_VERIFIED")}
               for r in rep["results"] if r.get("earnings_date") and r.get("capture_ts")]
        full = len(evs) >= 100 and not rep.get("not_evaluated")
        ts = rep.get("updated_utc") or rep.get("started_utc") or max(str(r["capture_ts"]) for r in rep["results"] if r.get("capture_ts"))
        reg.apply_snapshot(evs, ts, complete=full)
    return reg


def window_phase(plan, now) -> str:
    start, cutoff = _dt((plan or {}).get("window_start_utc")), _dt((plan or {}).get("cutoff_utc"))
    if start is None or cutoff is None:
        return "UNKNOWN"
    return "AHEAD" if now < start else "OPEN" if now <= cutoff else "CLOSED"


def blocker(evaluated: bool, phase: str, calendar_ok: bool, any_run: bool) -> Optional[str]:
    """Why a stock has no prediction. Only for stocks with no evaluation; explains the blocker, never invents a result."""
    if evaluated:
        return None
    if phase == "AHEAD":
        return "Waiting: the capture window has not opened. Nothing can be captured before it opens."
    if phase == "UNKNOWN":
        return "Blocked: release timing (BMO/AMC) is unknown, so no window can be computed."
    if phase == "CLOSED":
        return "Missed: the window closed and no collection captured this stock. A prediction made now would be after the cut-off and cannot count."
    if not calendar_ok:
        return "Window open, no prediction: calendar status is not confirmed; the calendar must be refreshed first, and no collection is running."
    return ("Window open, no prediction: no scheduled collection has captured this stock. The one-time test covers at most 10 stocks and no recurring collection is enabled; "
            "this stock is predicted only if the test selected it." if not any_run else
            "Window open, no prediction: a run report exists but did not include this stock (not tested). No recurring collection is enabled.")


def build_board(saved_rows: list, live_rows: list, run_results: dict, *, now: datetime, variant: str = "continuation", registry=None, plan_fn=None) -> dict:
    """saved_rows: saved-calendar rows (smart_move_routes.current_rows). live_rows: LiveScorer rows. run_results: load_run_results()."""
    live = {(r["symbol"], r["earnings_date"]): r for r in live_rows if r.get("earnings_date") and r.get("capture_ts")}
    rows = []
    cur = registry.current() if registry is not None else None
    removed = []
    if cur is not None:                                          # events the registry knows that the saved calendar does not (e.g. newly confirmed by a later run) become rows too
        have = {(s["symbol"], s.get("earnings_date")) for s in saved_rows if s["calendar"].get("eligible_for_shadow")}
        saved_rows = list(saved_rows)
        for sym, v in cur.items():
            if (sym, v["earnings_date"]) in have:
                continue
            r = run_results.get((sym, v["earnings_date"])) or {}
            plan = plan_fn(v["earnings_date"], v.get("kind")) if plan_fn and v.get("kind") in ("BMO", "AMC") else None
            saved_rows.append({"symbol": sym, "company": r.get("company"), "sector": None, "earnings_date": v["earnings_date"], "kind": v.get("kind"), "plan": plan,
                               "calendar": {"eligible_for_shadow": True, "status_at_observation": r.get("calendar_status"), "calendar_observed_at": v["observed_ts"]}})
    for s in saved_rows:
        cal = s["calendar"]
        if not cal.get("eligible_for_shadow") or not s.get("earnings_date"):
            continue
        key = (s["symbol"], s["earnings_date"])
        if cur is not None and (s["symbol"] not in cur or cur[s["symbol"]]["earnings_date"] != s["earnings_date"]):
            removed.append({"symbol": s["symbol"], "earnings_date": s["earnings_date"], "why": "no longer in the current event registry (removed or rescheduled)"})
            continue
        if key[1] < now.astimezone(_et()).date().isoformat():
            continue                                              # past events are not upcoming
        plan = s.get("plan")
        run, lv = run_results.get(key), live.get(key)
        src = "RUN_REPORT" if run else "LIVE_STORE" if lv else "SAVED_CALENDAR"
        evaluated = bool(run or lv)
        if run:
            engines = {v: run[v] for v in ("continuation", "contrarian")}
            miss, avail, last = run.get("missing_factors"), run.get("available_points"), run.get("capture_ts")
            cal_now, mode = run.get("calendar_status"), "SIMULATION" if run.get("_simulation") else "LIVE"
        elif lv:
            engines = lv["engine"]
            miss, avail, last = lv.get("missing_factors"), lv.get("available_points"), lv.get("capture_ts")
            cal_now, mode = lv["calendar"].get("status_now"), lv.get("evidence_mode") or "LIVE"
            plan = lv.get("plan") or plan
        else:
            engines = {v: None for v in ("continuation", "contrarian")}
            miss, avail, last = s.get("missing_factors"), None, cal.get("calendar_observed_at")
            cal_now, mode = cal.get("status_at_observation"), "SAVED"
        calendar_ok = cal_now in ("VENDOR_CONFIRMED", "COMPANY_VERIFIED")
        variants = {}
        for v in ("continuation", "contrarian"):
            c = classify(engines[v], evaluated=evaluated, missing=miss, plan=plan, now=now, calendar_ok=calendar_ok)
            e = engines[v] or {}
            c.update(signal=e.get("signal") if evaluated else None, bull=e.get("bull") if evaluated else None, bear=e.get("bear") if evaluated else None,
                     reason_codes=list(e.get("reason_codes") or []) if evaluated else [])
            variants[v] = c
        last_dt = _dt(last)
        phase = window_phase(plan, now)
        rows.append({"phase": phase, "blocker": blocker(evaluated, phase, calendar_ok, bool(run_results)), "evaluated": evaluated, "symbol": s["symbol"], "company": s.get("company"), "earnings_date": s["earnings_date"], "release": _release_label(s.get("kind"), plan),
                     "window": {"start_utc": (plan or {}).get("window_start_utc"), "cutoff_utc": (plan or {}).get("cutoff_utc")},
                     "status": variants[variant]["status"], "status_why": variants[variant]["why"], "variants": variants,
                     "points": {"available": avail if evaluated else None, "max": 40, "bull": variants[variant]["bull"], "bear": variants[variant]["bear"], "min_to_decide": MIN_AVAILABLE},
                     "factors": factor_states(miss, evaluated), "evaluation_source": src, "evidence_mode": mode, "run": run.get("_run") if run else None,
                     "last_update": last_dt.isoformat() if last_dt else None, "age_hours": round((now - last_dt).total_seconds() / 3600, 1) if last_dt else None,
                     "calendar_status": cal_now, "calendar_ok": calendar_ok, "sector": s.get("sector")})
    order = {"BUY": 0, "SELL": 1, "NO_TRADE": 2, "DATA_INCOMPLETE": 3, "PENDING": 4}
    rows.sort(key=lambda r: (r["earnings_date"], order[r["status"]], r["symbol"]))
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
    ev = [r for r in rows if r["evaluated"]]
    summary = {"total": len(rows), "evaluated": len(ev), "not_evaluated": len(rows) - len(ev),
               "evaluated_real": sum(1 for r in ev if r["evidence_mode"] != "SIMULATION"), "evaluated_simulated": sum(1 for r in ev if r["evidence_mode"] == "SIMULATION"),
               "complete_data": sum(1 for r in ev if r["points"]["available"] is not None and r["points"]["available"] >= MIN_AVAILABLE and not any(f["state"] == "MISSING" for f in r["factors"])),
               "incomplete_data": sum(1 for r in ev if r["status"] == "DATA_INCOMPLETE" or any(f["state"] == "MISSING" for f in r["factors"])),
               "no_capture": sum(1 for r in rows if not r["evaluated"]),
               "window_open_no_prediction": sum(1 for r in rows if r["phase"] == "OPEN" and not r["evaluated"]),
               "window_ahead": sum(1 for r in rows if r["phase"] == "AHEAD"), "window_closed_missed": sum(1 for r in rows if r["phase"] == "CLOSED" and not r["evaluated"])}
    upcoming = sorted(t for t in (_dt(r["window"]["start_utc"]) for r in rows if r["phase"] == "AHEAD") if t)
    summary["next_window_opens_utc"] = upcoming[0].isoformat() if upcoming else None
    return {"as_of": now.isoformat(), "summary": summary, "variant": variant, "counts": counts, "total": len(rows), "rows": rows,
            "removed_events": removed, "registry": {"current": len(cur)} if cur is not None else None,
            "sources": {"run_reports": len(run_results), "live_rows": len(live), "saved_calendar_rows": len(saved_rows)}}


def _et():
    from zoneinfo import ZoneInfo
    return ZoneInfo("America/New_York")
