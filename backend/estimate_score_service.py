import provider_config as cfg
from estimate_revision_service import calculate_estimate_revisions


def _revision_data(db, symbol: str) -> dict:
    """
    Revision windows for `symbol`, from whichever store actually has them.

    Alpha Vantage writes to estimate_revisions; the original scorer read the
    legacy estimate_snapshots table, which also requires a companies row. On a
    database where only Alpha Vantage has run, that raised
    "Company X does not exist in the companies table", the caller swallowed it,
    and a 25-point component reported UNAVAILABLE while the data sat in the
    other table. Prefer the configured provider, keep the legacy path for
    installs whose history lives there.
    """
    # Twelve Data first: its /eps_trend returns all four windows (7/30/60/90)
    # in one response, so the component can reach full confidence immediately.
    # Alpha Vantage omits the 90-day window and its free tier is 25 calls/day.
    # The provider windows are gone with their providers. What remains is
    # this app's own snapshots, which is what the component was built on:
    # estimates stored over time and compared against themselves.
    try:
        return calculate_estimate_revisions(db, symbol)
    except ValueError:
        # No companies row and no provider data: genuinely nothing to score.
        return {
            "symbol": symbol.upper(),
            "status": "NO_DATA",
            "snapshot_count": 0,
            "revisions": {},
            "available_windows": 0,
        }


# ---------------------------------------------------------
# ESTIMATES & REVISIONS SCORE
# ---------------------------------------------------------

