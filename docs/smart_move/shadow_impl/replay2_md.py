"""Readable summary of results/real_replay2/replay2.json (no outcomes in it)."""
import json
import sys
from pathlib import Path

src = Path(sys.argv[1] if len(sys.argv) > 1 else "results/real_replay2/replay2.json")
d = json.loads(src.read_text())
L = ["# Updated development replay (saved data, valid evidence only) and the separate SEC-F3 experiment", "",
     "No request, no sealed data, no outcome loaded, no accuracy statement. This is a replay: the event's own date and label are taken as stored after the fact. Baseline prereg %s; score40 prereg %s." % (d["baseline_prereg_sha256"], d["score40_prereg_sha256"]), "",
     "SEC-derived prior events (EXPERIMENTAL; a separate experiment, not the pre-registered provider source): usable by tag %s; ambiguous and excluded %s." % (json.dumps(d["sec_priors"]["usable_by_tag"]), json.dumps(d["sec_priors"]["ambiguous_excluded_by_reason"])), ""]
for name, e in d["experiments"].items():
    s = e["summary"]
    L += ["## " + name, "", "events %d, replayed %d" % (s["events_in_dev_scope"], s["replayed"]), "", "| | BUY | SELL | NO TRADE |", "|---|---|---|---|"]
    for v in ("continuation", "contrarian"):
        c = s["signals_" + v]
        L.append("| %s | %d | %d | %d |" % (v, c.get("BUY", 0), c.get("SELL", 0), c.get("NO_TRADE", 0)))
    L += ["", "coverage (available points of 40): " + json.dumps(s["coverage_weight_available"]), "", "missing evidence: " + json.dumps(s["missing_evidence"]), "", "reason codes (per variant): " + json.dumps(s["reason_codes"]), ""]
out = src.with_suffix(".md")
out.write_text("\n".join(L) + "\n", encoding="ascii")
print(out)
