"""MONDAY_BMO_FRIDAY: a SEPARATE prediction window for earnings released before the open on a day with no trading session between the frozen window and the release (a Monday, or the day
after a market holiday). Owner decision 2026-11-05. The frozen model is NOT changed: the same prereg (hash-checked), the same scorer, factors, weights, BUY/SELL thresholds, 24/40 coverage,
ADV20 liquidity gate, freshness rules and adapters are called unchanged. The ONLY difference is the plan handed to them:

    frozen:  window = release - 48 h .. release - 24 h  (Saturday 06:00 ET .. Sunday 06:00 ET for a Monday 06:00 ET release)  -> NOT_TRADABLE (no session before the release)
    variant: window = previous session close .. base-session close - 30 min  (Thursday 16:00 ET .. Friday 15:30 ET)            -> tradable: a Friday close order

Everything the variant produces goes to its OWN folder (<publish-dir>/monday_bmo_friday): its own append-only hash-chained ledger, status file, outcomes and accuracy. The frozen
ledger, universe and status are never read for writing and never written. Nothing here places an order.
"""
from __future__ import annotations

import hashlib
import json
import os
import statistics
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "docs" / "smart_move" / "shadow_impl"))
import smart_move_engine20 as E20  # noqa: E402
import smart_move_live as L  # noqa: E402
import smart_move_live_publisher as PUB  # noqa: E402
import smart_move_outcomes as O  # noqa: E402
from smvshadow import adapters as ad  # noqa: E402
from smvshadow import calendar_confirm as cc  # noqa: E402
from smvshadow import cutoff as co  # noqa: E402
from smvshadow import freshness as fr  # noqa: E402
from smvshadow import grader as G  # noqa: E402
from smvshadow import scorer as sc  # noqa: E402
from smvshadow.freshness import missing  # noqa: E402

UTC, ET = timezone.utc, co.ET
VARIANT = "MONDAY_BMO_FRIDAY"
SUBDIR = "monday_bmo_friday"
SCHEMA = "smart_move.monday_bmo_friday_prediction.v1"
STATUS_SCHEMA = "smart_move.monday_bmo_friday_status.v1"
ACCURACY = "accuracy.json"
OUTCOMES_DIR = "outcomes"
CUTOFF_BEFORE_CLOSE_MIN = 30            # the Friday entry deadline: 30 minutes before the close (20 minutes before the frozen 10-minute market-on-close order deadline, so the order is always placeable)
FACTOR_NAMES = {3: "Earnings-reaction history", 5: "Price / technical", 7: "Volatility (skew)", 8: "Sector / market"}


def _dt(x) -> datetime:
    return PUB._dt(x)


# ------------------------------------------------------------------ the variant window
def applies(event_date: date, kind: str) -> bool:
    """Only BMO events the FROZEN rule marks not tradable because no session separates its window from the release. Any other event is left to the frozen model alone."""
    if kind != "BMO" or not co._mc().is_trading_day(event_date):
        return False
    return co.plan_event(event_date, kind)["tradable"] is False


def variant_plan(event_date: date, kind: str = "BMO") -> dict:
    """Same keys as cutoff.plan_event (the adapters and the scorer read window_start_utc, cutoff_utc, last_usable_session, tradable) plus the frozen values for the record."""
    frozen = co.plan_event(event_date, kind)
    mc = co._mc()
    base, react = frozen["base_session"], frozen["reaction_session"]
    close = mc.session_close_utc(base)
    cutoff = close - timedelta(minutes=CUTOFF_BEFORE_CLOSE_MIN)
    start = mc.session_close_utc(mc.prev_trading_day(base))
    return {"assumed_release_utc": frozen["assumed_release_utc"], "assumed_release_et": frozen["assumed_release_et"], "basis": frozen["basis"] + "+" + VARIANT,
            "window_start_utc": start, "deadline_utc": cutoff, "cutoff_utc": cutoff, "cutoff_et": cutoff.astimezone(ET), "evidence_only": False, "last_usable_session": co.last_usable_session(cutoff),
            "dst_between_cutoff_and_release": frozen["assumed_release_utc"].astimezone(ET).utcoffset() != cutoff.astimezone(ET).utcoffset(),
            "base_session": base, "reaction_session": react, "tradable": co.tradable(cutoff, base), "early_close_base": bool(mc.is_early_close(base)),
            "frozen_window_start_utc": frozen["window_start_utc"], "frozen_cutoff_utc": frozen["cutoff_utc"], "frozen_tradable": frozen["tradable"], "variant": VARIANT}


