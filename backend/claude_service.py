"""
Claude, for words only.

Two jobs, both about explaining what the app has already decided:

  explain_call   A stored call -- decision, score, the parameters that pushed
                 for and against it -- turned into two plain-English
                 sentences a person can read at a glance.

  news_tone      A symbol's recent headlines turned into one line on their
                 tone.

What Claude never does here: supply a price, a quote, a volume, a score or a
buy/sell decision. Every number comes from TWS, Unusual Whales or the SEC, and
the decision comes from the model. Claude is handed those figures and asked
to explain them, and told not to add any of its own -- a language model asked
for market data will produce something that looks like market data.

Calls are made on demand and the result is stored with the call, so the cost
is one request per explanation somebody actually asked for, not one per scan.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Optional

log = logging.getLogger(__name__)

MODEL = "claude-opus-5"

# A short, clear explanation needs little thinking; effort is the lever that
# keeps these quick and cheap without changing model.
EFFORT = "low"

# Explanations are two sentences. The ceiling leaves room for adaptive
# thinking at low effort so a reply is never cut off mid-sentence.
MAX_TOKENS = 4000

TIMEOUT = 45.0

# Anthropic re-runs a declined request on a fallback model server-side,
# routed by refusal category, rather than returning a refusal to the screen.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

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

_client = None
_client_lock = threading.Lock()


def configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _get_client():
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            import anthropic

            _client = anthropic.Anthropic(timeout=TIMEOUT, max_retries=2)
    return _client


def _ask(system: str, payload: str) -> dict:
    """
    One request. Returns {"text": ..., "status": "OK"} or a status with the
    reason it did not work -- never raises into the caller.
    """
    if not configured():
        return {"status": "NOT_CONFIGURED",
                "detail": "ANTHROPIC_API_KEY is not set in backend/.env."}

    import anthropic

    started = time.monotonic()
    try:
        response = _get_client().beta.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            output_config={"effort": EFFORT},
            # The instructions are identical on every call, so they are the
            # cacheable prefix; the call's own figures follow as the message.
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": payload}],
        )
    except anthropic.AuthenticationError:
        return {"status": "AUTH_FAILED",
                "detail": "The Anthropic API key was rejected."}
    except anthropic.PermissionDeniedError:
        return {"status": "AUTH_FAILED",
                "detail": "The API key lacks permission for this model."}
    except anthropic.RateLimitError:
        return {"status": "RATE_LIMITED",
                "detail": "Anthropic rate limit reached; try again shortly."}
    except anthropic.BadRequestError as exc:
        log.warning("claude bad request: %s", exc)
        return {"status": "ERROR", "detail": "The explanation request was rejected."}
    except anthropic.APIStatusError as exc:
        return {"status": "PROVIDER_ERROR",
                "detail": f"Anthropic returned {exc.status_code}."}
    except anthropic.APIConnectionError:
        return {"status": "PROVIDER_OFFLINE",
                "detail": "Could not reach Anthropic."}
    except Exception as exc:  # noqa: BLE001 - an explanation must never break a screen
        log.warning("claude call failed: %s", exc)
        return {"status": "ERROR", "detail": type(exc).__name__}

    if response.stop_reason == "refusal":
        return {"status": "REFUSED",
                "detail": "The explanation was declined."}

    text = " ".join(b.text for b in response.content
                    if getattr(b, "type", "") == "text").strip()
    if not text:
        return {"status": "EMPTY", "detail": "No explanation came back."}

    usage = getattr(response, "usage", None)
    return {
        "status": "OK",
        "text": text,
        "model": getattr(response, "model", MODEL),
        "truncated": response.stop_reason == "max_tokens",
        "seconds": round(time.monotonic() - started, 1),
        "usage": {
            "input": getattr(usage, "input_tokens", None),
            "output": getattr(usage, "output_tokens", None),
            "cache_read": getattr(usage, "cache_read_input_tokens", None),
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
