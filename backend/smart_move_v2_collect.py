"""V2 evidence pipeline: the extra Unusual Whales endpoints (analyst actions, consensus estimates, flow alerts, short interest, institutional ownership; news only as an opt-in sample).

Every request goes through `svc._call(kind, symbol, params, request_id)` = RefreshService._call, i.e. the existing governor (authorization cap, 2 s spacing, hourly caps, one retry,
401/429 stop for the day), request ledger and kill switch. This module adds: planning with de-duplication, response validation, append-only evidence records with hashes,
a checkpoint for safe resume, and full request accounting. It never builds a provider client; with a mock `svc` it is tested without any network.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

EVIDENCE = "v2_evidence"            # <store>/v2_evidence.jsonl (append-only), bodies in the store's hashed payload folder
UTC = timezone.utc

# kind -> (endpoint documented in api_spec.yaml, params, required keys in a row, minimum days before a SAME-kind re-request is allowed)
KINDS = {
    "uw_analysts": ("/api/screener/analysts", {"limit": 50}, ("action", "timestamp"), 1),
    "uw_estimates": ("/api/companies/{t}/earnings-estimates", {}, ("date",), 7),            # one snapshot per week builds the revision history
    "uw_flow_alerts": ("/api/stock/{t}/flow-alerts", {"limit": 100}, ("created_at", "type"), 1),
    "uw_short_float": ("/api/shorts/{t}/interest-float/v2", {}, ("market_date",), 10),      # snapshots 10+ days apart give the positioning change
    "uw_inst_own": ("/api/institution/{t}/ownership", {"limit": 100}, ("filing_date",), 30),
    "uw_news": ("/api/news/headlines", {"limit": 25}, ("created_at",), 1),
}
CORE = ("uw_analysts", "uw_estimates", "uw_flow_alerts", "uw_short_float", "uw_inst_own")
NEWS_LOOKBACK_DAYS = 7          # the news classifier's lookback (smart_move_news.LOOKBACK_DAYS)
NEWS_MAX_PAGES = 3              # pages 0..2 of 25 headlines per stock; a full page whose oldest headline is still inside the lookback may hide newer-than-lookback items on the next page


def news_next_page(rows, limit: int, page: int, now: datetime) -> bool:
    """True when this page was full AND its oldest headline is still inside the lookback (so the next page may hold more in-window headlines) and the page cap is not reached."""
    if not rows or len(rows) < limit or page + 1 >= NEWS_MAX_PAGES:
        return False
    stamps = []
    for r in rows:
        try:
            t = datetime.fromisoformat(str(r.get("created_at")).replace("Z", "+00:00"))
            stamps.append(t if t.tzinfo else t.replace(tzinfo=UTC))
        except (TypeError, ValueError):
            continue
    return bool(stamps) and min(stamps) > now - timedelta(days=NEWS_LOOKBACK_DAYS)


def merge_news(store, recs) -> list:
    """All saved pages of one stock's headlines (latest record per page), de-duplicated on (publication time, source, headline); the stored bodies are never modified."""
    latest = {}
    for r in sorted(recs, key=lambda r: r["retrieved_at"]):
        latest[(r.get("params") or {}).get("page", 0)] = r
    seen, out = set(), []
    for pg in sorted(latest):
        for h in _rows(store, latest[pg], "uw_news") or []:
            k = (h.get("created_at"), h.get("source"), str(h.get("headline", "")).strip().lower())
            if k not in seen:
                seen.add(k)
                out.append(h)
    return out


def validate(kind: str, body: bytes) -> dict:
    """Structure check of a saved response. Returns {ok, rows, reason}. Never raises."""
    try:
        obj = json.loads(body)
    except (TypeError, ValueError):
        return {"ok": False, "rows": 0, "reason": "NOT_JSON"}
    data = obj.get("data") if isinstance(obj, dict) else None
    if kind == "uw_estimates" and isinstance(data, dict):
        data = data.get("estimates")
    if kind == "uw_short_float" and isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return {"ok": False, "rows": 0, "reason": "NO_DATA_ARRAY"}
    need = KINDS[kind][2]
    bad = [r for r in data if not isinstance(r, dict) or any(k not in r for k in need)]
    if data and len(bad) == len(data):
        return {"ok": False, "rows": len(data), "reason": "MISSING_REQUIRED_FIELDS:" + ",".join(need)}
    return {"ok": True, "rows": len(data), "reason": None}


def _records(store, kind=None, symbol=None) -> list:
    return [r for r in store.read(EVIDENCE) if (kind is None or r["kind"] == kind) and (symbol is None or r["symbol"] == symbol)]


