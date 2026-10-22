"""Phase 5 tests — experimental risk-control layer (capital-constrained portfolio,
gap-risk availability, sizing). Pure-logic, synthetic trades; no network/DB."""
import risk_experiment_service as rx


def _t(sym, ed_in, ed_out, daily, side="BUY"):
    return {"symbol": sym, "earnings_date": ed_in, "side": side,
            "entry_date": ed_in, "exit_date": ed_out, "daily_by_date": daily, "net_pct": 0.0}


def test_allocation_sizing_scales_return():
    # one +10% trade at 10% allocation -> +1% total return
    t = _t("A", "2025-01-01", "2025-01-02", {"2025-01-02": 0.10})
    p = rx.simulate_portfolio([t], 0.10, capital=100_000)
    assert abs(p["total_return_pct"] - 1.0) < 1e-6
    assert p["trades_taken"] == 1 and p["trades_rejected_capacity"] == 0


def test_no_leverage_and_capacity_rejection():
    # three fully-overlapping trades at 50% each -> only 2 fit (no leverage), 1 rejected
    ds = {"2025-01-05": 0.0}
    ts = [_t(f"S{i}", "2025-01-01", "2025-01-05", dict(ds)) for i in range(3)]
    p = rx.simulate_portfolio(ts, 0.50, capital=100_000)
    assert p["trades_taken"] == 2 and p["trades_rejected_capacity"] == 1
    assert p["max_gross_exposure_pct"] <= 100.0 + 1e-6
    assert p["max_concurrent"] == 2


def test_cash_reservation_and_min_cash():
    # two 50% trades, non-overlapping -> both taken, min cash hits ~50%
    t1 = _t("A", "2025-01-01", "2025-01-03", {"2025-01-03": 0.0})
    t2 = _t("B", "2025-02-01", "2025-02-03", {"2025-02-03": 0.0})
    p = rx.simulate_portfolio([t1, t2], 0.50, capital=100_000)
    assert p["trades_taken"] == 2
    assert p["min_cash_pct"] <= 50.0 + 1e-6


def test_compounding_non_overlapping():
    # two non-overlapping +10% trades at 50% -> (1.05)^2 - 1 = 10.25%
    t1 = _t("A", "2025-01-01", "2025-01-02", {"2025-01-02": 0.10})
    t2 = _t("B", "2025-02-01", "2025-02-02", {"2025-02-02": 0.10})
    p = rx.simulate_portfolio([t1, t2], 0.50, capital=100_000)
    assert abs(p["total_return_pct"] - 10.25) < 0.01


def test_worst_event_loss_tracked():
    t = _t("A", "2025-01-01", "2025-01-02", {"2025-01-02": -0.20})   # -20% move
    p = rx.simulate_portfolio([t], 0.10, capital=100_000)
    # 10% of capital lost 20% -> -2% of capital
    assert abs(p["worst_event_loss_pct_of_capital"] - (-2.0)) < 1e-6


def test_drawdown_bounded_and_negative_for_loss():
    t = _t("A", "2025-01-01", "2025-01-02", {"2025-01-02": -0.20})
    p = rx.simulate_portfolio([t], 0.10, capital=100_000)
    assert -3.0 < p["max_drawdown_pct"] <= 0.0


def test_gap_risk_missing_factors_not_substituted():
    gr = rx.gap_risk("A", "2025-06-01", eidx=100,
                     closes=[100 + i * 0.1 for i in range(120)],
                     past_moves=[3.0, -2.0, 5.0], surprises=[2.0, -1.0], spy_vol=0.012)
    f = gr["factors"]
    # unavailable factors must be None, never a number
    assert f["implied_vs_real"] is None
    assert f["liquidity_spreads"] is None
    assert f["estimate_dispersion"] is None
    assert f["timing_halts"] is None
    # available factors computed; coverage capped at the available weight (55)
    assert gr["coverage_weight"] <= rx.AVAILABLE_WEIGHT == 55
    assert gr["score"] is not None


def test_gap_risk_no_history_is_none():
    gr = rx.gap_risk("A", "2025-06-01", eidx=0, closes=[], past_moves=[], surprises=[], spy_vol=None)
    assert gr["factors"]["hist_gap_mag_var"] is None
    # with no available data, score is None (never fabricated)
    assert gr["score"] is None
