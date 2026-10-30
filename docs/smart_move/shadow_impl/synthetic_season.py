"""Synthetic shadow season: 28 invented events through the whole offline pipeline (calendar verification -> selection -> governor -> capture -> frozen scorer -> NO TRADE records).
Every number is invented; the output demonstrates BUY / SELL / NO TRADE mechanics and reason codes only and says nothing about accuracy.

    python synthetic_season.py [--out results]
"""
import argparse
import itertools
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE / "tests")]
from fixtures import FakeProvider, make_history, make_series, rows_from        # noqa: E402
from smvshadow import calendar_confirm as cc, governor as gv, scorer as sc      # noqa: E402
from smvshadow.pipeline import ShadowPipeline                                   # noqa: E402
from smvshadow.store import PayloadStore                                        # noqa: E402

UTC = timezone.utc
EVENT, START, TODAY = date(2026, 11, 5), datetime(2026, 11, 3, 11, 5, tzinfo=UTC), date(2026, 11, 3)
HIST = {"up": [5, 4, 6, 5, 4, 5, 6, 5], "down": [-5, -4, -6, -5, -4, -5, -6, -5], "mixed": [5, -5, 4, -4, 5, -5, 4, -4], "quiet": [0.3, -0.2, 0.4, -0.3, 0.2, -0.4, 0.3, -0.2]}
TREND = {"up": 0.35, "down": -0.35, "flat": 0.0}


def world(symbol, hist, trend, skew):
    rows, react = make_history(EVENT, "BMO", HIST[hist])
    start = date(2023, 10, 1)
    ser = make_series(symbol, start, date(2026, 11, 2), reactions=react, trend_from=date(2026, 10, 5) if trend != "flat" else None, trend_pct_per_day=TREND[trend])
    return {"rows": rows_from(ser), "history": rows, "skew": skew}


def build():
    combos = list(itertools.product(("up", "down", "mixed", "quiet"), ("up", "down", "flat"), ("call_rich", "put_rich", "flat")))
    syms = ["S%02d" % i for i in range(28)]
    spec = {}
    for i, s in enumerate(syms):
        h, t, k = combos[(i * 5) % len(combos)]
        spec[s] = (h, t, k)
    return spec


def observations(spec):
    log, meta = cc.ObservationLog(), {}
    def uw(sym, flag="company", label="premarket", age_h=1):
        row = {"symbol": sym, "report_date": EVENT.isoformat(), "report_time": label, "date_confirmed": True}
        if flag:
            row["source"] = flag
        return cc.from_uw_row(row, START - timedelta(hours=age_h), "u-" + sym)
    def fz(sym, raw="11/5/2026 7:00:00 AM", age_h=1):
        return cc.from_finviz(sym, raw, START - timedelta(hours=age_h), "f-" + sym)
    for i, s in enumerate(sorted(spec)):
        if i % 14 == 13:
            pass                                                           # no observation at all
        elif i % 14 == 12:
            log.append(uw(s, flag="estimation")); log.append(fz(s))        # source=estimation
        elif i % 14 == 11:
            log.append(uw(s))                                              # one vendor
        elif i % 14 == 10:
            log.append(uw(s)); log.append(fz(s, "11/5/2026 4:30:00 PM"))   # timing contradiction
        elif i % 14 == 9:
            log.append(uw(s, age_h=30)); log.append(fz(s, age_h=30))       # stale calendar
        else:
            log.append(uw(s)); log.append(fz(s))
        meta[s] = {"sector_etf": "XLK", "liquid": None, "prediction_snapshot_at": START - timedelta(days=(i % 9))}
    return log, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "results"))
    a = ap.parse_args()
    import tempfile
    spec = build()
    clock = gv.FakeClock(START)
    worlds = {"__default__": world("DEF", "quiet", "flat", "flat"), "SPY": world("SPY", "quiet", "up", "flat"), "XLK": world("XLK", "quiet", "up", "flat")}
    worlds.update({s: world(s, *v) for s, v in spec.items()})
    fetch = FakeProvider(worlds, clock.now)
    log, meta = observations(spec)
    pre, sha = sc.load_prereg()
    with tempfile.TemporaryDirectory() as tmp:
        out = ShadowPipeline(gv.Governor(clock, authorized_daily=1700), PayloadStore(tmp), fetch, pre, sha, run_id="00000000-0000-0000-0000-000000000001", seed="synthetic-season").run(meta, log, START, TODAY)
    rows = []
    for s in sorted(spec):
        ev = out["evaluations"][s]
        preds = {p["variant"]: p for p in out["predictions"] if p["symbol"] == s}
        r = {"symbol": s, "synthetic_inputs": dict(zip(("history", "trend", "skew"), spec[s])), "calendar_status": ev["status"], "eligible": ev["eligible_for_shadow"], "calendar_no_trade_reasons": ev["no_trade_reasons"]}
        for v in ("continuation", "contrarian"):
            p = preds.get(v)
            r[v] = {"signal": p["signal"], "bull": p["bull_points"], "bear": p["bear_points"], "available": p["weight_available"], "reasons": p["reason_codes"]} if p else {"signal": "NO_TRADE", "reasons": ev["no_trade_reasons"] or ["NOT_CAPTURED"]}
        rows.append(r)
    summ = {v: dict(Counter(r[v]["signal"] for r in rows)) for v in ("continuation", "contrarian")}
    reasons = dict(Counter(c for r in rows for v in ("continuation", "contrarian") for c in r[v]["reasons"]).most_common())
    res = {"note": "SYNTHETIC - invented data, mechanics only, no accuracy meaning", "prereg_sha256": sha, "events": len(rows), "calendar_status": dict(Counter(r["calendar_status"] for r in rows)),
           "signals": summ, "reason_code_counts": reasons, "requests_used": out["requests_used"], "plan_hash": out["plan"]["plan_hash"], "rows": rows}
    odir = Path(a.out)
    odir.mkdir(parents=True, exist_ok=True)
    (odir / "synthetic_season_results.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str), encoding="ascii")
    lines = ["# Synthetic season results (invented data; mechanics only, no accuracy meaning)", "", "prereg SHA-256 " + sha, "", "| symbol | history/trend/skew | calendar | continuation | contrarian | reasons (continuation) |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| %s | %s | %s | %s | %s | %s |" % (r["symbol"], "/".join(r["synthetic_inputs"].values()), r["calendar_status"], r["continuation"]["signal"], r["contrarian"]["signal"], ", ".join(r["continuation"]["reasons"])))
    lines += ["", "signals: " + json.dumps(summ), "", "reason codes: " + json.dumps(reasons)]
    (odir / "synthetic_season_results.md").write_text("\n".join(lines) + "\n", encoding="ascii")
    print(json.dumps({"events": len(rows), "calendar_status": res["calendar_status"], "signals": summ, "requests_used": res["requests_used"]}))


if __name__ == "__main__":
    main()