# ------------------------------------------------------------------ events
def events(svc, now: datetime, *, horizon_days: Optional[int] = None) -> list:
    """Vendor-confirmed events the variant applies to. Default: variant window open now. With horizon_days: every applicable event whose cutoff has not passed and whose release is within the horizon."""
    out = []
    for sym, pairs in svc._calobs().items():
        obs = [o for o, _ in pairs if o.observed_at <= now]
        if not obs:
            continue
        ev = cc.evaluate(sym, obs, now, now.astimezone(ET).date())
        if not ev["eligible_for_shadow"] or ev["event_date"] is None or not applies(ev["event_date"], ev["kind"]):
            continue
        plan = variant_plan(ev["event_date"], ev["kind"])
        if horizon_days is None:
            if not (plan["window_start_utc"] <= now <= plan["cutoff_utc"]):
                continue
        elif not (now <= plan["cutoff_utc"] and plan["assumed_release_utc"] <= now + timedelta(days=horizon_days)):
            continue
        sector = next((r["sector"] for _, r in pairs if r.get("sector")), None)
        try:
            cap = max(float(r["marketcap"]) for _, r in pairs if r.get("marketcap"))
        except ValueError:
            cap = 0.0
        out.append({"symbol": sym, "event_date": ev["event_date"], "kind": ev["kind"], "plan": plan, "sector": sector, "etf": L.SECTOR_ETF.get(sector), "marketcap": cap, "eval": ev, "pairs": pairs})
    out.sort(key=lambda e: (e["plan"]["cutoff_utc"], -e["marketcap"], hashlib.sha256(e["symbol"].encode()).hexdigest()))
    return out


def stale_slots(svc, now: datetime, *, lookahead_days: int = 5) -> list:
    """Release dates the variant applies to whose variant window is open now or opens within 24 h, and whose newest UW premarket calendar observation is older than the service's calendar limit (or absent).
    Date-based on purpose: a stale calendar row is exactly what hides an event from events(), so the refresh decision must not depend on events()."""
    out = []
    newest = {}
    for r in svc.store.read("cal_obs"):
        if r.get("source") == "UW_CALENDAR" and r.get("label") == "premarket":
            newest[r["date"]] = max(newest.get(r["date"], ""), r["observed_at"])
    today = now.astimezone(ET).date()
    for k in range(0, lookahead_days + 1):
        d = today + timedelta(days=k)
        if not applies(d, "BMO"):
            continue
        p = variant_plan(d, "BMO")
        if not (p["window_start_utc"] - timedelta(hours=24) <= now <= p["cutoff_utc"]):
            continue
        at = newest.get(d.isoformat())
        if at is None or now - _dt(at) > timedelta(hours=svc.cfg.calendar_interval_h):
            out.append(d)
    return out


# ------------------------------------------------------------------ evidence (same kinds, same freshness, window-aware against the VARIANT window)
def plan_evidence(svc, evs: list, now: datetime, *, with_f7: bool = False) -> list:
    exp = L.expected_session(now).isoformat()
    tasks, seen = [], set()
    spy_after = max((e["plan"]["window_start_utc"] for e in evs), default=None)
    etf_after = {}
    for e in evs:
        if e["etf"]:
            etf_after[e["etf"]] = max(etf_after.get(e["etf"], e["plan"]["window_start_utc"]), e["plan"]["window_start_utc"])

    def add(kind, symbol, params, why, after):
        key = L._key(kind, symbol, params)
        if key in seen:
            return
        seen.add(key)
        rec = svc._latest(kind, symbol, params)
        fresh = L.rec_fresh(rec, kind, now, svc.cfg)
        if fresh and after is not None and rec["retrieved_at"] < after.isoformat():
            fresh, why = False, why + " (re-request: retrieved before the variant window opened)"
        if not fresh:
            tasks.append({"kind": kind, "symbol": symbol, "params": params, "why": why})
    if evs:
        add("ohlc", "SPY", {"timeframe": "3Y"}, "market", spy_after)
    for e in evs:
        if e["etf"]:
            add("ohlc", e["etf"], {"timeframe": "3Y"}, "sector of " + e["symbol"], etf_after[e["etf"]])
    for e in evs:
        ws = e["plan"]["window_start_utc"]
        add("ohlc", e["symbol"], {"timeframe": "3Y"}, "price", ws)
        add("earnings", e["symbol"], {"limit": 24}, "history", ws)
        if with_f7:
            add("vol_context", e["symbol"], {"date": exp}, "F7 live capture", ws)
            add("iv_rank", e["symbol"], {"date": exp}, "F7 live capture", ws)
    return tasks


def fund(tasks: list, evs: list, *, budget: int, with_f7: bool = False) -> dict:
    """Same funding order as the frozen engine's plan_tasks: SPY, whole stocks by priority (bars and history together), sector ETFs by use, then F7."""
    sel = [e["symbol"] for e in evs]
    etf_of = {e["symbol"]: e["etf"] for e in evs}
    by = {}
    for t in tasks:
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
        if sum(1 for t in pair if t) > left:
            unfunded += [dict(t, why="does not fit the budget") for t in pair if t]
            continue
        for t, why in zip(pair, ("3Y daily bars: F5 and liquidity", "earnings history: F3")):
            take(t, why)
    for etf, n in Counter(e for e in etf_of.values() if e).most_common():
        take(by.get(("ohlc", etf)), "sector ETF for %d stock(s): F8 sector" % n)
    if with_f7:
        for sym in sel:
            for k in ("vol_context", "iv_rank"):
                take(by.get((k, sym)), "F7 live capture")
    unfunded_syms = {t["symbol"] for t in unfunded if t["symbol"] in sel}
    return {"funded": funded, "unfunded": unfunded, "budget": budget, "attempts_planned": len(funded), "spare": left, "stocks_fully_funded": [x for x in sel if x not in unfunded_syms],
            "already_fresh_stocks": [x for x in sel if ("ohlc", x) not in by and ("earnings", x) not in by]}