def plan(symbols: Iterable[str], store, now: datetime, *, include_news: bool = False) -> list:
    """Tasks still needed. A kind is skipped when a record of that kind already exists within its minimum interval (no duplicate request); the request ID is stable per symbol/kind/day."""
    tasks = []
    for sym in symbols:
        for kind in CORE + (("uw_news",) if include_news else ()):
            recs = [r for r in _records(store, kind, sym) if r["status"] == "OK"]
            gap = KINDS[kind][3]
            if recs and now - datetime.fromisoformat(max(r["retrieved_at"] for r in recs)) < timedelta(days=gap):
                continue
            tasks.append({"kind": kind, "symbol": sym, "id": "%s-%s-%s" % (kind, sym, now.strftime("%Y%m%d"))})
    return tasks


def run(svc, tasks: list, *, max_requests: int, checkpoint: Optional[Path] = None, stopped: Callable[[], bool] = lambda: False, calendar_ok: Optional[Callable[[datetime], bool]] = None) -> dict:
    """Sequential (one request in flight, within the shared 3-slot limit). Stops on a provider stop; resumes from the checkpoint without repeating finished task IDs."""
    done = set(json.loads(Path(checkpoint).read_text())["done"]) if checkpoint and Path(checkpoint).exists() else set()
    tasks = list(tasks)
    acc = {"planned": len(tasks), "attempted": 0, "ok": 0, "failed": 0, "invalid": 0, "skipped_resume": 0, "by_kind": {}, "stopped": False, "failures": []}
    for t in tasks:
        if t["id"] in done:
            acc["skipped_resume"] += 1
            continue
        if acc["attempted"] >= max_requests:
            acc["budget_exhausted"] = True
            break
        if stopped():
            acc["stopped"] = True
            break
        params = dict(KINDS[t["kind"]][1])
        if t["kind"] in ("uw_analysts", "uw_news"):
            params["ticker"] = t["symbol"]
        if t["kind"] == "uw_news" and t.get("page"):
            params["page"] = t["page"]
        body, sha, at = svc._call(t["kind"], t["symbol"], params, t["id"])
        acc["attempted"] += 1
        acc["by_kind"][t["kind"]] = acc["by_kind"].get(t["kind"], 0) + 1
        if body is None:
            acc["failed"] += 1
            acc["failures"].append({"kind": t["kind"], "symbol": t["symbol"], "reason": "NOT_DONE (denied, failed or provider stop)"})
            if stopped():
                acc["stopped"] = True
                break
            continue
        v = validate(t["kind"], body)
        if calendar_ok is not None and not calendar_ok(at):                 # the response arrived against a calendar older than the frozen limit (e.g. a sleep inside the request): rejected, never scored, re-requested after a refresh
            svc.store.append(EVIDENCE, {"id": t["id"], "kind": t["kind"], "symbol": t["symbol"], "retrieved_at": at.isoformat(), "raw_sha256": sha, "status": "REJECTED_STALE_CALENDAR", "rows": v["rows"], "invalid_reason": "calendar older than the limit at retrieval", "params": params})
            acc["rejected_stale_calendar"] = acc.get("rejected_stale_calendar", 0) + 1
            acc["stopped"] = True
            break
        svc.store.append(EVIDENCE, {"id": t["id"], "kind": t["kind"], "symbol": t["symbol"], "retrieved_at": at.isoformat(), "raw_sha256": sha, "status": "OK" if v["ok"] else "INVALID",
                                    "rows": v["rows"], "invalid_reason": v["reason"], "params": params})
        acc["ok" if v["ok"] else "invalid"] += 1
        if v["ok"] and t["kind"] == "uw_news":
            page = int(t.get("page") or 0)
            try:
                rows = json.loads(body).get("data")
            except (TypeError, ValueError):
                rows = None
            if isinstance(rows, list) and news_next_page(rows, int(params.get("limit", 25)), page, at):
                tasks.append({"kind": "uw_news", "symbol": t["symbol"], "id": "%s-p%d" % (t["id"].split("-p")[0], page + 1), "page": page + 1})
                acc["news_pages_added"] = acc.get("news_pages_added", 0) + 1
        if v["ok"]:
            done.add(t["id"])
            if checkpoint:
                Path(checkpoint).write_text(json.dumps({"done": sorted(done)}), encoding="utf-8")
    return acc


# ---------------------------------------------------------------- reading the saved evidence back, as of a capture time
def _rows(store, rec, kind):
    obj = json.loads(store.get(rec["raw_sha256"]))
    d = obj.get("data")
    if kind == "uw_estimates":
        return (d or {}).get("estimates") if isinstance(d, dict) else d
    return [d] if (kind == "uw_short_float" and isinstance(d, dict)) else d


