"""
The Trdgo.us assistant.

A chat helper powered by Claude (the key already configured in
``claude_service``), grounded on this app's live Unusual Whales data. The user
asks a question; a compact snapshot of the ticker in context -- its quote, the
model's options read, key levels and recent headlines -- is gathered and handed
to Claude, which answers from that data plus general market knowledge.

It is an analysis helper, not an adviser: the system prompt forbids buy/sell,
sizing, timing and any personalized investment advice, matching the rest of the
app. Unusual Whales has no chat API of its own, so this stands in for the
"ask the data" experience using the model this app already pays for.
"""

from __future__ import annotations

from typing import Optional

SYSTEM = """You are the Trdgo.us assistant, a helper for understanding US stocks and options.

Answer the user's question using the LIVE DATA block provided plus general, factual market and options knowledge (what terms mean, how metrics are read). Ground any specific numbers in the data given; if the data does not cover something, say so plainly rather than inventing figures.

Hard rules:
- This is an analysis tool, not financial advice. Never tell the user to buy, sell, hold, add, trim, size or time any position, and never give personalized investment advice. If they ask what to do, say you can explain what the data shows but cannot give advice, then describe the relevant data.
- Do not predict exact prices or guarantee outcomes.
- Be concise and plain-spoken: a few sentences, not an essay. No repeated disclaimers -- at most one short line if advice was requested.
- The figures come from Unusual Whales and this app's own model; refer to them as such.
"""


def _f(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _snapshot(symbol: str) -> str:
    """A compact, best-effort live-data block for the ticker in context."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "(No ticker in context — answer generally or ask the user to search one.)"

    lines: list[str] = [f"Ticker: {symbol}"]

    try:
        import live_market_service as market
        q = market.get_quote(symbol) or {}
        price = q.get("price")
        if price is not None:
            chg = q.get("change_percent")
            lines.append(f"Price: {price}"
                         + (f" ({chg:+.2f}% today)" if _f(chg) is not None else ""))
    except Exception:  # noqa: BLE001 - snapshot is best effort
        pass

    try:
        import options_levels_service as levels
        d = levels.get_levels(symbol)
        if d.get("status") == "OK":
            sc = d.get("score") or {}
            if sc.get("value") is not None:
                lines.append(f"Options model score: {sc.get('value')} "
                             f"({sc.get('label')})")
            oi = d.get("open_interest") or {}
            if oi.get("put_call_oi") is not None:
                lines.append(f"Put/call OI ratio: {oi.get('put_call_oi')}")
            g = d.get("gamma") or {}
            if g.get("net_gex") is not None:
                lines.append(f"Net GEX: {g.get('net_gex')} ({g.get('regime')}), "
                             f"gamma flip {g.get('gamma_flip')}")
            em = d.get("expected_move") or {}
            if em.get("percent") is not None:
                lines.append(f"Expected move: ±{em.get('percent')}% "
                             f"(IV {em.get('implied_volatility')}%)")
            mp = d.get("max_pain")
            if mp is not None:
                lines.append(f"Max pain: {mp}")
    except Exception:  # noqa: BLE001
        pass

    try:
        import uw_news_adapter as news
        getter = getattr(news, "get_symbol_news", None) or news.get_news
        items = (getter(symbol, 4) or {}).get("items") or []
        heads = [i.get("headline") for i in items if i.get("headline")][:4]
        if heads:
            lines.append("Recent headlines:")
            lines.extend(f"  - {h}" for h in heads)
    except Exception:  # noqa: BLE001
        pass

    if len(lines) == 1:
        lines.append("(No live data available for this ticker right now.)")
    return "\n".join(lines)


def answer(message: str, symbol: str = "",
           history: Optional[list[dict]] = None) -> dict:
    """Answer one chat message, grounded on the ticker-in-context's live data."""
    message = (message or "").strip()
    if not message:
        return {"status": "EMPTY", "detail": "Ask a question first."}

    import claude_service
    if not claude_service.configured():
        return {"status": "NOT_CONFIGURED",
                "detail": "The assistant is not configured (no Claude API key)."}

    # A short rolling transcript so follow-ups keep context, bounded so the
    # prompt stays small and cheap.
    convo = ""
    for turn in (history or [])[-6:]:
        role = "User" if turn.get("role") == "user" else "Assistant"
        text = str(turn.get("content") or "").strip()
        if text:
            convo += f"{role}: {text[:500]}\n"

    payload = (f"LIVE DATA\n{_snapshot(symbol)}\n\n"
               + (f"CONVERSATION SO FAR\n{convo}\n" if convo else "")
               + f"USER QUESTION\n{message}")

    out = claude_service.ask(SYSTEM, payload)
    if out.get("status") != "OK":
        return {"status": out.get("status", "ERROR"),
                "detail": out.get("detail") or "The assistant could not answer."}
    return {"status": "OK", "text": out["text"], "symbol": symbol.upper() or None,
            "model": out.get("model")}
