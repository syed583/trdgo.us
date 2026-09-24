"""
Claude, for words only.

Two jobs, both about explaining what the app has already decided:

  explain_call   A stored call -- decision, score, the parameters that pushed
                 for and against it -- turned into two plain-English
                 sentences a person can read at a glance.

  news_tone      A symbol's recent headlines turned into one line on their
                 tone.

What Claude never does here: supply a price, a quote, a volume, a score or a
buy/sell decision. Every number comes from Unusual Whales or the SEC, and the
decision comes from the model. Claude is handed those figures and asked to
explain them, and told not to add any of its own -- a language model asked for
market data will produce something that looks like market data.

Calls are made on demand and the result is stored with the call, so the cost
is one request per explanation somebody actually asked for, not one per scan.

The Messages API is called directly over HTTP. The official SDK is not a
dependency here on purpose: this is two small text requests, the API key is
the only thing needed, and one fewer package is one fewer thing to install and
keep current on the server.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Optional

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

# Two-sentence explanations do not need a frontier model, and this is billed
# per explanation the operator asks for. Haiku 4.5 is fast and cheap and
# writes these well; change it here if a heavier model is ever wanted.
MODEL = "claude-haiku-4-5-20251001"

# Explanations are two short sentences; this ceiling only guards against a
# reply being cut off mid-sentence.
MAX_TOKENS = 1024

TIMEOUT = 45.0

EXPLAIN_SYSTEM = """You explain a stock call that a quantitative model has already made.

You are given the model's decision, its score (0-100, where 50 is neutral, above is bullish, below is bearish), its confidence, the outlook it is about (TODAY = until today's close, TOMORROW = the next session, SWING = the next few weeks), and the parameters that pushed toward and against the decision, each with the figure behind it.

Write exactly two short sentences for a retail trader, each at most 30 words:
1. Why the model reached this decision, naming the one or two strongest reasons in plain words.
2. The main thing arguing against it, or, if the call was withheld, why it was withheld.

Rules:
- Use only the figures you are given. Never add a price, target, percentage, volume, date or fact that is not in the input.
- Do not recommend buying, selling, sizing or timing. Describe what the model saw.
- Plain words, no jargon without a short gloss, no headings, no bullet points, no hedging boilerplate."""

NEWS_SYSTEM = """You read a list of recent news headlines about one US-listed stock and describe their overall tone.

Reply with one sentence of at most 25 words that starts with one of: "Positive:", "Negative:", "Mixed:" or "Neutral:", followed by the main theme.

