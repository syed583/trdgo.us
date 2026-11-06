"""Evidence collection for a SELECTED set of earnings stocks, for the frozen Smart Money Move engine (Phase 15 / "engine20"). It reuses the EXISTING pieces only:
  * the engine's own evidence planner `RefreshService.plan_evidence` (SPY, sector ETFs, then per stock: 3Y daily bars `ohlc`, earnings history `earnings`, optional `vol_context` / `iv_rank`) so the evidence kinds are exactly the ones
    the frozen scorer reads, and an item that is already fresh is never requested again;
  * the governed path: `svc._call` -> governor (authorized cap = attempts incl. failures, spacing, 401/403/429 stop, kill switch) -> `InfraFetcher` (attestation gate); no other client exists here;
  * the calendar guard (a calendar refresh only when it is older than 5 h) and the same kill-switch release/re-engage as the batch runner.
It adds NO scoring: `report()` runs the frozen `LiveScorer` and only classifies its output. Nothing here sends a request unless run() is called with a live service, an armed run and a valid attestation (enforced by the runner)."""
from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import smart_move_calendar_guard as G
import smart_move_live as L
from smvshadow import cutoff as co  # noqa: E402  (read only: the frozen window and order-deadline rules)
from smvshadow import scorer as SC  # noqa: E402  (read only: the frozen liquidity floor)

UTC = timezone.utc
MODEL_VERSION = "score40-v1"
GATES = ("OUT_OF_WINDOW", "UNKNOWN_TIMING", "TIMING_CONTRADICTED", "NOT_TRADABLE", "ILLIQUID_OR_UNKNOWN", "INSUFFICIENT_COVERAGE", "STALE_EVIDENCE", "SUSPECT_WINDOW")


PRIORITY_URGENT_H = 4.0      # a cutoff this close is served strictly by deadline, whatever the proxy says


def cap_tier(market_cap) -> str:
    """PRIORITISATION proxy only (the one liquidity-related number the saved calendar carries). It orders work when the budget cannot cover every open event; it never excludes an event: the frozen ADV20 gate still decides
    from the price bars, and a deferred event stays eligible for as long as its window is open."""
    try:
        c = float(market_cap)
    except (TypeError, ValueError):
        return "D"
    return "A" if c >= 10e9 else "B" if c >= 2e9 else "C" if c >= 3e8 else "D"


def tradability(row: dict) -> Optional[bool]:
    """Whether the frozen order-deadline rule makes this event tradable: True / False, or None whenever that cannot be established. Computed from the event's own date and session with the SAME function the scorer uses
    (`cutoff.plan_event`; the scorer's rows do not carry the flag, which is why an earlier version that read it from the row never fired). It answers None - never False - if the date or session is missing or unreadable, or
    if the plan recomputed here does not match the row's own window (another input, such as a filing time, changed it): an uncertain event is never skipped."""
    try:
        if row.get("kind") not in ("BMO", "AMC"):
            return None
        p = co.plan_event(date.fromisoformat(str(row["earnings_date"])[:10]), row["kind"])
        rp = row.get("plan") or {}
        if rp.get("cutoff_utc") is not None and (_dt(rp["cutoff_utc"]) != p["cutoff_utc"] or _dt(rp["window_start_utc"]) != p["window_start_utc"]):
            return None
        return bool(p["tradable"])
    except Exception:  # noqa: BLE001
        return None


def _dt(x) -> datetime:
    d = x if isinstance(x, datetime) else datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


