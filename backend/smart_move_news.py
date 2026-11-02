"""Transparent headline classifier (news-x1) — RESEARCH ONLY. It awards NO scoring points: classification must first be validated on independently labelled headlines.

Why the provider label is not used: all 56 saved Unusual Whales headlines carry sentiment "neutral" (including "Apple cuts ...", "Analyst downgrade ..." and "complaints of the screen cracking"), the API spec does not define the field,
and the feed mixes company wires, a headline aggregator, crypto-futures commentary on tokenised tickers and ticker collisions (e.g. "Vortex Energy" tagged VRTX, "New Book from AAPL" = a physician association).

Labels: POSITIVE | NEGATIVE | NEUTRAL | UNVERIFIED. Rules, applied in this order, each returns its reason:
  1. timestamp: a headline stamped at or after min(capture, earnings release) is never used (post-announcement information); one older than the lookback is out of window;
  2. source credibility: `LOW_CREDIBILITY` sources (crypto-futures commentary) -> UNVERIFIED; unknown sources -> UNVERIFIED;
  3. entity check: the company name (first significant word) or a cashtag ($TICKER) must appear; a bare ticker word or a tag alone is not enough -> otherwise UNVERIFIED (ticker collision);
  4. category exclusions that would double-count another factor: ANALYST_ACTION (rating/target changes: already in the ANALYST factor), PRICE_ACTION (price moves: already in the price factors), EARNINGS_RESULT of an
     earlier quarter, TRADE_DISCLOSURE; they are labelled NEUTRAL with the category shown;
  5. guidance / event lexicons: explicit phrases only (raise/lower/cut/withdraw guidance or outlook, recall, lawsuit, probe, complaints, record revenue, ...). Anything else is NEUTRAL.
The per-stock `news_direction` needs at least 2 more POSITIVE than NEGATIVE verified, in-window, non-excluded headlines (or the reverse); otherwise NEUTRAL, or UNVERIFIED when no headline passed the checks.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

VERSION = "news-x1"
LOOKBACK_DAYS = 7
NET_MIN = 2
TRUSTED = {"business wire", "pr newswire", "globenewswire", "unusual whales"}          # company/regulatory wires
NEWSFEED = {"tradex"}                                                                     # headline squawk with cashtags; usable only with the entity check
LOW_CREDIBILITY = {"blockchain news"}                                                     # price-prediction commentary on tokenised futures, not company news

ANALYST = re.compile(r"\b(upgrad\w*|downgrad\w*|price target|target (?:of|to|at)|raises? target|lifts? targets?|analysts?|initiates? coverage|outperform|underperform|overweight|underweight)\b", re.I)
PRICE = re.compile(r"(\bshares? (?:touch|hit|reach)\w* record high\b|\brecord high\b|\bfalls? by [\d.]+%|\bdropped [\d.]+%|\bjumps? to \$|\bsurges?\b|\bplunges?\b|\bMACD\b|\bRSI\b|\bBollinger\b)", re.I)
TRADE_DISCLOSURE = re.compile(r"\brevealed transactions\b|\bdisclos\w+ (?:the )?(?:purchase|sale)\b", re.I)
EARNINGS_RESULT = re.compile(r"\b(?:posted|reports?|reported)\b.*\b(?:q[1-4]|quarter)\b.*\b(?:revenue|eps)\b|\b(?:above|below) the .{0,12}estimate\b", re.I)
EARNINGS_DATE = re.compile(r"\bannounces? date of\b.*\bearnings\b", re.I)
GUIDE_POS = re.compile(r"\b(?:raises?|raised|lifts?|lifted|increases?|increased)\b.{0,25}\b(?:guidance|outlook|forecast)\b", re.I)
GUIDE_NEG = re.compile(r"\b(?:lowers?|lowered|cuts?|cut|reduces?|reduced|withdraws?|withdrew|suspends?)\b.{0,25}\b(?:guidance|outlook|forecast)\b", re.I)
POS = re.compile(r"\b(?:record (?:revenue|sales|quarter)|fda approv\w+|wins? (?:contract|award)|awarded (?:a )?contract|(?:increased|increases|boosts?) (?:\w+ ){0,3}shipments? by \d+)\b", re.I)
NEG = re.compile(r"\b(?:recall\w*|lawsuit|sued|probe|investigation|subpoena|fined|complaints?|layoffs?|bankruptcy|delays?|halts?|data breach|class action|warning letter)\b", re.I)
STOP = {"inc", "corp", "corporation", "co", "company", "plc", "ltd", "the", "holdings", "group", "technologies", "pharmaceuticals", "cos"}


def _t(x) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def name_token(company: Optional[str]) -> Optional[str]:
    for w in re.findall(r"[A-Za-z][A-Za-z&.-]+", company or ""):
        if w.lower().strip(".") not in STOP:
            return w.lower().strip(".")
    return None


def classify(h: dict, symbol: str, company: Optional[str], capture_ts: datetime, release_ts: Optional[datetime] = None, lookback_days: int = LOOKBACK_DAYS) -> dict:
    text = str(h.get("headline") or "")
    src = str(h.get("source") or "").strip().lower()
    t = _t(h.get("created_at"))
    out = {"label": "NEUTRAL", "category": "COMPANY_NEWS", "reason": "", "source": h.get("source"), "created_at": h.get("created_at"), "headline": text}

    def done(label, cat, why):
        out.update(label=label, category=cat, reason=why)
        return out
    cut = min(capture_ts, release_ts) if release_ts else capture_ts
    if t is None:
        return done("UNVERIFIED", "NO_TIMESTAMP", "no valid timestamp")
    if t >= cut:
        return done("UNVERIFIED", "NOT_PRE_ANNOUNCEMENT", "stamped at or after the capture/release cut: never used")
    if cut - t > timedelta(days=lookback_days):
        return done("NEUTRAL", "OUT_OF_WINDOW", "older than %d days" % lookback_days)
    if src in LOW_CREDIBILITY:
        return done("UNVERIFIED", "LOW_CREDIBILITY_SOURCE", "price-prediction commentary on tokenised futures, not company news")
    if src not in TRUSTED and src not in NEWSFEED:
        return done("UNVERIFIED", "UNKNOWN_SOURCE", "source not on the credibility list")
    tok = name_token(company)
    cashtag = ("$" + symbol.upper()) in text.upper()
    named = bool(tok and re.search(r"\b" + re.escape(tok) + r"\b", text, re.I))
    if not (named or cashtag):
        return done("UNVERIFIED", "ENTITY_MISMATCH", "neither the company name nor a $%s cashtag appears (ticker collision or a different company)" % symbol)
    if tok and re.search(r"\b" + re.escape(tok[:-1]) + r"[a-z]\b", text, re.I) and not named:
        return done("UNVERIFIED", "ENTITY_MISMATCH", "a similar but different company name")
    if TRADE_DISCLOSURE.search(text):
        return done("NEUTRAL", "TRADE_DISCLOSURE", "excluded: insider/politician trade list, not company news")
    if ANALYST.search(text):
        return done("NEUTRAL", "ANALYST_ACTION", "excluded: rating/target changes are counted once, in the ANALYST factor")
    if EARNINGS_RESULT.search(text):
        return done("NEUTRAL", "EARNINGS_RESULT", "excluded: a reported result of an earlier quarter, not pre-announcement information")
    if EARNINGS_DATE.search(text):
        return done("NEUTRAL", "EARNINGS_DATE", "scheduling notice, no direction")
    if PRICE.search(text):
        return done("NEUTRAL", "PRICE_ACTION", "excluded: price moves are already in the price factors")
    if GUIDE_POS.search(text):
        return done("POSITIVE", "GUIDANCE", "raises guidance/outlook")
    if GUIDE_NEG.search(text):
        return done("NEGATIVE", "GUIDANCE", "lowers/withdraws guidance/outlook")
    if NEG.search(text):
        return done("NEGATIVE", "COMPANY_EVENT", "explicit negative event term: " + NEG.search(text).group(0))
    if POS.search(text):
        return done("POSITIVE", "COMPANY_EVENT", "explicit positive event term: " + POS.search(text).group(0))
    return done("NEUTRAL", "COMPANY_NEWS", "no explicit directional term")


def stock_direction(headlines, symbol, company, capture_ts, release_ts=None, lookback_days: int = LOOKBACK_DAYS) -> dict:
    cls = [classify(h, symbol, company, capture_ts, release_ts, lookback_days) for h in (headlines or [])]
    valid = [c for c in cls if c["category"] not in ("LOW_CREDIBILITY_SOURCE", "UNKNOWN_SOURCE", "ENTITY_MISMATCH", "NO_TIMESTAMP", "NOT_PRE_ANNOUNCEMENT", "OUT_OF_WINDOW")]
    pos, neg = sum(c["label"] == "POSITIVE" for c in valid), sum(c["label"] == "NEGATIVE" for c in valid)
    if headlines is None:
        direction, why = "NOT_COLLECTED", "no headlines saved for this stock"
    elif not valid:
        direction, why = "UNVERIFIED", "no headline passed the source/entity/timestamp checks (%d seen)" % len(cls)
    elif pos - neg >= NET_MIN:
        direction, why = "POSITIVE", "%d positive vs %d negative verified headlines" % (pos, neg)
    elif neg - pos >= NET_MIN:
        direction, why = "NEGATIVE", "%d negative vs %d positive verified headlines" % (neg, pos)
    else:
        direction, why = "NEUTRAL", "%d positive / %d negative of %d verified in-window headlines: no net signal" % (pos, neg, len(valid))
    return {"version": VERSION, "direction": direction, "reason": why, "verified_in_window": len(valid), "positive": pos, "negative": neg, "seen": len(cls), "scoring_points": 0,
            "validation": "NOT VALIDATED (author labels only): no points are awarded", "labels": cls}
