"""Smart Move prediction history, accuracy tracking and reports. Pure local code over SAVED files: it sends no request, never touches a provider, the kill switch or the scoring engine.

    predictions   <- predictions.jsonl files written by the live layer / the scheduled run (read only)
    outcomes      <- outcomes.jsonl in a tracking folder of its own (append only; one row per event; entered only by import_outcomes, never fetched)
    accuracy      <- the frozen grader (smvshadow.grader.grade) over in-window, hindsight-free predictions; descriptive statistics only

Rules enforced here (each covered by a test):
  * evidence modes never mix: SIMULATION and LIVE are graded and reported separately; a SIMULATION report can never allow an accuracy claim;
  * a prediction captured on or after the start of its earnings day (ET) is LATE and excluded from grading; an outcome recorded before its earnings date is refused;
  * a prediction is never edited; a changed prediction is a new version, the history shows the whole chain;
  * an outcome is never overwritten: a second, different outcome for the same event is refused and counted;
  * the claim flag is the grader's: False until the pre-registered minimum sample (and an evidence audit) is met, and this module never sets the audit flag.
"""
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "shadow_impl"))
from smvshadow import grader  # noqa: E402  (frozen grading rules, unchanged)

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
VARIANTS = ("continuation", "contrarian")
SIGNALS = ("BUY", "SELL", "NO_TRADE")
OUTCOMES_FILE = "outcomes.jsonl"


def _read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                out.append({"_unreadable_line": True})
    return [r for r in out if not r.get("_unreadable_line")]


def _d(x) -> date:
    return x if isinstance(x, date) and not isinstance(x, datetime) else date.fromisoformat(str(x)[:10])


def _ts(x) -> datetime:
    t = datetime.fromisoformat(str(x))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


# ---------------------------------------------------------------- history
def load_predictions(store_dirs: Iterable) -> list:
    """Every stored prediction version from the given live-store folders (predictions.jsonl), tagged with its source folder. Read only."""
    rows = []
    for sd in store_dirs:
        for r in _read_jsonl(Path(sd) / "predictions.jsonl"):
            if r.get("symbol") and r.get("earnings_date") and r.get("engine"):
                rows.append(dict(r, _source=str(sd)))
    return rows


def is_late(p: dict) -> bool:
    """Captured on or after 00:00 ET of the earnings day: cannot be a pre-announcement capture (the real window closes at least 24 h before the release)."""
    return _ts(p["captured_at"]).astimezone(ET).date() >= _d(p["earnings_date"])


def in_window(p: dict, variant: str) -> bool:
    v = p["engine"].get(variant) or {}
    return "OUT_OF_WINDOW" not in (v.get("reason_codes") or []) and "OUT_OF_WINDOW" not in (p.get("blocked_by") or []) and not is_late(p)


