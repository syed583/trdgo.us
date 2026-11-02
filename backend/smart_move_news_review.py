"""News human-review workflow and warnings (Phase 2, offline). Builds the review queue from a news audit, records reviewer labels in an append-only file, reports the validation gate, associates headlines with
earnings events and produces per-event warnings. NEWS_GUIDANCE stays DISABLED: nothing here switches any scoring on; `guidance_gate()` only reports measured requirements and always returns enabled=False."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import smart_move_news_audit as NA
import smart_move_news_quality as NQ

UTC = timezone.utc
LABELS = NQ.HUMAN_LABELS                                     # POSITIVE, NEGATIVE, NEUTRAL, NOT_RELEVANT, UNSURE
REVIEWABLE = ("VALID", "UNVERIFIED", "IRRELEVANT")           # stale, duplicate and invalid headlines need no human label


def item_id(symbol: str, published, source, headline) -> str:
    return hashlib.sha256("|".join([str(symbol), str(published), str(source), NA.norm(headline)]).encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ association with events
def associate(symbol: str, published, events: Iterable[dict]) -> dict:
    """The event a headline belongs to: of the symbol's events whose cutoff is after the publication and at most 7 days after it, the one with the EARLIEST cutoff. `also_in_window` lists other candidates (a
    headline between two quarterly events is never silently attached to both). No candidate -> NO_EVENT_IN_WINDOW."""
    pub = NA._dt(published)
    if pub is None:
        return {"event_id": None, "state": "NO_TIMESTAMP", "also_in_window": []}
    cands = []
    for e in events:
        if e["symbol"] != symbol or not e.get("cutoff_utc"):
            continue
        co_ = NA._dt(e["cutoff_utc"])
        if pub < co_ <= pub + timedelta(days=NA.FRESH_DAYS):
            cands.append((co_, e["event_id"]))
    cands.sort()
    if not cands:
        return {"event_id": None, "state": "NO_EVENT_IN_WINDOW", "also_in_window": []}
    return {"event_id": cands[0][1], "state": "ASSOCIATED" if len(cands) == 1 else "ASSOCIATED_WITH_OTHER_CANDIDATES", "also_in_window": [c[1] for c in cands[1:]]}


# ------------------------------------------------------------------ queue
def build_queue(audit: dict) -> list:
    """One item per reviewable headline of a news audit (see NA.audit). Items are identified by a stable hash so a label survives re-running the audit."""
    seen, out = set(), []
    for ev in audit["events"]:
        for h in ev["headlines"]:
            if h["class"] not in REVIEWABLE:
                continue
            iid = item_id(ev["symbol"], h["published"], h["source"], h["headline"])
            if iid in seen:
                continue
            seen.add(iid)
            out.append({"item_id": iid, "event_id": ev["event_id"], "symbol": ev["symbol"], "event_date": ev["event_date"], "headline": h["headline"], "source": h["source"], "published": h["published"],
                        "audit_class": h["class"], "audit_reason": h["reason"], "classifier_label": h.get("news_x1_category"), "labels": []})
    return sorted(out, key=lambda r: (r["event_date"] or "", r["symbol"], r["published"] or ""))


class LabelStore:
    """Append-only reviewer labels. A reviewer may revise a label (a new line; the newest line of a reviewer wins); nothing is edited or deleted."""

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.rows = [json.loads(ln) for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()] if self.path and self.path.exists() else []

    def record(self, item: str, label: str, reviewer: str, at=None, note: str = "", known_items: Optional[set] = None) -> dict:
        if label not in LABELS:
            raise ValueError("label must be one of %s" % (LABELS,))
        if not str(reviewer or "").strip():
            raise ValueError("a reviewer name is required")
        if known_items is not None and item not in known_items:
            raise KeyError("unknown review item " + item)
        row = {"item_id": item, "label": label, "reviewer": reviewer.strip(), "at": (at or datetime.now(UTC)).isoformat(), "note": note, "seq": len(self.rows) + 1}
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, sort_keys=True) + chr(10))
        self.rows.append(row)
        return row

    def current(self) -> dict:
        """{item_id: {reviewer: label}} using each reviewer's newest label."""
        out: dict = {}
        for r in self.rows:
            out.setdefault(r["item_id"], {})[r["reviewer"]] = r["label"]
        return out

    def consensus(self) -> dict:
        """{item_id: {state, label}}: LABELLED (one reviewer, or all agree), DISPUTED (reviewers disagree). UNSURE never counts as a vote on its own."""
        out = {}
        for iid, by in self.current().items():
            votes = {v for v in by.values() if v != "UNSURE"}
            out[iid] = {"state": "DISPUTED" if len(votes) > 1 else "LABELLED" if votes else "UNSURE", "label": next(iter(votes)) if len(votes) == 1 else None, "reviewers": len(by)}
        return out