def load_inputs(store, symbol: str, capture_ts: datetime, ending_quarter: Optional[str] = None) -> dict:
    """The saved V2 inputs for one symbol AS OF capture_ts (anything retrieved later is invisible). None for a kind that was never collected."""
    out = {"analyst_actions": None, "est_snapshots": None, "flow": None, "short_snaps": None, "inst": None, "news": None}
    recs = [r for r in _records(store, symbol=symbol) if r["status"] == "OK" and datetime.fromisoformat(r["retrieved_at"]) <= capture_ts]
    by = {}
    for r in sorted(recs, key=lambda r: r["retrieved_at"]):
        by.setdefault(r["kind"], []).append(r)
    if by.get("uw_analysts"):
        out["analyst_actions"] = _rows(store, by["uw_analysts"][-1], "uw_analysts") or []
    if by.get("uw_flow_alerts"):
        out["flow"] = _rows(store, by["uw_flow_alerts"][-1], "uw_flow_alerts") or []
    if by.get("uw_inst_own"):
        out["inst"] = _rows(store, by["uw_inst_own"][-1], "uw_inst_own") or []
    if by.get("uw_news"):
        out["news"] = merge_news(store, by["uw_news"])
    if by.get("uw_estimates"):
        import smart_move_v2_snapshots as SN
        snaps = []
        for r in by["uw_estimates"]:
            rows = _rows(store, r, "uw_estimates") or []
            if ending_quarter:                                  # exact period: the fiscal quarter the upcoming report covers (the old "nearest period on/after the save day" mapping picked a different period for 92 of 92 stocks)
                row = SN.quarter_row(rows, ending_quarter)
                if row is not None:
                    snaps.append({"captured_at": r["retrieved_at"], "eps": row.get("eps_estimate_average"), "revenue": row.get("revenue_estimate_average"), "period": row.get("date")})
                continue                                        # no exact row: no snapshot, never a guess
            fwd = sorted((x for x in rows if str(x.get("date"))[:10] >= r["retrieved_at"][:10]), key=lambda x: str(x["date"]))     # legacy mapping, kept only when the quarter is unknown
            if fwd:
                snaps.append({"captured_at": r["retrieved_at"], "eps": fwd[0].get("eps_estimate_average"), "revenue": fwd[0].get("revenue_estimate_average"), "period": fwd[0].get("date")})
        out["est_snapshots"] = snaps
    if by.get("uw_short_float"):
        snaps = []
        for r in by["uw_short_float"]:
            snaps = _rows(store, r, "uw_short_float") or []        # the endpoint returns the whole dated history (semi-monthly settlements), so one response is enough; the newest response wins
        out["short_snaps"] = snaps
    return out


# ---------------------------------------------------------------- what future collections are needed (a plan only; nothing here schedules or requests)
NEEDS = {
    "uw_estimates": "a second consensus snapshot at least 7 days after the first (the endpoint gives no revision history for most stocks), so revisions can be measured",
    "uw_flow_alerts": "a fresh pull on each day inside the capture window (alerts are a rolling window of the most recent trades)",
    "uw_short_float": "no repeat needed for the history (the endpoint returns the dated settlement history); refresh about every 15 days for the newest settlement",
    "uw_inst_own": "a refresh after the 13F deadline (45 days after quarter end) because early filers bias the net change; about every 30 days",
    "uw_analysts": "a fresh pull on each day inside the capture window (rating actions are dated)",
}


def repeat_plan(store, symbols: Iterable[str], now: datetime) -> dict:
    """Per symbol and kind: when it was last collected and the earliest time a repeat adds information. Pure; derived from the saved evidence records."""
    out, due_now = {}, 0
    for sym in symbols:
        recs = [r for r in _records(store, symbol=sym) if r["status"] == "OK"]
        row = {}
        for kind in CORE:
            ks = [r for r in recs if r["kind"] == kind]
            if not ks:
                row[kind] = {"last": None, "next_useful": "now", "need": NEEDS[kind]}
                due_now += 1
                continue
            last = datetime.fromisoformat(max(r["retrieved_at"] for r in ks))
            nxt = last + timedelta(days={"uw_estimates": 7, "uw_flow_alerts": 1, "uw_short_float": 15, "uw_inst_own": 30, "uw_analysts": 1}[kind])
            row[kind] = {"last": last.isoformat(), "snapshots": len(ks), "next_useful": nxt.isoformat(), "due_now": nxt <= now, "need": NEEDS[kind]}
            due_now += nxt <= now
        out[sym] = row
    return {"as_of": now.isoformat(), "symbols": len(out), "requests_due_now": due_now, "by_symbol": out,
            "note": "plan only; recurring live collection is not activated. Revision measurement needs the second estimates snapshot (>= 7 days after the first)."}
