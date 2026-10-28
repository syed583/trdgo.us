"""Phase 1 test generator: runs each earnings model's PRODUCTION score_one() at boundary
scores, LOW_DATA coverage and gate conditions, and writes the backend's real output
(signal / tier / raw_signal / im_gate) next to the label the SPEC says it must be.
Read-only: no DB, no network. Usage: python -I gen_signal_cases.py <out.json>"""
import json
import os
import sys

for k in list(os.environ):
    if k.startswith(("DATABASE", "UW_", "FINNHUB", "ANTHROPIC", "SUPABASE")):
        os.environ.pop(k)
os.environ["APP_ENV"] = "development"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import claude_last_service as cl
import claude_pro_service as cp
import chatgpt_service as cg
import gemini_service as gm
import grok_service as gk
import kimi_service as ki
import meta_service as mt
import pre_earnings_direction_service as ped

cl._spy_15d_return = lambda: 0.01
ORIG = cl.finalize
NAMES = ["eps_estimates", "revenue_estimates", "guidance", "historical_reaction", "price_action", "sector_market",
         "options_positioning", "unusual_options", "disparity", "dark_pool", "peer_readthrough", "short_insider"]


def mk_eq(b):
    sigs = {k: v for k, v in b.items() if k in ("price_action", "ema_trend", "options_flow", "unusual_activity",
                                                "disparity", "oi_positioning", "insider_activity")}
    return {"params": [{"name": n, "bias": b.get(n)} for n in NAMES], "signals": sigs, "short_interest": None}


BULL = {n: .8 for n in NAMES}
BULL.update(options_flow=.8, unusual_activity=.8, disparity=.8, oi_positioning=.8, insider_activity=.8, ema_trend=.8)
BEAR = {k: -.8 for k in BULL}
ROW = {"date": "2026-11-05", "report_time": "amc"}
CALM = {"historical_moves": [{"move_pct": 3.0}] * 8, "spark": [100.0] * 30, "expected_move": {"percent": 1.0}}


def run(mod, eq, trade, score, cov):
    def fin(leaves, *a, **k):
        for p in leaves:
            p.setdefault("base_max", p.get("max"))
        return (score, cov)
    cl.finalize = fin
    try:
        if mod in (gk, cg):
            return mod.score_one("T", eq, trade, ROW, with_analyst=False)
        if mod in (ki, cp):
            return mod.score_one("T", eq, trade, ROW, with_analyst=False, mkt=0.2)
        if mod is gm:
            return mod.score_one("T", eq, trade, ROW, hit_rate=0.4)
        return mod.score_one("T", eq, trade, ROW)
    finally:
        cl.finalize = ORIG


# --- the SPEC label for a score (independent of the code under test) ---
def spec_claude(s):
    return "BUY" if s > 70 else "SELL" if s < 50 else "NEUTRAL"


def spec_55_45(s):
    return "BUY" if s >= 55 else "SELL" if s <= 45 else "NEUTRAL"


def spec_gemini(s):
    return ("STRONG BUY" if s >= 68 else "SPECULATIVE BUY" if s >= 58 else "STRONG SHORT" if s <= 32
            else "SPECULATIVE SHORT" if s <= 42 else "NEUTRAL")


def spec_chatgpt(s):
    return "STRONG BUY" if s >= 80 else "BUY" if s >= 65 else "NEUTRAL" if s >= 40 else "SELL" if s >= 21 else "STRONG SELL"


def spec_kimi(s):
    return ("STRONG BUY" if s >= 65 else "BUY" if s >= 58 else "STRONG SELL" if s <= 35 else "SELL" if s <= 42 else "NEUTRAL")


def spec_ped(s):
    return "BUY" if s >= 65 else "SELL" if s <= 35 else "NEUTRAL"


MODELS = [  # id, module, spec, boundary scores, bullish-capable eq
    ("claude", cl, spec_claude, [0, 49.9, 50.0, 50.1, 69.9, 70.0, 70.1, 100]),
    ("claude_pro", cp, spec_55_45, [0, 44.9, 45.0, 45.1, 54.9, 55.0, 55.1, 100]),
    ("gemini", gm, spec_gemini, [0, 31.9, 32.0, 32.1, 41.9, 42.0, 42.1, 57.9, 58.0, 58.1, 67.9, 68.0, 68.1, 100]),
    ("grok", gk, spec_55_45, [0, 44.9, 45.0, 45.1, 54.9, 55.0, 55.1, 100]),
    ("chatgpt", cg, spec_chatgpt, [0, 20.9, 21.0, 21.1, 39.9, 40.0, 40.1, 64.9, 65.0, 65.1, 79.9, 80.0, 80.1, 100]),
    ("meta", mt, spec_55_45, [0, 44.9, 45.0, 45.1, 54.9, 55.0, 55.1, 100]),
    ("kimi", ki, spec_kimi, [0, 34.9, 35.0, 35.1, 41.9, 42.0, 42.1, 57.9, 58.0, 58.1, 64.9, 65.0, 65.1, 100]),
    ("ped", ped, spec_ped, [0, 34.9, 35.0, 35.1, 64.9, 65.0, 65.1, 100]),
]

