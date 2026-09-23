def score_fundamentals(data: dict):
    """
    TRDGO Fundamental Score V1

    Directional range:
        -20 to +20

    IMPORTANT:
    This version does NOT treat company size as bullish.
    It evaluates financial quality and data reliability.
    """

    if not data:
        return {
            "score": 0,
            "max_score": 20,
            "bias": "NO_DATA",
            "confidence": 0,
            "reasons": ["No fundamental data available"]
        }

    fundamentals = data.get("fundamentals", {})
    quality = data.get("data_quality", {})

    score = 0
    reasons = []

    # ---------------------------------------------------------
    # HELPER
    # ---------------------------------------------------------

    def usable(name):
        item = fundamentals.get(name)

        if not item:
            return None

        if item.get("is_stale"):
            return None

        return item.get("value")

    # ---------------------------------------------------------
    # GET CURRENT VALUES
    # ---------------------------------------------------------

    revenue = usable("revenue")
    net_income = usable("net_income")
    assets = usable("total_assets")
    liabilities = usable("total_liabilities")
    cash = usable("cash")
    debt = usable("debt")
    operating_cash_flow = usable("operating_cash_flow")
    capex = usable("capex")

    # ---------------------------------------------------------
    # PROFITABILITY
    # MAX +/- 5
    # ---------------------------------------------------------

    if revenue is not None and net_income is not None:

        if revenue > 0:
            net_margin = net_income / revenue

            if net_margin >= 0.20:
                score += 5
                reasons.append(
                    f"Strong net profit margin: {net_margin:.1%}"
                )

            elif net_margin >= 0.10:
                score += 3
                reasons.append(
                    f"Healthy net profit margin: {net_margin:.1%}"
                )

            elif net_margin > 0:
                score += 1
                reasons.append(
                    f"Positive net profit margin: {net_margin:.1%}"
                )

            elif net_margin < 0:
                score -= 5
                reasons.append(
                    f"Company is loss-making: {net_margin:.1%}"
                )

    # ---------------------------------------------------------
    # BALANCE SHEET
    # MAX +/- 5
    # ---------------------------------------------------------

    if assets is not None and liabilities is not None:

        if assets > 0:
            liability_ratio = liabilities / assets

            if liability_ratio <= 0.40:
                score += 5
                reasons.append(
                    "Strong balance sheet relative to liabilities"
                )

            elif liability_ratio <= 0.60:
                score += 3
                reasons.append(
                    "Healthy balance sheet"
                )

            elif liability_ratio <= 0.80:
                score += 1
                reasons.append(
                    "Moderate balance-sheet leverage"
                )

            else:
                score -= 3
                reasons.append(
                    "High liabilities relative to assets"
                )

    # ---------------------------------------------------------
    # CASH VS DEBT
    # MAX +/- 4
    # ---------------------------------------------------------

    if cash is not None and debt is not None:

        if debt <= 0 and cash > 0:
            score += 4
            reasons.append(
                "Strong cash position with minimal reported debt"
            )

        elif cash > debt:
            score += 4
            reasons.append(
                "Cash exceeds reported debt"
            )

        elif debt > 0:

            cash_debt_ratio = cash / debt

            if cash_debt_ratio >= 0.75:
                score += 2
                reasons.append(
                    "Cash provides good debt coverage"
                )

            elif cash_debt_ratio < 0.25:
                score -= 2
                reasons.append(
                    "Low cash coverage relative to debt"
                )

    # ---------------------------------------------------------
    # OPERATING CASH FLOW
    # MAX +/- 4
    # ---------------------------------------------------------

    if operating_cash_flow is not None:

        if operating_cash_flow > 0:
            score += 4
            reasons.append(
                "Positive operating cash flow"
            )

        elif operating_cash_flow < 0:
            score -= 4
            reasons.append(
                "Negative operating cash flow"
            )

    # ---------------------------------------------------------
    # FREE CASH FLOW APPROXIMATION
    # MAX +/- 2
    # ---------------------------------------------------------

    if (
        operating_cash_flow is not None
        and capex is not None
    ):

        free_cash_flow = operating_cash_flow - capex

        if free_cash_flow > 0:
            score += 2
            reasons.append(
                "Positive approximate free cash flow"
            )

        elif free_cash_flow < 0:
            score -= 2
            reasons.append(
                "Negative approximate free cash flow"
            )

    # ---------------------------------------------------------
    # DATA QUALITY / CONFIDENCE
    # ---------------------------------------------------------

    current_fields = quality.get(
        "current_fields",
        []
    )

    stale_fields = quality.get(
        "stale_fields",
        []
    )

    missing_fields = quality.get(
        "missing_fields",
        []
    )

    total_fields = (
        len(current_fields)
        + len(stale_fields)
        + len(missing_fields)
    )

    if total_fields > 0:
        confidence = round(
            (len(current_fields) / total_fields) * 100,
            2
        )
    else:
        confidence = 0

    if stale_fields:
        reasons.append(
            "Some SEC fundamental fields are stale"
        )

    if missing_fields:
        reasons.append(
            "Some SEC fundamental fields are missing"
        )

    # ---------------------------------------------------------
    # CLAMP SCORE
    # ---------------------------------------------------------

    score = max(
        -20,
        min(20, score)
    )

    # ---------------------------------------------------------
    # BIAS
    # ---------------------------------------------------------

    if score >= 15:
        bias = "STRONG_BULLISH"

    elif score >= 7:
        bias = "BULLISH"

    elif score <= -15:
        bias = "STRONG_BEARISH"

    elif score <= -7:
        bias = "BEARISH"

    else:
        bias = "NEUTRAL"

    return {
        "score": score,
        "max_score": 20,
        "bias": bias,
        "confidence": confidence,
        "reasons": reasons
    }