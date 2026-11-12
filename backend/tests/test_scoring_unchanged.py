"""Guard: Phase 2A must not change any scoring model.

The SHA-256 of every earnings-model and intraday-model scoring module (line endings normalised) is pinned to
the production base commit 38ca61d. Changing a weight, formula, threshold or gate in any of them fails this
test, so an intentional scoring change has to update the pins deliberately and visibly.
"""
import hashlib
import pathlib

BASE_COMMIT = "38ca61df44029153e65c059d9bb2ce11210bb2bc"
PINS = {
    "claude_last_service.py": "57d0c31371fd4f974ada189a7919038297892edde11660dd44b05e8beafc575b",
    "claude_pro_service.py": "74251ed2388ea8777748c8cdcaef52ea50db1668143ab5714ac3f13b04353448",
    "gemini_service.py": "1f510bb17646630114e4fcee6be37c36c0aa94128be6635e7e8600a4688ee9f3",
    "grok_service.py": "12d6a57a9b72e04d97ffa052f1f4f060e0b090830504af76a95e29e932390867",
    "chatgpt_service.py": "87b0f577b23262913202966723096058c77ade2393efb0855d815e5897ff0afc",
    "meta_service.py": "abad7bd4bcfdc2970dd8c665db153a6e638342c2733412965445142a832e07a4",
    "kimi_service.py": "81f9a81fcf14d4f28104bad71256d1f2152dbf1ef84807a5a0219e73ed4f8838",
    "pre_earnings_direction_service.py": "eef1ee9c2d8026850376a8014196c6f556eb7f2123e404c57653bf895368518f",
    "earnings_engine.py": "d2da225e84ecae07e3da3429639828e38fa7b87db033b067d45e22cdc1cd9039",
    "earnings_equity_service.py": "516f87dbdca1acf76e7cdeb08053295f17231f6fee85ee908054ac93527d23f3",
    "earnings_trade_service.py": "32e7db8f5a2ac1719764884f2390af9d9c0cb74500aa159de2fbfbad920cd931",
    "tab_settings.py": "bb59d2e49f214eadc9aee0b2860b8c933d656c91d2d2f2564f9e13e6ab32d3f9",   # re-pinned 2026-11-12 (A.21): PAPER_EXECUTION.max_stop_pct 2.5 (stop cap, owner decision); no weight or threshold
    "gemini_intraday_service.py": "5b0ba43b68b60ef58de116123e4497924046e5639e0ddaced1685566b84cac20",
    "intraday_grok_service.py": "f23e8626543a3764bba25a56e5b988792b6b96c4ee6391fda463764f604ae6dc",
    "kimi_intraday_service.py": "03ad3db52a75e7fb193a9226b68ceba06c41926391ad830387b21a56fc4a2e90",
    "meta_intraday_service.py": "afae7e3c83b62de3324f58eb48feaed900ed71bf08e6906e82e5feb362833740",
    "intraday_model.py": "4b5cf66d5b691405b42250b7129dd65cca86d91751ebba5306b72d890a31f77a",
    "intraday_model_service.py": "0890228c60dccf7accdbcec66b83f2ab497c89c6564156551a44195bb413e92c",
    "intraday_signal_v2.py": "dfc6065ef70176ed5219d70b9f09063f8081fadfaa9541f68ff36a05a2337492",
    "intraday_indicators.py": "91c40a7ed0097dd2c26983dc56025367949d170fe28bef2387403b42c2a70fc8",
    "intraday_inputs.py": "50c6c2c693b0474a9ca71f5a59edc18aae3402b5c66689e56bb534dfbcf0952b",
    "intraday_enrich.py": "a759943c8f6c2876f1158ae990cd119134f5a81a66702526e2dda1b49c068d9d",
    "intraday_premarket.py": "d435fc307492e04829c01224184d9f297d855750874e3eb9b4c6f0879c56de55",
    "directional_model.py": "b0401ad55658a7cf04f78a57f86a5b6cc2d8f2eb5d72ea1b7c40f36f95bacdc6",
    "directional_signals.py": "8e2d2399f2f0c5e6bc47612740d22ed68a48860a3203ad98029e08e7d18cd3cb",
    "directional_score_service.py": "5451619653cdeb6d52c808a64d7f6b5df093c43d1040c39323773ffc7d2f374d",
    "signal_verification.py": "500780848ded88fb7455b6a1e1af2ddd89af24c89cee04705ea7c4c8c06495fd",
}

BACKEND = pathlib.Path(__file__).resolve().parent.parent


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_no_scoring_module_differs_from_the_production_base():
    changed = [name for name, want in PINS.items() if _sha(BACKEND / name) != want]
    assert not changed, f"scoring modules changed since {BASE_COMMIT[:7]}: {changed}"


def test_eight_earnings_models_and_intraday_models_are_all_covered():
    earnings = {"claude_last_service.py", "claude_pro_service.py", "gemini_service.py", "grok_service.py",
                "chatgpt_service.py", "meta_service.py", "kimi_service.py", "pre_earnings_direction_service.py"}
    # the ChatGPT and Claude Pro intraday boards were retired (2026-11-06): four intraday boards remain
    intraday = {"gemini_intraday_service.py", "intraday_grok_service.py", "kimi_intraday_service.py", "meta_intraday_service.py"}
    assert earnings <= set(PINS) and intraday <= set(PINS) and "tab_settings.py" in PINS
    assert len(PINS) == 27
