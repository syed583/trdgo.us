"""Per-event UNIVERSE table for the rolling-horizon monitor (P1, development only). One row per earnings event in the next N days, saying what the collector knows and what it is doing about it:
calendar vendors and confirmation, session, prediction window (UTC / New York / Dubai), calendar slot tier and freshness, F3 / F5 / F8 evidence status, the prioritisation proxy, whether the event is QUEUED, DEFERRED or has a prediction,
and the latest change (new / rescheduled / missing / removed).

READ-ONLY: it reads the saved calendar observations, the evidence store and the prediction ledger, and writes ONE derived file (universe.json) with an atomic replace. It sends no request, scores nothing, and never edits the ledger,
the stores or any stored record. A DEFERRED event is never excluded: `excluded_by_proxy` is always False; the proxy only orders work, and the frozen ADV20 gate still decides from the price bars.

collection_status: PREDICTED:<result> | WINDOW_CLOSED_NO_PREDICTION | REMOVED | NOT_ELIGIBLE | EVIDENCE_COMPLETE | QUEUED_THIS_RUN | DEFERRED | WAITING_WINDOW | CALENDAR_MISSING_UNCONFIRMED
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "docs" / "smart_move" / "shadow_impl"))
import smart_move_calendar_tiers as CT  # noqa: E402
import smart_move_engine20 as E20  # noqa: E402
import smart_move_event_records as ER  # noqa: E402
import smart_move_live as L  # noqa: E402
import smart_move_live_publisher as PUB  # noqa: E402
from smvshadow import cutoff as co  # noqa: E402

UTC = timezone.utc
DUBAI = ZoneInfo("Asia/Dubai")
UNIVERSE_FILE = "universe.json"
SIDE = {"BMO": "premarket", "AMC": "afterhours"}


def _dt(x) -> datetime:
    t = x if isinstance(x, datetime) else datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _times(t) -> dict:
    t = _dt(t)
    return {"utc": t.isoformat(), "new_york": t.astimezone(co.ET).strftime("%Y-%m-%d %H:%M %Z"), "dubai": t.astimezone(DUBAI).strftime("%Y-%m-%d %H:%M")}


def _ev_status(svc, kind: str, symbol: str, params: dict, now) -> str:
    rec = svc._latest(kind, symbol, params)
    if rec is None:
        return "MISSING"
    return "FRESH" if L.rec_fresh(rec, kind, now, svc.cfg) else "STALE"


def build(svc, now, *, horizon_days: int, slot_state: dict, funded_symbols: Optional[set] = None, selected_symbols: Optional[set] = None, ledger_path=None, change_index: Optional[dict] = None) -> dict:
    now = _dt(now)
    funded_symbols = funded_symbols or set()
    selected_symbols = selected_symbols or set()
    change_index = change_index if change_index is not None else (slot_state.get("symbol_index") or {})
    cal_rows = svc.store.read("cal_obs")
    meta = {}
    for r in cal_rows:
        m = meta.setdefault(r["symbol"], {"company": None, "marketcap": None, "sector": None})
        for k in ("company", "marketcap", "sector"):
            if r.get(k) and not m[k]:
                m[k] = r[k]
    recs = ER.records_from_saved(cal_rows, now, {})
    led = PUB.read_ledger(ledger_path) if ledger_path else {"records": [], "ok": True, "problem": None}
    results = {}
    for x in led["records"]:                                                           # newest record per event wins; the ledger itself is only read
        if x.get("record_type") in ("PREDICTION", "WINDOW_MISSED"):
            results[(x.get("symbol"), x.get("event_date"))] = x.get("result") if x.get("record_type") == "PREDICTION" else "WINDOW_MISSED"
    spy = _ev_status(svc, "ohlc", "SPY", {"timeframe": "3Y"}, now)
    today = now.astimezone(co.ET).date()
    last_day = today + timedelta(days=max(1, min(int(horizon_days), CT.HORIZON_MAX)))
    rows = []
    for r in recs:
        if not r["event_date"]:
            continue
        ed = datetime.fromisoformat(r["event_date"]).date()
        if ed < today - timedelta(days=2) or ed > last_day:
            continue
        sym, sess = r["symbol"], r["session"]
        m = meta.get(sym, {})
        ws, cu = (_dt(r["window_start_utc"]), _dt(r["cutoff_utc"])) if r.get("window_start_utc") else (None, None)
        window = "UNKNOWN" if ws is None else "CLOSED" if now > cu else "OPEN" if ws <= now else "NOT_YET_OPEN"
        slot = CT.classify((ed, SIDE[sess]), now) if sess in SIDE else None
        slot_state_row = (slot_state.get("slots") or {}).get(slot["key"]) if slot else None
        cal_fresh = None if slot is None else ("NEVER_REFRESHED" if not slot_state_row else "FRESH" if CT.slot_is_fresh(slot_state, slot, now) else "STALE")
        etf = L.SECTOR_ETF.get(m.get("sector"))
        ev = {}
        if window == "OPEN":
            ev = {"F5_price": _ev_status(svc, "ohlc", sym, {"timeframe": "3Y"}, now), "F3_earnings_history": _ev_status(svc, "earnings", sym, {"limit": 24}, now),
                  "F8_market": spy, "F8_sector_etf": _ev_status(svc, "ohlc", etf, {"timeframe": "3Y"}, now) if etf else "NO_SECTOR_ETF"}
        else:
            ev = {"F5_price": "NOT_DUE", "F3_earnings_history": "NOT_DUE", "F8_market": "NOT_DUE", "F8_sector_etf": "NOT_DUE"}
        chg = change_index.get(sym) or {}
        res = results.get((sym, r["event_date"]))
        needed = [ev["F5_price"], ev["F3_earnings_history"], ev["F8_market"]]
        deferred_reason = None
        if chg.get("state") == "REMOVED":
            status = "REMOVED"
        elif res:
            status = "PREDICTED:" + res if res != "WINDOW_MISSED" else "WINDOW_CLOSED_NO_PREDICTION"
        elif window == "CLOSED":
            status = "WINDOW_CLOSED_NO_PREDICTION"
        elif window == "NOT_YET_OPEN":
            status = "WAITING_WINDOW"
        elif chg.get("state") == "MISSING":
            status = "CALENDAR_MISSING_UNCONFIRMED"
        elif not r["prediction_eligible"]:
            status = "NOT_ELIGIBLE"
        elif all(x == "FRESH" for x in needed):
            status = "EVIDENCE_COMPLETE"
        elif sym in funded_symbols:
            status = "QUEUED_THIS_RUN"
        else:
            status = "DEFERRED"
            deferred_reason = ("selected but not funded: the run budget is used up" if sym in selected_symbols else "ranked below the run's slots by deadline and priority proxy") + "; still eligible while its window is open"
        rows.append({"symbol": sym, "company": m.get("company"), "event_date": r["event_date"], "session": sess, "sector": m.get("sector"), "market_cap": m.get("marketcap"), "priority_proxy_tier": E20.cap_tier(m.get("marketcap")),
                     "excluded_by_proxy": False, "vendor_status": r["vendor_status"], "vendor_conflict": r["vendor_conflict"], "prediction_eligible": r["prediction_eligible"], "ineligible_reasons": r["ineligible_reasons"],
                     "window": window, "window_start": _times(ws) if ws else None, "cutoff": _times(cu) if cu else None, "hours_to_cutoff": round((cu - now).total_seconds() / 3600, 2) if cu else None,
                     "calendar_tier": slot["tier"] if slot else None, "calendar_slot": slot["key"] if slot else None, "calendar_slot_state": cal_fresh, "calendar_slot_observed_at": (slot_state_row or {}).get("observed_at"),
                     "calendar_change": chg.get("state"), "evidence": ev, "collection_status": status, "deferred": status == "DEFERRED", "deferred_reason": deferred_reason, "latest_result": res})
    rows.sort(key=lambda x: (x["cutoff"]["utc"] if x["cutoff"] else "9", x["priority_proxy_tier"], x["symbol"]))
    by_day = {}
    for x in rows:
        d = by_day.setdefault(x["event_date"], Counter())
        d[x["collection_status"].split(":")[0]] += 1
    comp = CT.completeness(slot_state, now, horizon_days)
    return {"schema": "smart-move-universe-1", "generated_utc": now.isoformat(), "generated": _times(now), "horizon_days": max(1, min(int(horizon_days), CT.HORIZON_MAX)), "ledger_ok": led["ok"], "ledger_problem": led["problem"],
            "calendar_completeness": comp, "summary": {"events": len(rows), "by_status": dict(Counter(x["collection_status"].split(":")[0] for x in rows)), "by_tier": dict(Counter(x["priority_proxy_tier"] for x in rows)),
                                                       "deferred": sum(1 for x in rows if x["deferred"]), "excluded_by_proxy": 0, "by_event_date": {d: dict(c) for d, c in sorted(by_day.items())}},
            "note": "Discovered is not eligible and eligible is not collected: each row says which. The proxy only orders work; no event is excluded by it.", "rows": rows}


def write(out_dir, universe: dict) -> Path:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (UNIVERSE_FILE + ".tmp")
    tmp.write_text(json.dumps(universe, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, d / UNIVERSE_FILE)
    return d / UNIVERSE_FILE
