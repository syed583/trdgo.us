"""News quality review on STORED headlines only (research; no scoring, no provider request): who wrote it, is it about the company, is it analyst commentary or company guidance, is it earnings related.

Every headline keeps UNVERIFIED unless the existing news-x1 checks (timestamp, source credibility, entity match) pass. The provider's `sentiment` is reported but never used (it is constant "neutral").
It also writes a labelling sheet (blank `human_label` column) so independent labels can be collected BEFORE any scoring is considered. Nothing here activates NEWS_GUIDANCE.
"""
from __future__ import annotations

import csv
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable

import smart_move_news as NW

EARNINGS_TERMS = re.compile(r"\b(earnings|guidance|outlook|forecast|revenue|eps|quarter|q[1-4]|results|profit|margin)\b", re.I)
HUMAN_LABELS = ("POSITIVE", "NEGATIVE", "NEUTRAL", "NOT_RELEVANT", "UNSURE")


def review(items: Iterable[dict]) -> dict:
    """items: [{symbol, company, retrieved_at (ISO), headline-row fields...}]. Each is classified as of its own retrieval time."""
    rows, seen, dup = [], set(), 0
    for it in items:
        k = (it["symbol"], it.get("created_at"), it.get("source"), str(it.get("headline", "")).strip().lower())
        if k in seen:
            dup += 1
            continue
        seen.add(k)
        cap = datetime.fromisoformat(it["retrieved_at"])
        c = NW.classify(it, it["symbol"], it.get("company"), cap)
        text = str(it.get("headline") or "")
        guidance = bool(NW.GUIDE_POS.search(text) or NW.GUIDE_NEG.search(text))
        rows.append({"symbol": it["symbol"], "published": it.get("created_at"), "retrieved_at": it["retrieved_at"], "source": it.get("source"), "headline": text,
                     "provider_sentiment": it.get("sentiment"), "research_label": c["label"], "category": c["category"], "reason": c["reason"],
                     "company_specific": c["category"] in ("COMPANY_NEWS", "COMPANY_EVENT", "GUIDANCE_POSITIVE", "GUIDANCE_NEGATIVE") or guidance,
                     "ticker_collision": c["category"] == "ENTITY_MISMATCH", "unreliable_source": c["category"] == "LOW_CREDIBILITY_SOURCE",
                     "analyst_target_or_rating": c["category"] == "ANALYST_ACTION" or bool(NW.ANALYST.search(text)), "explicit_guidance_change": guidance,
                     "earnings_related": bool(EARNINGS_TERMS.search(text)), "in_window": c["category"] not in ("OUT_OF_WINDOW", "NOT_PRE_ANNOUNCEMENT", "NO_TIMESTAMP")})
    n = len(rows)
    flag = lambda k: sum(1 for r in rows if r[k])  # noqa: E731
    return {"headlines": n, "duplicates_removed": dup, "labels": dict(Counter(r["research_label"] for r in rows)), "categories": dict(Counter(r["category"] for r in rows)),
            "provider_sentiment": dict(Counter(str(r["provider_sentiment"]) for r in rows)), "sources": dict(Counter(str(r["source"]) for r in rows)),
            "company_specific": flag("company_specific"), "ticker_collisions": flag("ticker_collision"), "unreliable_source": flag("unreliable_source"), "analyst_target_or_rating": flag("analyst_target_or_rating"),
            "explicit_guidance_change": flag("explicit_guidance_change"), "earnings_related": flag("earnings_related"), "in_window": flag("in_window"),
            "usable_candidates": sum(1 for r in rows if r["in_window"] and r["research_label"] in ("POSITIVE", "NEGATIVE")),
            "note": "Usable candidates are research candidates only. Provider sentiment is not used. Nothing is scored.", "rows": rows}


def labelling_sheet(rows: list, path) -> Path:
    """Blank human_label column; an independent reviewer fills it in. Never overwrites."""
    p = Path(path)
    if p.exists():
        raise FileExistsError(p)
    cols = ["symbol", "published", "source", "headline", "provider_sentiment", "research_label", "category", "company_specific", "analyst_target_or_rating", "explicit_guidance_change", "earnings_related", "human_label", "reviewer", "notes"]
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(dict(r, human_label="", reviewer="", notes=""))
    return p


def validation_gate(labelled: list, min_labelled: int = 200, min_per_class: int = 30, min_agreement: float = 0.8) -> dict:
    """The plan's pass/fail gate for enabling news scoring: independently labelled examples, per-class minimum, agreement of the classifier with the human label on non-UNSURE rows.
    labelled: [{research_label, human_label}]. Returns the measured numbers and whether each pre-declared requirement is met. It does NOT enable anything."""
    usable = [r for r in labelled if r.get("human_label") in ("POSITIVE", "NEGATIVE", "NEUTRAL")]
    per = Counter(r["human_label"] for r in usable)
    directional = [r for r in usable if r["human_label"] in ("POSITIVE", "NEGATIVE") or r["research_label"] in ("POSITIVE", "NEGATIVE")]
    agree = sum(1 for r in directional if r["human_label"] == r["research_label"])
    rate = agree / len(directional) if directional else None
    checks = {"enough_labelled": len(usable) >= min_labelled, "enough_per_class": all(per.get(c, 0) >= min_per_class for c in ("POSITIVE", "NEGATIVE")),
              "directional_agreement": rate is not None and rate >= min_agreement}
    return {"labelled": len(usable), "per_class": dict(per), "directional_rows": len(directional), "agreement": round(rate, 3) if rate is not None else None, "requirements": checks,
            "may_be_considered_for_scoring": all(checks.values()), "approval": "Even when every requirement is met, scoring needs the owner's explicit approval; this function enables nothing."}