cases = []


def add(model, case, score, cov, r, expect_signal, expect_label):
    cases.append({"model": model, "case": case, "score": score, "coverage": cov,
                  "expect_signal": expect_signal, "expect_label": expect_label,
                  "backend": {k: r.get(k) for k in ("signal", "tier", "raw_signal", "im_gate", "priced_in")}})


for mid, mod, spec, scores in MODELS:
    for s in scores:
        # Kimi's confirmation rule would hide a lone bullish/bearish tier: feed it aligned evidence.
        eq = mk_eq(BEAR if spec(s) in ("SELL", "STRONG SELL", "STRONG SHORT", "SPECULATIVE SHORT") else BULL)
        r = run(mod, eq, CALM, s, 100.0)
        sig = spec(s)
        exp_sig = "BUY" if "BUY" in sig else "SELL" if ("SELL" in sig or "SHORT" in sig) else "NEUTRAL"
        add(mid, "boundary", s, 100.0, r, exp_sig, sig)
    # LOW_DATA: coverage just under 70 must replace any call, 70.0 must not.
    r = run(mod, mk_eq(BULL), CALM, 80.0, 69.9)
    add(mid, "low_data_69.9", 80.0, 69.9, r, "LOW_DATA", "LOW DATA")
    r = run(mod, mk_eq(BULL), CALM, 80.0, 70.0)
    add(mid, "coverage_70.0_scored", 80.0, 70.0, r, "BUY", spec(80.0))

# Missing data through the REAL finalize (nothing patched): no inputs at all must not raise a call.
for mid, mod, spec, _ in MODELS:
    try:
        r = (mod.score_one("T", mk_eq({n: None for n in NAMES}), CALM, ROW, with_analyst=False)
             if mod in (gk, cg) else
             mod.score_one("T", mk_eq({n: None for n in NAMES}), CALM, ROW, hit_rate=None) if mod is gm else
             mod.score_one("T", mk_eq({n: None for n in NAMES}), CALM, ROW, with_analyst=False, mkt=None) if mod in (ki, cp) else
             mod.score_one("T", mk_eq({n: None for n in NAMES}), CALM, ROW))
        add(mid, "real_no_data", r.get("score"), r.get("coverage"), r, None, None)
    except ValueError:
        cases.append({"model": mid, "case": "real_no_data_raises_pending", "score": None, "coverage": 0,
                      "expect_signal": "PENDING", "expect_label": "--",
                      "backend": {"signal": "PENDING", "tier": None, "raw_signal": None, "im_gate": None, "priced_in": None}})

# Gates ---------------------------------------------------------------
# Claude implied-move gate: score 75 would be BUY; ratio > 1.5 forces NEUTRAL.
rich = {"historical_moves": [{"move_pct": 4.0}] * 8, "spark": [100.0] * 30, "expected_move": {"percent": 6.01}}
add("claude", "gate_no_trade_ratio_gt_1.5", 75.0, 100.0, run(cl, mk_eq(BULL), rich, 75.0, 100.0), "NEUTRAL", "NEUTRAL")
half = {"historical_moves": [{"move_pct": 4.0}] * 8, "spark": [100.0] * 30, "expected_move": {"percent": 6.0}}
add("claude", "gate_half_size_still_BUY", 75.0, 100.0, run(cl, mk_eq(BULL), half, 75.0, 100.0), "BUY", "BUY")
priced = {"historical_moves": [{"move_pct": 4.0}] * 8, "spark": [100.0] * 19 + [100.0 + i for i in range(11)],
          "expected_move": {"percent": 2.0}}
add("claude", "gate_priced_in", 75.0, 100.0, run(cl, mk_eq(BULL), priced, 75.0, 100.0), "NEUTRAL", "NEUTRAL")
# Kimi confirmation rule: bullish score but options group opposes -> NEUTRAL.
opp = dict(BULL, disparity=-.8, unusual_activity=-.8, oi_positioning=-.8)
add("kimi", "bucket_rule_options_opposes", 60.0, 100.0, run(ki, mk_eq(opp), CALM, 60.0, 100.0), "NEUTRAL", "NEUTRAL")
# Gemini speculative-BUY needs the IV filter: rich implied move -> NEUTRAL.
add("gemini", "iv_filter_blocks_spec_buy", 60.0, 100.0, run(gm, mk_eq(BULL), rich, 60.0, 100.0), "NEUTRAL", "NEUTRAL")

json.dump(cases, open(sys.argv[1], "w"), indent=1)
print(f"{len(cases)} backend cases written")
