"""READ-ONLY count of distinct earnings EVENTS (symbol + actual event date) for the Smart Move shadow-pilot capacity decision. Version 2.

    python event_count_universe.py [--root C:\\apps\\us-stock-reader] [--out-dir C:\\audit-reports\\smart_move_counts] [--today YYYY-MM-DD]

SAFETY (each point is covered by tests): one connection; session set to default_transaction_read_only = on and verified with SHOW before any query; 30 s
statement timeout, 5 s lock timeout, 15 s connect timeout; only SELECT / WITH / SHOW / SET (the three session settings) are ever sent; no INSERT, UPDATE,
DELETE, TRUNCATE, DDL or provider request exists in this file; the connection string and password are never printed (a failure prints only the exception
class); the output folder must not lie inside the application folder and an existing output file is never overwritten; the event query returns one row per
(symbol, earnings_date) and aborts above 200,000 rows. Tables read: model_predictions, earnings_event_outcomes, earnings_calendar (if present) and
local_warm_state row 2 (only the symbol names of the warmer queue). Output: symbols, dates and counts.

Counting rules. Raw events = distinct (symbol, earnings_date) pairs, however many models or snapshot days predicted them. Two dates of one symbol up to
45 days apart are examined: they are MERGED only when the first date was never predicted again after the second date appeared AND the first date never
occurred (last snapshot before it, no outcome row) = CONFIDENT date shift. Every other close pair is kept as two events and listed as UNCERTAIN (concurrent
predictions, or the first date may have occurred). Every merge and every uncertain pair is written out with its reason.
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

VERSION = "event-count-v2"
CORE_PER_EVENT, FULL_EXTRA, SHARED_PER_DAY = 5, 13, 24
FULL_CAP = 30
CAP_GRID = (30, 70, 115, 170, 195, 220)
CLOSE_DAYS = 45
ROW_LIMIT = 200_000
REQUIRED = {"model_predictions": ["symbol", "earnings_date", "report_timing", "snapshot_date", "predicted_at", "model"],
            "local_warm_state": ["id", "state"]}
OPTIONAL = {"earnings_event_outcomes": ["symbol", "earnings_date"], "earnings_calendar": ["symbol", "earnings_date", "eps_actual"]}


def db_url(root):
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        env = Path(root) / "backend" / ".env"
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines() if env.exists() else []:
            if line.strip().startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not url:
        sys.exit("STOPPED: no DATABASE_URL (not printed)")
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def kind_of(label):
    s = (label or "").strip().lower()
    if s.startswith("before") or s.startswith("bmo") or s.startswith("pre"):
        return "BMO"
    if s.startswith("after") or s.startswith("amc") or s.startswith("post"):
        return "AMC"
    return "UNKNOWN"


def classify_pairs(events, outcomes):
    """events: symbol -> list of dicts(date, kind, first_snap, last_snap, n_snaps, n_models). Returns (deduped events, merges, uncertain pairs)."""
    kept, merges, uncertain = [], [], []
    for sym, evs in events.items():
        evs = sorted(evs, key=lambda e: e["date"])
        links = []                                           # confident links between consecutive dates
        for e1, e2 in zip(evs, evs[1:]):
            gap = (e2["date"] - e1["date"]).days
            if gap > CLOSE_DAYS:
                continue
            concurrent = e1["last_snap"] >= e2["first_snap"]
            may_have_occurred = e1["last_snap"] >= e1["date"] or (sym, e1["date"]) in outcomes
            rec = {"symbol": sym, "date_1": e1["date"].isoformat(), "date_2": e2["date"].isoformat(), "gap_days": gap,
                   "date_1_snapshots": [e1["first_snap"].isoformat(), e1["last_snap"].isoformat(), e1["n_snaps"]],
                   "date_2_snapshots": [e2["first_snap"].isoformat(), e2["last_snap"].isoformat(), e2["n_snaps"]]}
            if concurrent:
                rec.update(decision="KEPT_SEPARATE", code="UNCERTAIN_CONCURRENT_PREDICTIONS",
                           reason="both dates were being predicted at the same time (first date's last snapshot is not before the second date's first snapshot): may be two real events")
                uncertain.append(rec)
            elif may_have_occurred:
                rec.update(decision="KEPT_SEPARATE", code="UNCERTAIN_FIRST_DATE_MAY_HAVE_OCCURRED",
                           reason="the first date was predicted up to or after its own date, or has an outcome row: it may have been a real event")
                uncertain.append(rec)
            else:
                rec.update(decision="MERGED_KEEP_DATE_2", code="CONFIDENT_DATE_SHIFT",
                           reason="the first date was last predicted before it occurred, has no outcome row, and the second date's predictions start afterwards: the event moved")
                merges.append(rec)
                links.append((e1["date"], e2["date"]))
        merged_into = {}
        for a, b in links:
            merged_into[a] = b
        for e in evs:
            if e["date"] in merged_into:
                continue
            kept.append(dict(e, symbol=sym))
    return kept, merges, uncertain


def plan_table(days_counter):
    tot = sum(days_counter.values())
    out = {}
    if not tot:
        return out
    out["core_ceiling_grid"] = {}
    for c in CAP_GRID:
        req = [min(n, c) * CORE_PER_EVENT + min(n, FULL_CAP) * FULL_EXTRA + SHARED_PER_DAY for n in days_counter.values()]
        out["core_ceiling_grid"][str(c)] = {
            "core_coverage": round(sum(min(n, c) for n in days_counter.values()) / tot, 3),
            "days_above_ceiling": sum(1 for n in days_counter.values() if n > c),
            "peak_requests_day_with_full_30": max(req), "mean_requests_per_active_day_with_full_30": round(sum(req) / len(req), 1),
            "total_requests_in_window_with_full_30": sum(req)}
    out["full_capture_30_per_day"] = {"full_coverage": round(sum(min(n, FULL_CAP) for n in days_counter.values()) / tot, 3),
                                      "peak_requests_day_full_capture_only": max(min(n, FULL_CAP) * (CORE_PER_EVENT + FULL_EXTRA) + SHARED_PER_DAY for n in days_counter.values())}
    return out


def summarise(evs, today, label):
    past = [e for e in evs if e["date"] <= today]
    fut = [e for e in evs if e["date"] > today]
    s = {"label": label, "events": len(evs), "past_or_today": len(past), "future": len(fut), "symbols": len({e["symbol"] for e in evs}),
         "timing": dict(Counter(e["kind"] for e in evs))}
    d = Counter(e["date"] for e in evs)
    if d:
        top = d.most_common(10)
        s["event_date_range"] = [min(d).isoformat(), max(d).isoformat()]
        s["active_days"] = len(d)
        s["peak_day"] = {"date": top[0][0].isoformat(), "events": top[0][1]}
        s["top_10_days"] = [{"date": k.isoformat(), "events": v} for k, v in top]
        s["mean_events_per_active_day"] = round(len(evs) / len(d), 1)
        wk = defaultdict(Counter)
        for e in evs:
            y, w, _ = e["date"].isocalendar()
            wk["%d-W%02d" % (y, w)][e["kind"]] += 1
        s["weekly_distribution"] = {k: dict(v, total=sum(v.values())) for k, v in sorted(wk.items())}
        s["by_weekday"] = dict(Counter(e["date"].strftime("%a") for e in evs))
        s["max_events_in_any_14_calendar_days"] = max(sum(v for k, v in d.items() if a <= k < a + timedelta(days=14)) for a in d)
        s["plans_all_days"] = plan_table(d)
        s["plans_past_only"] = plan_table(Counter(e["date"] for e in past))
        s["plans_future_only"] = plan_table(Counter(e["date"] for e in fut))
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"C:\apps\us-stock-reader")
    ap.add_argument("--out-dir", default=r"C:\audit-reports\smart_move_counts")
    ap.add_argument("--today", default="")
    a = ap.parse_args()
    out_dir = Path(a.out_dir).resolve()
    root = Path(a.root).resolve()
    if root == out_dir or root in out_dir.parents:
        sys.exit("STOPPED: the output folder must not be inside the application folder")
    import psycopg
    try:
        conn = psycopg.connect(db_url(a.root), connect_timeout=15, prepare_threshold=None, autocommit=True)
    except Exception as e:  # never print the message: it may contain connection details
        sys.exit("STOPPED: could not connect to the database (%s)" % type(e).__name__)
    cur = conn.cursor()
    cur.execute("SET default_transaction_read_only = on")
    cur.execute("SET statement_timeout = '30s'")
    cur.execute("SET lock_timeout = '5s'")
    cur.execute("SHOW transaction_read_only")
    if cur.fetchone()[0] != "on":
        sys.exit("STOPPED: the session is not read-only")
    today = date.fromisoformat(a.today) if a.today else datetime.now(ZoneInfo("America/New_York")).date()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / ("event_counts_%s.json" % datetime.now().strftime("%Y%m%d-%H%M%S"))
    if target.exists():
        sys.exit("STOPPED: %s exists (never overwritten)" % target)

    def one(sql, *p):
        cur.execute(sql, p)
        r = cur.fetchone()
        return r[0] if r else None

    # ---- schema compatibility check (before any count)
    schema = {}
    for t, cols in list(REQUIRED.items()) + list(OPTIONAL.items()):
        cur.execute("select column_name from information_schema.columns where table_schema = 'public' and table_name = %s", (t,))
        have = {r[0] for r in cur.fetchall()}
        schema[t] = {"present": bool(have), "missing_columns": [c for c in cols if c not in have]}
    bad = {t: v for t, v in schema.items() if t in REQUIRED and (not v["present"] or v["missing_columns"])}
    if bad:
        sys.exit("STOPPED: schema differs from the expected one: " + json.dumps(bad))

    rep = {"version": VERSION, "taken_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "today_et": today.isoformat(), "schema_check": schema,
           "definitions": {"raw_event": "distinct (symbol, earnings_date) with a non-null date", "merge_rule": "merged only for CONFIDENT_DATE_SHIFT within %d days; all other close pairs stay separate and are listed" % CLOSE_DAYS,
                           "ALL": "symbols with any prediction row", "U878": "symbols with an event dated today or earlier (the earlier export's definition)",
                           "FUTURE": "symbols with an event after today",
                           "WARMED": "warmer queue = universe names reporting in the next 10 trading days (local_warm_state row 2): a rolling window of one day, NOT a season",
                           "request_model": {"core_per_event": CORE_PER_EVENT, "full_extra_per_event": FULL_EXTRA, "shared_per_day": SHARED_PER_DAY, "full_cap": FULL_CAP, "core_grid": list(CAP_GRID)}}}
    rep["rows"] = {"model_predictions": one("select count(*) from model_predictions"),
                   "null_earnings_date_rows": one("select count(*) from model_predictions where earnings_date is null"),
                   "symbols_any_row": one("select count(distinct symbol) from model_predictions"),
                   "symbols_only_null_dates": one("select count(*) from (select symbol from model_predictions group by symbol having count(earnings_date) = 0) x"),
                   "earnings_date_range": [str(one("select min(earnings_date) from model_predictions")), str(one("select max(earnings_date) from model_predictions"))],
                   "snapshot_date_range": [str(one("select min(snapshot_date) from model_predictions")), str(one("select max(snapshot_date) from model_predictions"))]}
    cur.execute("select model, count(*) filter (where earnings_date is null) from model_predictions group by model order by 1")
    rep["null_dates_by_model"] = {m: n for m, n in cur.fetchall() if n}
    cur.execute("""
        select symbol, earnings_date,
               (array_agg(report_timing order by predicted_at desc) filter (where report_timing is not null and report_timing <> ''))[1],
               min(snapshot_date), max(snapshot_date), count(distinct snapshot_date), count(distinct model)
        from model_predictions where earnings_date is not null group by symbol, earnings_date limit %d""" % (ROW_LIMIT + 1))
    fetched = cur.fetchall()
    if len(fetched) > ROW_LIMIT:
        sys.exit("STOPPED: more than %d distinct events: aborting (bounded run)" % ROW_LIMIT)
    raw_events = defaultdict(list)
    for sym, ed, lab, fs, ls, ns, nm in fetched:
        raw_events[sym].append({"date": ed, "kind": kind_of(lab), "first_snap": fs, "last_snap": ls, "n_snaps": ns, "n_models": nm})
    outcomes = set()
    if schema["earnings_event_outcomes"]["present"] and not schema["earnings_event_outcomes"]["missing_columns"]:
        cur.execute("select symbol, earnings_date from earnings_event_outcomes")
        outcomes = {(s, d) for s, d in cur.fetchall()}
    cal_rows = []
    if schema["earnings_calendar"]["present"] and not schema["earnings_calendar"]["missing_columns"]:
        cur.execute("select symbol, earnings_date, eps_actual is not null from earnings_calendar")
        cal_rows = cur.fetchall()
    raw_list = [dict(e, symbol=s) for s, evs in raw_events.items() for e in evs]
    kept, merges, uncertain = classify_pairs(raw_events, outcomes)
    rep["events"] = {"raw_distinct_symbol_date_pairs": len(raw_list), "deduplicated_events": len(kept), "merged_confident_date_shifts": len(merges),
                     "close_pairs_kept_separate_uncertain": len(uncertain), "symbols_with_a_close_pair": len({r["symbol"] for r in merges + uncertain}),
                     "note": "deduplicated_events is an upper bound: uncertain pairs are counted as two events"}
    rep["merges_all"] = merges
    rep["uncertain_pairs_all"] = uncertain
    uncertain_syms_future = {r["symbol"] for r in uncertain if r["date_2"] > today.isoformat()}
    u878 = {e["symbol"] for e in kept if e["date"] <= today}
    fut_syms = {e["symbol"] for e in kept if e["date"] > today}
    warmed = None
    try:
        cur.execute("select state from local_warm_state where id = 2")
        r = cur.fetchone()
        if r:
            st = json.loads(r[0]) if isinstance(r[0], str) else r[0]
            warmed = set((st.get("symbols") or {}).keys())
            rep["warmer_state_summary"] = {"total": st.get("total"), "complete": st.get("complete"), "symbols_listed": len(warmed), "day_id": st.get("day_id")}
    except Exception as e:  # recorded, not fatal
        rep["warmer_state_summary"] = {"error": type(e).__name__}
    rep["universes"] = {"ALL": rep["rows"]["symbols_any_row"], "U878": len(u878), "FUTURE": len(fut_syms), "U878_and_FUTURE": len(u878 & fut_syms),
                        "WARMED_queue": len(warmed) if warmed is not None else None,
                        "WARMED_in_U878": len(warmed & u878) if warmed is not None else None, "WARMED_with_events_in_data": len(warmed & {e["symbol"] for e in kept}) if warmed is not None else None}
    rep["summary_deduplicated_all_symbols"] = summarise(kept, today, "deduplicated events, all symbols")
    rep["summary_raw_all_symbols"] = summarise(raw_list, today, "raw distinct symbol/date pairs (no merging), all symbols")
    rep["summary_U878_symbols"] = summarise([e for e in kept if e["symbol"] in u878], today, "deduplicated events of the U878 symbols")
    if warmed is not None:
        rep["summary_WARMED_symbols"] = summarise([e for e in kept if e["symbol"] in warmed], today, "deduplicated events of the warmer-queue symbols (rolling window, not a season)")
    fut = [e for e in kept if e["date"] > today]
    past = [e for e in kept if e["date"] <= today]
    in_pair = {(r["symbol"], r["date_2"]) for r in merges + uncertain}
    proj = [e for e in fut if e["kind"] == "UNKNOWN" or (e["date"] - e["first_snap"]).days > CLOSE_DAYS or (e["symbol"], e["date"].isoformat()) in in_pair or (today - e["last_snap"]).days > 7]
    rep["quality"] = {
        "future_events": len(fut),
        "future_events_projected_or_unconfirmed_union": len(proj),
        "future_events_timing_unknown": sum(1 for e in fut if e["kind"] == "UNKNOWN"),
        "future_events_first_seen_more_than_45_days_ahead": sum(1 for e in fut if (e["date"] - e["first_snap"]).days > CLOSE_DAYS),
        "future_events_with_a_date_shift_or_uncertain_pair": sum(1 for e in fut if (e["symbol"], e["date"].isoformat()) in in_pair),
        "future_events_not_refreshed_for_7_days": sum(1 for e in fut if (today - e["last_snap"]).days > 7),
        "past_events_without_outcome_row_older_than_5_days": sum(1 for e in past if (today - e["date"]).days > 5 and (e["symbol"], e["date"]) not in outcomes) if schema["earnings_event_outcomes"]["present"] else None,
        "past_events_last_snapshot_more_than_7_days_before_event": sum(1 for e in past if (e["date"] - e["last_snap"]).days > 7),
        "stale_total_union": sum(1 for e in past if ((today - e["date"]).days > 5 and (e["symbol"], e["date"]) not in outcomes) or (e["date"] - e["last_snap"]).days > 7) + sum(1 for e in fut if (today - e["last_snap"]).days > 7),
        "earnings_calendar_rows": len(cal_rows),
        "earnings_calendar_past_rows_without_actual_older_than_3_days": sum(1 for s, d, has in cal_rows if d < today - timedelta(days=3) and not has),
        "events_timing_unknown_total": sum(1 for e in kept if e["kind"] == "UNKNOWN"),
        "projected_examples": [{"symbol": e["symbol"], "date": e["date"].isoformat(), "timing": e["kind"], "first_snapshot": e["first_snap"].isoformat(), "last_snapshot": e["last_snap"].isoformat()} for e in proj[:20]],
        "stale_examples": [{"symbol": e["symbol"], "date": e["date"].isoformat(), "last_snapshot": e["last_snap"].isoformat()} for e in past if (today - e["date"]).days > 5 and (e["symbol"], e["date"]) not in outcomes][:20]}
    target.write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    print("event counts saved:", target)
    print("rows:", json.dumps(rep["rows"]))
    print("events:", json.dumps(rep["events"]))
    print("universes:", json.dumps(rep["universes"]))
    for r in merges[:20]:
        print("  MERGE %s %s -> %s (%s)" % (r["symbol"], r["date_1"], r["date_2"], r["code"]))
    for r in uncertain[:20]:
        print("  UNCERTAIN %s %s / %s (%s)" % (r["symbol"], r["date_1"], r["date_2"], r["code"]))
    for k in ("summary_deduplicated_all_symbols", "summary_raw_all_symbols", "summary_U878_symbols", "summary_WARMED_symbols"):
        s = rep.get(k)
        if s:
            print("%-34s events %s (past %s, future %s) symbols %s timing %s peak day %s max-in-14-days %s" % (k, s["events"], s["past_or_today"], s["future"], s["symbols"], s["timing"], s.get("peak_day"), s.get("max_events_in_any_14_calendar_days")))
            g = (s.get("plans_all_days") or {}).get("core_ceiling_grid")
            if g:
                print("   coverage/peak requests by core ceiling (+30 full): " + "; ".join("%s: %s / %s" % (c, v["core_coverage"], v["peak_requests_day_with_full_30"]) for c, v in g.items()) +
                      " | full 30/day coverage %s" % s["plans_all_days"]["full_capture_30_per_day"]["full_coverage"])
    print("quality:", json.dumps({k: v for k, v in rep["quality"].items() if not k.endswith("examples")}))


if __name__ == "__main__":
    main()