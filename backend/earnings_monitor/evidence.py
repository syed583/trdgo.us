"""The evidence ledger: what was DISCOVERED, COLLECTED, VERIFIED, judged RELEVANT to which model, and actually USED by which model, kept apart on purpose.

  discovered  the item appears in a cache the application already holds (nothing is requested);
  collected   its provider fields are stored here;
  verified    it is structured data supplied by the provider (a number, a filing, a flagged print), not text we interpreted; a headline is never "verified" by being a headline;
  relevant    the model's own rules read this KIND of evidence (derived from the model's code, not guessed);
  used        the model's latest scored row actually carries that input as available.

A headline or a print NEVER changes a prediction by itself. Models are re-read only when the input they already use changes; every other kind of evidence is stored and shown separately until a new model
version that uses it is explicitly approved."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Optional

from .store import Store, iso

KINDS = ("ESTIMATES", "GUIDANCE", "NEWS", "OPTIONS_FLOW", "DARK_POOL", "PRICE_VOLUME", "ANALYST", "INSIDER_SHORT", "SECTOR_PEERS", "HISTORY", "FILING")
# The input keys of earnings_equity_service.get_analysis (the `params` every formula model re-weights) and the kind of evidence each one is.
PARAM_KIND = (("eps", "ESTIMATES"), ("revenue", "ESTIMATES"), ("estimate", "ESTIMATES"), ("guidance", "GUIDANCE"), ("options", "OPTIONS_FLOW"), ("flow", "OPTIONS_FLOW"), ("unusual", "OPTIONS_FLOW"),
              ("disparity", "OPTIONS_FLOW"), ("skew", "OPTIONS_FLOW"), ("gamma", "OPTIONS_FLOW"), ("implied", "OPTIONS_FLOW"), ("iv_rank", "OPTIONS_FLOW"), ("dark", "DARK_POOL"),
              ("price_action", "PRICE_VOLUME"), ("ema", "PRICE_VOLUME"), ("trend", "PRICE_VOLUME"), ("momentum", "PRICE_VOLUME"), ("technical", "PRICE_VOLUME"), ("analyst", "ANALYST"),
              ("reco", "ANALYST"), ("rating", "ANALYST"), ("target", "ANALYST"), ("insider", "INSIDER_SHORT"), ("short", "INSIDER_SHORT"), ("institutional", "INSIDER_SHORT"),
              ("peer", "SECTOR_PEERS"), ("sector", "SECTOR_PEERS"), ("market", "SECTOR_PEERS"), ("history", "HISTORY"), ("reaction", "HISTORY"), ("beat", "HISTORY"))
# Kinds no formula model reads at all: stored and displayed, never fed into a score.
NOT_MODEL_INPUT = ("NEWS", "FILING")
BIAS_STEP = 0.05                      # a bias must move this far to count as a changed input (below it is rounding noise)


def kind_of_param(key: str) -> Optional[str]:
    k = (key or "").lower()
    for frag, kind in PARAM_KIND:
        if frag in k:
            return kind
    return None


def relevance_map(model_sources: dict) -> dict:
    """{kind: [models whose own code reads that kind]}, from the models' source text. Empty when the code is not available (then nothing is claimed as relevant)."""
    rel: dict = {}
    for model, text in model_sources.items():
        names = set(re.findall(r"[\"']([a-z][a-z0-9_]{2,40})[\"']", (text or "").lower()))      # quoted identifiers only: the input names the code actually refers to
        for name in names:
            kind = kind_of_param(name)
            if kind:
                rel.setdefault(kind, set()).add(model)
    return {k: sorted(v) for k, v in rel.items()}


def used_by(model_rows: dict, symbol: str, kind: str) -> list:
    """Models whose latest scored row for this symbol carries an input of this KIND as AVAILABLE (a bias was computed). model_rows = {model: {SYMBOL: row with params}}."""
    out = []
    for model, rows in (model_rows or {}).items():
        row = (rows or {}).get(symbol)
        for p in (row or {}).get("params") or []:
            if kind_of_param(str(p.get("name") or p.get("key") or "")) == kind and p.get("bias") is not None:
                out.append(model)
                break
    return sorted(out)


def _hash(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


def fingerprint(item: dict) -> str:
    """What makes an item 'the same' for change detection: a numeric input by its bias at BIAS_STEP resolution, anything else by its content."""
    d = item.get("data") or {}
    if "bias" in d and isinstance(d["bias"], (int, float)):
        return "bias:%d" % round(d["bias"] / BIAS_STEP)
    return _hash(d if d else [item.get("title"), item.get("summary")])


def record(store: Store, event: dict, items: list, now: datetime, relevant: dict, model_rows: dict) -> list:
    """Upsert this scan's items for one event. Returns the list of {item, status} where status is NEW or CHANGED (UNCHANGED items are only touched)."""
    out = []
    for it in items:
        kind = it["kind"]
        dk = "%s|%s|%s|%s" % (event["id"], kind, it["provider"], it["ext_id"])
        fp = fingerprint(it)
        used = used_by(model_rows, event["symbol"], kind)
        rel = sorted(set(relevant.get(kind, [])) | set(used)) if kind not in NOT_MODEL_INPUT else []
        verified = 1 if it.get("provider_structured") else 0
        row = store.one("SELECT id, data, collected_at FROM evidence_items WHERE dedupe_key=?", (dk,))
        data = dict(it.get("data") or {}, _fp=fp)
        if row is None:
            with store.tx() as c:
                c.execute("INSERT INTO evidence_items(event_id,kind,provider,ext_id,occurred_at,title,summary,data,stage,verified,verified_by,relevant_models,used_by_models,discovered_at,collected_at,last_seen,dedupe_key) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (event["id"], kind, it["provider"], it["ext_id"], it.get("occurred_at"), it.get("title"), it.get("summary"), json.dumps(data, default=str), "VERIFIED" if verified else "COLLECTED",
                           verified, "provider_field" if verified else None, json.dumps(rel), json.dumps(used), iso(now), it.get("collected_at") or iso(now), iso(now), dk))
            out.append({"item": it, "status": "NEW", "used_by": used, "relevant": rel})
            continue
        old = json.loads(row["data"] or "{}")
        changed = old.get("_fp") != fp
        with store.tx() as c:
            c.execute("UPDATE evidence_items SET last_seen=?, relevant_models=?, used_by_models=?" + (", data=?, summary=?, title=?, collected_at=?" if changed else "") + " WHERE id=?",
                      (iso(now), json.dumps(rel), json.dumps(used)) + ((json.dumps(data, default=str), it.get("summary"), it.get("title"), it.get("collected_at") or iso(now)) if changed else ()) + (row["id"],))
        if changed:
            out.append({"item": it, "status": "CHANGED", "used_by": used, "relevant": rel, "previous": old})
    return out
