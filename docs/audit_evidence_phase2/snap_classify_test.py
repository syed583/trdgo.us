import os, sys
os.environ["APP_ENV"] = "development"
sys.path.insert(0, r"C:\Users\initi\Downloads\US-STOCKS\backend")
import database as d
assert d.PROD_REF not in (d.DATABASE_URL or "")
sys.path.insert(0, r"wt_sig\backend\tools")
import verify_earnings_readonly as v
# (label, earnings_date, timing, predicted_at UTC, expected class)
C = [
 ("BMO 05:00 EDT same day",      "2026-10-28","Before Open","2026-10-28 09:00:00+00","BEFORE_RELEASE"),
 ("BMO 07:00 EDT same day",      "2026-10-28","Before Open","2026-10-28 11:00:00+00","AMBIGUOUS"),
 ("BMO 10:00 EDT same day",      "2026-10-28","Before Open","2026-10-28 14:00:00+00","AFTER_RELEASE"),
 ("AMC 14:20 EDT same day",      "2026-10-28","After Close","2026-10-28 18:20:00+00","BEFORE_RELEASE"),
 ("AMC 15:55 EDT same day",      "2026-10-28","After Close","2026-10-28 19:55:00+00","BEFORE_RELEASE"),
 ("AMC 16:55 EDT same day",      "2026-10-28","After Close","2026-10-28 20:55:00+00","AFTER_RELEASE"),
 ("prior day",                   "2026-10-28","After Close","2026-10-27 15:00:00+00","BEFORE_RELEASE"),
 ("day after",                   "2026-10-28","Before Open","2026-10-29 15:00:00+00","AFTER_RELEASE"),
 ("UTC midnight stamp (backfill)","2026-10-28","Before Open","2026-10-27 00:00:00+00","RETROSPECTIVE"),
 ("EST BMO 05:55 (EDT would be 06:55)","2026-11-02","Before Open","2026-11-02 10:55:00+00","BEFORE_RELEASE"),
 ("EST BMO 06:05",               "2026-11-02","Before Open","2026-11-02 11:05:00+00","AMBIGUOUS"),
 ("EST AMC 15:55 (EDT would be 16:55)","2026-11-02","After Close","2026-11-02 20:55:00+00","BEFORE_RELEASE"),
 ("EST AMC 16:05",               "2026-11-02","After Close","2026-11-02 21:05:00+00","AFTER_RELEASE"),
 ("late-evening ET prior day vs CET date","2026-10-29","Before Open","2026-10-29 03:30:00+00","BEFORE_RELEASE"),
 ("timing unknown same day",     "2026-10-28",None,"2026-10-28 12:00:00+00","AMBIGUOUS"),
]
vals = ",".join(f"({i},'M','{l}','{e}'::date,{('NULL' if t is None else repr(t))},'{p}'::timestamptz,'{p}'::timestamptz,'BUY',50.0,NULL::timestamptz,NULL::smallint,NULL::float,'{p}'::date)" for i,(l,e,t,p,x) in enumerate(C))
src = f"FROM (VALUES {vals}) AS t(id, model, symbol, earnings_date, report_timing, predicted_at, ts2, signal, score, evaluated_at, correct, actual_move_pct, snapshot_date)"
q = v._CLASSIFIED.replace("FROM model_predictions WHERE earnings_date IS NOT NULL", src + " WHERE earnings_date IS NOT NULL") + "SELECT symbol, cls, to_char(pa_et,'YYYY-MM-DD HH24:MI') et FROM c"
bad = 0
res = {r["symbol"]: r for r in v._run_ro(q)}
for (l,e,t,p,x) in C:
    r = res[l]
    ok = r["cls"] == x; bad += (not ok)
    print(f"{'PASS' if ok else 'FAIL'}  {l:42s} ET {r['et']}  -> {r['cls']}")
print("failures:", bad)
