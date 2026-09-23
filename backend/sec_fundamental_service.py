from datetime import datetime, timezone

from sec_service import get_company_facts


# ---------------------------------------------------------
# FRESHNESS SETTINGS
# ---------------------------------------------------------

# Financial facts older than this are considered stale.
MAX_AGE_DAYS = 450


# ---------------------------------------------------------
# GET LATEST FACT FOR ONE SEC TAG
# ---------------------------------------------------------

def _latest_fact(facts, tag, unit="USD"):
    us_gaap = facts.get("us-gaap", {})

    item = us_gaap.get(tag)

    if not item:
        return None

    units = item.get("units", {})

    values = units.get(unit)

    if not values:
        return None

    valid = []

    for item_value in values:

        if item_value.get("val") is None:
            continue

        if item_value.get("filed") is None:
            continue

        valid.append(item_value)

    if not valid:
        return None

    valid.sort(
        key=lambda x: (
            x.get("filed", ""),
            x.get("end", "")
        )
    )

    latest = valid[-1]

    filed_date = latest.get("filed")

    age_days = None
    is_stale = True

    if filed_date:
        try:
            filed_dt = datetime.strptime(
                filed_date,
                "%Y-%m-%d"
            ).replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)

            age_days = (now - filed_dt).days

            is_stale = age_days > MAX_AGE_DAYS

        except Exception:
            is_stale = True

    return {
        "value": latest.get("val"),
        "form": latest.get("form"),
        "fiscal_year": latest.get("fy"),
        "fiscal_period": latest.get("fp"),
        "period_start": latest.get("start"),
        "period_end": latest.get("end"),
        "filed": latest.get("filed"),
        "accession_number": latest.get("accn"),
        "sec_tag": tag,
        "age_days": age_days,
        "is_stale": is_stale,
        "data_status": (
            "STALE"
            if is_stale
            else "CURRENT"
        )
    }


# ---------------------------------------------------------
# FIND NEWEST FACT ACROSS MULTIPLE POSSIBLE SEC TAGS
# ---------------------------------------------------------

def _latest_fact_multi(facts, tags, unit="USD"):
    candidates = []

    for tag in tags:

        result = _latest_fact(
            facts,
            tag,
            unit
        )

        if result is not None:
            candidates.append(result)

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (
            x.get("filed") or "",
            x.get("period_end") or ""
        )
    )

    return candidates[-1]


# ---------------------------------------------------------
# CLEAN FUNDAMENTALS
# ---------------------------------------------------------

def get_clean_fundamentals(symbol: str):
    company_data = get_company_facts(symbol)

    facts = company_data["facts"]

    # -----------------------------------------------------
    # REVENUE
    # -----------------------------------------------------

    revenue = _latest_fact_multi(
        facts,
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "Revenues",
            "SalesRevenueNet"
        ],
        "USD"
    )

    # -----------------------------------------------------
    # NET INCOME
    # -----------------------------------------------------

    net_income = _latest_fact_multi(
        facts,
        [
            "NetIncomeLoss",
            "ProfitLoss"
        ],
        "USD"
    )

    # -----------------------------------------------------
    # DILUTED EPS
    # -----------------------------------------------------

    eps_diluted = _latest_fact_multi(
        facts,
        [
            "EarningsPerShareDiluted"
        ],
        "USD/shares"
    )

    # -----------------------------------------------------
    # TOTAL ASSETS
    # -----------------------------------------------------

    total_assets = _latest_fact_multi(
        facts,
        [
            "Assets"
        ],
        "USD"
    )

    # -----------------------------------------------------
    # TOTAL LIABILITIES
    # IMPORTANT:
    # Do NOT use LiabilitiesAndStockholdersEquity here.
    # That includes shareholder equity and is not the
    # same thing as total liabilities.
    # -----------------------------------------------------

    total_liabilities = _latest_fact_multi(
        facts,
        [
            "Liabilities"
        ],
        "USD"
    )

    # -----------------------------------------------------
    # CASH
    # -----------------------------------------------------

    cash = _latest_fact_multi(
        facts,
        [
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
        ],
        "USD"
    )

    # -----------------------------------------------------
    # DEBT
    # -----------------------------------------------------

    debt = _latest_fact_multi(
        facts,
        [
            "LongTermDebtCurrent",
            "LongTermDebtNoncurrent",
            "LongTermDebtAndFinanceLeaseObligationsCurrent",
            "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
            "LongTermDebt"
        ],
        "USD"
    )

    # -----------------------------------------------------
    # OPERATING CASH FLOW
    # -----------------------------------------------------

    operating_cash_flow = _latest_fact_multi(
        facts,
        [
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"
        ],
        "USD"
    )

    # -----------------------------------------------------
    # CAPEX
    # -----------------------------------------------------

    capex = _latest_fact_multi(
        facts,
        [
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsForAdditionsToPropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
            "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets"
        ],
        "USD"
    )

    # -----------------------------------------------------
    # BUILD FUNDAMENTAL DATA
    # -----------------------------------------------------

    fundamentals = {
        "revenue": revenue,
        "net_income": net_income,
        "eps_diluted": eps_diluted,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "cash": cash,
        "debt": debt,
        "operating_cash_flow": operating_cash_flow,
        "capex": capex
    }

    # -----------------------------------------------------
    # DATA QUALITY
    # -----------------------------------------------------

    stale_fields = []
    missing_fields = []
    current_fields = []

    for name, value in fundamentals.items():

        if value is None:
            missing_fields.append(name)

        elif value.get("is_stale"):
            stale_fields.append(name)

        else:
            current_fields.append(name)

    total_fields = len(fundamentals)
    current_count = len(current_fields)

    if total_fields > 0:
        completeness_percent = round(
            (current_count / total_fields) * 100,
            2
        )
    else:
        completeness_percent = 0

    # -----------------------------------------------------
    # OVERALL DATA STATUS
    # -----------------------------------------------------

    if stale_fields:
        overall_status = "WARNING"

    elif missing_fields:
        overall_status = "PARTIAL"

    else:
        overall_status = "GOOD"

    # -----------------------------------------------------
    # RETURN
    # -----------------------------------------------------

    return {
        "symbol": company_data["symbol"],
        "cik": company_data["cik"],
        "company_name": company_data["company_name"],
        "source": "SEC EDGAR",

        "data_quality": {
            "status": overall_status,
            "current_fields": current_fields,
            "stale_fields": stale_fields,
            "missing_fields": missing_fields,
            "current_data_percent": completeness_percent
        },

        "fundamentals": fundamentals
    }