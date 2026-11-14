"""B.1: one place that decides what the model's own working looks like to a reader who is not an administrator.

A.25 hid the parameters on the Analysis screen, A.26 mapped the model names on Strategy Trades, A.29 closed the
/api/calls/* routes. Each fixed one screen; the sibling routes serving the same data still sent everything. This
module is the single helper those routes now share, so the next route cannot be forgotten in a different way.

Two rules, learned from two near-misses:

  1. EMPTY THE COLLECTION, KEEP THE KEY. Removing a key crashes the page that reads it without a guard -
     `WhyCall.tsx` does `w.missing.length` and `DirectionalScorePanel.tsx` does `d.signals.filter(...)`. An empty
     list leaks nothing and keeps every consumer working.
  2. FAIL CLOSED. The caller decides `admin` from the session; any doubt means not an administrator.

What a non-admin keeps: the decision, the scores, the confidence, the coverage, the prices, the plain-language
labels and explanations. What never leaves the server: the scored parameters, their weights and points, the
category roll-up, the buy/sell breakdown, the model version string and the data-vendor names.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# Collections that ARE the model. Emptied, never removed (rule 1).
EMPTIED_KEYS = ("signals", "categories", "parameters", "params", "breakdown", "factors", "missing")

# Scalars and objects that carry the working. Set to None.
NULLED_KEYS = ("buy_sell", "model_version", "weights", "weight_table", "formula", "thresholds")

# Per-row keys inside a reason list: the arithmetic and the source sentence.
PRIVATE_ROW_KEYS = ("points", "points_label", "detail", "weight", "buy_weight", "sell_weight",
                    "rule", "raw", "source", "sources")

# The data vendors. A reader is told there is a provider, never which one.
VENDORS = ("Unusual Whales", "unusualwhales", "UnusualWhales", "Finnhub", "finnhub", "Finviz", "finviz",
           "Benzinga", "benzinga", "Marketaux", "marketaux", "mtapi", "MetaTrader", "Intraday model")
_VENDOR_RE = re.compile("|".join(re.escape(v) for v in VENDORS))
VENDOR_REPLACEMENT = "the data provider"

# The model names, in the order the owner fixed for Strategy Trades (A.26).
AGENT_ORDER = ("grok", "meta", "kimi", "gemini")
MODEL_LIST_KEYS = ("models", "model_list", "engines")
EXTRA_MODELS = ("claude", "chatgpt", "claude_pro", "claude-pro")


def agent_label(model: str) -> str:
    """'grok' -> 'Agent 1'. An unknown model becomes a stable 'Agent N' by position, never its own name."""
    key = str(model or "").strip().lower().replace("-", "_")
    if key in AGENT_ORDER:
        return "Agent %d" % (AGENT_ORDER.index(key) + 1)
    if key in EXTRA_MODELS:
        return "Agent %d" % (len(AGENT_ORDER) + 1 + EXTRA_MODELS.index(key))
    return "Agent" if key else ""


def scrub_text(value: Any) -> Any:
    """Replace any data-vendor name inside a string. Non-strings pass through untouched."""
    if isinstance(value, str) and _VENDOR_RE.search(value):
        return _VENDOR_RE.sub(VENDOR_REPLACEMENT, value)
    return value


def _clean_row(row: Any) -> Any:
    if not isinstance(row, dict):
        return scrub_text(row)
    return {k: scrub_text(v) for k, v in row.items() if k not in PRIVATE_ROW_KEYS}


def public_payload(payload: Optional[dict], *, admin: bool = False, map_models: bool = False,
                   model_keys: tuple = ("model", "model_name", "engine")) -> Optional[dict]:
    """The payload as a non-admin may see it. `admin=True` returns it unchanged.

    `map_models` additionally replaces a model name with its Agent label, for the pages a normal user still sees
    (All Models, Model Accuracy) where the row itself is the point and only the name must go.
    """
    if admin or payload is None or not isinstance(payload, dict):
        return payload

    out: dict = {}
    for k, v in payload.items():
        if k in EMPTIED_KEYS:
            out[k] = [] if isinstance(v, (list, tuple)) else ({} if isinstance(v, dict) else None)
        elif k in NULLED_KEYS:
            out[k] = None
        elif k in PRIVATE_ROW_KEYS:
            continue
        elif map_models and k in model_keys and isinstance(v, str):
            out[k] = agent_label(v)
        elif map_models and k in MODEL_LIST_KEYS and isinstance(v, (list, tuple)):
            # a plain list of model names, e.g. {"models": ["grok", "meta", ...]}
            out[k] = [agent_label(x) if isinstance(x, str) else x for x in v]
        elif isinstance(v, dict):
            out[k] = public_payload(v, admin=False, map_models=map_models, model_keys=model_keys)
        elif isinstance(v, list):
            out[k] = [public_payload(x, admin=False, map_models=map_models, model_keys=model_keys)
                      if isinstance(x, dict) else scrub_text(x) for x in v]
        else:
            out[k] = scrub_text(v)
    return out


def public_rows(rows, *, admin: bool = False, map_models: bool = False) -> list:
    if admin:
        return list(rows or [])
    return [public_payload(r, admin=False, map_models=map_models) if isinstance(r, dict) else scrub_text(r)
            for r in (rows or [])]


def reason_rows(rows, *, admin: bool = False) -> list:
    """A list of plain reasons: labels kept, arithmetic and source sentence removed."""
    if admin:
        return list(rows or [])
    return [_clean_row(r) for r in (rows or [])]


# The words that must never appear in a non-admin response. The tests serialise the whole answer and look for them.
FORBIDDEN_STRINGS = VENDORS + ("buy_weight", "sell_weight", "points_label",
                               "sec_filings", "iv_greeks", "options_flow", "gex", "relative_strength",
                               "Grok", "Kimi", "Gemini", "ChatGPT", "directional-")