# ------------------------------------------------------------------ scoring: the frozen scorer, the variant plan
def score(svc, evs: list, now: datetime) -> list:
    """Mirror of LiveScorer.score_all for the variant events only: identical evidence selection, adapters, scorer call, gates; the plan is the variant window."""
    pre, sha = sc.load_prereg()
    scorer = L.LiveScorer(svc)
    rows = []
    for e in evs:
        sym, ev, pairs, plan = e["symbol"], e["eval"], e["pairs"], e["plan"]
        raw = [r for _, r in pairs]
        uw = next((r for r in raw if r["source"] == "UW_CALENDAR"), {})
        sector, etf = e["sector"], e["etf"]
        row = {"dataset": "LIVE_CURRENT", "variant": VARIANT, "symbol": sym, "company": uw.get("company"), "sector": sector, "earnings_date": str(ev["event_date"]), "kind": ev["kind"], "evidence_mode": scorer._mode_of(raw),
               "calendar": {"status_at_observation": ev["status"], "status_now": ev["status"], "reasons": ev["reasons"], "no_trade_reasons": ev["no_trade_reasons"], "eligible_for_shadow": ev["eligible_for_shadow"],
                            "vendors": ev["vendors"], "calendar_observed_at": ev["calendar_observed_at"].isoformat() if ev["calendar_observed_at"] else None, "uw_flag": uw.get("vendor_flag")},
               "observations": [{k: r[k] for k in ("source", "date", "label", "clock", "vendor_flag", "observed_at", "raw_sha256", "request_id")} for r in sorted(raw, key=lambda r: (r["source"], r["date"]))]}
        exp = L.expected_session(now)
        used, early = [], set()

        def latest(kind, symbol, params):
            r = svc._latest(kind, symbol, params)
            if r and r["retrieved_at"] <= now.isoformat():
                if r["retrieved_at"] < plan["window_start_utc"].isoformat():
                    early.add((kind, symbol))
                    return None
                return r
            return None

        def why(default, *keys):
            return "RETRIEVED_BEFORE_WINDOW" if any(k in early for k in keys) else default
        cand_rec, hist_rec = latest("ohlc", sym, {"timeframe": "3Y"}), latest("earnings", sym, {"limit": 24})
        spy_rec, sec_rec = latest("ohlc", "SPY", {"timeframe": "3Y"}), (latest("ohlc", etf, {"timeframe": "3Y"}) if etf else None)
        ctx_rec, ivr_rec = latest("vol_context", sym, {"date": exp.isoformat()}), latest("iv_rank", sym, {"date": exp.isoformat()})
        cfg = svc.cfg
        cand, sha_c = scorer._rows_of(cand_rec) if L.rec_fresh(cand_rec, "ohlc", now, cfg) else (None, None)
        hist, sha_h = scorer._rows_of(hist_rec) if L.rec_fresh(hist_rec, "earnings", now, cfg) else (None, None)
        spy, sha_s = scorer._rows_of(spy_rec) if L.rec_fresh(spy_rec, "ohlc", now, cfg) else (None, None)
        sec, _ = scorer._rows_of(sec_rec) if (sec_rec and L.rec_fresh(sec_rec, "ohlc", now, cfg)) else (None, None)
        ctx, sha_x = scorer._rows_of(ctx_rec) if L.rec_fresh(ctx_rec, "vol_context", now, cfg) else (None, None)
        ivr, _ = scorer._rows_of(ivr_rec) if L.rec_fresh(ivr_rec, "iv_rank", now, cfg) else (None, None)
        for r, k in ((cand_rec, "ohlc"), (hist_rec, "earnings"), (spy_rec, "ohlc"), (sec_rec, "ohlc"), (ctx_rec, "vol_context"), (ivr_rec, "iv_rank")):
            if r:
                used.append({"kind": k, "symbol": r["symbol"], "retrieved_at": r["retrieved_at"], "raw_sha256": r["raw_sha256"], "mode": r["mode"], "fresh": L.rec_fresh(r, k, now, cfg)})
        react = set()
        for r in (hist or []):
            k = ad._kind(r.get("report_time"))
            try:
                if k:
                    react.add(co.reaction_session(date.fromisoformat(str(r["report_date"])[:10]), k))
            except (KeyError, ValueError):
                pass

        def safe(factor, fn, why_):
            try:
                return fn()
            except (fr.LeakageError, Exception) as exc:  # noqa: BLE001
                return missing(factor, "live", why_ if isinstance(exc, fr.LeakageError) else type(exc).__name__, now)
        evid = {}
        evid[5] = safe(5, lambda: ad.f5_price_technical(cand, spy, plan, now, react, sha_c), "OUT_OF_WINDOW") if cand and spy else missing(5, "live.ohlc", why("NO_FRESH_PRICE_EVIDENCE", ("ohlc", sym)) if not cand else why("NO_FRESH_MARKET_EVIDENCE", ("ohlc", "SPY")), now)
        evid[3] = safe(3, lambda: ad.f3_reaction_history(hist, cand, ev["event_date"], plan, now, sha_h), "OUT_OF_WINDOW") if hist and cand else missing(3, "live.earnings+ohlc", why("NO_FRESH_HISTORY_OR_PRICE", ("earnings", sym), ("ohlc", sym)), now)
        evid[8] = safe(8, lambda: ad.f8_sector_market(spy, sec, plan, now, sha_s), "OUT_OF_WINDOW") if spy else missing(8, "live.ohlc:SPY", why("NO_FRESH_MARKET_EVIDENCE", ("ohlc", "SPY")), now)
        f7_ok = bool(ctx and ctx_rec and plan["window_start_utc"] <= datetime.fromisoformat(ctx_rec["retrieved_at"]) <= plan["cutoff_utc"])
        evid[7] = safe(7, lambda: ad.f7_volatility_context({"data": ctx}, ivr, plan, now, sha_x), "OUT_OF_WINDOW") if f7_ok else missing(7, "uw.volatility.context", "NOT_CAPTURED_LIVE_IN_WINDOW", now)
        preds = {v: sc.score(pre, sha, evid, symbol=sym, event_date=ev["event_date"], event_version=1, kind=ev["kind"], timing_status=ev["timing_status"], plan=plan, capture_ts=now, variant=v) for v in ("continuation", "contrarian")}
        blocked = []
        newest_cal = ev["calendar_observed_at"]
        if newest_cal is not None and now - newest_cal > timedelta(hours=cfg.calendar_interval_h):
            blocked.append("STALE_CALENDAR")
            row["calendar"]["status_now"] = "STALE"
            row["calendar"]["eligible_for_shadow"] = False
        if not ev["eligible_for_shadow"]:
            blocked += ["CALENDAR_" + (ev["status"] if ev["status"] not in cc.ELIGIBLE else "NOT_ELIGIBLE")] + ev["no_trade_reasons"]
        if any(x.missing_reason in ("STALE_AGE",) for x in evid.values() if x is not None):
            blocked.append("STALE_EVIDENCE")
        if not plan["tradable"]:
            blocked.append("NOT_TRADABLE")                                  # an early-close Friday: the deadline passes before the variant cutoff; the frozen scorer would say the same
        row["engine"] = {}
        for v, p in preds.items():
            sig, codes = p["signal"], list(p["reason_codes"])
            if blocked and sig in ("BUY", "SELL"):
                sig, codes = "NO_TRADE", codes + ["BLOCKED_BY_GATE"]
            row["engine"][v] = {"signal": sig, "bull": p["bull_points"], "bear": p["bear_points"], "available": p["weight_available"], "max": 40, "reason_codes": codes, "score_before_gate": p["signal"]}
        miss = [{"factor": "F%d" % f, "name": FACTOR_NAMES[f], "reason": x.missing_reason or x.status} for f, x in sorted(evid.items()) if not x.usable]
        if evid[8].usable and evid[8].extracted.get("sector_rel20") is None:
            miss.append({"factor": "F8", "name": "Sector part of F8", "reason": "NO_SECTOR_MAPPING_OR_ETF_EVIDENCE", "partial": True})
        if any(x["mode"] == "SIMULATION" for x in used):
            row["evidence_mode"] = "SIMULATION"
        row.update(available_points=preds["continuation"]["weight_available"], missing_factors=miss, blocked_by=sorted(set(blocked)), evidence=used,
                   adv20=(getattr(evid[5], "extracted", None) or {}).get("adv20") if evid[5].usable else None, subscores={v: preds[v]["explanation"]["subscores"] for v in preds}, capture_ts=now.isoformat(),
                   plan={"assumed_release_et": plan["assumed_release_et"].isoformat(), "window_start_utc": plan["window_start_utc"].isoformat(), "cutoff_utc": plan["cutoff_utc"].isoformat(), "basis": plan["basis"],
                         "variant": VARIANT, "base_session": plan["base_session"].isoformat(), "reaction_session": plan["reaction_session"].isoformat(), "frozen_cutoff_utc": plan["frozen_cutoff_utc"].isoformat(),
                         "tradable": plan["tradable"], "early_close_base": plan["early_close_base"]})
        rows.append(row)
    return rows


