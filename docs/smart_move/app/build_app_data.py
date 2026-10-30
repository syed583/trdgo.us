"""Builds the Smart Move dashboard's evidence pack from the SAVED P1/P2 evidence and the existing replay/synthetic results. No request, no database.

    python build_app_data.py --p1-out <first run folder> --p1-out2 <continuation folder> --candles-dir <saved candle folder> --out-dir <new folder>

The pack holds only what the dashboard shows: parsed calendar observations (vendor, symbol, date, raw label/clock, raw vendor flag, retrieval time, SHA-256 of the raw
response), the SPY regular-session candles needed for F8, the development-period replay rows (no outcome fields), the synthetic season, and a meta file with the SHA-256 of
every pack file and of its inputs. The API verifies those hashes when it loads the pack.
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "shadow_impl"))
from smvshadow import calendar_confirm as cc  # noqa: E402


def sha(b):
    return hashlib.sha256(b).hexdigest()


def dump(path, obj):
    b = json.dumps(obj, indent=1, sort_keys=True, default=str).encode("ascii")
    path.write_bytes(b)
    return sha(b)


def observations(out1, out2):
    rows, sources = [], []
    for folder, results in ((out1, "p1_results.json"), (out2, "p1_continue_results.json")):
        for r in json.loads((Path(folder) / results).read_text(encoding="utf-8"))["results"]:
            if r.get("status") != 200:
                continue
            f = [p for p in sorted((Path(folder) / "raw").glob("*.gz")) if p.name.endswith("_" + r["id"] + ".gz")][0]
            body = gzip.open(f).read()
            at = r["retrieved_at_utc"]
            sources.append({"request_id": r["id"], "provider": r["provider"], "retrieved_at_utc": at, "raw_sha256": sha(body), "status": r["status"], "rows": r.get("rows")})
            if r["provider"] == "UNUSUAL_WHALES":
                for row in json.loads(body).get("data", []):
                    rows.append({"source": "UW_CALENDAR", "symbol": row["symbol"], "date": row["report_date"], "label": row.get("report_time"), "clock": None, "vendor_flag": row.get("source"),
                                 "company": row.get("full_name"), "sector": row.get("sector"), "marketcap": row.get("marketcap"), "observed_at": at, "raw_sha256": sha(body), "request_id": r["id"]})
            elif r["provider"] == "FINVIZ_ELITE":
                for line in list(csv.reader(io.StringIO(body.decode("utf-8", errors="replace"))))[1:]:
                    o = cc.from_finviz(line[0], line[1], datetime.fromisoformat(at), sha(body))
                    if o:
                        rows.append({"source": "FINVIZ", "symbol": o.symbol, "date": str(o.date), "label": None, "clock": o.clock, "vendor_flag": None, "company": None, "sector": None,
                                     "marketcap": None, "observed_at": at, "raw_sha256": sha(body), "request_id": r["id"]})
            else:
                for row in json.loads(body).get("earningsCalendar", []):
                    if row.get("symbol") and row.get("date"):
                        rows.append({"source": "FINNHUB", "symbol": str(row["symbol"]).upper(), "date": row["date"], "label": row.get("hour") or None, "clock": None, "vendor_flag": None,
                                     "company": None, "sector": None, "marketcap": None, "observed_at": at, "raw_sha256": sha(body), "request_id": r["id"]})
    return rows, sources


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--p1-out", required=True)
    ap.add_argument("--p1-out2", required=True)
    ap.add_argument("--candles-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    out = Path(a.out_dir)
    if (out / "meta.json").exists():
        sys.exit("STOPPED: pack exists (never overwritten)")
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    obs, srcs = observations(a.p1_out, a.p1_out2)
    files["calendar_observations.json"] = dump(out / "calendar_observations.json", {"observations": obs, "sources": srcs})
    spy_raw = (Path(a.candles_dir) / "SPY.json").read_bytes()
    spy = [r for r in json.loads(spy_raw) if str(r.get("market_time")) == "r" and str(r["date"])[:10] >= "2026-09-01"]      # only the recent sessions the current-calendar F8 needs
    files["spy_regular.json"] = dump(out / "spy_regular.json", {"source_file_sha256": sha(spy_raw), "rows": [{k: r[k] for k in ("date", "market_time", "open", "high", "low", "close", "volume")} for r in spy]})
    etfs = {}
    for etf in ("XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLB", "XLU", "XLRE", "XLC"):
        raw = (Path(a.candles_dir) / (etf + ".json")).read_bytes()
        etfs[etf] = {"source_file_sha256": sha(raw), "rows": [{k: r[k] for k in ("date", "market_time", "open", "high", "low", "close", "volume")} for r in json.loads(raw)
                                                                if str(r.get("market_time")) == "r" and str(r["date"])[:10] >= "2026-09-01"]}
    files["sector_etfs.json"] = dump(out / "sector_etfs.json", etfs)
    rp = json.loads((ROOT / "shadow_impl/results/real_replay2/replay2.json").read_text(encoding="ascii"))
    replay = {"note": rp["note"], "baseline_prereg_sha256": rp["baseline_prereg_sha256"], "score40_prereg_sha256": rp["score40_prereg_sha256"], "sec_priors": rp["sec_priors"], "experiments": {}}
    for k, e in rp["experiments"].items():
        replay["experiments"][k] = {"summary": e["summary"], "rows": e["rows"]}
    files["replay_dev.json"] = dump(out / "replay_dev.json", replay)
    syn = json.loads((ROOT / "shadow_impl/results/synthetic_season_results.json").read_text(encoding="ascii"))
    files["synthetic.json"] = dump(out / "synthetic.json", syn)
    meta = {"pack_version": 1, "files_sha256": files,
            "inputs_sha256": {"replay2.json": sha((ROOT / "shadow_impl/results/real_replay2/replay2.json").read_bytes()), "synthetic_season_results.json": sha((ROOT / "shadow_impl/results/synthetic_season_results.json").read_bytes()),
                              "SPY.json(candle file)": sha(spy_raw)},
            "baseline_prereg_sha256": rp["baseline_prereg_sha256"], "score40_prereg_sha256": rp["score40_prereg_sha256"],
            "note": "derived from saved evidence only; no outcome fields; nothing here was requested after 2026-10-30 13:08 UTC"}
    dump(out / "meta.json", meta)
    print("pack written:", out, {k: v[:12] for k, v in files.items()})


if __name__ == "__main__":
    main()
