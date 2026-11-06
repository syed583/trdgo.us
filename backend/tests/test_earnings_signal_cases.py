"""Every earnings model's FINAL signal at its spec boundaries, LOW_DATA and gates (Phase 1).

Runs gen_signal_cases.py (production score_one(), no DB/network) and checks the backend's
signal against the spec band written independently in that script. The browser side of the same
cases is frontend/scripts/check_signal_view.mjs."""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def test_backend_signals_match_spec(tmp_path):
    out = tmp_path / "cases.json"
    subprocess.run([sys.executable, str(HERE / "gen_signal_cases.py"), str(out)], check=True)
    cases = json.loads(out.read_text())
    assert len(cases) >= 100
    bad = [c for c in cases if c["expect_signal"] is not None and c["backend"]["signal"] != c["expect_signal"]]
    assert not bad, bad[:5]
