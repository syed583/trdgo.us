import hashlib
import os
import subprocess

S = os.path.dirname(os.path.abspath(__file__))
WT = os.path.join(S, "wt_sig")
files = """claude_last_service.py claude_pro_service.py gemini_service.py grok_service.py chatgpt_service.py meta_service.py kimi_service.py pre_earnings_direction_service.py earnings_engine.py earnings_equity_service.py earnings_trade_service.py tab_settings.py
chatgpt_intraday_service.py claude_pro_intraday_service.py gemini_intraday_service.py intraday_grok_service.py kimi_intraday_service.py meta_intraday_service.py
intraday_model.py intraday_model_service.py intraday_signal_engine.py intraday_signal_v2.py intraday_indicators.py intraday_inputs.py intraday_enrich.py intraday_premarket.py
directional_model.py directional_signals.py directional_score_service.py signal_verification.py""".split()
pins = []
for f in files:
    b = subprocess.run(["git", "-C", WT, "show", f"38ca61d:backend/{f}"], capture_output=True).stdout.replace(b"\r\n", b"\n")
    assert b, f
    pins.append(f'    "{f}": "{hashlib.sha256(b).hexdigest()}",')
body = '''"""Guard: Phase 2A must not change any scoring model.

The SHA-256 of every earnings-model and intraday-model scoring module (line endings normalised) is pinned to
the production base commit 38ca61d. Changing a weight, formula, threshold or gate in any of them fails this
test, so an intentional scoring change has to update the pins deliberately and visibly.
"""
import hashlib
import pathlib

BASE_COMMIT = "38ca61df44029153e65c059d9bb2ce11210bb2bc"
PINS = {
''' + "\n".join(pins) + '''
}

BACKEND = pathlib.Path(__file__).resolve().parent.parent


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\\r\\n", b"\\n")).hexdigest()


def test_no_scoring_module_differs_from_the_production_base():
    changed = [name for name, want in PINS.items() if _sha(BACKEND / name) != want]
    assert not changed, f"scoring modules changed since {BASE_COMMIT[:7]}: {changed}"


def test_eight_earnings_models_and_intraday_models_are_all_covered():
    earnings = {"claude_last_service.py", "claude_pro_service.py", "gemini_service.py", "grok_service.py",
                "chatgpt_service.py", "meta_service.py", "kimi_service.py", "pre_earnings_direction_service.py"}
    intraday = {"chatgpt_intraday_service.py", "claude_pro_intraday_service.py", "gemini_intraday_service.py",
                "intraday_grok_service.py", "kimi_intraday_service.py", "meta_intraday_service.py"}
    assert earnings <= set(PINS) and intraday <= set(PINS) and "tab_settings.py" in PINS
    assert len(PINS) == 30
'''
open(os.path.join(WT, "backend", "tests", "test_scoring_unchanged.py"), "w", encoding="utf-8", newline="\n").write(body)
print(len(pins), "pins written")
