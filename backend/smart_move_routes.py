"""Smart Move dashboard API: READ-ONLY, saved evidence only.

Serves three clearly separated datasets from the evidence pack in docs/smart_move/app_data (built from the saved P1/P2 evidence and the existing replay/synthetic results):

  CURRENT_CALENDAR   the calendar rows the three vendors returned on 2026-10-30 09:01-09:03 ET, judged by the vendor-level calendar rules and run through the frozen 40-point
                     engine. No price evidence is saved for these names, so almost every factor is missing and the honest output is NO TRADE with reasons. NOT a prediction.
  HISTORICAL_REPLAY  development-period events replayed from saved candles by the same engine. The primary experiment uses the pre-registered provider event history and no F7;
                     the SEC-derived F3 experiments and the F7 sensitivity are separate, labelled exploratory/unverified. NOT live predictions.
  SYNTHETIC          invented examples that exercise the engine's mechanics.

Nothing is fabricated: every signal shown is produced by smvshadow.scorer (the frozen engine, prereg hash checked) or read from a stored engine result; missing factors stay
missing. No endpoint writes anything, makes a provider request or touches a database. Mount it with `app.include_router(router)`.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

import threading

from fastapi import APIRouter, Body, HTTPException, Query

ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT / "docs" / "smart_move" / "shadow_impl"
DATA_DIR = Path(os.getenv("SMART_MOVE_DATA_DIR") or ROOT / "docs" / "smart_move" / "app_data")
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

router = APIRouter(prefix="/api/smart-move", tags=["smart-move"])

FACTOR_NAMES = {"F3": "Earnings-reaction history", "F5": "Price / technical", "F7": "Volatility (skew)", "F8": "Sector / market"}
SECTOR_ETF = {"Technology": "XLK", "Financial Services": "XLF", "Healthcare": "XLV", "Consumer Cyclical": "XLY", "Consumer Defensive": "XLP", "Energy": "XLE", "Industrials": "XLI",
              "Basic Materials": "XLB", "Utilities": "XLU", "Real Estate": "XLRE", "Communication Services": "XLC"}
EXPERIMENTS = {
    "primary": {"key": "A_provider_history | valid_evidence_only", "label": "Primary (pre-registered): provider event history, valid evidence only, F7 absent", "primary": True, "exploratory": False, "f7": False},
    "sec_strict": {"key": "B_strict_BMO_certain_plus_AMC_reconciled | valid_evidence_only", "label": "EXPLORATORY SEC-derived F3 (strict: BMO-certain + AMC-reconciled). Not the pre-registered source.", "primary": False, "exploratory": True, "f7": False},
    "sec_moderate": {"key": "B_moderate_adds_AMC_unconfirmed | valid_evidence_only", "label": "EXPLORATORY SEC-derived F3 (moderate: adds AMC-unconfirmed). Not the pre-registered source.", "primary": False, "exploratory": True, "f7": False},
    "primary_f7": {"key": "A_provider_history | F7_sensitivity_on_P2_events", "label": "Sensitivity: primary + F7 on the 40 P2 events. F7 is dated but point-in-time UNVERIFIED.", "primary": False, "exploratory": True, "f7": True},
    "sec_strict_f7": {"key": "B_strict_BMO_certain_plus_AMC_reconciled | F7_sensitivity_on_P2_events", "label": "Sensitivity: SEC-strict + F7 (40 P2 events). Exploratory; F7 UNVERIFIED.", "primary": False, "exploratory": True, "f7": True},
    "sec_moderate_f7": {"key": "B_moderate_adds_AMC_unconfirmed | F7_sensitivity_on_P2_events", "label": "Sensitivity: SEC-moderate + F7 (40 P2 events). Exploratory; F7 UNVERIFIED.", "primary": False, "exploratory": True, "f7": True},
}
DATASET_LABELS = {
    "LIVE_CURRENT": "Current evidence from the live store (the refresh service). If this server runs the SIMULATED provider every row says so; if refresh is disabled the page shows the exact blockers instead of signals.",
    "CURRENT_CALENDAR": "Current calendar observation (saved). Not a prediction: no price evidence is saved for these names.",
    "HISTORICAL_REPLAY": "Historical replay of the frozen engine on saved development-period data. Not a live prediction; the event date and label are as stored after the fact.",
    "SYNTHETIC": "Synthetic example (invented data) that exercises the engine mechanics. Not real.",
}
LIMITATIONS = [
    "Every signal here comes from the frozen 40-point engine run on SAVED evidence; nothing is a live trading signal.",
    "Current-calendar rows have no saved price history, so F3, F5 and F7 are missing and the engine cannot reach BUY or SELL for them.",
    "The replay uses the event date and BMO/AMC label as stored after the fact (no 48 h-before calendar snapshot was ever saved).",
    "Vendor agreement (VENDOR_CONFIRMED) is not direct company confirmation; vendor independence is unproven.",
    "Sector comes from the saved Unusual Whales row where present; names seen only by Finviz or Finnhub have no sector, so F8 is partial for them.",
    "F7 (volatility skew) is dated but not verified point-in-time and is excluded from the primary model.",
    "No outcome is used or shown; no accuracy claim is made.",
]


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _iso(x) -> Optional[str]:
    return x.isoformat() if hasattr(x, "isoformat") else x


@lru_cache(maxsize=1)
def pack() -> dict:
    """Loads the evidence pack and verifies every file against meta.json; a mismatch makes the whole API refuse (status PACK_INTEGRITY_ERROR)."""
    meta_path = DATA_DIR / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError("evidence pack not found: " + str(DATA_DIR))
    meta = json.loads(meta_path.read_bytes())
    out = {"meta": meta}
    for name, want in meta["files_sha256"].items():
        raw = (DATA_DIR / name).read_bytes()
        if _sha(raw) != want:
            raise ValueError("evidence pack file %s does not match its recorded SHA-256" % name)
        out[name.rsplit(".", 1)[0]] = json.loads(raw)
    return out


def _engine():
    from smvshadow import adapters as ad, calendar_confirm as cc, calendar_validation as cv, cutoff as co, scorer as sc
    from smvshadow.freshness import missing
    return ad, cc, cv, co, sc, missing


@lru_cache(maxsize=1)
def prereg():
    _, _, _, _, sc, _ = _engine()
    return sc.load_prereg()


def _calobs(rows):
    ad, cc, cv, co, sc, missing = _engine()
    out = {}
    for r in rows:
        at = datetime.fromisoformat(r["observed_at"])
        o = cc.CalObs(r["source"], r["symbol"], date.fromisoformat(r["date"]), r["label"], r["clock"], r["vendor_flag"], r["raw_sha256"], at)
        out.setdefault(r["symbol"], []).append((o, r))
    return out


def _missing_list(ev: dict) -> list:
    out = []
    for f, e in sorted(ev.items()):
        if not e.usable:
            out.append({"factor": "F%d" % f, "name": FACTOR_NAMES.get("F%d" % f, "F%d" % f), "reason": e.missing_reason or e.status})
        elif f == 8 and e.extracted.get("sector_rel20") is None:
            out.append({"factor": "F8", "name": "Sector part of F8", "reason": "NO_SECTOR_MAPPING", "partial": True})
    return out


def _variant(p: dict) -> dict:
    return {"signal": p["signal"], "bull": p["bull_points"], "bear": p["bear_points"], "available": p["weight_available"], "max": 40, "reason_codes": p["reason_codes"]}


@lru_cache(maxsize=1)
def current_rows() -> list:
    """One row per symbol seen in the saved vendor responses, judged at the time of observation and scored by the frozen engine with only the evidence that exists."""
    ad, cc, cv, co, sc, missing = _engine()
    pk = pack()
    pre, sha = prereg()
    spy = pk["spy_regular"]["rows"]
    etf = {k: v["rows"] for k, v in pk["sector_etfs"].items()}
    rows = []
    for sym, pairs in sorted(_calobs(pk["calendar_observations"]["observations"]).items()):
        obs = [o for o, _ in pairs]
        raw = [r for _, r in pairs]
        at = max(o.observed_at for o in obs)
        today = at.astimezone(co.ET).date()
        ev = cc.evaluate(sym, obs, at, today)
        uw = next((r for r in raw if r["source"] == "UW_CALENDAR"), {})
        company = uw.get("company")
        sector = uw.get("sector")
        etf_sym = SECTOR_ETF.get(sector)
        row = {"dataset": "CURRENT_CALENDAR", "symbol": sym, "company": company, "sector": sector,
               "sector_note": ("from the saved Unusual Whales row; mapped to " + etf_sym) if etf_sym else ("saved sector %r has no ETF mapping" % sector if sector else "no sector saved (not seen by Unusual Whales): F8 sector part unavailable"),
               "earnings_date": str(ev["event_date"]) if ev["event_date"] else None, "kind": ev["kind"],
               "calendar": {"status_at_observation": ev["status"], "reasons": ev["reasons"], "no_trade_reasons": ev["no_trade_reasons"], "eligible_for_shadow": ev["eligible_for_shadow"],
                            "vendors": ev["vendors"], "dependency": ev["dependency"], "stale_vendor_rows": ev.get("stale_vendor_rows", []), "calendar_observed_at": _iso(ev["calendar_observed_at"]),
                            "uw_flag": uw.get("vendor_flag"), "note": "vendor agreement, not direct company confirmation" if ev["status"] == cc.VENDOR_CONFIRMED else None},
               "observations": [{k: r[k] for k in ("source", "date", "label", "clock", "vendor_flag", "observed_at", "raw_sha256", "request_id")} for r in sorted(raw, key=lambda r: (r["source"], r["date"]))]}
        if ev["event_date"] is None:
            for v in ("continuation", "contrarian"):
                row.setdefault("engine", {})[v] = {"signal": "NO_TRADE", "bull": 0, "bear": 0, "available": 0, "max": 40, "reason_codes": ev["no_trade_reasons"] or ["NO_OBSERVATION"]}
            row["missing_factors"] = [{"factor": f, "name": n, "reason": "NO_EVENT_DATE"} for f, n in FACTOR_NAMES.items()]
            row["available_points"] = 0
            row["subscores"] = {}
            rows.append(row)
            continue
        plan = co.plan_event(ev["event_date"], ev["kind"])
        kind = ev["kind"]
        evid = {3: missing(3, "saved.earnings+ohlc", "NO_PRICE_EVIDENCE_SAVED", at), 5: missing(5, "saved.ohlc", "NO_PRICE_EVIDENCE_SAVED", at), 7: missing(7, "uw.volatility.context", "NOT_CAPTURED", at)}
        try:
            cap_rows = spy
            last = co.last_completed_session_at(at)
            spy_cut = [r for r in cap_rows if r["date"][:10] <= last.isoformat()]
            sec_cut = [r for r in etf.get(etf_sym, []) if r["date"][:10] <= last.isoformat()] if etf_sym else None
            evid[8] = ad.f8_sector_market(spy_cut, sec_cut, plan, at, pk["spy_regular"]["source_file_sha256"])
        except Exception as exc:  # noqa: BLE001 - e.g. the capture is already after this event's cutoff
            evid[8] = missing(8, "saved.ohlc:SPY", "OUT_OF_WINDOW" if "cutoff" in str(exc).lower() or "after" in str(exc).lower() else type(exc).__name__, at)
        preds = {v: sc.score(pre, sha, evid, symbol=sym, event_date=ev["event_date"], event_version=1, kind=kind, timing_status=ev["timing_status"], plan=plan, capture_ts=at, variant=v)
                 for v in ("continuation", "contrarian")}
        row["engine"] = {v: _variant(p) for v, p in preds.items()}
        row["available_points"] = preds["continuation"]["weight_available"]
        row["missing_factors"] = _missing_list(evid)
        row["subscores"] = {v: preds[v]["explanation"]["subscores"] for v in preds}
        row["plan"] = {"assumed_release_et": _iso(plan["assumed_release_et"]), "window_start_utc": _iso(plan["window_start_utc"]), "cutoff_utc": _iso(plan["cutoff_utc"]), "basis": plan["basis"]}
        rows.append(row)
    return rows


def _status_now(row: dict, now: datetime) -> dict:
    ad, cc, cv, co, sc, missing = _engine()
    pk = pack()
    sym = row["symbol"]
    obs = [o for o, _ in _calobs([r for r in pk["calendar_observations"]["observations"] if r["symbol"] == sym]).get(sym, [])]
    ev = cc.evaluate(sym, obs, now, now.astimezone(co.ET).date())
    newest = max((o.observed_at for o in obs), default=None)
    return {"status_now": ev["status"], "no_trade_reasons_now": ev["no_trade_reasons"], "evidence_age_hours": round((now - newest).total_seconds() / 3600, 1) if newest else None}


def _replay_rows(exp_id: str) -> list:
    spec = EXPERIMENTS[exp_id]
    pk = pack()
    e = pk["replay_dev"]["experiments"][spec["key"]]
    out = []
    for r in e["rows"]:
        miss = []
        for f, ev in sorted(r["evidence"].items()):
            if not ev["usable"]:
                miss.append({"factor": "F" + f, "name": FACTOR_NAMES.get("F" + f, "F" + f), "reason": ev["missing_reason"] or ev["status"]})
        if r["replayed"] and r["evidence"].get("8", {}).get("usable"):
            miss.append({"factor": "F8", "name": "Sector part of F8", "reason": "NO_SECTOR_MAPPING", "partial": True})
        rows = {"dataset": "HISTORICAL_REPLAY", "experiment": exp_id, "experiment_label": spec["label"], "primary": spec["primary"], "exploratory": spec["exploratory"], "f7_unverified": spec["f7"],
                "symbol": r["symbol"], "company": None, "sector": None, "sector_note": "no sector saved for this symbol (not in a saved calendar row)",
                "earnings_date": r["earnings_date"], "kind": r["kind"],
                "calendar": {"status_at_observation": "HINDSIGHT_NOT_VERIFIABLE", "reasons": ["no calendar snapshot from before the event was saved"], "no_trade_reasons": [], "eligible_for_shadow": False,
                             "vendors": [], "calendar_observed_at": None, "uw_flag": None, "note": "date and BMO/AMC label are as stored after the fact", "hindsight_flags": r["hindsight_flags"]},
                "observed_at": r.get("capture_ts_utc"), "last_session": r.get("last_session"),
                "engine": {v: {"signal": r["signal_" + v], "bull": r.get("bull_" + v, 0), "bear": r.get("bear_" + v, 0), "available": r["weight_available"], "max": 40, "reason_codes": r.get("codes_" + v, r["reason_codes"])}
                           for v in ("continuation", "contrarian")},
                "available_points": r["weight_available"], "missing_factors": miss, "evidence": r["evidence"], "replayed": r["replayed"], "observations": []}
        out.append(rows)
    return out


def _synthetic_rows() -> list:
    syn = pack()["synthetic"]
    rows = []
    for r in syn["rows"]:
        eng = {}
        for v in ("continuation", "contrarian"):
            x = r[v]
            eng[v] = {"signal": x["signal"], "bull": x.get("bull", 0), "bear": x.get("bear", 0), "available": x.get("available", 0), "max": 40, "reason_codes": x["reasons"]}
        rows.append({"dataset": "SYNTHETIC", "symbol": r["symbol"], "company": "(synthetic)", "sector": None, "sector_note": "synthetic", "earnings_date": "2026-11-05", "kind": "BMO",
                     "calendar": {"status_at_observation": r["calendar_status"], "reasons": [], "no_trade_reasons": r["calendar_no_trade_reasons"], "eligible_for_shadow": r["eligible"], "vendors": [], "calendar_observed_at": None, "uw_flag": None, "note": "synthetic"},
                     "observed_at": None, "engine": eng, "available_points": eng["continuation"]["available"], "missing_factors": [], "synthetic_inputs": r["synthetic_inputs"], "observations": []})
    return rows


def _filter(rows, *, date_from, date_to, signal, variant, status, missing, q, kind):
    def sig_ok(r):
        if not signal:
            return True
        vs = ("continuation", "contrarian") if variant in (None, "", "any") else (variant,)
        return any(r["engine"][v]["signal"] == signal for v in vs)
    out = []
    for r in rows:
        d = r.get("earnings_date")
        if date_from and (not d or d < date_from):
            continue
        if date_to and (not d or d > date_to):
            continue
        if not sig_ok(r):
            continue
        if status and r["calendar"].get("status_now", r["calendar"]["status_at_observation"]) != status and r["calendar"]["status_at_observation"] != status:
            continue
        if missing and not any(m["factor"] == missing for m in r["missing_factors"]):
            continue
        if kind and r["kind"] != kind:
            continue
        if q and q.upper() not in r["symbol"].upper() and q.upper() not in (r.get("company") or "").upper():
            continue
        out.append(r)
    return out


def _summary(rows) -> dict:
    s = {"events": len(rows)}
    for v in ("continuation", "contrarian"):
        c = {"BUY": 0, "SELL": 0, "NO_TRADE": 0}
        for r in rows:
            c[r["engine"][v]["signal"]] += 1
        s[v] = c
    cov = {}
    for r in rows:
        cov[str(r["available_points"])] = cov.get(str(r["available_points"]), 0) + 1
    s["available_points_distribution"] = dict(sorted(cov.items(), key=lambda kv: int(kv[0])))
    miss = {}
    for r in rows:
        for m in r["missing_factors"]:
            miss[m["factor"]] = miss.get(m["factor"], 0) + 1
    s["missing_factor_counts"] = miss
    codes = {}
    for r in rows:
        for v in ("continuation", "contrarian"):
            for c in r["engine"][v]["reason_codes"]:
                codes[c] = codes.get(c, 0) + 1
    s["reason_code_counts_per_variant"] = dict(sorted(codes.items(), key=lambda kv: -kv[1]))
    return s


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _guard():
    try:
        return pack()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail={"status": "PACK_UNAVAILABLE", "message": "Smart Move evidence pack could not be loaded: %s" % type(exc).__name__})


def _page(rows, limit, offset):
    return rows[offset: offset + limit]


# ----------------------------------------------------------------------------- endpoints
@router.get("/meta")
def meta():
    pk = _guard()
    pre, sha = prereg()
    cur = current_rows()
    src = pk["calendar_observations"]["sources"]
    now = _now()
    first = min(s["retrieved_at_utc"] for s in src)
    last = max(s["retrieved_at_utc"] for s in src)
    age = round((now - datetime.fromisoformat(last)).total_seconds() / 3600, 1)
    return {"status": "OK", "source": "SAVED_EVIDENCE_PACK", "read_only": True, "live_predictions": False, "disclaimer": "Saved-evidence dashboard. No live signals. No accuracy claim.",
            "engine": {"name": "frozen 40-point shadow engine", "prereg_file": "docs/smart_move/shadow/prereg_score40_v1.json", "prereg_sha256": sha, "scored_factors": FACTOR_NAMES, "max_points": 40,
                       "decision": "BUY if bull >= 14, bear <= 4 and bull - bear >= 10; SELL is the mirror; coverage >= 0.60 of 40; otherwise NO TRADE"},
            "datasets": {k: {"label": v} for k, v in DATASET_LABELS.items()}, "experiments": {k: {kk: vv for kk, vv in v.items() if kk != "key"} for k, v in EXPERIMENTS.items()},
            "current_calendar": {"requests": src, "first_retrieved_utc": first, "last_retrieved_utc": last, "evidence_age_hours": age, "stale": age > 24, "symbols": len(cur),
                                 "status_counts_at_observation": _summary_status(cur, "status_at_observation")},
            "pack": {"files_sha256": pk["meta"]["files_sha256"], "baseline_prereg_sha256": pk["meta"]["baseline_prereg_sha256"], "integrity_verified": True},
            "limitations": LIMITATIONS, "generated_at_utc": _iso(now)}


def _summary_status(rows, key):
    c = {}
    for r in rows:
        c[r["calendar"][key]] = c.get(r["calendar"][key], 0) + 1
    return c


@router.get("/calendar")
def calendar(date_from: Optional[str] = None, date_to: Optional[str] = None, signal: Optional[str] = Query(None, pattern="^(BUY|SELL|NO_TRADE)$"), variant: Optional[str] = Query(None, pattern="^(any|continuation|contrarian)$"),
             status: Optional[str] = None, missing: Optional[str] = Query(None, pattern="^F[0-9]+$"), q: Optional[str] = None, kind: Optional[str] = Query(None, pattern="^(BMO|AMC|UNKNOWN)$"),
             limit: int = Query(500, ge=1, le=1000), offset: int = Query(0, ge=0)):
    _guard()
    now = _now()
    rows = []
    for r in current_rows():
        r2 = dict(r)
        r2["calendar"] = dict(r["calendar"], **_status_now(r, now))
        rows.append(r2)
    f = _filter(rows, date_from=date_from, date_to=date_to, signal=signal, variant=variant, status=status, missing=missing, q=q, kind=kind)
    slim = [{k: v for k, v in r.items() if k != "subscores"} for r in _page(f, limit, offset)]
    return {"status": "OK", "dataset": "CURRENT_CALENDAR", "label": DATASET_LABELS["CURRENT_CALENDAR"], "source": "SAVED_EVIDENCE_PACK", "as_of": _iso(now), "counts": {"total": len(rows), "filtered": len(f), "returned": len(slim)},
            "summary": _summary(f), "status_counts_now": _summary_status(f, "status_now"), "rows": slim}


@router.get("/replay")
def replay(experiment: str = Query("primary"), date_from: Optional[str] = None, date_to: Optional[str] = None, signal: Optional[str] = Query(None, pattern="^(BUY|SELL|NO_TRADE)$"),
           variant: Optional[str] = Query(None, pattern="^(any|continuation|contrarian)$"), missing: Optional[str] = Query(None, pattern="^F[0-9]+$"), q: Optional[str] = None,
           kind: Optional[str] = Query(None, pattern="^(BMO|AMC|UNKNOWN)$"), limit: int = Query(500, ge=1, le=1000), offset: int = Query(0, ge=0)):
    _guard()
    if experiment not in EXPERIMENTS:
        raise HTTPException(status_code=404, detail="unknown experiment")
    rows = _replay_rows(experiment)
    f = _filter(rows, date_from=date_from, date_to=date_to, signal=signal, variant=variant, status=None, missing=missing, q=q, kind=kind)
    spec = EXPERIMENTS[experiment]
    return {"status": "OK", "dataset": "HISTORICAL_REPLAY", "label": DATASET_LABELS["HISTORICAL_REPLAY"], "experiment": experiment, "experiment_label": spec["label"], "primary": spec["primary"],
            "exploratory": spec["exploratory"], "f7_unverified": spec["f7"], "source": "SAVED_EVIDENCE_PACK", "counts": {"total": len(rows), "filtered": len(f), "returned": len(_page(f, limit, offset))},
            "summary": _summary(f), "rows": _page(f, limit, offset)}


@router.get("/synthetic")
def synthetic(signal: Optional[str] = Query(None, pattern="^(BUY|SELL|NO_TRADE)$"), variant: Optional[str] = Query(None, pattern="^(any|continuation|contrarian)$")):
    _guard()
    rows = _synthetic_rows()
    f = _filter(rows, date_from=None, date_to=None, signal=signal, variant=variant, status=None, missing=None, q=None, kind=None)
    return {"status": "OK", "dataset": "SYNTHETIC", "label": DATASET_LABELS["SYNTHETIC"], "source": "SAVED_EVIDENCE_PACK", "counts": {"total": len(rows), "filtered": len(f)}, "summary": _summary(f), "rows": f}


@router.get("/stock/{symbol}")
def stock(symbol: str):
    _guard()
    sym = symbol.upper().strip()
    now = _now()
    cur = [r for r in current_rows() if r["symbol"] == sym]
    reps = []
    for exp in EXPERIMENTS:
        reps += [r for r in _replay_rows(exp) if r["symbol"] == sym]
    import smart_move_live as L
    svc = live_service()
    live_rows = [r for r in L.LiveScorer(svc).score_all(svc.clock.now()) if r["symbol"] == sym]
    if not cur and not reps and not live_rows:
        raise HTTPException(status_code=404, detail={"status": "UNKNOWN_SYMBOL", "message": "%s is not in any saved Smart Move dataset" % sym})
    cur_out = []
    for r in cur:
        r2 = dict(r)
        r2["calendar"] = dict(r["calendar"], **_status_now(r, now))
        cur_out.append(r2)
    return {"status": "OK", "symbol": sym, "source": "SAVED_EVIDENCE_PACK", "as_of": _iso(now), "current_calendar": cur_out, "live_current": live_rows, "historical_replay": reps,
            "labels": {"current_calendar": DATASET_LABELS["CURRENT_CALENDAR"], "historical_replay": DATASET_LABELS["HISTORICAL_REPLAY"]}, "limitations": LIMITATIONS}


# ----------------------------------------------------------------------------- live layer (current evidence + governed refresh)
_service = None
_service_lock = threading.Lock()


def _warmer_usage(ts):
    """Requests the existing warmer used this hour, when it runs in this same process; 0 otherwise (the governor still keeps its reservation for the warmer)."""
    try:
        import local_warmer
        return int(local_warmer._state.get("hour_calls", 0))
    except Exception:  # noqa: BLE001
        return 0


def live_service():
    """The one refresh service of this process. LIVE by default with the kill switch engaged and zero authorization; SMART_MOVE_MODE=SIMULATION attaches the fake provider."""
    global _service
    with _service_lock:
        if _service is None:
            import smart_move_live as L
            mode = os.getenv("SMART_MOVE_MODE", "LIVE").upper()
            cfg = L.LiveConfig(mode=mode)
            fetcher, clock = None, L.SystemClock()
            if mode == "SIMULATION":
                import tempfile
                import smart_move_sim as S
                if os.getenv("SMART_MOVE_SIM_NOW"):
                    clock = L.OffsetClock(datetime.fromisoformat(os.environ["SMART_MOVE_SIM_NOW"]))
                cfg.store_dir = Path(os.getenv("SMART_MOVE_LIVE_DIR") or tempfile.mkdtemp(prefix="smart_move_sim_"))
                cfg.refresh_enabled, cfg.spacing_s = True, 0.0
                cfg.authorized_daily = int(os.getenv("SMART_MOVE_AUTHORIZED_DAILY") or 1700)
                fetcher = S.SimulatedProvider(clock)
            elif cfg.refresh_enabled and cfg.authorized_daily > 0:
                fetcher = L.InfraFetcher()
            _service = L.RefreshService(cfg, fetcher, clock, existing_usage_fn=_warmer_usage)
            if os.getenv("SMART_MOVE_AUTO_REFRESH") == "1" and fetcher is not None and _service.enabled:
                _service.start_auto()
        return _service


def reset_live_service():
    global _service
    with _service_lock:
        if _service is not None:
            _service.stop_auto()
        _service = None


@router.get("/feed")
def feed():
    return {"status": "OK", **live_service().feed()}


@router.get("/live")
def live(date_from: Optional[str] = None, date_to: Optional[str] = None, signal: Optional[str] = Query(None, pattern="^(BUY|SELL|NO_TRADE)$"), variant: Optional[str] = Query(None, pattern="^(any|continuation|contrarian)$"),
         status: Optional[str] = None, missing: Optional[str] = Query(None, pattern="^F[0-9]+$"), q: Optional[str] = None, kind: Optional[str] = Query(None, pattern="^(BMO|AMC|UNKNOWN)$"),
         limit: int = Query(500, ge=1, le=1000), offset: int = Query(0, ge=0)):
    svc = live_service()
    import smart_move_live as L
    now = svc.clock.now()
    rows = L.LiveScorer(svc).score_all(now)
    f = _filter(rows, date_from=date_from, date_to=date_to, signal=signal, variant=variant, status=status, missing=missing, q=q, kind=kind)
    fd = svc.feed()
    blockers = list(fd["blockers"])
    if not rows:
        blockers.append({"code": "NO_LIVE_EVIDENCE", "message": "No calendar evidence has been collected by the refresh service yet, so there is nothing to score."})
    elif not any(r["calendar"]["eligible_for_shadow"] for r in rows):
        blockers.append({"code": "NO_ELIGIBLE_EVENT", "message": "None of the %d collected events is vendor-confirmed with known timing and a fresh calendar." % len(rows)})
    if fd["freshness"]["calendar"]["state"] == "STALE":
        blockers.append({"code": "STALE_CALENDAR", "message": "The newest calendar evidence is %s h old (limit %s h): new signals are blocked until it is refreshed." % (fd["freshness"]["calendar"]["age_hours"], fd["freshness"]["calendar"]["limit_hours"])})
    stale_rows = [r for r in rows if r["blocked_by"]]
    sim = any(r["evidence_mode"] == "SIMULATION" for r in rows) or svc.cfg.mode == "SIMULATION"
    label = "SIMULATED PROVIDER: invented companies and prices in the real vendor formats. Not market data. Not real signals." if sim else DATASET_LABELS["LIVE_CURRENT"]
    return {"status": "OK", "dataset": "LIVE_CURRENT", "label": label, "mode": "SIMULATION" if sim else svc.cfg.mode, "source": "LIVE_STORE", "as_of": _iso(now), "counts": {"total": len(rows), "filtered": len(f), "returned": len(_page(f, limit, offset)), "blocked": len(stale_rows)},
            "summary": _summary(f), "blockers": blockers, "feed_status": fd["status"], "rows": _page(f, limit, offset)}


@router.post("/refresh")
def refresh(body: dict = Body(default={})):
    """Manual refresh. Obeys exactly the same governor as the automatic one (kill switch, authorization, ceiling, shared hourly caps, spacing, retry limit) plus a minimum gap between manual runs."""
    svc = live_service()
    scope = body.get("scope", "all")
    if scope not in ("all", "calendar", "prices"):
        raise HTTPException(status_code=422, detail="scope must be all, calendar or prices")
    mx = body.get("max_requests")
    mx = int(mx) if mx is not None else None
    now = svc.clock.now()
    hard = [b for b in svc.blockers(now) if b["code"] in ("KILL_SWITCH", "NOT_AUTHORIZED", "NO_FETCHER", "PROVIDER_STOP")]
    if hard:
        svc.log("MANUAL_REFRESH_REFUSED", blockers=[b["code"] for b in hard])
        raise HTTPException(status_code=403, detail={"status": "REFRESH_REFUSED", "blockers": hard, "requests_sent": 0})
    if svc.cfg.mode == "SIMULATION":
        return {"status": "OK", **svc.manual_refresh(scope, mx)}
    threading.Thread(target=svc.manual_refresh, args=(scope, mx), daemon=True, name="smart-move-manual-refresh").start()
    return {"status": "STARTED", "scope": scope, "note": "running in the background under the same request limits; watch /feed"}


@router.post("/refresh/kill")
def kill(body: dict = Body(default={})):
    """Engages the kill switch. Releasing it is deliberately not possible from here: remove the KILL file / change the configuration locally."""
    svc = live_service()
    svc.engage_kill_switch(str(body.get("reason") or "manual via API"))
    return {"status": "OK", "kill_switch": True, "note": "no further request can be sent until the switch is released locally"}
