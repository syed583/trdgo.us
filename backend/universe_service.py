"""Tradable universe: the top 1,000 US common stocks by average daily DOLLAR volume.

Dollar volume = 30-day average share volume x last close, from the Unusual Whales
stock screener (common stock only; paged 500 rows at a time, a handful of calls).
The ranked list is cached for 24h in the SWR/DB cache (environment-namespaced, so
local and production each hold their own) and is what every earnings tab, the
All-in-One sheet and the cache warmer restrict themselves to.

`restrict()` fails OPEN: if the universe cannot be built (provider down, no key) the
rows pass through unfiltered rather than emptying every tab.
"""
from __future__ import annotations

from typing import Optional

SOURCE = "Top-1000 US stocks by average daily dollar volume (Unusual Whales screener)"
TOP_N = 1000
_PAGES = 8                       # 8 x 500 rows covers every optionable common stock we rank
_KEY = "universe:top1000"
_TTL = 24 * 3600.0


def _dollar_volume(r: dict) -> float:
    try:
        return float(r.get("avg30_volume") or 0) * float(r.get("close") or 0)
    except (TypeError, ValueError):
        return 0.0


def build() -> dict:
    """The ranked universe. RAISES on failure (never returns None): swr caches whatever
    a builder returns, and a cached None once poisoned the key for 24h."""
    import time
    import unusualwhales_service as uw
    rows: list[dict] = []
    for off in range(_PAGES):
        page: list[dict] = []
        status = "?"
        for attempt in range(4):                       # a rate-limited page is retried, not treated as the end
            out = uw.get("/api/screener/stocks",
                         {"limit": 500, "offset": off, "issue_types[]": "Common Stock"})
            status = out.get("status")
            if status == "OK":
                page = uw._rows(out)
                break
            time.sleep(5 * (attempt + 1))
        else:
            if off == 0:
                raise RuntimeError(f"screener page 0 failed ({status})")
            break                                      # later pages: rank what we have
        rows.extend(page)
        if len(page) < 500:
            break
    best: dict[str, dict] = {}
    for r in rows:
        t = (r.get("ticker") or "").upper().strip()
        dv = _dollar_volume(r)
        if t and dv > 0 and (t not in best or dv > best[t]["dollar_volume"]):
            best[t] = {"ticker": t, "dollar_volume": round(dv), "close": r.get("close"),
                       "marketcap": r.get("marketcap")}
    ranked = sorted(best.values(), key=lambda x: x["dollar_volume"], reverse=True)[:TOP_N]
    if len(ranked) < 100:                              # a partial fetch is not a universe
        raise RuntimeError(f"universe too small ({len(ranked)} names)")
    for i, x in enumerate(ranked, 1):
        x["rank"] = i
    return {"source": SOURCE, "count": len(ranked), "rows": ranked,
            "min_dollar_volume": ranked[-1]["dollar_volume"]}


def get(wait: float = 0.0) -> Optional[dict]:
    """The universe, building it if needed. The screener fetch takes ~25s, so a cold call
    can return a LOADING status while the build carries on; `wait` > 0 polls for it (used
    by the cache warmer), wait=0 returns None straight away so a page request never blocks.
    A cached None (left by an older failed build) is repaired by rebuilding directly."""
    import time
    import swr
    deadline = time.time() + max(0.0, wait)
    while True:
        v = swr.serve(_KEY, build, _TTL)
        if isinstance(v, dict) and v.get("rows"):
            return v
        if v is None and wait > 0:                     # poisoned entry: bypass the cache once
            try:
                nv = build()
                swr._run(_KEY, lambda: nv)             # stores + persists the good value
                return nv
            except Exception:  # noqa: BLE001
                pass
        if time.time() >= deadline:
            return None
        time.sleep(5)


def peek() -> Optional[dict]:
    """The cached universe without building it (None if not built yet)."""
    import swr
    v = swr.peek(_KEY)
    return v if isinstance(v, dict) and v.get("rows") else None


def symbols() -> Optional[set[str]]:
    u = get()
    return {r["ticker"] for r in u["rows"]} if u else None


def restrict(rows: list[dict], key: str = "symbol") -> list[dict]:
    """Keep only rows whose symbol is in the universe (all rows if it is unavailable)."""
    syms = symbols()
    if not syms:
        return rows
    return [r for r in rows if (r.get(key) or "").upper() in syms]
