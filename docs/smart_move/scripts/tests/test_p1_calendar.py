"""Offline checks of the P1 runner: manifest caps, ticker rule, refusal paths. No request is sent."""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import p1_calendar as p1

HERE = Path(__file__).resolve().parent


def test_manifest_caps_and_days():
    m = p1.manifest(date(2026, 10, 30))                                  # Friday: today and Monday
    assert m["trading_days"] == ["2026-10-30", "2026-11-02"] and m["max_possible_requests"] == 10
    assert [r["provider"] for r in m["requests"]].count("UNUSUAL_WHALES") == 4 and all(r["max_requests"] == 2 for r in m["requests"] if r["provider"] == "UNUSUAL_WHALES")


def test_ticker_rule_is_market_cap_only_and_bounded():
    rows = [{"symbol": "A%s" % chr(65 + i % 26), "marketcap": str(i)} for i in range(300)] + [{"symbol": "BAD1", "marketcap": "9"}, {"symbol": "NOCAP", "marketcap": None}]
    t = p1.pick_tickers(rows)
    assert len(t) <= 100 and "BAD1" not in t and "NOCAP" not in t and t == sorted(t, key=lambda s: (-max(float(r["marketcap"]) for r in rows if r["symbol"] == s), s))


def test_run_refuses_a_wrong_hash(tmp_path):
    m = tmp_path / "m.json"
    m.write_text(json.dumps(p1.manifest(date(2026, 10, 30))))
    r = subprocess.run([sys.executable, str(HERE.parent / "p1_calendar.py"), "run", "--manifest", str(m), "--manifest-sha256", "0" * 64], capture_output=True, text=True)
    assert r.returncode != 0 and "does not match" in r.stderr + r.stdout
