"""Per-event news-quality audit on SAVED headlines only (Phase 13). Offline: reads stored evidence, sends nothing, scores nothing, writes only the report files it is asked to.

Every saved headline is classified against ITS event's prediction cutoff:
  INVALID     blank headline, missing/unparseable timestamp, published at/after the cutoff, or published after it was retrieved.
  DUPLICATE   the same normalised headline already seen for the event (the earliest retrieved copy is the one that counts).
  UNVERIFIED  the page was retrieved after the cutoff (availability before the cutoff cannot be proven), the source is unknown/low-credibility, or the company cannot be checked.
  STALE       published more than 7 days before the cutoff.
  IRRELEVANT  another company/ticker collision, the provider's ticker list excludes the symbol, an insider/politician trade list, a price-move note, or the result of an earlier quarter.
  VALID       none of the above: a pre-cutoff, fresh, trusted-source headline that names the company (or its cashtag).
It only READS the existing news-x1 rules (smart_move_news); sentiment scores and NEWS_GUIDANCE are not touched, and the provider's own sentiment label is reported but never used."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import smart_move_news as NW
import smart_move_news_quality as NQ

UTC = timezone.utc
CLASSES = ("VALID", "STALE", "DUPLICATE", "IRRELEVANT", "UNVERIFIED", "INVALID")
FRESH_DAYS = NW.LOOKBACK_DAYS                                    # seven days, the existing news-x1 window
QUARTER_WORDS = re.compile(r"\b(first|second|third|fourth|q[1-4])\b[\s-]*(?:quarter)?", re.I)
YEARS = re.compile(r"\b(20\d\d)\b")


def _dt(x) -> Optional[datetime]:
    if x in (None, ""):
        return None
    try:
        d = x if isinstance(x, datetime) else datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def norm(text) -> str:
    return " ".join(re.sub(r"[^a-z0-9$ ]+", " ", str(text or "").lower()).split())


def quarter_relevance(headline: str, quarter_end: Optional[str]) -> dict:
    """Reports what the headline says about a period and whether that can be compared with the event's fiscal period. A fiscal quarter number is never derived from a calendar date: the check is on the YEAR only
    and only when the event's period end is known."""
    mention = bool(QUARTER_WORDS.search(headline)) and ("quarter" in headline.lower() or bool(re.search(r"\bq[1-4]\b", headline, re.I)))
    years = sorted(set(YEARS.findall(headline)))
    if not mention and not years:
        return {"mentions_period": False, "years": [], "relevance": "NO_PERIOD_MENTION"}
    if not quarter_end or quarter_end == "QUARTER_UNKNOWN":
        return {"mentions_period": mention, "years": years, "relevance": "NOT_CHECKABLE_QUARTER_UNKNOWN"}
    y = int(quarter_end[:4])
    if years and not ({str(y), str(y + 1)} & set(years)):
        return {"mentions_period": mention, "years": years, "relevance": "YEAR_DIFFERS_FROM_EVENT_PERIOD"}
    return {"mentions_period": mention, "years": years, "relevance": "YEAR_COMPATIBLE_WITH_EVENT_PERIOD" if years else "PERIOD_WORD_WITHOUT_YEAR"}


