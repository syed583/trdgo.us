"""
Deterministic, data-derived insights.

Every line produced here is a restatement of a value the backend already
verified, with the field it came from attached. Nothing is inferred about the
market, and there is no language model in the path - so the output is labelled
DATA_BASED_INSIGHT to keep it distinct from any future generated commentary.

Where an input is missing, that absence is itself reported rather than
skipped, because "we could not check this" is the part a trader most needs.
"""

from __future__ import annotations

from typing import Any, Optional

import live_market_service as market

INSIGHT_KIND = "DATA_BASED_INSIGHT"


def _fmt(value, suffix="", digits=2):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return f"{round(value, digits)}{suffix}"
    return str(value)


def build_insights(symbol: str) -> dict:
    """Assemble the insight set for one symbol from verified backend fields."""
    symbol = symbol.upper()

    import live_options_analytics as opt_analytics
    import live_options_service as options
    import live_score_service as scoring

    quote = market.get_quote(symbol)
    score = scoring.get_trdgo_score(symbol)
    chain = options.load_chain(symbol)
    metrics = (opt_analytics.get_metrics(chain, options.get_iv_history(symbol))
               if chain.get("status") == "OK" else {})

    final = score.get("final") or {}
    components = score.get("components") or {}

    findings: list[dict] = []
    gaps: list[dict] = []

    # ---- direction -------------------------------------------------------
    direction = final.get("direction_score")
    decision = final.get("decision")
    confidence = final.get("confidence_score")
    conf_label = final.get("confidence_label")

    if direction is not None:
        findings.append({
            "category": "Direction",
            "text": (f"{symbol} scores {round(direction)}/100 "
                     f"({str(decision).replace('_', ' ')}) with "
                     f"{conf_label or 'unlabelled'} confidence "
                     f"({_fmt(confidence)}/100)."),
            "field": "score.final.direction_score",
            "tone": ("positive" if "BUY" in str(decision)
                     else "negative" if "SELL" in str(decision) else "neutral"),
        })

    # The gate is the single most important thing to surface.
    if direction is not None and confidence is not None:
        if direction >= 70 and confidence < 60:
            findings.append({
                "category": "Gate",
                "text": (f"High score ({round(direction)}) is gated by low "
                         f"confidence ({round(confidence)}). Treated as WATCH, "
                         "not a buy signal."),
                "field": "trdgo_score_service gating",
                "tone": "warning",
            })

    # ---- components ------------------------------------------------------
    for name, comp in components.items():
        label = name.replace("_", " ").title()
        status = str(comp.get("status", "")).upper()
        value, mx = comp.get("score"), comp.get("max_score")

        if status != "OK" or value is None:
            gaps.append({
                "category": label,
                "text": f"{label} not scored: {status.replace('_', ' ').title()}.",
                "reason": (comp.get("reasons") or ["No verified data"])[0],
                "status": status,
            })
            continue

        reasons = comp.get("reasons") or []
        findings.append({
            "category": label,
            "text": (f"{label} scores {value}/{mx}"
                     + (f" — {reasons[0]}" if reasons else ".")),
            "field": f"score.components.{name}",
            "tone": ("positive" if "BULL" in str(comp.get("bias", ""))
                     else "negative" if "BEAR" in str(comp.get("bias", ""))
                     else "neutral"),
        })

    # ---- options ---------------------------------------------------------
    if metrics.get("status") == "OK":
        em = metrics.get("expected_move_percent")
        realized = metrics.get("realized_move_percent")
        ivp = metrics.get("iv_percentile")

        if em is not None:
            findings.append({
                "category": "Options",
                "text": (f"Options imply a ±{em}% move by "
                         f"{metrics.get('expiry_label')}."),
                "field": "options.expected_move_percent",
                "tone": "neutral",
            })
        if ivp is not None:
            findings.append({
                "category": "Volatility",
                "text": (f"Implied volatility sits in the {round(ivp)}th "
                         f"percentile of the last year (IV rank "
                         f"{_fmt(metrics.get('iv_rank'), digits=0)})."),
                "field": "options.iv_percentile",
                "tone": "warning" if ivp >= 70 else "positive" if ivp <= 30 else "neutral",
            })
        if em is not None and realized is not None:
            cheaper = realized > em
            findings.append({
                "category": "Volatility",
                "text": (f"Realised moves averaged {realized}% versus the "
                         f"{em}% now implied — options look "
                         f"{'cheap' if cheaper else 'rich'} against recent history."),
                "field": "options.realized_move_percent",
                "tone": "positive" if cheaper else "warning",
            })
    else:
        gaps.append({
            "category": "Options",
            "text": "Option analytics unavailable.",
            "reason": chain.get("error") or chain.get("status", "No chain"),
            "status": chain.get("status", "DATA_UNAVAILABLE"),
        })

    # ---- price context ---------------------------------------------------
    if quote.get("status") == "OK":
        findings.append({
            "category": "Price",
            "text": (f"Last {quote['price']}, "
                     f"{'up' if (quote.get('change') or 0) >= 0 else 'down'} "
                     f"{abs(quote.get('change') or 0)} "
                     f"({quote.get('change_percent')}%) "
                     f"during {quote['market']['label'].lower()}."),
            "field": "quote.price",
            "tone": "positive" if (quote.get("change") or 0) >= 0 else "negative",
        })
    else:
        gaps.append({
            "category": "Price",
            "text": "No live quote.",
            "reason": quote.get("error") or quote.get("status"),
            "status": quote.get("status", "DATA_UNAVAILABLE"),
        })

    return {
        "symbol": symbol,
        "kind": INSIGHT_KIND,
        "kind_label": "Data-based insight",
        "disclaimer": (
            "Every line below restates a verified backend field. No language "
            "model produced this text and nothing is inferred beyond the data."
        ),
        "findings": findings,
        "gaps": gaps,
        "summary": {
            "direction_score": direction,
            "decision": decision,
            "confidence": confidence,
            "confidence_label": conf_label,
            "missing_components": final.get("missing_components") or [],
            "risk_level": (score.get("risk") or {}).get("risk_level"),
        },
        "market": market.market_clock(),
        "status": "OK" if findings else "INSUFFICIENT_DATA",
        "source": "IBKR+SEC+DATABASE",
    }
