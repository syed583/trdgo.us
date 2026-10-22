"""Audit tests for Track B — the PARTIAL-FACTOR historical backtest.

Pure-logic tests: no network, no DB. They assert the reconstruction is
partial-factor (options/estimates absent), look-ahead safe, that the portfolio
drawdown does not double-count duplicated events, and that costs/signs are right.
"""
import historical_backtest_service as htb


def test_reconstruct_is_partial_factor():
    # Only price-action + prior-beats may be present; options/estimates stay None.
    closes = [100 + i * 0.1 for i in range(60)]
    eq, trade = htb._reconstruct("TEST", closes, er_bias=0.5)
    biases = {p["name"]: p["bias"] for p in eq["params"]}
    assert biases["options_positioning"] is None
    assert biases["unusual_options"] is None
    assert biases["eps_estimates"] is None
    assert biases["revenue_estimates"] is None
    assert biases["disparity"] is None
    assert biases["guidance"] == 0.5                     # prior-beats carried as guidance
    assert eq["signals"]["options_flow"] is None
    assert trade["expected_move"]["percent"] is None     # IV / expected move absent


def test_beats_bias_lookahead_safe():
    eh = [
        {"report_date": "2025-01-10", "estimated_eps": "1.0", "reported_eps": "1.2"},  # beat
        {"report_date": "2025-04-10", "estimated_eps": "1.0", "reported_eps": "0.8"},  # miss
        {"report_date": "2025-07-10", "estimated_eps": "1.0", "reported_eps": "1.1"},  # beat (future)
    ]
    # Evaluating before 2025-07-01 must NOT see the 2025-07-10 report.
    b = htb._beats_bias(eh, "2025-07-01")
    # only the first two count: 1 beat / 2 -> (0.5-0.5)*2 = 0.0
    assert b == 0.0


def test_cost_pct_sell_includes_borrow():
    long_cost = htb._cost_pct("BUY", 100.0, held=3)
    short_cost = htb._cost_pct("SELL", 100.0, held=3)
    assert short_cost > long_cost                        # borrow added for SELL only


def test_daily_rets_sign():
    closes = [100.0, 101.0, 102.0, 103.0]
    opens = [100.0, 101.0, 102.0, 103.0]
    buy = htb._daily_rets(closes, opens, 0, 3, "BUY")
    sell = htb._daily_rets(closes, opens, 0, 3, "SELL")
    assert sum(r for _, r in buy) > 0 and sum(r for _, r in sell) < 0


def _trade(side, ei, xi, net=0.0, cost=0.1):
    closes = [100.0, 101.0, 102.0, 103.0, 104.0]
    opens = list(closes)
    return {"side": side, "cost_pct": cost, "net_pct": net,
            "daily": htb._daily_rets(closes, opens, ei, xi, side)}


def test_portfolio_no_duplicate_event_inflation():
    one = [_trade("SELL", 0, 3)]
    two = [_trade("SELL", 0, 3), _trade("SELL", 0, 3)]   # same losing position twice
    p1 = htb._portfolio(one, net=False)
    p2 = htb._portfolio(two, net=False)
    assert abs(p1["max_drawdown_pct"] - p2["max_drawdown_pct"]) < 1e-6
    assert abs(p1["total_return_pct"] - p2["total_return_pct"]) < 1e-6


def test_portfolio_drawdown_bounded():
    p = htb._portfolio([_trade("SELL", 0, 3)], net=False)   # SELL on rising price = loss
    assert -10.0 < p["max_drawdown_pct"] <= 0.0


def _ftrade(net, ed_in, ed_out):
    return {"side": "BUY", "net_pct": net, "entry_date": ed_in, "exit_date": ed_out}


def test_fixed_frac_compounds_fixed_fraction():
    # two non-overlapping -10% trades at 50% per trade -> (1-0.05)^2 ~ -9.75%
    ts = [_ftrade(-10.0, "2025-01-01", "2025-01-03"),
          _ftrade(-10.0, "2025-02-01", "2025-02-03")]
    p = htb._portfolio_fixed_frac(ts, frac=0.5)
    assert p["positions_taken"] == 2
    assert abs(p["total_return_pct"] - (-9.75)) < 0.01


def test_fixed_frac_caps_concurrency():
    # three fully-overlapping trades, cap = 2 -> one is skipped (not leveraged)
    ts = [_ftrade(1.0, "2025-01-01", "2025-01-10") for _ in range(3)]
    p = htb._portfolio_fixed_frac(ts, frac=0.5)   # max_concurrent = 2
    assert p["positions_taken"] == 2 and p["positions_skipped_capacity"] == 1


def test_fixed_frac_less_extreme_than_fully_invested():
    # A losing book: realistic 10%/trade sizing must be far less extreme than the
    # fully-invested equal-weight daily book that amplifies when few names open.
    cons = [_trade("SELL", 0, 3) for _ in range(5)]        # overlapping losers (gross)
    for i, t in enumerate(cons):
        t["entry_date"], t["exit_date"], t["net_pct"] = f"2025-01-0{i+1}", f"2025-01-0{i+4}", -2.0
    full = htb._portfolio(cons, net=False)
    frac = htb._portfolio_fixed_frac(cons, frac=0.1)
    assert abs(frac["max_drawdown_pct"]) < abs(full["max_drawdown_pct"])
