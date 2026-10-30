"""Freezes the Smart Move H1a pilot sample from the exported universe csv. Pure local computation: NO provider request, NO database.

    python freeze_sample.py --csv universe_XXXX.csv --out-dir <dir>

Pre-registered method (version smv-pilot-2, fixed here before any request):
  eligible  = symbols with n_graded >= 1 and a majority timing (n_bmo != n_amc); tie or none -> excluded 'MIXED_OR_NO_TIMING'
  stratum   = (majority timing BMO/AMC) x (liquidity tertile)
  liquidity = dollar volume avg_volume_20 * last_base_close when >= 80 % of eligible symbols have it ('DOLLAR_VOLUME'); otherwise the
              last_base_close tertile when >= 80 % have a price ('PRICE_PROXY', a weak proxy); otherwise 'NONE' (strata = timing only and the
              manifest says so; liquidity is then described afterwards from the pilot's own candle volumes, never used for selection);
              with a basis, symbols without the figure are put in the lowest tertile and flagged 'LIQ_UNKNOWN'
  order     = ascending SHA-256('smartmove-pilot-1' + symbol) inside a stratum
  quota     = 50 = 8 per stratum + 1 extra to each of the two strata with the most eligible symbols (ties by stratum name); a stratum with
              fewer symbols gives all of them and the shortfall goes to the other strata in name order, one at a time
The manifest (json) and its .sha256 are written once (never overwritten); the manifest carries the input csv SHA-256, every eligible symbol with
its stratum and hash, the selection, every exclusion with its reason, and the selection-bias notes.
"""
import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SEED = "smartmove-pilot-1"
METHOD = "smv-pilot-2"
N = 50


def h(sym):
    return hashlib.sha256((SEED + sym).encode("ascii")).hexdigest()


def tertile_cuts(values):
    v = sorted(values)
    return v[len(v) // 3], v[(2 * len(v)) // 3]


def build(rows):
    elig, excluded = [], []
    for r in rows:
        nb, na = int(r["n_bmo"] or 0), int(r["n_amc"] or 0)
        if nb == na:
            excluded.append({"symbol": r["symbol"], "reason": "MIXED_OR_NO_TIMING", "n_bmo": nb, "n_amc": na})
            continue
        r["timing"] = "BMO" if nb > na else "AMC"
        vol = float(r["avg_volume_20"]) if r["avg_volume_20"] not in ("", None) else None
        px = float(r["last_base_close"]) if r["last_base_close"] not in ("", None) else None
        r["dollar_volume"] = vol * px if vol is not None and px is not None else None
        r["price"] = px
        elig.append(r)
    have = sum(1 for r in elig if r["dollar_volume"] is not None)
    have_px = sum(1 for r in elig if r["price"] is not None)
    if elig and have / len(elig) >= 0.8:
        basis = "DOLLAR_VOLUME"
    elif elig and have_px / len(elig) >= 0.8:
        basis = "PRICE_PROXY"
    else:
        basis = "NONE"                      # no usable liquidity figure: strata are timing only
    key = "dollar_volume" if basis == "DOLLAR_VOLUME" else "price"
    known = [r[key] for r in elig if r[key] is not None] if basis != "NONE" else []
    lo, hi = tertile_cuts(known) if known else (0, 0)
    for r in elig:
        val = r[key]
        if basis == "NONE":
            r["flag"], r["tertile"] = "", "ALL"
        else:
            r["flag"] = "" if val is not None else "LIQ_UNKNOWN"
            r["tertile"] = "L1" if (val is None or val < lo) else ("L2" if val < hi else "L3")
        r["stratum"] = r["timing"] + "-" + r["tertile"]
        r["hash"] = h(r["symbol"])
    return elig, excluded, basis, (lo, hi)


def allocate(sizes):
    names = sorted(sizes)
    quota = {s: min(8, sizes[s]) for s in names}
    extra = sorted(names, key=lambda s: (-sizes[s], s))[:2]
    for s in extra:
        if quota[s] < sizes[s]:
            quota[s] += 1
    short = N - sum(quota.values())
    while short > 0:
        moved = False
        for s in names:
            if short > 0 and quota[s] < sizes[s]:
                quota[s] += 1
                short -= 1
                moved = True
        if not moved:
            break
    return quota


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--source-name", default="")
    ap.add_argument("--source-sha256", default="")
    ap.add_argument("--compact-sha256", default="")
    a = ap.parse_args()
    src = Path(a.csv)
    raw = src.read_bytes()
    with open(src, newline="", encoding="ascii") as fh:
        rows = list(csv.DictReader(fh))
    elig, excluded, basis, cuts = build(rows)
    by = {}
    for r in elig:
        by.setdefault(r["stratum"], []).append(r)
    for v in by.values():
        v.sort(key=lambda r: r["hash"])
    quota = allocate({s: len(v) for s, v in by.items()})
    picked = []
    for s in sorted(by):
        for rank, r in enumerate(by[s][:quota[s]], 1):
            picked.append({"symbol": r["symbol"], "stratum": s, "rank_in_stratum": rank, "hash": r["hash"], "flag": r["flag"], "n_graded": int(r["n_graded"])})
    picked.sort(key=lambda p: (p["stratum"], p["rank_in_stratum"]))
    notin = [r for s in by for r in by[s][quota[s]:]]
    manifest = {
        "method_version": METHOD, "seed": SEED, "target_n": N, "frozen_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input_csv": src.name, "input_csv_sha256": hashlib.sha256(raw).hexdigest(), "input_rows": len(rows),
        "source_file_name": a.source_name, "source_file_sha256_reported_by_vps": a.source_sha256, "compact_text_sha256_verified_locally": a.compact_sha256,
        "liquidity_basis": basis, "liquidity_cuts": list(cuts), "eligible": len(elig), "selected": len(picked),
        "stratum_sizes": {s: len(v) for s, v in sorted(by.items())}, "stratum_quota": quota,
        "selected_symbols": picked,
        "not_selected_eligible": sorted(r["symbol"] for r in notin),
        "excluded": excluded,
        "liq_unknown_selected": sum(1 for p in picked if p["flag"]),
        "selection_bias_notes": [
            "universe = symbols with at least one GRADED outcome, i.e. names the application predicted for between 2026-10-13 and the export date (one earnings season, about 2.5 weeks)",
            "symbols whose events are all PENDING_TIMING or whose timing is mixed are excluded; if missing timing is related to size or exchange this biases the sample",
            "grading runs alphabetically 120 symbols per cycle, so the graded universe can over-represent early letters until the backlog clears",
            "liquidity basis is %s; with PRICE_PROXY the tertiles measure share price, not trading volume" % basis,
            "ETFs and foreign private issuers are not removed here (no SEC data is read in H1a); they are identified and excluded at H1b and counted then",
            "the history window of the later events (since 2024-10-01) is chosen by the verified volatility-history depth, not by the application's own coverage",
        ]}
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    target = out / "h1a_manifest.json"
    if target.exists():
        sys.exit("STOPPED: %s exists (never overwritten)" % target)
    body = json.dumps(manifest, indent=1, sort_keys=True).encode("ascii")
    target.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    Path(str(target) + ".sha256").write_text("%s  h1a_manifest.json\n" % digest, encoding="ascii")
    print("manifest:", target)
    print("eligible", len(elig), "| selected", len(picked), "| excluded", len(excluded), "| basis", basis)
    print("strata:", json.dumps({s: [len(by[s]), quota[s]] for s in sorted(by)}))
    print("MANIFEST SHA-256:", digest)


if __name__ == "__main__":
    main()