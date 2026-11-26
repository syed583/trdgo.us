"""Kit V: the reason on a user's order row, in plain words, with no supplier's name in it.

The order log stores the machine reason exactly as it happened (so an operator can still search for it). What a
USER reads on My Broker goes through here first. Two rules:
    * a code becomes a sentence the user can act on;
    * the name of the company that runs our trading connection never reaches the page - the user's question is
      "why was my trade missed", not "which supplier".
"""
from __future__ import annotations

import re
from typing import Optional

_VENDOR = re.compile(r"(mtapi[\w.\-]*|mt5rest[\w.\-]*|cloudtrial|https?://\S+)", re.IGNORECASE)
_LICENCE = ("trial expired", "cloudtrial", "purchase full version", "license", "licence", "apikey", "api key",
            "unauthorized", "forbidden", "subscription")
_DOWN = ("transport", "timeout", "timed out", "http_5", "http_429", "connection", "urlopen")


def strip_vendor(text: Optional[str]) -> str:
    return re.sub(r"\s{2,}", " ", _VENDOR.sub("the trading connection", str(text or ""))).strip()


def plain(reason: Optional[str], event: Optional[str] = None) -> str:
    """One sentence for the user. Unknown text falls through with supplier names removed - never blanked."""
    raw = str(reason or "").strip()
    up, low = raw.upper(), raw.lower()
    if any(w in low for w in _LICENCE):
        return "The trading connection's licence had expired. No order could be placed until it was renewed."
    if up.startswith(("API_ERROR", "TRANSPORT", "HTTP_")) or (event in ("SKIPPED", "CLOSE_FAIL") and any(w in low for w in _DOWN)):
        return "The trading connection was not answering."
    if up.startswith("MAX_OPEN_POSITIONS"):
        m = re.search(r"(\d+) open, your limit is (\d+)", raw)
        return ("Your position limit was reached (%s open, limit %s)." % (m.group(1), m.group(2))) if m \
            else "Your position limit was reached."
    if up.startswith("DAILY_LOSS_LIMIT"):
        return "Your daily loss limit was reached. No new trades were opened for the rest of the day."
    if up.startswith("NOT_ENOUGH_MARGIN"):
        m = re.search(r"needs ([\d.]+) and ([\d.]+) is free", raw)
        return ("Not enough margin: this order needed %s and %s was free." % (m.group(1), m.group(2))) if m \
            else "Not enough margin."
    if up.startswith("NO_MARGIN"):
        return "Not enough margin: this account had no equity to trade with."
    if up.startswith("NOT_MAPPED"):
        return "This stock is not mapped to your broker's symbol. Map it under \"Which stocks trade in my account\"."
    if up.startswith("SKIPPED_SIZE"):
        return "Your share count does not fit your broker's lot size for this stock."
    if up.startswith("AUTO_TRADE_OFF"):
        return "Auto-trade was off."
    if up.startswith("PAUSED_BY_YOU"):
        return "You had paused trading on this account."
    if up.startswith("PAUSED_BY_OPERATOR"):
        return "An administrator had paused trading on this account."
    if up.startswith("NO_CONSENT"):
        return "The risk notice had not been accepted."
    if up.startswith("EQUITY_UNKNOWN"):
        return "Your account equity could not be read, so no new risk was taken."
    if "STOP" in up and "CAP" in up:
        return "The stop on this signal was wider than the 2.5% limit."
    if up.startswith(("NO_USER_SESSION", "SESSION_", "NO_ACCOUNT", "PROVIDER_UNAVAILABLE", "OPERATOR_ACCOUNT")):
        return "Your broker session was not available."
    if up.startswith("AT_BROKER:STOP_OUT"):
        return "Closed at your broker by a margin stop-out."
    if up.startswith("AT_BROKER:STOP"):
        return "Closed at your broker by the stop-loss."
    if up.startswith("AT_BROKER:TARGET"):
        return "Closed at your broker by the take-profit."
    if up.startswith("AT_BROKER:PRICE_UNKNOWN"):
        return "Closed at your broker; the closing price is not available."
    if up.startswith("AT_BROKER"):
        return "Closed at your broker."
    if "filled moments ago" in low:
        return "The close was retried: the position was too new to confirm."
    if event == "CLOSE_FAIL":
        return "The close could not be completed and was retried."
    labels = {"SCORE_EXIT": "Closed with the software: score exit.", "STOP": "Closed with the software: stop.",
              "TARGET": "Closed with the software: target.", "EOD_CLOSE": "Closed before the bell.",
              "EOD_SWEEP": "Closed before the bell.", "PENDING_RETRY": "Closed at the next open.",
              "TIMED_EXIT": "Closed with the software: timed exit.", "MARKET_CLOSE": "Closed at the bell."}
    if up in labels:
        return labels[up]
    return strip_vendor(raw)
