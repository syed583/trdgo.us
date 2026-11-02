"""News human-review API (local Smart Move server only). Reads a saved review queue (automated classifications) and an append-only label file; the ONLY write is appending a reviewer label to that file.
No provider, no scoring, no prediction, no NEWS_GUIDANCE switch: the response always says news guidance is disabled.
Environment: SMART_MOVE_NEWS_REVIEW_DIR = folder holding queue.json (list built offline by smart_move_news_review.build_queue) and labels.jsonl (appended to)."""
import json
import os
from collections import Counter
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query

import smart_move_news_review as NR

router = APIRouter(prefix="/api/smart-move/news-review", tags=["smart-move-news-review"])
GUIDANCE = {"enabled": False, "status": "DISABLED_UNTIL_VALIDATED_AND_APPROVED", "note": "Human labels are research data only. Nothing here changes a score, a prediction or a recommendation."}


def _dir() -> Optional[Path]:
    d = os.getenv("SMART_MOVE_NEWS_REVIEW_DIR")
    return Path(d) if d else None


def _queue():
    d = _dir()
    if d is None:
        return None, {"status": "NO_QUEUE", "detail": "No news review queue is configured (SMART_MOVE_NEWS_REVIEW_DIR)."}
    f = d / "queue.json"
    if not f.is_file():
        return None, {"status": "NO_QUEUE", "detail": "The review queue has not been built yet."}
    try:
        q = json.loads(f.read_text(encoding="utf-8-sig"))
        if not isinstance(q, list):
            raise ValueError("not a list")
    except (OSError, ValueError) as e:
        return None, {"status": "UNREADABLE", "detail": "The review queue could not be read: %s" % type(e).__name__}
    return q, None


def _store() -> "NR.LabelStore":
    return NR.LabelStore(_dir() / "labels.jsonl")


@router.get("/queue")
def queue(audit_class: Optional[str] = Query(None, pattern="^(VALID|STALE|DUPLICATE|IRRELEVANT|UNVERIFIED|INVALID)$"), state: Optional[str] = Query(None, pattern="^(UNLABELLED|LABELLED|DISPUTED|UNSURE)$"),
          event_id: Optional[str] = Query(None, max_length=60), q: Optional[str] = Query(None, max_length=60), limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    items, err = _queue()
    if err:
        return dict(err, rows=[], total=0, news_guidance=GUIDANCE)
    store = _store()
    cons, cur = store.consensus(), store.current()
    rows = []
    for x in items:
        c = cons.get(x["item_id"], {"state": "UNLABELLED", "label": None})
        rows.append(dict(x, human_labels=cur.get(x["item_id"], {}), human_state=c["state"] if x["reviewable"] else "NOT_REVIEWABLE", human_consensus=c.get("label")))
    ql = (q or "").lower()
    out = [r for r in rows if (audit_class is None or r["audit_class"] == audit_class) and (state is None or r["human_state"] == state) and (event_id is None or r["event_id"] == event_id)
           and (not ql or ql in r["headline"].lower() or ql in r["symbol"].lower() or ql in str(r.get("company") or "").lower())]
    gate = NR.guidance_gate([x for x in items if x["reviewable"]], store)
    return {"status": "OK", "total": len(out), "limit": limit, "offset": offset, "rows": out[offset: offset + limit], "progress": NR.review_progress(items, store),
            "by_audit_class": dict(Counter(i["audit_class"] for i in items)),
            "gate": {k: gate[k] for k in ("labelled", "per_class", "agreement", "requirements", "may_be_considered_for_scoring", "disputed", "unlabelled", "status", "enabled")},
            "news_guidance": GUIDANCE, "labels": list(NR.LABELS)}


@router.post("/label")
def label(body: dict = Body(...)):
    items, err = _queue()
    if err:
        raise HTTPException(status_code=409, detail=err["detail"])
    known = {x["item_id"] for x in items if x["reviewable"]}
    store = _store()
    try:
        row = store.record(str(body.get("item_id") or ""), str(body.get("label") or ""), str(body.get("reviewer") or ""), note=str(body.get("note") or "")[:500], known_items=known)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except KeyError:
        raise HTTPException(status_code=404, detail="That headline is not in the reviewable queue.")
    return {"status": "RECORDED", "label": row, "human_state": store.consensus()[row["item_id"]]["state"], "news_guidance": GUIDANCE}
