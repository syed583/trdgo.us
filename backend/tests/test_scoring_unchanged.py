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
    "tab_settings.py": "b45280fd7d4ff1226fdaee7db0459835d8baee98e8df332d93f0dcd1dd6cf4f9",
    "chatgpt_intraday_service.py": "3d9ec4dd2f16c0b36571686fad49451302760e379f5898a717425ba5bfa32bf5",
    "claude_pro_intraday_service.py": "14b76e081b4beaff3095413bec5a16d3054ab9ee5b60b10ff936a97f1ef7e0d0",
    "gemini_intraday_service.py": "53ebd1c0e7183b84087e2858e730720ad2d64c5ef75a2eef681ffdb24ca874d1",
    "intraday_grok_service.py": "fe3bbaeed69ffc7364e73562b192912c459faf4f4fdb31dad205912f5d5fb2a9",
    "kimi_intraday_service.py": "3323173a08432aec2bdc27b605b379a9e72f4094daae6deaaba62e0177be5baa",
    "meta_intraday_service.py": "907cb26865e76d5027cb43438558e67ad3ab692cd1047ddac0edb2a41546b064",
    "intraday_model.py": "a27a8f5cc0d98128ff21cdae5b1fb0148d005a3f3677e7054e978c64c5438058",
    "intraday_model_service.py": "0890228c60dccf7accdbcec66b83f2ab497c89c6564156551a44195bb413e92c",
    "intraday_signal_engine.py": "1b46f1830206f75d455b86ce8be91d1274d56e7088dc1285a01619d71f0bed87",
    "intraday_signal_v2.py": "2fd9899816d31fba09b4695c78aeca4aa0d65e3c358fbdd43f354575b7ffd41d",
    "intraday_indicators.py": "91c40a7ed0097dd2c26983dc56025367949d170fe28bef2387403b42c2a70fc8",
    "intraday_inputs.py": "50c6c2c693b0474a9ca71f5a59edc18aae3402b5c66689e56bb534dfbcf0952b",
    "intraday_enrich.py": "a759943c8f6c2876f1158ae990cd119134f5a81a66702526e2dda1b49c068d9d",
    "intraday_premarket.py": "d435fc307492e04829c01224184d9f297d855750874e3eb9b4c6f0879c56de55",
    "directional_model.py": "b0401ad55658a7cf04f78a57f86a5b6cc2d8f2eb5d72ea1b7c40f36f95bacdc6",
    "directional_signals.py": "8e2d2399f2f0c5e6bc47612740d22ed68a48860a3203ad98029e08e7d18cd3cb",
    "directional_score_service.py": "5451619653cdeb6d52c808a64d7f6b5df093c43d1040c39323773ffc7d2f374d",
    "signal_verification.py": "13496b90d59567138ac87c098ad5ab0d478d834093eceda8e65674346ab567ed",
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
    intraday = {"chatgpt_intraday_service.py", "claude_pro_intraday_service.py", "gemini_intraday_service.py",
                "intraday_grok_service.py", "kimi_intraday_service.py", "meta_intraday_service.py"}
    assert earnings <= set(PINS) and intraday <= set(PINS) and "tab_settings.py" in PINS
    assert len(PINS) == 30