def history(preds: list) -> list:
    """One entry per event with its whole version chain, newest last. Nothing is merged or edited."""
    chains = defaultdict(list)
    for p in preds:
        chains[(p["symbol"], str(p["earnings_date"])[:10], p.get("mode") or "LIVE")].append(p)
    out = []
    for (sym, ed, mode), vs in sorted(chains.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        vs.sort(key=lambda p: (p.get("version", 1), p["captured_at"]))
        last = vs[-1]
        out.append({
            "symbol": sym, "earnings_date": ed, "mode": mode, "versions": len(vs), "late_capture": is_late(vs[0]),
            "first_captured_at": vs[0]["captured_at"], "last_captured_at": last["captured_at"], "blocked_by": last.get("blocked_by") or [],
            "latest": {v: {"signal": (last["engine"].get(v) or {}).get("signal"), "bull": (last["engine"].get(v) or {}).get("bull"), "bear": (last["engine"].get(v) or {}).get("bear"),
                           "available": (last["engine"].get(v) or {}).get("available"), "reason_codes": (last["engine"].get(v) or {}).get("reason_codes") or []} for v in VARIANTS},
            "chain": [{"version": p.get("version", 1), "captured_at": p["captured_at"], "digest": (p.get("digest") or "")[:12],
                       "signals": {v: (p["engine"].get(v) or {}).get("signal") for v in VARIANTS}, "evidence_items": len(p.get("evidence") or [])} for p in vs]})
    return out


# ---------------------------------------------------------------- outcomes (append only, imported, never fetched)
def load_outcomes(track_dir) -> list:
    return _read_jsonl(Path(track_dir) / OUTCOMES_FILE)


def import_outcomes(track_dir, rows: Iterable[dict], *, mode: str, recorded_at: Optional[datetime] = None) -> dict:
    """rows: {symbol, earnings_date, move_pct, source}. move_pct is the next-session close-to-close move in percent (grader convention). Append only."""
    track = Path(track_dir)
    track.mkdir(parents=True, exist_ok=True)
    existing = {(o["symbol"], o["earnings_date"], o["mode"]): o for o in load_outcomes(track)}
    now = recorded_at or datetime.now(UTC)
    res = Counter()
    with open(track / OUTCOMES_FILE, "a", encoding="utf-8") as fh:
        for r in rows:
            try:
                sym, ed, mv = str(r["symbol"]).upper().strip(), _d(r["earnings_date"]).isoformat(), float(r["move_pct"])
            except (KeyError, ValueError, TypeError):
                res["refused_malformed"] += 1
                continue
            if abs(mv) > 100:
                res["refused_implausible_move"] += 1
                continue
            if now.astimezone(ET).date() <= _d(ed) and mode != "SIMULATION":
                res["refused_recorded_before_the_event_ended"] += 1       # a real outcome cannot exist before the session after the announcement
                continue
            key = (sym, ed, mode)
            if key in existing:
                res["duplicate_same" if abs(existing[key]["move_pct"] - mv) < 1e-9 else "refused_conflicting_overwrite"] += 1
                continue
            row = {"symbol": sym, "earnings_date": ed, "mode": mode, "status": "GRADED", "move_pct": mv, "source": str(r.get("source") or "unspecified"), "recorded_at": now.isoformat()}
            fh.write(json.dumps(row) + "\n")
            existing[key] = row
            res["imported"] += 1
    return dict(res)


def import_outcomes_csv(track_dir, csv_path, *, mode: str) -> dict:
    with open(csv_path, newline="", encoding="utf-8") as fh:
        return import_outcomes(track_dir, csv.DictReader(fh), mode=mode)


# ---------------------------------------------------------------- accuracy (frozen grader, descriptive only)
def accuracy(preds: list, outcomes: list, *, mode: str) -> dict:
    """Grade one evidence mode. Keeps modes apart; a SIMULATION result carries an explicit label and can never allow a claim."""
    mp = [p for p in preds if (p.get("mode") or "LIVE") == mode]
    mo = {(o["symbol"], o["earnings_date"]): o for o in outcomes if o.get("mode") == mode}
    g_preds, excluded = [], Counter()
    for p in mp:
        if is_late(p):
            excluded["late_capture_excluded"] += 1
            continue
        for v in VARIANTS:
            e = p["engine"].get(v)
            if not e:
                continue
            g_preds.append({"symbol": p["symbol"], "earnings_date": _d(p["earnings_date"]), "event_version": 1, "variant": v, "prediction_version": p.get("version", 1),
                            "signal": e["signal"], "in_window": in_window(p, v), "bull_points": e.get("bull"), "adv20": None})
    outc = {(o["symbol"], _d(o["earnings_date"])): o for o in mo.values()}
    rep = grader.grade(g_preds, outc, {}, audit_passed=False)          # audit_passed is never set here: only a separate evidence audit may allow a claim
    rep["mode"] = mode
    rep["extra_audit"] = dict(excluded, events_with_outcome=sum(1 for p in {(p["symbol"], str(p["earnings_date"])[:10]) for p in mp} if p in mo),
                              outcomes_without_prediction=sum(1 for k in mo if not any((p["symbol"], str(p["earnings_date"])[:10]) == k for p in mp)))
    rep["cost_assumption"] = "liquidity (adv20) is not stored with predictions, so the grader's most conservative side cost (20 bps per side) is used"
    if mode == "SIMULATION":
        rep["claim_allowed"] = False
        rep["claim_text"] = "SIMULATED data: this tests the reporting only and says nothing about the model"
    return rep


# ---------------------------------------------------------------- summary + report
def signal_summary(preds: list, mode: str) -> dict:
    latest = {}
    for p in preds:
        if (p.get("mode") or "LIVE") != mode:
            continue
        k = (p["symbol"], str(p["earnings_date"])[:10])
        if k not in latest or p.get("version", 1) >= latest[k].get("version", 1):
            latest[k] = p
    out = {v: Counter() for v in VARIANTS}
    miss, blocked = Counter(), Counter()
    for p in latest.values():
        for v in VARIANTS:
            out[v][(p["engine"].get(v) or {}).get("signal", "NO_TRADE")] += 1
        for b in p.get("blocked_by") or []:
            blocked[b] += 1
        for c in ((p["engine"].get("continuation") or {}).get("reason_codes") or []):
            if c.startswith(("INSUFFICIENT", "NO_", "MISSING", "RETRIEVED", "STALE", "UNKNOWN")):
                miss[c] += 1
    return {"events": len(latest), "signals": {v: {s: out[v].get(s, 0) for s in SIGNALS} for v in VARIANTS}, "blocked_by": dict(blocked.most_common()), "missing_reason_codes": dict(miss.most_common())}


def build_report(preds: list, outcomes: list, *, generated_at: Optional[datetime] = None) -> dict:
    modes = sorted({(p.get("mode") or "LIVE") for p in preds} | {o.get("mode") for o in outcomes if o.get("mode")})
    hist = history(preds)
    return {"generated_at": (generated_at or datetime.now(UTC)).isoformat(), "modes": modes, "events": len(hist), "prediction_versions": len(preds),
            "late_captures": sum(1 for h in hist if h["late_capture"]),
            "by_mode": {m: {"summary": signal_summary(preds, m), "accuracy": accuracy(preds, outcomes, mode=m)} for m in modes},
            "notice": "Shadow research only. No trade is placed. No accuracy claim is allowed until the pre-registered minimum sample and an evidence audit are met."}


def report_markdown(rep: dict) -> str:
    L = ["# Smart Move prediction tracking report", "", "Generated %s | events %d | prediction versions %d | late captures excluded %d" % (rep["generated_at"], rep["events"], rep["prediction_versions"], rep["late_captures"]),
         "", "> " + rep["notice"], ""]
    if not rep["modes"]:
        L += ["No stored predictions yet.", ""]
    for m, blk in rep["by_mode"].items():
        s, a = blk["summary"], blk["accuracy"]
        L += ["## Evidence mode: %s%s" % (m, "  (SIMULATED data, reporting test only)" if m == "SIMULATION" else ""), "",
              "Events: %d" % s["events"], "", "| Variant | BUY | SELL | NO TRADE |", "|---|---|---|---|"]
        for v in VARIANTS:
            L.append("| %s | %d | %d | %d |" % (v, s["signals"][v]["BUY"], s["signals"][v]["SELL"], s["signals"][v]["NO_TRADE"]))
        L += ["", "Blocked by: %s" % (json.dumps(s["blocked_by"]) or "none"), "Missing-data reason codes: %s" % (json.dumps(s["missing_reason_codes"]) or "none"), "",
              "### Outcome tracking (descriptive; frozen grader)", "", "**%s**" % a["claim_text"], "",
              "Grading audit: %s | extra: %s" % (json.dumps(a["audit"]), json.dumps(a["extra_audit"]))]
        if a["variants"]:
            L += ["", "| Variant | Graded | Decided | Coverage | Strict hit | 95% interval | Min sample met | Weeks |", "|---|---|---|---|---|---|---|---|"]
            for v, x in a["variants"].items():
                L.append("| %s | %d | %d | %s | %s | %s | %s | %d |" % (v, x["graded_events"], x["decided"], x["coverage"], x["strict_hit_rate"], x["strict_hit_ci95"], x["minimum_sample_met"], x["earnings_weeks"]))
        else:
            L += ["", "No graded events yet (no stored outcomes for this mode)."]
        L += ["", "_%s_" % a["cost_assumption"], ""]
    return "\n".join(L) + "\n"


def write_report(out_dir, preds: list, outcomes: list, *, generated_at: Optional[datetime] = None, stamp: Optional[str] = None) -> dict:
    """Writes tracking_report_<stamp>.json/.md into out_dir. Never overwrites an existing report. Returns the report dict plus the paths."""
    rep = build_report(preds, outcomes, generated_at=generated_at)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = stamp or _ts(rep["generated_at"]).strftime("%Y%m%d_%H%M%S")
    j, m = out / ("tracking_report_%s.json" % stamp), out / ("tracking_report_%s.md" % stamp)
    if j.exists() or m.exists():
        raise FileExistsError("report %s already exists: reports are never overwritten" % stamp)
    j.write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    m.write_text(report_markdown(rep), encoding="utf-8")
    rep["paths"] = {"json": str(j), "md": str(m)}
    return rep


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Smart Move tracking report from SAVED files only (no request is ever sent).")
    ap.add_argument("--store-dir", action="append", default=[], help="live-store folder holding predictions.jsonl (repeatable)")
    ap.add_argument("--tracking-dir", required=True, help="folder for outcomes.jsonl")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--import-outcomes-csv", default="", help="CSV: symbol,earnings_date,move_pct,source (append only)")
    ap.add_argument("--outcome-mode", default="LIVE", choices=["LIVE", "SIMULATION"])
    a = ap.parse_args(argv)
    if a.import_outcomes_csv:
        print("outcome import:", import_outcomes_csv(a.tracking_dir, a.import_outcomes_csv, mode=a.outcome_mode))
    rep = write_report(a.out_dir, load_predictions(a.store_dir), load_outcomes(a.tracking_dir))
    print(json.dumps({"events": rep["events"], "modes": rep["modes"], "paths": rep["paths"]}))


if __name__ == "__main__":
    main()
