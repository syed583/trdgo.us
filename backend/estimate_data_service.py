from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from models import Company, EstimateSnapshot


# ---------------------------------------------------------
# NUMBER CONVERSION
# ---------------------------------------------------------

def _to_decimal(value):
    if value is None:
        return None

    try:
        return Decimal(str(value))

    except (InvalidOperation, ValueError, TypeError):
        return None


# ---------------------------------------------------------
# FIND COMPANY
# ---------------------------------------------------------

def _get_company(
    db: Session,
    symbol: str
):
    return (
        db.query(Company)
        .filter(
            Company.symbol == symbol.upper()
        )
        .first()
    )


# ---------------------------------------------------------
# SAVE ONE ESTIMATE SNAPSHOT
# ---------------------------------------------------------

def save_estimate_snapshot(
    db: Session,
    symbol: str,
    eps_estimate=None,
    revenue_estimate=None,
    analyst_count=None,
    snapshot_time=None,
    source=None
):
    """
    Save one point-in-time analyst estimate snapshot.

    IMPORTANT:
    Old snapshots are NOT overwritten.

    This allows TRDGO to later calculate:
        7-day revisions
        30-day revisions
        60-day revisions
        90-day revisions

    without look-ahead bias.
    """

    symbol = symbol.upper()

    company = _get_company(
        db,
        symbol
    )

    if not company:
        raise ValueError(
            f"Company {symbol} does not exist "
            f"in the companies table"
        )

    # -----------------------------------------------------
    # SNAPSHOT TIME
    # -----------------------------------------------------

    if snapshot_time is None:

        snapshot_time = datetime.now(
            timezone.utc
        )

    elif isinstance(snapshot_time, str):

        snapshot_time = datetime.fromisoformat(
            snapshot_time.replace(
                "Z",
                "+00:00"
            )
        )

    # -----------------------------------------------------
    # VALIDATE ANALYST COUNT
    # -----------------------------------------------------

    if analyst_count is not None:

        try:
            analyst_count = int(
                analyst_count
            )

        except (ValueError, TypeError):

            analyst_count = None

    # -----------------------------------------------------
    # CREATE SNAPSHOT
    # -----------------------------------------------------

    snapshot = EstimateSnapshot(
        company_id=company.id,

        eps_estimate=_to_decimal(
            eps_estimate
        ),

        revenue_estimate=_to_decimal(
            revenue_estimate
        ),

        analyst_count=analyst_count,

        snapshot_time=snapshot_time,

        source=source
    )

    db.add(snapshot)

    db.commit()
    db.refresh(snapshot)

    return {
        "action": "CREATED",
        "symbol": symbol,
        "snapshot_id": snapshot.id,
        "snapshot_time": (
            snapshot.snapshot_time.isoformat()
            if snapshot.snapshot_time
            else None
        ),
        "source": snapshot.source
    }


# ---------------------------------------------------------
# IMPORT MULTIPLE ESTIMATE SNAPSHOTS
# ---------------------------------------------------------

def import_estimate_snapshots(
    db: Session,
    symbol: str,
    snapshots: list
):
    """
    Import multiple point-in-time estimate snapshots.

    Each snapshot remains separate so historical
    revisions can be reconstructed later.
    """

    results = []

    for item in snapshots:

        try:

            result = save_estimate_snapshot(
                db=db,

                symbol=symbol,

                eps_estimate=item.get(
                    "eps_estimate"
                ),

                revenue_estimate=item.get(
                    "revenue_estimate"
                ),

                analyst_count=item.get(
                    "analyst_count"
                ),

                snapshot_time=item.get(
                    "snapshot_time"
                ),

                source=item.get(
                    "source"
                )
            )

            results.append(
                result
            )

        except Exception as exc:

            db.rollback()

            results.append({
                "action": "ERROR",

                "symbol": symbol.upper(),

                "snapshot_time": item.get(
                    "snapshot_time"
                ),

                "error": str(exc)
            })

    created = len([
        result
        for result in results
        if result.get("action")
        == "CREATED"
    ])

    errors = len([
        result
        for result in results
        if result.get("action")
        == "ERROR"
    ])

    return {
        "symbol": symbol.upper(),

        "import_time": datetime.now(
            timezone.utc
        ).isoformat(),

        "total_received": len(
            snapshots
        ),

        "created": created,

        "errors": errors,

        "results": results
    }


# ---------------------------------------------------------
# GET ESTIMATE HISTORY
# ---------------------------------------------------------

def get_estimate_history(
    db: Session,
    symbol: str,
    limit: int = 100
):
    """
    Return historical analyst estimate snapshots
    newest first.
    """

    symbol = symbol.upper()

    company = _get_company(
        db,
        symbol
    )

    if not company:
        raise ValueError(
            f"Company {symbol} does not exist "
            f"in the companies table"
        )

    snapshots = (
        db.query(EstimateSnapshot)
        .filter(
            EstimateSnapshot.company_id
            == company.id
        )
        .order_by(
            EstimateSnapshot.snapshot_time.desc()
        )
        .limit(limit)
        .all()
    )

    results = []

    for snapshot in snapshots:

        results.append({
            "snapshot_id": snapshot.id,

            "snapshot_time": (
                snapshot.snapshot_time.isoformat()
                if snapshot.snapshot_time
                else None
            ),

            "eps_estimate": (
                float(snapshot.eps_estimate)
                if snapshot.eps_estimate
                is not None
                else None
            ),

            "revenue_estimate": (
                float(snapshot.revenue_estimate)
                if snapshot.revenue_estimate
                is not None
                else None
            ),

            "analyst_count": (
                snapshot.analyst_count
            ),

            "source": (
                snapshot.source
            )
        })

    return {
        "symbol": symbol,
        "count": len(results),
        "snapshots": results
    }