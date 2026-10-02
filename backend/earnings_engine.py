"""
The scoring primitive shared by the two earnings engines.

Each engine supplies a list of Params -- one per the structure document's
weighted parameters, with the exact weights given there. A Param carries its own
weight and a signed bias in [-1, +1] (None when the data is unavailable). The
score is computed over the weight that actually returned data, and the coverage
is reported alongside, so a thin-data name reads as "65/100, 70% covered" rather
than being quietly penalised for missing inputs.

Nothing here is a validated probability; the weights are the document's proposed
development settings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Param:
    name: str
    label: str
    weight: int
    # -1..+1 toward the engine's positive side (BUY for equity; "the move will
    # beat the premium" for the options straddle). None = data unavailable.
    bias: Optional[float]
    detail: str = ""
    evidence: dict = field(default_factory=dict)
    unavailable_reason: str = ""

    @property
    def available(self) -> bool:
        return self.bias is not None

    @property
    def points(self) -> float:
        """Signed contribution, in points of this parameter's weight."""
        return 0.0 if self.bias is None else max(-1.0, min(1.0, self.bias)) * self.weight

    def as_dict(self) -> dict:
        b = self.bias
        lean = (None if b is None else "Bullish" if b > 0.15
                else "Bearish" if b < -0.15 else "Neutral")
        return {
            "name": self.name, "label": self.label, "weight": self.weight,
            "available": self.available,
            "bias": None if b is None else round(b, 3),
            "points": round(self.points, 2),
            "points_label": (f"{self.points:+.1f} of {self.weight}"
                             if self.available else "no data"),
            "detail": self.detail, "evidence": self.evidence,
            "leaning": lean, "unavailable_reason": self.unavailable_reason,
        }


def score(params: list[Param], *, pos_label: str, neg_label: str,
          buy_at: float = 58.0, sell_at: float = 42.0,
          min_coverage: float = 40.0) -> dict:
    """
    Combine weighted params into a 0-100 evidence score and a decision.

    `pos_label`/`neg_label` name the two sides (e.g. BUY/SELL, or
    STRADDLE/NO TRADE). Score is 50 (neutral) plus the net lean over the weight
    that returned data; 50 means balanced or empty.
    """
    present = sum(p.weight for p in params if p.available)
    possible = sum(p.weight for p in params) or 1
    coverage = round(present / possible * 100, 1)

    if present == 0:
        return {"score": None, "decision": "NO TRADE", "coverage_pct": 0.0,
                "evidence_present": 0, "evidence_possible": possible,
                "confidence": 0.0, "agreement_pct": 0.0,
                "reasons": ["No parameter returned data."],
                "params": [p.as_dict() for p in params]}

    net = sum(p.points for p in params if p.available)          # signed
    s = 50.0 + (net / present) * 50.0
    s = max(0.0, min(100.0, s))

    # Agreement: share of available weight leaning the dominant direction.
    bull = sum(p.weight for p in params if p.available and (p.bias or 0) > 0.15)
    bear = sum(p.weight for p in params if p.available and (p.bias or 0) < -0.15)
    dominant = max(bull, bear)
    agreement = round(dominant / present * 100, 1) if present else 0.0

    # Confidence blends how much data arrived with how aligned it is.
    confidence = round(min(100.0, (coverage * 0.5) + (agreement * 0.5)), 1)

    reasons: list[str] = []
    actionable = coverage >= min_coverage and confidence >= 45.0
    if coverage < min_coverage:
        reasons.append(f"Only {coverage:.0f}% of the evidence was available "
                       f"(needs {min_coverage:.0f}%).")
    if agreement < 55.0:
        reasons.append("The parameters are split; no clear edge.")

    if not actionable:
        decision = "NO TRADE"
    elif s >= buy_at:
        decision = pos_label
    elif s <= sell_at:
        decision = neg_label
    else:
        decision = "NO TRADE"
        if not reasons:
            reasons.append("The evidence is close to neutral.")

    return {
        "score": round(s, 1),
        "decision": decision,
        "coverage_pct": coverage,
        "evidence_present": present,
        "evidence_possible": possible,
        "agreement_pct": agreement,
        "confidence": confidence,
        "actionable": actionable,
        "reasons": reasons,
        "params": [p.as_dict() for p in params],
    }