def score_estimates(
    db,
    symbol: str
):
    """
    TRDGO Estimates & Revisions Score V1

    Score range:
        -25 to +25

    Uses:
        - EPS estimate revisions
        - Revenue estimate revisions
        - Multiple revision windows
        - Analyst coverage
        - Data availability

    IMPORTANT:
    Missing revision history does NOT count as neutral
    evidence. Confidence is reduced instead.
    """

    revision_data = _revision_data(
        db,
        symbol
    )

    # Reject TEST source snapshots before any production estimate score is produced.
    current_source = revision_data.get("current_snapshot", {}).get("source")
    if str(current_source or "").upper() == "TEST":
        return {
            "symbol": symbol.upper(),
            "score": 0,
            "max_score": 25,
            "bias": "TEST_DATA",
            "confidence": 0,
            "available_windows": 0,
            "analyst_count": revision_data.get("current_snapshot", {}).get("analyst_count"),
            "current_eps_estimate": revision_data.get("current_snapshot", {}).get("eps_estimate"),
            "current_revenue_estimate": revision_data.get("current_snapshot", {}).get("revenue_estimate"),
            "source": current_source,
            "reasons": ["Estimate source is TEST; test data is not production verified"]
        }

    status = revision_data.get(
        "status",
        "NO_DATA"
    )

    current = revision_data.get(
        "current_snapshot",
        {}
    )

    revisions = revision_data.get(
        "revisions",
        {}
    )

    available_windows = revision_data.get(
        "available_windows",
        0
    )

    score = 0
    reasons = []

    # -----------------------------------------------------
    # NO DATA
    # -----------------------------------------------------

    if status in (
        "NO_DATA",
        "NO_VALID_TIMESTAMPS"
    ):
        return {
            "symbol": symbol.upper(),
            "score": 0,
            "max_score": 25,
            "bias": "NO_DATA",
            "confidence": 0,
            "available_windows": 0,
            "reasons": [
                "No valid analyst estimate data available"
            ]
        }

    # -----------------------------------------------------
    # WINDOW WEIGHTS
    #
    # More recent revisions receive more weight.
    #
    # 7d  = 8 points
    # 30d = 6 points
    # 60d = 4 points
    # 90d = 3 points
    #
    # Total revision weight = 21
    # Analyst coverage = 4
    # Total = 25
    # -----------------------------------------------------

    window_weights = {
        "7d": 8,
        "30d": 6,
        "60d": 4,
        "90d": 3
    }

    # -----------------------------------------------------
    # SCORE EACH REVISION WINDOW
    # -----------------------------------------------------

    for window, weight in window_weights.items():

        revision = revisions.get(
            window,
            {}
        )

        if not revision.get(
            "available"
        ):
            continue

        eps_revision = revision.get(
            "eps_revision_percent"
        )

        revenue_revision = revision.get(
            "revenue_revision_percent"
        )

        signals = []

        if eps_revision is not None:
            signals.append(
                float(eps_revision)
            )

        if revenue_revision is not None:
            signals.append(
                float(revenue_revision)
            )

        if not signals:
            continue

        average_revision = (
            sum(signals)
            / len(signals)
        )

        # ---------------------------------------------
        # STRONG UPWARD REVISION
        # ---------------------------------------------

        if average_revision >= 5:

            score += weight

            reasons.append(
                f"{window} strong upward "
                f"estimate revision: "
                f"{average_revision:.2f}%"
            )

        # ---------------------------------------------
        # MODERATE UPWARD REVISION
        # ---------------------------------------------

        elif average_revision >= 2:

            points = max(
                1,
                round(weight * 0.70)
            )

            score += points

            reasons.append(
                f"{window} positive "
                f"estimate revision: "
                f"{average_revision:.2f}%"
            )

        # ---------------------------------------------
        # SLIGHT UPWARD REVISION
        # ---------------------------------------------

        elif average_revision > 0:

            points = max(
                1,
                round(weight * 0.35)
            )

            score += points

            reasons.append(
                f"{window} slight upward "
                f"estimate revision: "
                f"{average_revision:.2f}%"
            )

        # ---------------------------------------------
        # STRONG DOWNWARD REVISION
        # ---------------------------------------------

        elif average_revision <= -5:

            score -= weight

            reasons.append(
                f"{window} strong downward "
                f"estimate revision: "
                f"{average_revision:.2f}%"
            )

        # ---------------------------------------------
        # MODERATE DOWNWARD REVISION
        # ---------------------------------------------

        elif average_revision <= -2:

            points = max(
                1,
                round(weight * 0.70)
            )

            score -= points

            reasons.append(
                f"{window} negative "
                f"estimate revision: "
                f"{average_revision:.2f}%"
            )

        # ---------------------------------------------
        # SLIGHT DOWNWARD REVISION
        # ---------------------------------------------

        elif average_revision < 0:

            points = max(
                1,
                round(weight * 0.35)
            )

            score -= points

            reasons.append(
                f"{window} slight downward "
                f"estimate revision: "
                f"{average_revision:.2f}%"
            )

        else:

            reasons.append(
                f"{window} estimates unchanged"
            )

    # -----------------------------------------------------
    # ANALYST COVERAGE
    # MAX +4
    #
    # Coverage increases reliability.
    # It does NOT create bearish points.
    # -----------------------------------------------------

    analyst_count = current.get(
        "analyst_count"
    )

    if analyst_count is not None:

        if analyst_count >= 30:

            score += 4

            reasons.append(
                f"Strong analyst coverage: "
                f"{analyst_count} analysts"
            )

        elif analyst_count >= 15:

            score += 3

            reasons.append(
                f"Good analyst coverage: "
                f"{analyst_count} analysts"
            )

        elif analyst_count >= 5:

            score += 2

            reasons.append(
                f"Moderate analyst coverage: "
                f"{analyst_count} analysts"
            )

        elif analyst_count > 0:

            score += 1

            reasons.append(
                f"Limited analyst coverage: "
                f"{analyst_count} analysts"
            )

    # -----------------------------------------------------
    # CONFIDENCE
    #
    # Revision history is the main requirement.
    # -----------------------------------------------------

    if available_windows == 4:
        confidence = 100

    elif available_windows == 3:
        confidence = 80

    elif available_windows == 2:
        confidence = 60

    elif available_windows == 1:
        confidence = 35

    else:
        confidence = 15

    # -----------------------------------------------------
    # IMPORTANT SAFETY WARNING
    # -----------------------------------------------------

    if available_windows == 0:

        reasons.append(
            "No 7/30/60/90-day revision history; "
            "estimate score confidence is very low"
        )

    elif available_windows < 4:

        reasons.append(
            "Estimate revision history is incomplete"
        )

    # -----------------------------------------------------
    # CLAMP SCORE
    # -----------------------------------------------------

    score = max(
        -25,
        min(25, score)
    )

    # -----------------------------------------------------
    # BIAS
    # -----------------------------------------------------

    if confidence < 35:

        bias = "INSUFFICIENT_DATA"

    elif score >= 18:

        bias = "STRONG_BULLISH"

    elif score >= 8:

        bias = "BULLISH"

    elif score <= -18:

        bias = "STRONG_BEARISH"

    elif score <= -8:

        bias = "BEARISH"

    else:

        bias = "NEUTRAL"

    # -----------------------------------------------------
    # RETURN
    # -----------------------------------------------------

    return {
        "symbol": symbol.upper(),

        "score": score,

        "max_score": 25,

        "bias": bias,

        "confidence": confidence,

        "available_windows":
            available_windows,

        "analyst_count":
            analyst_count,

        "current_eps_estimate":
            current.get(
                "eps_estimate"
            ),

        "current_revenue_estimate":
            current.get(
                "revenue_estimate"
            ),

        "source":
            current.get(
                "source"
            ),

        "reasons": reasons
    }