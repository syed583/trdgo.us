from sqlalchemy.orm import Session

from models import Company, EarningsEvent


def get_earnings_history(
    db: Session,
    symbol: str,
    limit: int = 12
):
    company = db.query(Company).filter(
        Company.symbol == symbol.upper()
    ).first()

    if not company:
        return {
            "symbol": symbol.upper(),
            "events": [],
            "summary": {
                "total_events": 0,
                "eps_beats": 0,
                "eps_misses": 0,
                "eps_meets": 0,
                "revenue_beats": 0,
                "revenue_misses": 0,
                "revenue_meets": 0,
                "eps_beat_rate": None,
                "revenue_beat_rate": None
            }
        }

    events = (
        db.query(EarningsEvent)
        .filter(
            EarningsEvent.company_id == company.id,
            EarningsEvent.eps_actual.isnot(None)
        )
        .order_by(EarningsEvent.earnings_date.desc())
        .limit(limit)
        .all()
    )

    results = []

    eps_beats = 0
    eps_misses = 0
    eps_meets = 0

    revenue_beats = 0
    revenue_misses = 0
    revenue_meets = 0

    eps_comparable = 0
    revenue_comparable = 0

    for event in events:

        eps_surprise = None
        eps_surprise_percent = None
        eps_result = None

        revenue_surprise = None
        revenue_surprise_percent = None
        revenue_result = None

        # -------------------------------------------------
        # EPS RESULT
        # -------------------------------------------------

        if (
            event.eps_estimate is not None
            and event.eps_actual is not None
        ):
            eps_comparable += 1

            eps_estimate = float(event.eps_estimate)
            eps_actual = float(event.eps_actual)

            eps_surprise = eps_actual - eps_estimate

            if eps_estimate != 0:
                eps_surprise_percent = (
                    eps_surprise / abs(eps_estimate)
                ) * 100

            if eps_actual > eps_estimate:
                eps_result = "BEAT"
                eps_beats += 1

            elif eps_actual < eps_estimate:
                eps_result = "MISS"
                eps_misses += 1

            else:
                eps_result = "MEET"
                eps_meets += 1

        # -------------------------------------------------
        # REVENUE RESULT
        # -------------------------------------------------

        if (
            event.revenue_estimate is not None
            and event.revenue_actual is not None
        ):
            revenue_comparable += 1

            revenue_estimate = float(
                event.revenue_estimate
            )

            revenue_actual = float(
                event.revenue_actual
            )

            revenue_surprise = (
                revenue_actual - revenue_estimate
            )

            if revenue_estimate != 0:
                revenue_surprise_percent = (
                    revenue_surprise
                    / abs(revenue_estimate)
                ) * 100

            if revenue_actual > revenue_estimate:
                revenue_result = "BEAT"
                revenue_beats += 1

            elif revenue_actual < revenue_estimate:
                revenue_result = "MISS"
                revenue_misses += 1

            else:
                revenue_result = "MEET"
                revenue_meets += 1

        results.append({
            "earnings_date": event.earnings_date,
            "reporting_time": event.reporting_time,
            "eps_estimate": (
                float(event.eps_estimate)
                if event.eps_estimate is not None
                else None
            ),
            "eps_actual": (
                float(event.eps_actual)
                if event.eps_actual is not None
                else None
            ),
            "eps_surprise": eps_surprise,
            "eps_surprise_percent": eps_surprise_percent,
            "eps_result": eps_result,
            "revenue_estimate": (
                float(event.revenue_estimate)
                if event.revenue_estimate is not None
                else None
            ),
            "revenue_actual": (
                float(event.revenue_actual)
                if event.revenue_actual is not None
                else None
            ),
            "revenue_surprise": revenue_surprise,
            "revenue_surprise_percent": (
                revenue_surprise_percent
            ),
            "revenue_result": revenue_result,
            "status": event.status
        })

    eps_beat_rate = None

    if eps_comparable > 0:
        eps_beat_rate = round(
            (eps_beats / eps_comparable) * 100,
            2
        )

    revenue_beat_rate = None

    if revenue_comparable > 0:
        revenue_beat_rate = round(
            (revenue_beats / revenue_comparable) * 100,
            2
        )

    return {
        "symbol": company.symbol,
        "events": results,
        "summary": {
            "total_events": len(results),

            "eps_comparable_events": eps_comparable,
            "eps_beats": eps_beats,
            "eps_misses": eps_misses,
            "eps_meets": eps_meets,
            "eps_beat_rate": eps_beat_rate,

            "revenue_comparable_events": revenue_comparable,
            "revenue_beats": revenue_beats,
            "revenue_misses": revenue_misses,
            "revenue_meets": revenue_meets,
            "revenue_beat_rate": revenue_beat_rate
        }
    }