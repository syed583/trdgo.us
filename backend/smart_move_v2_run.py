"""Applies the EXPERIMENTAL V2 model to the saved evidence of an existing run folder. Local only: it works on a COPY of the run's store in a temp folder (the V1 run is never
written), sends no request, and writes its result to a new file in a separate V2 folder. Factors whose data is not in the saved evidence (analyst actions, options flow,
short/institutional, news) are reported missing, never filled in.

    python smart_move_v2_run.py --run-dir <run folder> --out <new .json file> [--as-of ISO]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "docs" / "smart_move" / "shadow_impl"))
import smart_move_live as L  # noqa: E402
import smart_move_v2 as V2  # noqa: E402
import smart_move_v2_collect as V2C  # noqa: E402
from smvshadow import adapters as ad  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

UTC = timezone.utc


def _close_series(rows):
    return {d: v[3] for d, v in ad.regular_series(rows).items()}


def run(run_dir, as_of: datetime, *, extra: dict | None = None, v2_store=None, store_dir=None, v1_report=None) -> dict:
    """extra: optional per-symbol inputs (the dict shape of smart_move_v2_collect.load_inputs); v2_store: a LiveStore holding saved V2 evidence, read AS OF each stock's capture time."""
    src = Path(store_dir) if store_dir else Path(run_dir) / "store"            # store_dir: a store with the REFRESHED calendar and the V2 evidence (the V2 collection run's store)
    v1_orig = {}
    rp = Path(v1_report) if v1_report else Path(run_dir) / "audit_report.json"
    if rp.exists():
        v1_orig = {r["symbol"]: r for r in json.loads(rp.read_text(encoding="utf-8")).get("results", [])}
    tmp = Path(tempfile.mkdtemp(prefix="sm_v2_"))
    try:
        shutil.copytree(src, tmp / "store")
        (tmp / "store" / "KILL").write_text("v2 read-only copy") if not (tmp / "store" / "KILL").exists() else None
        cfg = L.LiveConfig(mode="LIVE", store_dir=tmp / "store", refresh_enabled=False, authorized_daily=0, spacing_s=2.0)
        svc = L.RefreshService(cfg, None, gv.FakeClock(as_of))
        scorer = L.LiveScorer(svc)
        rows = scorer.score_all(as_of, persist=False)
        out = []
        for r in rows:
            if not r.get("capture_ts") or not r.get("earnings_date"):
                continue
            sym, ed = r["symbol"], r["earnings_date"]
            earn = svc._latest("earnings", sym, {"limit": 24})
            erows = scorer._rows_of(earn)[0] if earn else None
            own = svc._latest("ohlc", sym, {"timeframe": "3Y"})
            spy = svc._latest("ohlc", "SPY", {"timeframe": "3Y"})
            etf_sym = L.SECTOR_ETF.get(r.get("sector"))
            etf = svc._latest("ohlc", etf_sym, {"timeframe": "3Y"}) if etf_sym else None
            s_own, s_spy = (_close_series(scorer._rows_of(own)[0]) if own else {}), (_close_series(scorer._rows_of(spy)[0]) if spy else {})
            s_etf = _close_series(scorer._rows_of(etf)[0]) if etf else None
            last = max((d for d in s_own if d <= date.fromisoformat(r["capture_ts"][:10])), default=None)
            ex = (extra or {}).get(sym) or (V2C.load_inputs(v2_store, sym, datetime.fromisoformat(r["capture_ts"]), ending_quarter=__import__("smart_move_v2_snapshots").ending_quarter(erows, ed)) if v2_store is not None else {})
            cap_dt = datetime.fromisoformat(r["capture_ts"])
            gates = [c for c in r["engine"]["continuation"]["reason_codes"] if c in ("OUT_OF_WINDOW", "UNKNOWN_TIMING", "TIMING_CONTRADICTED", "NOT_TRADABLE", "ILLIQUID_OR_UNKNOWN", "STALE_EVIDENCE", "BLOCKED_BY_GATE", "SUSPECT_WINDOW")]
            res = {}
            for v in ("continuation", "contrarian"):
                facs = V2.from_v1(r["subscores"][v])
                facs.append(V2.implied_vs_hist(erows, ed))
                facs.append(V2.rel_strength(s_own, s_spy, s_etf, last, v) if last else V2._factor("REL_STRENGTH", 0, 0, False, reason="NO_PRICE_HISTORY"))
                facs.append(V2.analyst(ex.get("est_snapshots"), ex.get("analyst_actions"), cap_dt, v))
                facs.append(V2.options_flow(ex.get("flow"), ed, cap_dt, v))
                facs.append(V2.short_inst(ex.get("short_snaps"), ex.get("inst"), cap_dt, v))
                facs.append(V2.news_guidance(ex.get("news"), (r.get("plan") or {}).get("assumed_release_utc") and datetime.fromisoformat(r["plan"]["assumed_release_utc"]) or None, cap_dt, v))
                res[v] = V2.score(facs, v1_gates=gates, variant=v)
            o = v1_orig.get(sym)
            out.append({"v1_original": {"capture_ts": o["capture_ts"], "continuation": {k: o["continuation"][k] for k in ("signal", "bull", "bear", "available")}, "contrarian": {k: o["contrarian"][k] for k in ("signal", "bull", "bear", "available")}} if o else None,
                        "symbol": sym, "earnings_date": ed, "kind": r.get("kind"), "capture_ts": r["capture_ts"],
                        "eligibility": {"liquidity_ok": "ILLIQUID_OR_UNKNOWN" not in gates, "entry_ok": "NOT_TRADABLE" not in gates, "capture_window_ok": "OUT_OF_WINDOW" not in gates and "UNKNOWN_TIMING" not in gates, "v1_gates": gates}, "v1": {v: {k: r["engine"][v][k] for k in ("signal", "bull", "bear", "available")} for v in r["engine"]}, "v2": res,
                        "evidence": [{k: e[k] for k in ("kind", "symbol", "retrieved_at", "raw_sha256")} for e in r["evidence"]]})
        body = {"model": "V2", "version": V2.VERSION, "status": V2.STATUS, "config_hash": V2.CONFIG_HASH, "source_run": Path(run_dir).name, "as_of": as_of.isoformat(), "events": out, "simulation": False}
        body["audit_hash"] = hashlib_sha(json.dumps({k: body[k] for k in ("version", "config_hash", "source_run", "events")}, sort_keys=True, default=str))
        return body
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def hashlib_sha(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--as-of")
    ap.add_argument("--store-dir", help="store holding the refreshed calendar and the V2 evidence (defaults to <run-dir>/store)")
    ap.add_argument("--v1-report", help="the original V1 audit_report.json (defaults to <run-dir>/audit_report.json)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    if out.exists():
        print("STOPPED: output exists; never overwritten")
        return 3
    rep = json.loads((Path(a.run_dir) / "audit_report.json").read_text(encoding="utf-8"))
    as_of = datetime.fromisoformat(a.as_of) if a.as_of else max(datetime.fromisoformat(r["capture_ts"]) for r in rep["results"]) + __import__("datetime").timedelta(seconds=60)
    v2s = None
    if a.store_dir:
        import smart_move_live as L2
        v2s = L2.LiveStore(a.store_dir)
    body = run(a.run_dir, as_of, v2_store=v2s, store_dir=a.store_dir, v1_report=a.v1_report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(body, default=str, indent=1), encoding="utf-8")
    print("wrote", out, "events", len(body["events"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