Rules:
- Base it only on the headlines given. Do not add facts, prices or events that are not in them.
- If the headlines are routine or unrelated to the company's prospects, say "Neutral:" and say so.
- No recommendation to buy or sell."""


class _HTTPError(Exception):
    """Carries the status/detail an HTTP failure should surface as."""

    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _post(body: dict) -> dict:
    """
    POST one Messages request and return the parsed response JSON.

    Raises _HTTPError with a status the caller maps to the screen. Kept apart
    from _ask so a test can stand in for the network with one substitution.
    """
    data = json.dumps(body).encode("utf-8")
    headers = {
        "x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""),
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    }
    # An account/admin-level key is not tied to a workspace, so the API
    # requires the workspace to be named on every request. A workspace-scoped
    # key does not need this; set ANTHROPIC_WORKSPACE_ID only when using an
    # unscoped key.
    workspace = (os.environ.get("ANTHROPIC_WORKSPACE_ID") or "").strip()
    if workspace:
        headers["anthropic-workspace-id"] = workspace
    request = urllib.request.Request(
        API_URL, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8", "ignore") or "{}")
    except urllib.error.HTTPError as exc:
        code = exc.code
        # Read the body once: the API's own message is what an operator needs
        # to fix a 400 (a wrong model, an unscoped key). Logged, not returned,
        # so a public screen never shows provider internals.
        try:
            reason = exc.read().decode("utf-8", "ignore")[:300]
        except Exception:  # noqa: BLE001
            reason = ""
        if code in (401, 403):
            raise _HTTPError(
                "AUTH_FAILED", "The Anthropic API key was rejected.") from exc
        if code == 429:
            raise _HTTPError(
                "RATE_LIMITED",
                "Anthropic rate limit reached; try again shortly.") from exc
        if code == 400:
            log.warning("claude bad request: %s", reason)
            raise _HTTPError(
                "ERROR", "The explanation request was rejected.") from exc
        log.warning("claude HTTP %s: %s", code, reason)
        raise _HTTPError("PROVIDER_ERROR", f"Anthropic returned {code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise _HTTPError("PROVIDER_OFFLINE", "Could not reach Anthropic.") from exc


def _ask(system: str, payload: str) -> dict:
    """
    One request. Returns {"text": ..., "status": "OK"} or a status with the
    reason it did not work -- never raises into the caller.
    """
    if not configured():
        return {"status": "NOT_CONFIGURED",
                "detail": "ANTHROPIC_API_KEY is not set in backend/.env."}

    started = time.monotonic()
    body = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        # The instructions are identical on every call, so they are the
        # cacheable prefix; the call's own figures follow as the message.
        "system": [{"type": "text", "text": system,
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": payload}],
    }
    try:
        response = _post(body)
    except _HTTPError as exc:
        return {"status": exc.status, "detail": exc.detail}
    except Exception as exc:  # noqa: BLE001 - an explanation must never break a screen
        log.warning("claude call failed: %s", exc)
        return {"status": "ERROR", "detail": type(exc).__name__}

    if response.get("stop_reason") == "refusal":
        return {"status": "REFUSED", "detail": "The explanation was declined."}

    text = " ".join(b.get("text", "") for b in (response.get("content") or [])
                    if b.get("type") == "text").strip()
    if not text:
        return {"status": "EMPTY", "detail": "No explanation came back."}

    usage = response.get("usage") or {}
    return {
        "status": "OK",
        "text": text,
        "model": response.get("model", MODEL),
        "truncated": response.get("stop_reason") == "max_tokens",
        "seconds": round(time.monotonic() - started, 1),
        "usage": {
            "input": usage.get("input_tokens"),
            "output": usage.get("output_tokens"),
            "cache_read": usage.get("cache_read_input_tokens"),
        },
    }


def ask(system: str, payload: str) -> dict:
    """
    One question, for callers that build their own prompt.

    The same request path as everything else here -- status codes rather than
    exceptions, the same model and budget -- so a new explainer does not get
    its own quietly different failure behaviour.
    """
    return _ask(system, payload)


def explain_call(call: dict) -> dict:
    """Two plain sentences on why a stored call came out the way it did."""
    why = call.get("why") or {}

    def slim(items: list) -> list:
        return [{"parameter": i.get("label"), "points": i.get("points_label"),
                 "evidence": i.get("detail")} for i in (items or [])]

    payload = json.dumps({
        "symbol": call.get("symbol"),
        "outlook": call.get("horizon"),
        "decision": call.get("decision"),
        "score": call.get("direction_score"),
        "confidence_pct": call.get("confidence"),
        "agreement_pct": call.get("agreement_pct"),
        "pushed_toward_decision": slim(why.get("for")),
        "pushed_against_decision": slim(why.get("against")),
        "withheld_because": why.get("blocked") or [],
        "parameters_with_no_data": why.get("missing") or [],
    }, indent=1, sort_keys=True)
    return _ask(EXPLAIN_SYSTEM, payload)


# Headline tone changes slowly and every symbol page asks for it; one read per
# symbol per half hour is plenty.
_NEWS_TTL = 1800.0
_news_cache: dict = {}
_news_lock = threading.Lock()


def news_tone(symbol: str, headlines: list[str]) -> dict:
    symbol = (symbol or "").upper()
    usable = [h.strip() for h in headlines or [] if h and h.strip()][:20]
    if not usable:
        return {"status": "NO_DATA", "detail": "No recent headlines for this symbol."}

    key = (symbol, tuple(usable))
    with _news_lock:
        hit = _news_cache.get(key)
        if hit and time.time() - hit[0] < _NEWS_TTL:
            return hit[1]

    payload = json.dumps({"symbol": symbol, "headlines": usable}, indent=1)
    result = _ask(NEWS_SYSTEM, payload)
    result["headline_count"] = len(usable)
    if result.get("status") == "OK":
        with _news_lock:
            _news_cache[key] = (time.time(), result)
    return result