def classify_headline(h: dict, symbol: str, company: Optional[str], cutoff: datetime, retrieved_at: datetime, seen: dict, quarter_end: Optional[str] = None) -> dict:
    text = str(h.get("headline") or "").strip()
    pub = _dt(h.get("created_at"))
    src = str(h.get("source") or "").strip()
    sl = src.lower()
    out = {"headline": text, "source": src or None, "published": h.get("created_at"), "retrieved_at": retrieved_at.isoformat(), "provider_sentiment": h.get("sentiment"), "provider_tickers": h.get("tickers"),
           "class": None, "reason": None, "flags": {}}
    out["flags"]["earnings_related"] = bool(NQ.EARNINGS_TERMS.search(text))
    out["flags"]["analyst_action"] = bool(NW.ANALYST.search(text))
    out["flags"]["explicit_guidance_change"] = bool(NW.GUIDE_POS.search(text) or NW.GUIDE_NEG.search(text))
    out["flags"].update(quarter_relevance(text, quarter_end))
    nc = NW.classify(h, symbol, company, min(cutoff, retrieved_at))                    # the existing rules, read only, for comparison
    out["news_x1_category"] = nc["category"]

    def done(cls, why):
        out.update({"class": cls, "reason": why})
        return out
    if not text:
        return done("INVALID", "BLANK_HEADLINE")
    if pub is None:
        return done("INVALID", "MISSING_OR_UNPARSEABLE_TIMESTAMP")
    if pub >= cutoff:
        return done("INVALID", "PUBLISHED_AT_OR_AFTER_CUTOFF")
    if pub > retrieved_at:
        return done("INVALID", "PUBLISHED_AFTER_IT_WAS_RETRIEVED")
    key = norm(text)
    if key in seen:
        out["duplicate_of"] = seen[key]
        return done("DUPLICATE", "SAME_NORMALISED_HEADLINE_ALREADY_SEEN")
    seen[key] = {"published": h.get("created_at"), "source": src, "retrieved_at": retrieved_at.isoformat()}
    if retrieved_at > cutoff:
        return done("UNVERIFIED", "PAGE_RETRIEVED_AFTER_CUTOFF")
    if cutoff - pub > timedelta(days=FRESH_DAYS):
        return done("STALE", "OLDER_THAN_%d_DAYS_AT_CUTOFF" % FRESH_DAYS)
    if sl in NW.LOW_CREDIBILITY:
        return done("UNVERIFIED", "LOW_CREDIBILITY_SOURCE")
    if sl not in NW.TRUSTED and sl not in NW.NEWSFEED:
        return done("UNVERIFIED", "UNKNOWN_SOURCE")
    tickers = h.get("tickers")
    if isinstance(tickers, list) and tickers and symbol.upper() not in [str(t).upper() for t in tickers]:
        return done("IRRELEVANT", "PROVIDER_TICKERS_EXCLUDE_SYMBOL")
    tok = NW.name_token(company)
    cashtag = ("$" + symbol.upper()) in text.upper()
    named = bool(tok and re.search(r"\b" + re.escape(tok) + r"\b", text, re.I))
    if not tok and not cashtag:
        return done("UNVERIFIED", "COMPANY_NAME_UNKNOWN_ENTITY_NOT_CHECKABLE")
    if not (named or cashtag):
        return done("IRRELEVANT", "ENTITY_MISMATCH_TICKER_COLLISION_OR_OTHER_COMPANY")
    if tok and re.search(r"\b" + re.escape(tok[:-1]) + r"[a-z]\b", text, re.I) and not named:
        return done("IRRELEVANT", "SIMILAR_BUT_DIFFERENT_COMPANY_NAME")
    if NW.TRADE_DISCLOSURE.search(text):
        return done("IRRELEVANT", "TRADE_DISCLOSURE_LIST")
    if NW.PRICE.search(text):
        return done("IRRELEVANT", "PRICE_MOVE_NOTE")
    if NW.EARNINGS_RESULT.search(text):
        return done("IRRELEVANT", "RESULT_OF_AN_EARLIER_REPORT")
    return done("VALID", "PRE_CUTOFF_FRESH_TRUSTED_SOURCE_NAMES_THE_COMPANY")


