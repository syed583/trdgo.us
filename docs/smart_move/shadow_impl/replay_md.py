"""Turns results/real_replay/real_data_replay_dev_only.json into a readable, deterministic markdown summary (no outcomes in it)."""
import json
import sys
from collections import Counter
from pathlib import Path

src = Path(sys.argv[1] if len(sys.argv) > 1 else "results/real_replay/real_data_replay_dev_only.json")
d = json.loads(src.read_text())
L = ["# Replay of the frozen 40-point engines on saved development-period data", "",
     "Saved data only; no request; sealed validation/test untouched; no outcomes loaded; no accuracy statement. Baseline prereg %s; score40 prereg %s." % (d["baseline_prereg_sha256"], d["score40_prereg_sha256"]), "",
     "**This is a replay, not a shadow prediction**: the calendar status of the time (date and BMO/AMC as the vendors showed them 48 h before) was never saved, so the event date and label are taken as stored after the fact.", ""]
for key, title in (("A_provider_history", "A. Prior events from the saved provider history (as the frozen design assumes)"), ("B_sec_derived_prior_events", "B. Prior events derived from saved SEC item 2.02 filings (EXPLORATORY source; needs your approval before it counts)")):
    s, rows = d["summary_" + key], d["rows_" + key[0]]
    L += ["## " + title, "", "events in dev scope %d; replayed %d" % (s["events_in_dev_scope"], s["replayed"]), "",
          "| | BUY | SELL | NO TRADE |", "|---|---|---|---|"]
    for v in ("continuation", "contrarian"):
        c = s["signals_" + v]
        L.append("| %s | %d | %d | %d |" % (v, c.get("BUY", 0), c.get("SELL", 0), c.get("NO_TRADE", 0)))
    L += ["", "coverage (available points of 40): " + json.dumps(s["coverage_weight_available"]), "", "missing evidence: " + json.dumps(s["missing_evidence"]), "", "reason codes (per variant): " + json.dumps(s["reason_codes"]), "",
          "hindsight flags: " + json.dumps(s["hindsight_flags"]), ""]
    both = Counter((r["signal_continuation"], r["signal_contrarian"]) for r in rows)
    L += ["pair (continuation, contrarian): " + json.dumps({"%s/%s" % k: v for k, v in sorted(both.items())}), ""]
L += ["## Calendar row formats and vendor flags (labels/flags only)", "", "```", json.dumps(d["vendor_flag_formats"], indent=1), "```", "",
      "Unusual Whales label vs SEC filing window, dev window: " + json.dumps(d["label_vs_sec_filing_agreement_dev_window"]), "",
      "## Evidence classes", "", "| Evidence | Class | Used by the replay |", "|---|---|---|",
      "| daily candles up to the last completed session | PIT_BY_DATE (retrieved 2026-10-30; adjustment of old closes not documented) | F3 reactions, F5, F8 |",
      "| prior event dates and sides, provider history | HINDSIGHT-LABELLED (stored later; history starts 2024-10, older rows were dropped when saved) | A: too short for F3 |",
      "| prior event dates and sides, SEC 2.02 acceptance times | PIT (immutable filings); AMC only 'not contradicted' | B: F3 |",
      "| the event's own date and BMO/AMC label | HINDSIGHT (no 48 h calendar snapshot saved) | cutoff and window |",
      "| UW source flag on past rows | HINDSIGHT (retrieved after the event) | not used |",
      "| volatility context (F7) | MISSING_NOT_CAPTURED for the 50 symbols | none |",
      "| sector mapping per symbol | MISSING (not saved) | F8 sector part unavailable |",
      "| provider move fields, outcomes | never loaded | none |", ""]
out = src.with_suffix(".md")
out.write_text("\n".join(L) + "\n", encoding="ascii")
print(out)