def guidance_gate(queue: list, store: LabelStore, **kw) -> dict:
    """Measured validation requirements for NEWS_GUIDANCE using only items with a consensus label. ALWAYS enabled=False: enabling needs the owner's explicit approval, outside this code."""
    cons = store.consensus()
    rows = [{"research_label": q["classifier_label"] if q["classifier_label"] in ("POSITIVE", "NEGATIVE", "NEUTRAL") else "NEUTRAL", "human_label": cons[q["item_id"]]["label"]}
            for q in queue if cons.get(q["item_id"], {}).get("state") == "LABELLED"]
    g = NQ.validation_gate(rows, **kw)
    return dict(g, enabled=False, disputed=sum(1 for c in cons.values() if c["state"] == "DISPUTED"), queue_size=len(queue), unlabelled=len(queue) - len(cons),
                status="DISABLED_UNTIL_VALIDATED_AND_APPROVED")


# ------------------------------------------------------------------ warnings
def event_warnings(ev: dict) -> list:
    """Warning codes for one audited event (NA.audit_event result)."""
    st, c = ev["news_state"], ev.get("counts") or {}
    w = []
    if st == "NOT_COLLECTED":
        w.append("MISSING_NEWS_NOT_COLLECTED")
    elif st == "FAILED_RESPONSES_ONLY":
        w.append("MISSING_NEWS_REQUEST_FAILED")
    elif st == "EMPTY_PROVIDER_RESPONSE":
        w.append("EMPTY_NEWS_RESPONSE")
    elif st == "NO_CUTOFF_FOR_EVENT":
        w.append("NO_CUTOFF_NEWS_CANNOT_BE_JUDGED")
    elif st == "NO_VALID_HEADLINES":
        w.append("NO_VALID_HEADLINE")
        total = sum(c.values()) - c.get("DUPLICATE", 0)
        if total and c.get("STALE", 0) == total:
            w.append("ALL_HEADLINES_STALE")
        if c.get("UNVERIFIED", 0):
            w.append("UNRELIABLE_OR_UNVERIFIABLE_HEADLINES_PRESENT")
        if c.get("IRRELEVANT", 0):
            w.append("POSSIBLE_TICKER_COLLISION_OR_OFF_TOPIC")
    elif st == "HAS_VALID_HEADLINES":
        if c.get("UNVERIFIED", 0):
            w.append("UNRELIABLE_OR_UNVERIFIABLE_HEADLINES_PRESENT")
        if c.get("IRRELEVANT", 0):
            w.append("POSSIBLE_TICKER_COLLISION_OR_OFF_TOPIC")
        if (ev.get("freshest_valid_age_hours") or 0) > 72:
            w.append("FRESHEST_VALID_HEADLINE_OVER_72H_OLD")
    return w


def warnings_report(audit: dict) -> dict:
    rows = [{"event_id": e["event_id"], "symbol": e["symbol"], "news_state": e["news_state"], "warnings": event_warnings(e)} for e in audit["events"]]
    return {"events": rows, "by_warning": dict(Counter(w for r in rows for w in r["warnings"])), "events_with_any_warning": sum(1 for r in rows if r["warnings"])}