def audit_event(event: dict, news_records: list, company: Optional[str]) -> dict:
    """event: {event_id, symbol, event_date, session, cutoff_utc, ending_fiscal_quarter, quarter_state}. news_records: [{record_id, symbol, retrieved_at, raw_sha256, status, rows, headlines:[...]}] for the symbol."""
    sym, cutoff = event["symbol"], _dt(event["cutoff_utc"])
    base = {"event_id": event["event_id"], "symbol": sym, "event_date": event["event_date"], "session": event["session"], "cutoff_utc": event["cutoff_utc"], "company": company,
            "ending_fiscal_quarter": event.get("ending_fiscal_quarter"), "news_records": [r["record_id"] for r in news_records]}
    if cutoff is None:
        return dict(base, news_state="NO_CUTOFF_FOR_EVENT", counts={}, headlines=[], valid=0)
    if not news_records:
        return dict(base, news_state="NOT_COLLECTED", counts={}, headlines=[], valid=0)
    ok = [r for r in news_records if r.get("status") == "OK"]
    if not ok:
        return dict(base, news_state="FAILED_RESPONSES_ONLY", counts={}, headlines=[], valid=0)
    seen, rows = {}, []
    for r in sorted(ok, key=lambda r: r["retrieved_at"]):                                   # the earliest retrieved copy of a headline is the canonical one
        ra = _dt(r["retrieved_at"])
        for h in r["headlines"]:
            c = classify_headline(h, sym, company, cutoff, ra, seen, event.get("ending_fiscal_quarter") if event.get("quarter_state") == "KNOWN" else None)
            c["record_id"], c["raw_sha256"] = r["record_id"], r["raw_sha256"]
            rows.append(c)
    counts = Counter(x["class"] for x in rows)
    valid = [x for x in rows if x["class"] == "VALID"]
    ages = [(cutoff - _dt(x["published"])).total_seconds() / 3600 for x in valid]
    state = "EMPTY_PROVIDER_RESPONSE" if not rows else "HAS_VALID_HEADLINES" if valid else "NO_VALID_HEADLINES"
    return dict(base, news_state=state, counts={k: counts.get(k, 0) for k in CLASSES}, valid=len(valid), freshest_valid_age_hours=round(min(ages), 1) if ages else None, headlines=rows)


def load_news_records(store_dirs: Iterable[str]) -> list:
    """Every saved uw_news record from the given stores (v2_evidence.jsonl + payloads), the same payload counted once (earliest retrieval wins). Bodies are only read."""
    import sys
    from pathlib import Path as _P
    sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "docs" / "smart_move" / "shadow_impl"))
    from smvshadow.store import PayloadStore
    out, seen = [], {}
    for d in store_dirs:
        f = Path(d) / "v2_evidence.jsonl"
        if not f.exists():
            continue
        st = PayloadStore(d)
        for ln in f.read_text(encoding="utf-8-sig").splitlines():
            if not ln.strip():
                continue
            r = json.loads(ln)
            if r.get("kind") != "uw_news":
                continue
            heads, err = [], None
            try:
                body = json.loads(st.get(r["raw_sha256"]))
                heads = body.get("data") if isinstance(body, dict) else None
                if not isinstance(heads, list):
                    heads, err = [], "NO_DATA_ARRAY"
            except Exception as e:  # noqa: BLE001
                err = type(e).__name__
            rec = {"record_id": r.get("id"), "store": str(d), "symbol": r["symbol"], "retrieved_at": r["retrieved_at"], "raw_sha256": r["raw_sha256"], "status": r.get("status") if err is None else "UNREADABLE:" + err,
                   "rows": r.get("rows"), "headlines": [h for h in heads if isinstance(h, dict)], "non_dict_rows": sum(1 for h in heads if not isinstance(h, dict))}
            k = r["raw_sha256"]
            if k not in seen or rec["retrieved_at"] < seen[k]["retrieved_at"]:
                seen[k] = rec
    out = sorted(seen.values(), key=lambda r: (r["symbol"], r["retrieved_at"]))
    return out


def audit(events: Iterable[dict], news_records: list, companies: dict) -> dict:
    by_sym: dict = {}
    for r in news_records:
        by_sym.setdefault(r["symbol"], []).append(r)
    rows = [audit_event(e, by_sym.get(e["symbol"], []), companies.get(e["symbol"])) for e in events]
    heads = [h for r in rows for h in r["headlines"]]
    orphans = sorted(set(by_sym) - {e["symbol"] for e in rows})
    return {"events": rows, "summary": {"events": len(rows), "by_news_state": dict(Counter(r["news_state"] for r in rows)), "headlines": len(heads), "by_class": dict(Counter(h["class"] for h in heads)),
                                        "by_reason": dict(Counter(h["reason"] for h in heads)), "events_with_valid_headline": sum(1 for r in rows if r["valid"]), "news_records": len(news_records),
                                        "news_records_for_symbols_with_no_event": orphans, "provider_sentiment": dict(Counter(str(h["provider_sentiment"]) for h in heads)),
                                        "period_relevance": dict(Counter(h["flags"]["relevance"] for h in heads)), "note": "Research audit only. Sentiment scores and NEWS_GUIDANCE are not touched; provider sentiment is never used."}}