# ------------------------------------------------------------------ selection
def select_top(rows: list, market_caps: dict, now, *, n: int = 20, event_date: Optional[str] = None, session: Optional[str] = None, only: Optional[list] = None,
               policy: str = "cap", pending: Optional[set] = None, audit: Optional[list] = None) -> list:
    """`only` pins the owner-approved symbol list (kept in that order, every symbol still has to pass the same eligibility checks; a listed symbol that fails them is simply absent, never swapped for another). Vendor-confirmed events (no calendar blocker) with a known BMO/AMC session whose prediction window is open at `now`, optionally limited to one date and session, ranked by market cap. Market cap is a SELECTION
    heuristic only; the engine's own liquidity gate decides from the price bars."""
    now = _dt(now)
    ok = []

    def note(r, status, why):
        if audit is not None:
            audit.append({"symbol": r["symbol"], "event_date": r.get("earnings_date"), "session": r.get("kind"), "status": status, "reason": why})
    for r in rows:
        p = r.get("plan")
        if p is None:                                                    # no event date (vendors more than one day apart, or only stale vendor rows): no window exists, so it can never be selected; say why
            note(r, "SKIPPED", "no usable event date: %s%s" % (",".join(map(str, r.get("blocked_by") or ["NO_EVENT_DATE"])), (" (%s)" % ",".join(map(str, (r.get("calendar") or {}).get("no_trade_reasons") or []))) if (r.get("calendar") or {}).get("no_trade_reasons") else ""))
            continue
        if r["blocked_by"] or r["kind"] not in ("BMO", "AMC") or not (_dt(p["window_start_utc"]) <= now <= _dt(p["cutoff_utc"])):
            if audit is not None and r.get("kind") in ("BMO", "AMC"):
                note(r, "SKIPPED", "calendar not vendor-confirmed: %s" % ",".join(map(str, r["blocked_by"])) if r["blocked_by"] else "prediction window not open now")
            continue
        if policy == "deadline_priority" and tradability(r) is False:           # the frozen scorer answers NO VALID PREDICTION (NOT_TRADABLE) for this event whatever evidence it gets: requesting evidence would only spend budget
            note(r, "SKIPPED", "not tradable under the frozen order-deadline rule: the engine answers NO VALID PREDICTION (NOT_TRADABLE) whatever the evidence, so none is requested")
            continue
        if event_date and r["earnings_date"] != event_date or session and r["kind"] != session:
            note(r, "SKIPPED", "outside the --select-date/--select-session filter")
            continue
        if only is not None and r["symbol"] not in only:
            continue
        if policy in ("deadline", "deadline_priority") and pending is not None and r["symbol"] not in pending:
            note(r, "SKIPPED", "its evidence is already fresh: no request needed")        # an already-collected stock never occupies a selection slot
            continue
        ok.append(r)
    if only is not None:
        ok.sort(key=lambda r: (only.index(r["symbol"]), r["symbol"]))
    elif policy == "deadline_priority":
        urgent = lambda r: (_dt(r["plan"]["cutoff_utc"]) - now).total_seconds() <= PRIORITY_URGENT_H * 3600          # noqa: E731
        ok.sort(key=lambda r: (0, _dt(r["plan"]["cutoff_utc"]), cap_tier(market_caps.get(r["symbol"])), r["symbol"]) if urgent(r)
                else (1, cap_tier(market_caps.get(r["symbol"])), _dt(r["plan"]["cutoff_utc"]), -(market_caps.get(r["symbol"]) or 0), r["symbol"]))      # urgent cutoffs strictly by deadline; the rest by proxy tier, then deadline
    elif policy == "deadline":
        ok.sort(key=lambda r: (_dt(r["plan"]["cutoff_utc"]), -(market_caps.get(r["symbol"]) or 0), r["symbol"]))      # deterministic: closest cutoff first, then larger cap, then symbol
    else:
        ok.sort(key=lambda r: (-(market_caps.get(r["symbol"]) or 0), r["symbol"]))
    for i, r in enumerate(ok):
        note(r, "SELECTED" if i < n else "NOT_SELECTED", ("rank %d of %d by %s" % (i + 1, len(ok), "closest prediction cutoff" if policy == "deadline" else "urgent deadline, then proxy tier %s and deadline" % cap_tier(market_caps.get(r["symbol"])) if policy == "deadline_priority" else "market capitalisation")) if i < n
             else ("DEFERRED by priority, still eligible while its window is open: ranked %d beyond the %d slots (proxy tier %s)" % (i + 1, n, cap_tier(market_caps.get(r["symbol"]))) if policy == "deadline_priority" else "ranked %d, beyond the %d slots" % (i + 1, n)))
    return [{"rank": i + 1, "symbol": r["symbol"], "company": r.get("company"), "event_date": r["earnings_date"], "session": r["kind"], "sector": r.get("sector"), "etf": L.SECTOR_ETF.get(r.get("sector")),
             "market_cap": market_caps.get(r["symbol"])} for i, r in enumerate(ok[:n])]


