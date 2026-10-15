"""Smart Money model: the 6 parameter evals + bias/coverage + acceptance checks
(10b5-1 & exercises excluded, mid-price fills ignored, insufficient coverage)."""

from datetime import date, timedelta

import smart_money_model as sm

TODAY = date(2026, 10, 15)


def _d(days_ago):
    return (TODAY - timedelta(days=days_ago)).isoformat()


# --- 2) Insider Buy/Sell + acceptance #1 (10b5-1 / exercise excluded) ------
def _txn(code, plan, shares, price, days, title="", officer=False, director=False, ten=False, owner=None):
    return {"transaction_code": code, "is_10b5_1": plan, "amount": shares, "price": price,
            "stock_price": price, "transaction_date": _d(days), "filing_date": _d(days),
            "owner_name": owner or f"ins-{shares}-{code}", "officer_title": title,
            "is_officer": officer, "is_director": director, "is_ten_percent_owner": ten}


def test_insider_excludes_10b5_1_and_exercises():
    rows = [
        _txn("S", True, -100000, 10, 5),                 # 10b5-1 sale -> excluded
        _txn("M", False, 50000, 10, 5),                  # option exercise -> excluded
        _txn("P", False, 10000, 12, 5, "Chief Executive Officer", officer=True),  # CEO buy $120k
    ]
    val, _, extra = sm._insider_eval(rows, TODAY)
    assert extra["disc_sellers_30d"] == 0                # 10b5-1 sale not counted
    assert val == 20.0                                   # CEO open-market buy >= $100k


def test_insider_two_buyers_plus20():
    rows = [_txn("P", False, 6000, 10, 10, owner="A"),   # $60k
            _txn("P", False, 7000, 10, 10, owner="B")]   # $70k, 2 distinct buyers
    val, _, extra = sm._insider_eval(rows, TODAY)
    assert extra["buyers_30d"] == 2 and val == 20.0


def test_insider_discretionary_selling_negative():
    rows = [_txn("S", False, -200000, 10, 5, owner=f"S{i}") for i in range(4)]  # 4 sellers, $8M
    val, _, extra = sm._insider_eval(rows, TODAY)
    assert extra["disc_sellers_30d"] == 4 and val == -20.0


# --- 3) Unusual Options + acceptance #2 (mid-price ignored) ----------------
def _alert(typ, prem, ask, bid, sweep=True, floor=False, ml=False, voi=5, dte=10, delta=0.3, days=0):
    return {"type": typ, "total_premium": prem, "total_ask_side_prem": ask,
            "total_bid_side_prem": bid, "has_sweep": sweep, "has_floor": floor,
            "has_multileg": ml, "volume_oi_ratio": voi,
            "expiry": (TODAY + timedelta(days=dte)).isoformat(), "delta": delta,
            "created_at": _d(days) + "T14:00:00-04:00"}


def test_unusual_mid_fill_ignored():
    # ask == bid -> (ask - bid) = 0 contribution
    v, _, x = sm._unusual_eval([_alert("call", 200000, 100000, 100000)], 1_000_000, TODAY)
    assert x["net_premium"] == 0 and v == 0.0


def test_unusual_call_at_ask_bullish_and_filters():
    alerts = [
        _alert("call", 200000, 180000, 20000),           # bullish +160k
        _alert("put", 50000, 40000, 10000),              # premium < 100k -> excluded
        _alert("call", 300000, 280000, 20000, ml=True),  # multi-leg -> excluded
        _alert("call", 300000, 280000, 20000, dte=90),   # DTE > 45 -> excluded
    ]
    v, _, x = sm._unusual_eval(alerts, 1_000_000, TODAY)
    assert x["net_premium"] == 160000 and v > 0
    assert len(x["prints"]) == 1                          # only the valid one


# --- 4) Options Disparity ---------------------------------------------------
def test_disparity_call_heavy_and_net_delta_no_data():
    series = [{"date": "2026-10-15", "call_premium": 800, "put_premium": 200, "call_volume": 8, "put_volume": 2}]
    series += [{"date": f"2026-10-{d:02d}", "call_premium": 500, "put_premium": 500,
                "call_volume": 5, "put_volume": 5} for d in range(14, 1, -1)]
    v, _, x = sm._disparity_eval(series, price_chg=1.0, today=TODAY)
    assert v > 0 and x["call_share"] == 0.8 and x["net_delta"] is None


def test_disparity_divergence_flag():
    series = [{"date": "2026-10-15", "call_premium": 800, "put_premium": 200}]
    series += [{"date": f"2026-10-{d:02d}", "call_premium": 500, "put_premium": 500} for d in range(14, 1, -1)]
    _, _, x = sm._disparity_eval(series, price_chg=-1.5, today=TODAY)   # bullish flow, price down
    assert x["divergence"] and "price down" in x["divergence"]


# --- 5) Fund Buy/Sell (13F) + acceptance #4 (quarter/filed dates) ----------
def test_fund_flow_increasers_and_dates():
    rows = [{"units_changed": 100, "report_date": "2026-06-30", "filing_date": "2026-08-07",
             "shares_outstanding": 1000, "first_buy": "2024-01-01"} for _ in range(3)]
    rows += [{"units_changed": -10, "report_date": "2026-06-30", "filing_date": "2026-08-07",
              "shares_outstanding": 1000}]
    v, _, x = sm._fund_flow_eval(rows)
    assert x["increasers"] == 3 and x["decreasers"] == 1 and v > 0
    assert x["quarter_end"] == "2026-06-30" and x["filed"] == "2026-08-07"
    assert x["closed_positions"] is None                  # exited funds absent -> no data


# --- 6) Positions in Funds --------------------------------------------------
def test_fund_positions_rising_and_fundtype_no_data():
    rows = [{"historical_units": [300, 250, 200, 150], "shares_outstanding": 1000,
             "units": 300, "value": 3000, "name": "Active Cap", "units_changed": 50}]
    v, _, x = sm._fund_positions_eval(rows)
    assert v > 0 and x["ownership_trend_pct"][0] == 30.0 and x["fund_type_split"] is None


# --- 1) Insider News --------------------------------------------------------
def test_news_eval_window_and_keywords():
    news = [
        {"headline": "Company announces $10B buyback", "created_at": _d(1) + "T14:00:00-04:00", "source": "X"},
        {"headline": "SEC investigation opened", "created_at": _d(1) + "T14:00:00-04:00", "source": "Y"},
        {"headline": "stale buyback headline", "created_at": _d(9) + "T14:00:00-04:00", "source": "Z"},  # >3d
    ]
    v, _, x = sm._news_eval(news, [], TODAY)
    assert len(x["headlines"]) == 2                       # 3-day window excludes the stale one


# --- bias + acceptance #3 (coverage < 60 -> no bias) -----------------------
def test_bias_needs_group_agreement():
    strong = {n: {"value": v, "has_data": True} for n, v in
              {"insider_news": 5, "insider_trades": 10, "unusual": 10,
               "disparity": 5, "fund_flow": 5, "fund_positions": 3}.items()}
    assert sm._bias(70, strong) == "BULLISH"              # >=65, 4 groups bull, none strong bear
    mixed = {n: {"value": v, "has_data": True} for n, v in
             {"insider_news": 5, "insider_trades": 15, "unusual": 20,
              "disparity": -10, "fund_flow": -8, "fund_positions": -5}.items()}
    assert sm._bias(80, mixed) == "MIXED"                 # only 2 groups bull / a strong-bear group


def test_coverage_threshold_constant():
    assert sm.COVERAGE_MIN == 60                          # < 60 covered -> "Insufficient data"
