from datetime import datetime, timezone

from estimate_data_service import get_estimate_history


# ---------------------------------------------------------
# PARSE TIMESTAMP
# ---------------------------------------------------------

def _parse_time(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except Exception:
        return None


# ---------------------------------------------------------
# PERCENT CHANGE
# ---------------------------------------------------------

def _percent_change(old_value, new_value):
    if old_value is None or new_value is None:
        return None

    if old_value == 0:
        return None

    return (
        (new_value - old_value)
        / abs(old_value)
    ) * 100


# ---------------------------------------------------------
# FIND SNAPSHOT NEAR TARGET AGE
# ---------------------------------------------------------

def _find_snapshot_for_days(
    snapshots,
    current_time,
    days
):
    """
    Find the newest snapshot that existed at or before
    the requested lookback point.

    Example:
        current_time - 30 days

    This avoids accidentally using future information.
    """

    target_time = current_time.replace()

    from datetime import timedelta

    target_time = (
        current_time
        - timedelta(days=days)
    )

    candidates = []

    for snapshot in snapshots:

        snapshot_time = _parse_time(
            snapshot.get("snapshot_time")
        )

        if snapshot_time is None:
            continue

        if snapshot_time <= target_time:
            candidates.append(
                (
                    snapshot_time,
                    snapshot
                )
            )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item[0],
        reverse=True
    )

    return candidates[0][1]


# ---------------------------------------------------------
# CALCULATE ONE REVISION WINDOW
# ---------------------------------------------------------

def _calculate_revision(
    current_snapshot,
    historical_snapshot,
    days
):
    if historical_snapshot is None:
        return {
            "days": days,
            "available": False,
            "reason": (
                f"No snapshot available "
                f"at least {days} days ago"
            )
        }

    current_eps = current_snapshot.get(
        "eps_estimate"
    )

    old_eps = historical_snapshot.get(
        "eps_estimate"
    )

    current_revenue = current_snapshot.get(
        "revenue_estimate"
    )

    old_revenue = historical_snapshot.get(
        "revenue_estimate"
    )

    eps_change = _percent_change(
        old_eps,
        current_eps
    )

    revenue_change = _percent_change(
        old_revenue,
        current_revenue
    )

    return {
        "days": days,
        "available": True,

        "historical_snapshot_time":
            historical_snapshot.get(
                "snapshot_time"
            ),

        "current_snapshot_time":
            current_snapshot.get(
                "snapshot_time"
            ),

        "eps_old": old_eps,
        "eps_current": current_eps,

        "eps_revision_percent": (
            round(eps_change, 4)
            if eps_change is not None
            else None
        ),

        "revenue_old": old_revenue,
        "revenue_current": current_revenue,

        "revenue_revision_percent": (
            round(revenue_change, 4)
            if revenue_change is not None
            else None
        )
    }


# ---------------------------------------------------------
# ESTIMATE REVISION ANALYSIS
# ---------------------------------------------------------

def calculate_estimate_revisions(
    db,
    symbol: str
):
    """
    Calculate point-in-time estimate revisions.

    Windows:
        7 days
        30 days
        60 days
        90 days

    IMPORTANT:
    Historical snapshots are selected using timestamps.
    Future snapshots are never used for older windows.
    """

    history = get_estimate_history(
        db,
        symbol,
        limit=500
    )

    snapshots = history.get(
        "snapshots",
        []
    )

    if not snapshots:
        return {
            "symbol": symbol.upper(),
            "status": "NO_DATA",
            "snapshot_count": 0,
            "revisions": {}
        }

    # -----------------------------------------------------
    # SORT NEWEST FIRST
    # -----------------------------------------------------

    valid_snapshots = []

    for snapshot in snapshots:

        snapshot_time = _parse_time(
            snapshot.get("snapshot_time")
        )

        if snapshot_time is None:
            continue

        valid_snapshots.append(
            (
                snapshot_time,
                snapshot
            )
        )

    if not valid_snapshots:
        return {
            "symbol": symbol.upper(),
            "status": "NO_VALID_TIMESTAMPS",
            "snapshot_count": 0,
            "revisions": {}
        }

    valid_snapshots.sort(
        key=lambda item: item[0],
        reverse=True
    )

    current_time = valid_snapshots[0][0]
    current_snapshot = valid_snapshots[0][1]

    # -----------------------------------------------------
    # REVISION WINDOWS
    # -----------------------------------------------------

    windows = [
        7,
        30,
        60,
        90
    ]

    revisions = {}

    available_windows = 0

    for days in windows:

        historical_snapshot = (
            _find_snapshot_for_days(
                snapshots,
                current_time,
                days
            )
        )

        revision = _calculate_revision(
            current_snapshot,
            historical_snapshot,
            days
        )

        revisions[
            f"{days}d"
        ] = revision

        if revision.get("available"):
            available_windows += 1

    # -----------------------------------------------------
    # DATA STATUS
    # -----------------------------------------------------

    if available_windows == 4:
        status = "GOOD"

    elif available_windows > 0:
        status = "PARTIAL"

    else:
        status = "INSUFFICIENT_HISTORY"

    # -----------------------------------------------------
    # RETURN
    # -----------------------------------------------------

    return {
        "symbol": symbol.upper(),

        "status": status,

        "snapshot_count": len(
            valid_snapshots
        ),

        "current_snapshot": {
            "snapshot_time":
                current_snapshot.get(
                    "snapshot_time"
                ),

            "eps_estimate":
                current_snapshot.get(
                    "eps_estimate"
                ),

            "revenue_estimate":
                current_snapshot.get(
                    "revenue_estimate"
                ),

            "analyst_count":
                current_snapshot.get(
                    "analyst_count"
                ),

            "source":
                current_snapshot.get(
                    "source"
                )
        },

        "available_windows":
            available_windows,

        "required_windows": 4,

        "revisions": revisions
    }