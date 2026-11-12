"""The liquid universe the intraday boards, the live engine, the feeds and the paper tracker all score.

The v1 "AI Trade" / "Trdgo Stock" board that used to live here (rolling directional scan, swing track, rules engine,
Google-Sheet logger) was removed; only the shared universe remains. Liquid, optionable, and spread across sectors --
the names the rest of the app already fetches, so most of this is answered from warm caches.
"""
from __future__ import annotations

UNIVERSE: list[str] = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO",
    "AMD", "NFLX", "CRM", "ORCL", "ADBE", "INTC", "MU", "QCOM",
    "JPM", "BAC", "GS", "V", "MA",
    "XOM", "CVX", "COP",
    "UNH", "LLY", "JNJ", "PFE",
    "WMT", "COST", "HD", "NKE", "DIS",
    "CAT", "BA", "GE",
    "SPY", "QQQ",
    # Added 2026-11-12 (owner decision): the 17 strongest EQUITY names (common stock / ADR; no ETFs, index, inverse or
    # leveraged funds) by options volume (Unusual Whales screener, 2026-11-12) among the broker's USD symbols not already
    # listed. 55 in total so one full feed pass (6 symbols a minute) takes ~9 min, inside the 10-minute options-flow
    # freshness window. The 38 above are unchanged and keep their order.
    "SPCX", "MSTR", "MRVL", "PLTR", "SMCI", "HOOD", "TSM", "GOOG",
    "UMC", "DELL", "UBER", "GME", "BABA", "TTD", "MRNA", "NOW",
    "PCG",
]
