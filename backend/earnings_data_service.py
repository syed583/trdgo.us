from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from models import Company, EarningsEvent


# ---------------------------------------------------------
# NORMALIZE NUMBER
# ---------------------------------------------------------

def _to_decimal(value):
    if value is None:
        return None

    try:
        return Decimal(str(value))
    except Exception:
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
# SAVE ONE HISTORICAL EARNINGS EVENT
# ---------------------------------------------------------

def save_earnings_event(
    db: Session,
    symbol: str,
    earnings_date,
    reporting_time=None,
    eps_estimate=None,
    eps_actual=None,
    revenue_estimate=None,
    revenue_actual=None,
    status="reported"
):
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
    # NORMALIZE DATE
    # -----------------------------------------------------

    if isinstance(earnings_date, str):
        earnings_date = datetime.strptime(
            earnings_date,
            "%Y-%m-%d"
        ).date()

    # -----------------------------------------------------
    # CHECK EXISTING EVENT
    # -----------------------------------------------------

    existing = (
        db.query(EarningsEvent)
        .filter(
            EarningsEvent.company_id == company.id,
            EarningsEvent.earnings_date == earnings_date
        )
        .first()
    )

    # -----------------------------------------------------
    # UPDATE EXISTING
    # -----------------------------------------------------

    if existing:

        existing.reporting_time = (
            reporting_time
            if reporting_time is not None
            else existing.reporting_time
        )

        existing.eps_estimate = (
            _to_decimal(eps_estimate)
            if eps_estimate is not None
            else existing.eps_estimate
        )

        existing.eps_actual = (
            _to_decimal(eps_actual)
            if eps_actual is not None
            else existing.eps_actual
        )

        existing.revenue_estimate = (
            _to_decimal(revenue_estimate)
            if revenue_estimate is not None
            else existing.revenue_estimate
        )

        existing.revenue_actual = (
            _to_decimal(revenue_actual)
            if revenue_actual is not None
            else existing.revenue_actual
        )

        existing.status = status

        db.commit()
        db.refresh(existing)

        return {
            "action": "UPDATED",
            "symbol": symbol,
            "event_id": existing.id,
            "earnings_date": str(
                existing.earnings_date
            )
        }

    # -----------------------------------------------------
    # CREATE NEW EVENT
    # -----------------------------------------------------

    event = EarningsEvent(
        company_id=company.id,
        earnings_date=earnings_date,
        reporting_time=reporting_time,
        eps_estimate=_to_decimal(
            eps_estimate
        ),
        eps_actual=_to_decimal(
            eps_actual
        ),
        revenue_estimate=_to_decimal(
            revenue_estimate
        ),
        revenue_actual=_to_decimal(
            revenue_actual
        ),
        status=status
    )

    db.add(event)

    db.commit()
    db.refresh(event)

    return {
        "action": "CREATED",
        "symbol": symbol,
        "event_id": event.id,
        "earnings_date": str(
            event.earnings_date
        )
    }


# ---------------------------------------------------------
# IMPORT MULTIPLE EARNINGS EVENTS
# ---------------------------------------------------------

def import_earnings_history(
    db: Session,
    symbol: str,
    events: list
):
    results = []

    for item in events:

        try:

            result = save_earnings_event(
                db=db,
                symbol=symbol,
                earnings_date=item.get(
                    "earnings_date"
                ),
                reporting_time=item.get(
                    "reporting_time"
                ),
                eps_estimate=item.get(
                    "eps_estimate"
                ),
                eps_actual=item.get(
                    "eps_actual"
                ),
                revenue_estimate=item.get(
                    "revenue_estimate"
                ),
                revenue_actual=item.get(
                    "revenue_actual"
                ),
                status=item.get(
                    "status",
                    "reported"
                )
            )

            results.append(result)

        except Exception as exc:

            db.rollback()

            results.append({
                "action": "ERROR",
                "symbol": symbol.upper(),
                "earnings_date": item.get(
                    "earnings_date"
                ),
                "error": str(exc)
            })

    created = len([
        x for x in results
        if x["action"] == "CREATED"
    ])

    updated = len([
        x for x in results
        if x["action"] == "UPDATED"
    ])

    errors = len([
        x for x in results
        if x["action"] == "ERROR"
    ])

    return {
        "symbol": symbol.upper(),

        "import_time": datetime.now(
            timezone.utc
        ).isoformat(),

        "total_received": len(events),

        "created": created,

        "updated": updated,

        "errors": errors,

        "results": results
    }