# ------------------------------------------------------------------ the variant ledger (own folder, own chain)
def publish(rows: list, now, out_dir, *, scan_id: str, mode: str, scan_ok: bool, run: Optional[dict] = None, limits: Optional[dict] = None, requests_today: Optional[int] = None, unfunded: int = 0) -> dict:
    """Same rules as the frozen publisher (window must be OPEN at publication, look-ahead refused, unchanged evidence not re-published, WINDOW_MISSED noted once) with the variant fields added."""
    now = _dt(now)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    led = PUB.read_ledger(out / PUB.LEDGER)
    if not led["ok"]:
        raise PUB.LedgerError(led["problem"])
    recs = led["records"]
    prev, seq = (recs[-1]["record_sha256"] if recs else "0" * 64), len(recs)
    latest = {(r["symbol"], r["event_date"]): r for r in recs}
    seen_pred = {(r["symbol"], r["event_date"]) for r in recs if r["record_type"] == "PREDICTION"}
    seen_missed = {(r["symbol"], r["event_date"]) for r in recs if r["record_type"] == "WINDOW_MISSED"}
    c, skipped = Counter(), Counter()
    for r in rows:
        cl = E20.classify(r, now)
        p = r["plan"]
        key = (cl["symbol"], cl["event_date"])
        extra = {"variant": VARIANT, "monday_earnings_date": cl["event_date"], "friday_entry_deadline_utc": p["cutoff_utc"], "friday_entry_deadline_et": _dt(p["cutoff_utc"]).astimezone(ET).strftime("%Y-%m-%d %H:%M %Z"),
                 "base_session": p["base_session"], "reaction_session": p["reaction_session"], "window_start_utc": p["window_start_utc"], "frozen_cutoff_utc": p["frozen_cutoff_utc"]}
        if cl["window"] == "NOT_YET_OPEN":
            c["upcoming"] += 1
            continue
        if cl["window"] == "CLOSED":
            c["closed"] += 1
            cutoff = _dt(cl["window_cutoff_utc"])
            if mode == "LIVE" and key not in seen_pred and key not in seen_missed and now - cutoff <= PUB.MISSED_LOOKBACK:
                rec = PUB._append(out / PUB.LEDGER, dict({"schema": SCHEMA, "record_type": "WINDOW_MISSED", "symbol": cl["symbol"], "company": cl.get("company"), "event_date": cl["event_date"], "session": cl["session"],
                                                          "window_cutoff_utc": cutoff.isoformat(), "noted_at": now.isoformat(), "predicted": False, "scan_id": scan_id, "model_version": cl["model_version"],
                                                          "reason": "NO_PREDICTION_BEFORE_THE_FRIDAY_DEADLINE", "evidence_items_at_close": len(PUB._evidence(r))}, **extra), prev, seq + 1)
                prev, seq = rec["record_sha256"], seq + 1
                seen_missed.add(key)
                c["window_missed_noted"] += 1
            continue
        c["open"] += 1
        if mode != "LIVE":
            continue
        ev = PUB._evidence(r)
        if any(_dt(i["retrieved_at"]) > now + PUB.FUTURE_TOLERANCE for i in ev):
            skipped["evidence stamped after the scan instant (look-ahead refused)"] += 1
            continue
        if now > _dt(cl["window_cutoff_utc"]):
            skipped["Friday deadline passed at publication time"] += 1                  # requirement 17: never a signal after the Friday execution cutoff
            continue
        digest = hashlib.sha256(PUB._canon([[i["kind"], i["symbol"], i["retrieved_at"], i["sha256"]] for i in ev] + [cl["result"]])).hexdigest()
        last = latest.get(key)
        if last is not None and last["record_type"] == "PREDICTION" and last["evidence_digest"] == digest:
            c["unchanged"] += 1
            continue
        rec = PUB._append(out / PUB.LEDGER, dict({"schema": SCHEMA, "record_type": "PREDICTION", "symbol": cl["symbol"], "company": cl.get("company"), "event_date": cl["event_date"], "session": cl["session"],
                                                  "result": PUB.CATEGORY[cl["result"]], "result_text": cl["result"], "reason": cl["reason"], "reason_codes": cl["reason_codes"], "bull": cl["bull"], "bear": cl["bear"],
                                                  "coverage_points": cl["coverage_points"], "coverage_max": cl["coverage_max"], "liquidity": cl["liquidity"], "adv20_usd": cl.get("adv20_usd"),
                                                  "contrarian_signal": cl.get("contrarian_signal"), "missing_factors": cl["missing_factors"], "window": "OPEN", "window_cutoff_utc": cl["window_cutoff_utc"],
                                                  "evidence": ev, "evidence_latest": ev[-1]["retrieved_at"] if ev else None, "evidence_digest": digest, "generated_at": now.isoformat(),
                                                  "generated_at_et": now.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S %Z"), "scan_id": scan_id, "model_version": cl["model_version"], "mode": mode}, **extra), prev, seq + 1)
        prev, seq = rec["record_sha256"], seq + 1
        latest[key] = rec
        c["published"] += 1
        c["published_" + rec["result"]] += 1
    old = {}
    sp = out / PUB.STATUS
    if sp.exists():
        try:
            old = json.loads(sp.read_text(encoding="utf-8"))
        except ValueError:
            old = {}
    limits = limits or {}
    status = {"schema": STATUS_SCHEMA, "variant": VARIANT, "written_at": now.isoformat(), "written_at_et": now.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S %Z"), "scan_id": scan_id, "mode": mode, "scan_ok": bool(scan_ok),
              "last_successful_scan_at": now.isoformat() if (scan_ok and mode == "LIVE") else old.get("last_successful_scan_at"), "run": run or {},
              "limits": {"run_limit": limits.get("run_limit"), "daily_limit": limits.get("daily_limit"), "concurrency": 1},
              "requests": {"used_today": requests_today, "remaining_today": (max(0, limits["daily_limit"] - requests_today) if requests_today is not None and limits.get("daily_limit") is not None else None), "ny_date": now.astimezone(ET).date().isoformat()},
              "events": {"open_scanned": c["open"], "published_new": c["published"], "unchanged": c["unchanged"], "upcoming": c["upcoming"], "window_closed": c["closed"], "window_missed_noted": c["window_missed_noted"],
                         "unfunded_this_pass": unfunded, "skipped": dict(skipped), "published_by_result": {k[10:]: v for k, v in c.items() if k.startswith("published_") and k != "published"}},
              "ledger": {"records": seq, "head_sha256": prev}}
    PUB._atomic_json(sp, status)
    return status


# ------------------------------------------------------------------ outcomes and accuracy (existing methodology, separate files)
def latest_predictions(out_dir) -> list:
    """Latest PREDICTION per event from the variant ledger (chain verified)."""
    led = PUB.read_ledger(Path(out_dir) / PUB.LEDGER)
    if not led["ok"]:
        raise PUB.LedgerError(led["problem"])
    last = {}
    for r in led["records"]:
        if r["record_type"] == "PREDICTION":
            last[(r["symbol"], r["event_date"])] = r
    return list(last.values())


def outcomes_due(out_dir, now: datetime) -> dict:
    """Graded / due / waiting per predicted event. Never fetches."""
    preds = latest_predictions(out_dir)
    track = Path(out_dir) / OUTCOMES_DIR
    evs = [{"symbol": p["symbol"], "earnings_date": p["event_date"], "kind": p["session"]} for p in preds]
    return {"predictions": preds, "readiness": O.readiness(evs, track, now), "plan": O.plan_fetches(evs, track, now, max_requests=10 ** 6)}


def grade(svc, out_dir, now: datetime, *, live: bool, budget: int) -> dict:
    """For every predicted event whose reaction session (Monday) is closed plus the 30-minute safety delay and that has no outcome yet: LIVE fetches the daily bars (one governed request per stock, inside the
    run ceiling and the daily limit), computes the outcome with smart_move_outcomes.compute (reaction close / base close - 1, split quarantine), records it append-only, then rewrites accuracy.json with the frozen grader."""
    out = Path(out_dir)
    track = out / OUTCOMES_DIR
    d = outcomes_due(out, now)
    fetch, done, failed = d["plan"]["fetch"][:budget] if live else [], [], []
    stamp = now.strftime("%Y%m%dT%H%M%S")
    by_sym = {p["symbol"]: p for p in d["predictions"]}
    for sym in fetch:
        if svc.kill_engaged:
            break
        rid = "%s-%s-%s-outcome" % ("ohlc", sym, stamp)
        body, sha, at = svc._call("ohlc", sym, {"timeframe": "3Y"}, rid)
        if body is None:
            failed.append(sym)
            continue
        svc._ingest_evidence("ohlc", sym, {"timeframe": "3Y"}, body, sha, at, rid)
        done.append(sym)
    recorded = []
    for p in d["predictions"]:
        key = "%s|%s" % (p["symbol"], p["event_date"])
        if d["readiness"]["by_event"].get(key, {}).get("state") != "DUE":
            continue
        rec = svc._latest("ohlc", p["symbol"], {"timeframe": "3Y"})
        rows, _ = L.LiveScorer(svc)._rows_of(rec) if rec else (None, None)
        if not rows:
            continue
        oc = O.compute(p["symbol"], p["event_date"], p["session"], rows, _dt(rec["retrieved_at"]), mode="LIVE", source="store")
        oc["variant"] = VARIANT
        res = O.record(track, oc)
        recorded.append({"symbol": p["symbol"], "event_date": p["event_date"], "status": oc["status"], "move_pct": oc.get("move_pct"), "gap_pct": oc.get("gap_pct"), "record": res})
    acc = accuracy(out, now)
    PUB._atomic_json(out / ACCURACY, acc)
    return {"fetched": done, "fetch_failed": failed, "fetch_deferred": d["plan"]["fetch"][len(fetch):] if live else d["plan"]["fetch"], "not_due_yet": d["plan"]["not_due_yet"], "recorded": recorded, "accuracy": acc["summary"]}


def accuracy(out_dir, now: datetime) -> dict:
    """SEPARATE accuracy for the variant only: the frozen grader (flat band 0.5 %, strict and direction-only hit rates, Wilson intervals, cost-adjusted net return) plus BUY / SELL accuracy, average move,
    average weekend gap (Monday open vs Friday close) and the hypothetical per-trade performance. The claim text comes from the grader: no accuracy claim below 100 decided calls over 8 earnings weeks."""
    out = Path(out_dir)
    preds = latest_predictions(out)
    track = out / OUTCOMES_DIR
    outcomes = {(o["symbol"], o["earnings_date"]): o for o in O.latest(track)}
    gp = []
    for p in preds:
        if p["result"] in ("BUY", "SELL", "NO_TRADE"):
            gp.append({"symbol": p["symbol"], "earnings_date": date.fromisoformat(p["event_date"]), "event_version": 1, "variant": VARIANT, "prediction_version": p["seq"], "signal": p["result"], "in_window": True,
                       "bull_points": p["bull"], "bear_points": p["bear"], "adv20": p.get("adv20_usd")})
    rep = G.grade(gp, {(k[0], date.fromisoformat(k[1])): v for k, v in outcomes.items()}, {}, audit_passed=False)
    v = rep["variants"].get(VARIANT) or {}
    rows, per_signal = [], {"BUY": Counter(), "SELL": Counter()}
    moves, gaps, abs_gaps, nets = [], [], [], []
    for p in preds:
        o = outcomes.get((p["symbol"], p["event_date"]))
        graded = bool(o and o.get("status") == "GRADED" and o.get("move_pct") is not None)
        cls = G.outcome_class(o["move_pct"]) if graded else None
        hit = None
        if graded and p["result"] in ("BUY", "SELL"):
            hit = (p["result"] == "BUY" and cls == "UP") or (p["result"] == "SELL" and cls == "DOWN")
            per_signal[p["result"]]["hit" if hit else "miss"] += 1
            nets.append(G.net_return(1 if p["result"] == "BUY" else -1, o["move_pct"], p.get("adv20_usd"), 1.0))
        if graded:
            moves.append(o["move_pct"])
            if o.get("gap_pct") is not None:
                gaps.append(o["gap_pct"])
                abs_gaps.append(abs(o["gap_pct"]))
        rows.append({"symbol": p["symbol"], "company": p.get("company"), "monday_earnings_date": p["event_date"], "signal": p["result"], "bull": p["bull"], "bear": p["bear"], "coverage": "%s/%s" % (p.get("coverage_points"), p.get("coverage_max")),
                     "predicted_at": p["generated_at"], "friday_entry_deadline_et": p.get("friday_entry_deadline_et"), "outcome_status": (o or {}).get("status") or "NO_OUTCOME_YET", "move_pct": o.get("move_pct") if o else None,
                     "gap_pct": o.get("gap_pct") if o else None, "base_close": (o or {}).get("base_close"), "reaction_close": (o or {}).get("reaction_close"), "outcome_class": cls, "hit": hit})

    def rate(c):
        n = c["hit"] + c["miss"]
        return {"n": n, "hits": c["hit"], "rate": round(c["hit"] / n, 3) if n else None, "wilson95": G.wilson(c["hit"], n) if n else None}
    summ = {"variant": VARIANT, "generated_utc": now.isoformat(), "total_predictions": len(preds), "by_signal": dict(Counter(p["result"] for p in preds)), "graded_events": v.get("graded_events", 0), "decided_calls": v.get("decided", 0),
            "buy_accuracy": rate(per_signal["BUY"]), "sell_accuracy": rate(per_signal["SELL"]), "overall_directional_accuracy": {"strict_hit_rate": v.get("strict_hit_rate"), "wilson95": v.get("strict_hit_ci95"), "direction_only_hit_rate": v.get("direction_only_hit_rate"), "direction_only_n": v.get("direction_only_n")},
            "average_post_earnings_move_pct": round(statistics.mean(moves), 3) if moves else None, "average_abs_move_pct": round(statistics.mean(abs(m) for m in moves), 3) if moves else None,
            "weekend_gap_risk": {"n": len(gaps), "mean_gap_pct": round(statistics.mean(gaps), 3) if gaps else None, "mean_abs_gap_pct": round(statistics.mean(abs_gaps), 3) if abs_gaps else None, "max_abs_gap_pct": round(max(abs_gaps), 3) if abs_gaps else None,
                                  "note": "gap = Monday open vs Friday close; the Friday close order is exposed to the whole weekend"},
            "hypothetical_trading_performance": {"n": len(nets), "mean_net_pct_per_trade_cost_x1": round(statistics.mean(nets), 3) if nets else None, "net_by_cost_multiplier": v.get("net_mean_pct_cost_multiplier"), "note": "frozen grader convention: entry Friday close, exit Monday close, side costs by liquidity tier; no trade is placed"},
            "confusion": v.get("confusion"), "earnings_weeks": v.get("earnings_weeks", 0), "minimum_sample_met": v.get("minimum_sample_met", False), "claim_allowed": rep["claim_allowed"], "claim_text": rep["claim_text"],
            "separate_from_frozen_model": True}
    return {"schema": "smart_move.monday_bmo_friday_accuracy.v1", "summary": summ, "rows": sorted(rows, key=lambda r: (r["monday_earnings_date"], r["symbol"])), "grader_audit": rep["audit"]}


# ------------------------------------------------------------------ runner profile
def profile(a, svc, now: datetime, live: bool) -> int:
    """One pass of the variant. Dry run: list the applicable events, plan, write status; zero requests. Live: (1) refresh the vendor calendar for the applicable release dates when it is older than the service's
    calendar limit, (2) collect the missing evidence inside the ceiling, (3) score with the frozen scorer and publish to the variant ledger (only while the Friday window is open), (4) grade due outcomes with
    the budget left. The kill file is released only for the run and always re-engaged."""
    d = Path(a.state_dir)
    d.mkdir(parents=True, exist_ok=True)
    out = Path(a.publish_dir) / SUBDIR if a.publish_dir else d / SUBDIR
    with_f7 = bool(getattr(a, "with_f7", False))
    evs = events(svc, now)
    upcoming = events(svc, now, horizon_days=14)
    tasks = plan_evidence(svc, evs, now, with_f7=with_f7)
    plan = fund(tasks, evs, budget=a.ceiling, with_f7=with_f7)
    due = outcomes_due(out, now) if (out / PUB.LEDGER).exists() else {"plan": {"fetch": [], "not_due_yet": []}, "readiness": {"counts": {}}}
    summary = {"mode": "LIVE" if live else "DRY_RUN", "profile": "monday_bmo_friday", "variant": VARIANT, "as_of": now.isoformat(), "ceiling": a.ceiling,
               "events_window_open": [(e["symbol"], str(e["event_date"]), e["plan"]["cutoff_utc"].isoformat()) for e in evs],
               "events_upcoming_14d": [(e["symbol"], str(e["event_date"]), e["plan"]["window_start_utc"].isoformat(), e["plan"]["cutoff_utc"].isoformat()) for e in upcoming if e not in evs],
               "attempts_planned_for_evidence": plan["attempts_planned"], "spare": plan["spare"], "stocks_fully_funded": plan["stocks_fully_funded"], "stocks_already_fresh": plan["already_fresh_stocks"],
               "unfunded": [(t["kind"], t["symbol"]) for t in plan["unfunded"]], "tasks": [(t["kind"], t["symbol"], t["why"]) for t in plan["funded"]],
               "outcomes_due": due["plan"]["fetch"], "outcomes_not_due_yet": due["plan"]["not_due_yet"], "provider_requests": 0}
    (d / "monday_bmo_plan.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    limits = {"run_limit": a.ceiling, "daily_limit": a.daily_limit or a.authorize_requests or None}
    if not live:
        rows = score(svc, evs, now)
        publish(rows, now, out, scan_id=d.name, mode="DRY_RUN", scan_ok=True, run={"started_at": now.isoformat(), "requests_attempted": 0, "planned": plan["attempts_planned"]}, limits=limits, unfunded=len(plan["unfunded"]))
        if (out / PUB.LEDGER).exists():
            PUB._atomic_json(out / ACCURACY, accuracy(out, now))
        print(json.dumps({k: v for k, v in summary.items() if k != "tasks"}, indent=1, default=str))
        return 0
    svc.store.kill_file.unlink()
    svc.gov.authorized_daily, svc.gov.enabled = (a.daily_limit or a.authorize_requests), True
    try:
        att0 = E20.attempts_of(svc)
        stopped = lambda: bool(svc.gov.stopped_until and svc.clock.now() < svc.gov.stopped_until)  # noqa: E731
        cal = {"requests": 0, "dates": []}
        stale = stale_slots(svc, now)
        if stale and not stopped():
            budget = [min(2 * len(stale) + 2, a.ceiling)]
            cal["requests"] = svc.refresh_calendar(budget, dates=[(dt, "premarket") for dt in stale])
            cal["dates"] = [dt.isoformat() for dt in stale]
            evs = events(svc, svc.clock.now())                                                    # the refreshed calendar decides eligibility
        used = E20.attempts_of(svc) - att0
        plan = fund(plan_evidence(svc, evs, svc.clock.now(), with_f7=with_f7), evs, budget=a.ceiling - used, with_f7=with_f7)
        res = E20.run(svc, plan["funded"], now=svc.clock.now(), ceiling=a.ceiling - used, state_dir=d, stopped=stopped, max_consecutive_failures=3, abort_on_stale_bars=True)
        t = svc.clock.now()
        rows = score(svc, evs, t)
        rep = [E20.classify(r, t) for r in rows]
        (d / "monday_bmo_report.json").write_text(json.dumps({"as_of": t.isoformat(), "rows": rep}, indent=1, default=str), encoding="utf-8")
        attempts = E20.attempts_of(svc) - att0
        try:
            st = publish(rows, t, out, scan_id=d.name, mode="LIVE", scan_ok=(res["halted"] is None and res["failed"] == 0), run={"started_at": now.isoformat(), "finished_at": t.isoformat(), "requests_attempted": attempts, "ok": res["ok"], "failed": res["failed"], "halted": res["halted"], "calendar": cal},
                         limits=limits, requests_today=PUB._requests_today(svc.store, t), unfunded=len(plan["unfunded"]))
            left = max(0, a.ceiling - (E20.attempts_of(svc) - att0))
            gr = grade(svc, out, svc.clock.now(), live=True, budget=left) if (out / PUB.LEDGER).exists() else None
        except PUB.LedgerError as e:
            print("PUBLISH BLOCKED: the variant prediction ledger failed its integrity check:", e)
            return 8
        summary.update(calendar_refresh=cal, requests_attempted=E20.attempts_of(svc) - att0, halted=res["halted"], published=st["events"], outcomes=({k: v for k, v in gr.items() if k != "accuracy"} if gr else None), accuracy=(gr or {}).get("accuracy"))
        (d / "monday_bmo_plan.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
        print(json.dumps({"requests_attempted": summary["requests_attempted"], "ok": res["ok"], "failed": res["failed"], "halted": res["halted"], "published": st["events"], "counts": dict(Counter(r["result"] for r in rep)), "outcomes": summary["outcomes"]}, indent=1, default=str))
        return 0
    finally:
        svc.engage_kill_switch("monday_bmo_friday run finished: re-engaged")
