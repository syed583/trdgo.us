def score_earnings_history(history: dict):
    """
    TRDGO Earnings History Score V1

    Score range:
        -15 to +15

    Uses:
        - EPS beat/miss rate
        - Revenue beat/miss rate
        - Average EPS surprise
        - Average revenue surprise
        - Number of comparable earnings events

    IMPORTANT:
    A small sample receives lower confidence.
    """

    if not history:
        return {
            "score": 0,
            "max_score": 15,
            "bias": "NO_DATA",
            "confidence": 0,
            "events_used": 0,
            "reasons": [
                "No earnings history available"
            ]
        }

    events = history.get("events", [])
    summary = history.get("summary", {})

    if not events:
        return {
            "score": 0,
            "max_score": 15,
            "bias": "NO_DATA",
            "confidence": 0,
            "events_used": 0,
            "reasons": [
                "No completed earnings events available"
            ]
        }

    score = 0
    reasons = []

    # ---------------------------------------------------------
    # SAMPLE SIZE
    # ---------------------------------------------------------

    total_events = len(events)

    eps_comparable = summary.get(
        "eps_comparable_events",
        0
    )

    revenue_comparable = summary.get(
        "revenue_comparable_events",
        0
    )

    # ---------------------------------------------------------
    # EPS BEAT RATE
    # MAX +/- 5
    # ---------------------------------------------------------

    eps_beat_rate = summary.get(
        "eps_beat_rate"
    )

    if eps_beat_rate is not None:

        if eps_beat_rate >= 75:
            score += 5
            reasons.append(
                f"Strong EPS beat history: "
                f"{eps_beat_rate:.1f}%"
            )

        elif eps_beat_rate >= 60:
            score += 3
            reasons.append(
                f"Positive EPS beat history: "
                f"{eps_beat_rate:.1f}%"
            )

        elif eps_beat_rate >= 40:
            reasons.append(
                f"Mixed EPS history: "
                f"{eps_beat_rate:.1f}%"
            )

        elif eps_beat_rate >= 25:
            score -= 3
            reasons.append(
                f"Weak EPS beat history: "
                f"{eps_beat_rate:.1f}%"
            )

        else:
            score -= 5
            reasons.append(
                f"Very weak EPS beat history: "
                f"{eps_beat_rate:.1f}%"
            )

    # ---------------------------------------------------------
    # REVENUE BEAT RATE
    # MAX +/- 4
    # ---------------------------------------------------------

    revenue_beat_rate = summary.get(
        "revenue_beat_rate"
    )

    if revenue_beat_rate is not None:

        if revenue_beat_rate >= 75:
            score += 4
            reasons.append(
                f"Strong revenue beat history: "
                f"{revenue_beat_rate:.1f}%"
            )

        elif revenue_beat_rate >= 60:
            score += 2
            reasons.append(
                f"Positive revenue beat history: "
                f"{revenue_beat_rate:.1f}%"
            )

        elif revenue_beat_rate >= 40:
            reasons.append(
                f"Mixed revenue history: "
                f"{revenue_beat_rate:.1f}%"
            )

        elif revenue_beat_rate >= 25:
            score -= 2
            reasons.append(
                f"Weak revenue beat history: "
                f"{revenue_beat_rate:.1f}%"
            )

        else:
            score -= 4
            reasons.append(
                f"Very weak revenue beat history: "
                f"{revenue_beat_rate:.1f}%"
            )

    # ---------------------------------------------------------
    # COLLECT SURPRISE PERCENTAGES
    # ---------------------------------------------------------

    eps_surprises = []

    revenue_surprises = []

    for event in events:

        eps_surprise = event.get(
            "eps_surprise_percent"
        )

        if eps_surprise is not None:
            eps_surprises.append(
                float(eps_surprise)
            )

        revenue_surprise = event.get(
            "revenue_surprise_percent"
        )

        if revenue_surprise is not None:
            revenue_surprises.append(
                float(revenue_surprise)
            )

    # ---------------------------------------------------------
    # AVERAGE EPS SURPRISE
    # MAX +/- 3
    # ---------------------------------------------------------

    avg_eps_surprise = None

    if eps_surprises:

        avg_eps_surprise = (
            sum(eps_surprises)
            / len(eps_surprises)
        )

        if avg_eps_surprise >= 10:
            score += 3
            reasons.append(
                f"Strong average EPS surprise: "
                f"{avg_eps_surprise:.2f}%"
            )

        elif avg_eps_surprise >= 3:
            score += 2
            reasons.append(
                f"Positive average EPS surprise: "
                f"{avg_eps_surprise:.2f}%"
            )

        elif avg_eps_surprise > 0:
            score += 1
            reasons.append(
                f"Slight positive EPS surprise: "
                f"{avg_eps_surprise:.2f}%"
            )

        elif avg_eps_surprise <= -10:
            score -= 3
            reasons.append(
                f"Large negative average EPS surprise: "
                f"{avg_eps_surprise:.2f}%"
            )

        elif avg_eps_surprise <= -3:
            score -= 2
            reasons.append(
                f"Negative average EPS surprise: "
                f"{avg_eps_surprise:.2f}%"
            )

        elif avg_eps_surprise < 0:
            score -= 1
            reasons.append(
                f"Slight negative EPS surprise: "
                f"{avg_eps_surprise:.2f}%"
            )

    # ---------------------------------------------------------
    # AVERAGE REVENUE SURPRISE
    # MAX +/- 3
    # ---------------------------------------------------------

    avg_revenue_surprise = None

    if revenue_surprises:

        avg_revenue_surprise = (
            sum(revenue_surprises)
            / len(revenue_surprises)
        )

        if avg_revenue_surprise >= 5:
            score += 3
            reasons.append(
                f"Strong average revenue surprise: "
                f"{avg_revenue_surprise:.2f}%"
            )

        elif avg_revenue_surprise >= 1:
            score += 2
            reasons.append(
                f"Positive average revenue surprise: "
                f"{avg_revenue_surprise:.2f}%"
            )

        elif avg_revenue_surprise > 0:
            score += 1
            reasons.append(
                f"Slight positive revenue surprise: "
                f"{avg_revenue_surprise:.2f}%"
            )

        elif avg_revenue_surprise <= -5:
            score -= 3
            reasons.append(
                f"Large negative average revenue surprise: "
                f"{avg_revenue_surprise:.2f}%"
            )

        elif avg_revenue_surprise <= -1:
            score -= 2
            reasons.append(
                f"Negative average revenue surprise: "
                f"{avg_revenue_surprise:.2f}%"
            )

        elif avg_revenue_surprise < 0:
            score -= 1
            reasons.append(
                f"Slight negative revenue surprise: "
                f"{avg_revenue_surprise:.2f}%"
            )

    # ---------------------------------------------------------
    # CLAMP SCORE
    # ---------------------------------------------------------

    score = max(
        -15,
        min(15, score)
    )

    # ---------------------------------------------------------
    # SAMPLE-SIZE CONFIDENCE
    #
    # We do NOT claim high confidence from only 1 quarter.
    # ---------------------------------------------------------

    comparable_events = max(
        eps_comparable,
        revenue_comparable
    )

    if comparable_events >= 8:
        confidence = 100

    elif comparable_events >= 6:
        confidence = 85

    elif comparable_events >= 4:
        confidence = 70

    elif comparable_events >= 2:
        confidence = 45

    elif comparable_events == 1:
        confidence = 25

    else:
        confidence = 0

    if comparable_events < 4:
        reasons.append(
            "Low historical sample size; "
            "score confidence is limited"
        )

    # ---------------------------------------------------------
    # BIAS
    # ---------------------------------------------------------

    if score >= 11:
        bias = "STRONG_BULLISH"

    elif score >= 5:
        bias = "BULLISH"

    elif score <= -11:
        bias = "STRONG_BEARISH"

    elif score <= -5:
        bias = "BEARISH"

    else:
        bias = "NEUTRAL"

    # ---------------------------------------------------------
    # RETURN
    # ---------------------------------------------------------

    return {
        "score": score,
        "max_score": 15,
        "bias": bias,
        "confidence": confidence,
        "events_used": total_events,
        "eps_beat_rate": eps_beat_rate,
        "revenue_beat_rate": revenue_beat_rate,
        "average_eps_surprise_percent": (
            round(avg_eps_surprise, 2)
            if avg_eps_surprise is not None
            else None
        ),
        "average_revenue_surprise_percent": (
            round(avg_revenue_surprise, 2)
            if avg_revenue_surprise is not None
            else None
        ),
        "reasons": reasons
    }