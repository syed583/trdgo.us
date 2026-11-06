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
]