# ------------------------------------------------------------------ plan
def plan_tasks(svc, selected: list, now, *, budget: int, with_f7: bool = False) -> dict:
    """The engine's missing evidence for the selected stocks only, funded in this order: SPY (market + relative strength for every stock), then whole stocks in rank order (both bars and earnings history, so no stock is left
    half-funded), then sector ETFs by how many selected stocks they serve, then F7 context if requested. Anything that does not fit the budget is listed as unfunded, never silently dropped."""
    sel = [s["symbol"] for s in selected]
    etf_of = {s["symbol"]: s["etf"] for s in selected}
    need = svc.plan_evidence(_dt(now))                                   # already skips evidence that is fresh
    by = {}
    for t in need:
        by.setdefault((t["kind"], t["symbol"]), t)
    funded, unfunded, left = [], [], budget

    def take(t, why):
        nonlocal left
        if t is None:
            return True
        if left > 0:
            funded.append(dict(t, why=why))
            left -= 1
            return True
        unfunded.append(dict(t, why=why))
        return False
    take(by.get(("ohlc", "SPY")), "market and relative strength for every stock")
    for sym in sel:
        pair = [by.get(("ohlc", sym)), by.get(("earnings", sym))]
        need_n = sum(1 for t in pair if t)
        if need_n > left:
            unfunded += [dict(t, why="does not fit the budget") for t in pair if t]
            continue
        for t, why in zip(pair, ("3Y daily bars: F5 and liquidity", "earnings history: F3")):
            take(t, why)
    for etf, n in Counter(e for e in etf_of.values() if e).most_common():
        take(by.get(("ohlc", etf)), "sector ETF for %d selected stock(s): F8 sector" % n)
    if with_f7:
        for sym in sel:
            for k in ("vol_context", "iv_rank"):
                take(by.get((k, sym)), "F7 live capture")
    unfunded_syms = {t["symbol"] for t in unfunded if t["symbol"] in sel}
    return {"funded": funded, "unfunded": unfunded, "budget": budget, "attempts_planned": len(funded), "spare": left, "stocks_fully_funded": [x for x in sel if x not in unfunded_syms],
            "already_fresh_stocks": [x for x in sel if ("ohlc", x) not in by and ("earnings", x) not in by]}


# ------------------------------------------------------------------ execution (live only through the governed path)
def attempts_of(svc) -> int:
    """Every provider attempt the governor has granted today (successful, failed AND retried), the number a per-run hard maximum has to be measured on: tasks are not attempts when retries are on."""
    used = getattr(getattr(svc, "gov", None), "used_day", None)
    return int(sum(used.values())) if used else 0


def hourly_allowance_of(svc) -> int:
    """The governor's remaining allowance for this hour (our own rail), or a large number when the governor does not expose one."""
    fn = getattr(getattr(svc, "gov", None), "hourly_allowance", None)
    if not callable(fn):
        return 1 << 30
    try:
        return int(fn())
    except (TypeError, ValueError):
        return 1 << 30


def last_denial_reason(svc):
    """The reason the service recorded for its last not-done request (RefreshService._call stores it in the store state), or None."""
    try:
        return ((svc.store.state() or {}).get("last_error") or {}).get("reason")
    except (AttributeError, OSError, ValueError):
        return None


def scan_ok_of(res: dict) -> bool:
    """The publisher's scan_ok rule: a pass is successful when NO provider request failed and it either finished or stopped at our own hourly rail (HOURLY_CAP, a pause: the untouched events stay
    PENDING_REFRESH for the next pass). Any other halt, or any provider failure, makes the pass unsuccessful."""
    return int(res.get("failed") or 0) == 0 and res.get("halted") in (None, "HOURLY_CAP")


# ------------------------------------------------------------------ execution (live only through the governed path)
def run(svc, tasks: list, *, now, ceiling: int, state_dir, stopped=lambda: False, max_consecutive_failures: int = 3, abort_on_stale_bars: bool = False) -> dict:
    """Sequential, one request in flight. Every task goes through svc._call (governor + InfraFetcher) and a successful body is appended to the store's append-only evidence file. Stops on a provider stop, the ceiling,
    the kill switch, or repeated failures. Never retries (the service is built with retries 0 by the runner).
    abort_on_stale_bars (P1, opt-in): the shared SPY price payload is fetched first; if the provider still returns a bar older than the last COMPLETED session (or one retrieved before that session ended), every stock payload
    would be stale the same way, so the run stops with halted="STALE_BARS" instead of spending the rest of the budget on evidence the frozen freshness rule would reject."""
    d = Path(state_dir)
    d.mkdir(parents=True, exist_ok=True)
    done, failed, consecutive, halted = [], [], 0, None
    stamp = _dt(now).strftime("%Y%m%dT%H%M%S")
    attempts0 = attempts_of(svc)
    worst_case = 1 + int(getattr(getattr(svc, "cfg", None), "max_retries", 0) or 0)           # one task can cost this many attempts
    for t in tasks:
        if len(done) + len(failed) >= ceiling or (attempts_of(svc) - attempts0) + worst_case > ceiling:      # HARD per-run maximum, measured in attempts: a task whose worst case would pass it is not started
            halted = "CEILING_REACHED"
            break
        if svc.kill_engaged:
            halted = "KILL_SWITCH"
            break
        if stopped():
            halted = "PROVIDER_STOPPED"
            break
        if hourly_allowance_of(svc) < 1:                                                           # our OWN hourly rail is used up: pause here, no attempt is made (not a provider failure)
            halted = "HOURLY_CAP"
            break
        rid = "%s-%s-%s" % (t["kind"], t["symbol"], stamp)
        body, sha, at = svc._call(t["kind"], t["symbol"], t["params"], rid)
        if body is None:
            if last_denial_reason(svc) == "HOURLY_SHARED_CAP":                                      # the governor denied at our own hourly rail (ledger DENIED_HOURLY_SHARED_CAP): a pause, not a provider failure
                halted = "HOURLY_CAP"
                break
            failed.append({"kind": t["kind"], "symbol": t["symbol"]})
            consecutive += 1
            if consecutive >= max_consecutive_failures:
                halted = "CONSECUTIVE_FAILURES"
                break
            continue
        consecutive = 0
        svc._ingest_evidence(t["kind"], t["symbol"], t["params"], body, sha, at, rid)
        done.append({"kind": t["kind"], "symbol": t["symbol"], "retrieved_at": at.isoformat(), "raw_sha256": sha})
        if abort_on_stale_bars and t["kind"] == "ohlc" and t["symbol"] == "SPY" and not L.rec_fresh(svc._latest("ohlc", "SPY", t["params"]), "ohlc", svc.clock.now(), svc.cfg):
            halted = "STALE_BARS"
            break
    res = {"requests_attempted": len(done) + len(failed), "attempts": attempts_of(svc) - attempts0, "ok": len(done), "failed": len(failed), "failures": failed, "halted": halted, "done": done, "finished_at": svc.clock.now().isoformat()}
    if halted == "HOURLY_CAP":
        res["paused"] = "HOURLY_CAP"
    (d / "engine20_state.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str), encoding="utf-8")
    return res


# ------------------------------------------------------------------ report on the frozen engine's own output
def classify(row: dict, now) -> dict:
    """One selected stock -> BUY / SELL / NO TRADE (genuine) / NO VALID PREDICTION / PENDING FINAL REFRESH, plus coverage, liquidity, freshness, window and model version. The signal is the frozen engine's own
    continuation variant; the contrarian variant is carried alongside. Nothing is scored or defaulted here."""
    now = _dt(now)
    e = (row.get("engine") or {}).get("continuation") or {}
    c = (row.get("engine") or {}).get("contrarian") or {}
    codes = list(e.get("reason_codes") or [])
    gates = [x for x in codes if x in GATES]
    p = row["plan"]
    window = "OPEN" if _dt(p["window_start_utc"]) <= now <= _dt(p["cutoff_utc"]) else ("CLOSED" if now > _dt(p["cutoff_utc"]) else "NOT_YET_OPEN")
    stale = [f for f in (row.get("missing_factors") or []) if f.get("reason") in ("STALE_AGE", "RETRIEVED_BEFORE_WINDOW") or "NO_FRESH" in str(f.get("reason"))]
    sig = e.get("signal")
    if sig in ("BUY", "SELL"):
        label, why = sig, "frozen rule met: bull %s / bear %s" % (e.get("bull"), e.get("bear"))
    elif not gates and sig == "NO_TRADE":
        label, why = "NO TRADE (genuine)", "all gates passed; " + ", ".join(codes or ["no rule met"])
    elif stale and not [g for g in gates if g not in ("INSUFFICIENT_COVERAGE", "ILLIQUID_OR_UNKNOWN")]:
        label, why = "PENDING FINAL REFRESH", "fresh market-close data required: " + "; ".join("%s %s" % (f["factor"], f["reason"]) for f in stale)
    else:
        label, why = "NO VALID PREDICTION", "blocked: " + ", ".join(gates or codes)
    avail = e.get("available")
    adv = row.get("adv20")
    return {"symbol": row["symbol"], "company": row.get("company"), "event_date": row["earnings_date"], "session": row["kind"], "result": label, "reason": why, "coverage_points": avail, "coverage_max": e.get("max", 40),
            "bull": e.get("bull"), "bear": e.get("bear"), "contrarian_signal": c.get("signal"), "liquidity": "PASS" if adv and adv >= SC.LIQUIDITY_MIN else "FAIL" if adv else "UNKNOWN",
            "adv20_usd": adv, "missing_factors": [(f["factor"], f["reason"]) for f in (row.get("missing_factors") or [])], "window": window, "window_cutoff_utc": _dt(p["cutoff_utc"]).isoformat(),
            "evidence_retrieved": sorted({x.get("retrieved_at") for x in (row.get("evidence") or []) if x.get("retrieved_at")})[-1:] or None, "model_version": MODEL_VERSION, "reason_codes": codes}


def report(svc, selected: list, now) -> dict:
    """Run the FROZEN scorer on the store and classify the selected symbols."""
    rows = L.LiveScorer(svc).score_all(_dt(now))
    sel = {s["symbol"]: s for s in selected}
    out = [classify(r, now) for r in rows if r["symbol"] in sel]
    seen = {o["symbol"] for o in out}
    for s in selected:
        if s["symbol"] not in seen:
            out.append({"symbol": s["symbol"], "company": s.get("company"), "event_date": s["event_date"], "session": s["session"], "result": "NO VALID PREDICTION", "reason": "not scored (event not eligible at this time)",
                        "model_version": MODEL_VERSION})
    return {"as_of": _dt(now).isoformat(), "counts": dict(Counter(o["result"] for o in out)), "rows": sorted(out, key=lambda o: (sel[o["symbol"]]["rank"], o["symbol"])), "model_version": MODEL_VERSION,
            "note": "Signals are the frozen engine's own output; a NO TRADE caused by missing or stale data is shown as no valid prediction or pending refresh, never as a neutral view."}
