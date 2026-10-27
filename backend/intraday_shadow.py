"""Shadow mode: Legacy keeps opening the paper trades; V2 judges the same market data and writes ONLY to its own shadow tables.

Everything here is observation. Nothing in this module (or in V2 while ``mode == "shadow"``) writes to ``demo_trades``, places an
order or changes a Legacy decision. The decision log holds one row per CHANGE of decision for each engine / strategy / symbol /
session / direction, with the data timestamps that decision was based on, so the two engines can be compared afterwards.
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any, Optional

import tab_settings as cfg

LEGACY, V2 = "LEGACY", "V2"
OPENED, WOULD_OPEN, REJECTED = "OPENED", "WOULD_OPEN", "REJECTED"

_last: dict = {}                    # (engine, strategy, symbol, date, session, direction) -> last decision signature


def mode() -> str:
    return str(cfg.INTRADAY_WORKFLOW.get("mode", "legacy")).lower()


def enabled() -> bool:
    return mode() == "shadow"


def _js(obj: Any) -> str:
    return json.dumps(obj, default=str, sort_keys=True)


def data_timestamps(row: Optional[dict], now: datetime, quote: Optional[dict] = None) -> dict:
    """The timestamps / ages a decision was based on: the board's price age and engine age, every per-signal source age (aged to
    ``now``), the board build time and, for V2, the executable quote's age."""
    row = row or {}
    data = row.get("data") or {}
    gts = row.get("gate_ts")
    drift = max(0.0, now.timestamp() - float(gts)) if gts else 0.0
    sources = {}
    for k, v in (row.get("signal_meta") or {}).items():
        try:
            sources[k] = {"age_s": round(float(v.get("age_s")) + drift, 1) if v.get("age_s") is not None else None,
                          "status": v.get("status")}
        except (TypeError, ValueError, AttributeError):
            sources[k] = {"age_s": None, "status": None}
    out = {"decided_at": now.isoformat(), "board_built_at": None if not gts else datetime.fromtimestamp(float(gts), now.tzinfo).isoformat(),
           "price_age_s": data.get("price_age_s"), "engine_age_s": data.get("signals_age_s"), "phase": data.get("phase"),
           "sources": sources}
    if quote:
        out["quote_age_s"] = quote.get("age_s")
    return out


def record(s, *, engine: str, strategy: str, symbol: str, trading_date: date, session: str, direction: str, score: Optional[float],
           decision: str, code: Optional[str], reasons: list, data_ts: Optional[dict], snapshot_id: Optional[str] = None,
           trade_id: Optional[int] = None, window_state: Optional[str] = None, now: Optional[datetime] = None) -> bool:
    """Add a log row when the decision for this key CHANGED. The caller owns the transaction. Returns True when a row was added."""
    from models_demo_trades import IntradayShadowLog
    key = (engine, strategy, symbol, str(trading_date), session, direction)
    sig = (decision, code, (reasons or [""])[0][:80] if reasons else "")
    if _last.get(key) == sig:
        return False
    _last[key] = sig
    s.add(IntradayShadowLog(engine=engine, strategy=strategy, symbol=symbol, trading_date=trading_date, session_type=session,
                            direction=direction, score=score, decision=decision, code=code, reasons=_js(reasons or []),
                            data_ts=_js(data_ts or {}), snapshot_id=snapshot_id, trade_id=trade_id, window_state=window_state,
                            **({"ts": now} if now else {})))
    return True


def legacy_decision(s, now: datetime, strategy: str, symbol: str, info: dict, *, opened_id: Optional[int], held: bool, reason: str) -> None:
    """Record what the Legacy tracker did with one BUY / SELL row. ``reason`` is the gate that refused it ('' when opened)."""
    import market_calendar as mc
    sess = mc.session_at(now)
    direction = info.get("signal_raw") or info.get("signal")
    if direction not in ("BUY", "SELL"):
        return
    if opened_id is not None:
        decision, code, reasons = OPENED, None, []
    else:
        decision, code, reasons = REJECTED, ("LEGACY: position already held / traded today" if held else "LEGACY: " + (reason or "refused")), [reason]
    record(s, engine=LEGACY, strategy=strategy, symbol=symbol, trading_date=sess["trading_date"], session=sess["session"],
           direction=direction, score=info.get("score") if info.get("score") is not None else info.get("score_raw"),
           decision=decision, code=code, reasons=reasons, data_ts=data_timestamps(info, now), trade_id=opened_id, now=now)


# ------------------------------------------------------------------------------------------------------ the report ---
def report(session_factory, day_from: date, day_to: Optional[date] = None) -> dict:
    """Legacy vs V2 for the logged days: signals, entries, rejections by code, overlap, and the two books' realised results.
    Reads only; real observed data once shadow mode has run, nothing is simulated here."""
    from models_demo_trades import DemoTrade, IntradayShadowLog, ShadowTrade
    day_to = day_to or day_from
    s = session_factory()
    try:
        logs = s.query(IntradayShadowLog).filter(IntradayShadowLog.trading_date >= day_from,
                                                 IntradayShadowLog.trading_date <= day_to).order_by(IntradayShadowLog.id).all()
        final: dict = {}
        for r in logs:                                                    # the LAST decision per key is the outcome
            final[(r.engine, r.strategy, r.symbol, str(r.trading_date), r.session_type, r.direction)] = r
        per: dict = defaultdict(lambda: {"signals": 0, "opened": 0, "rejected": 0, "reasons": Counter()})
        for (eng, strat, sym, d, sess, dirn), r in final.items():
            p = per[(eng, strat)]
            p["signals"] += 1
            if r.decision in (OPENED, WOULD_OPEN):
                p["opened"] += 1
            else:
                p["rejected"] += 1
                p["reasons"][r.code or "?"] += 1
        by_session = Counter((eng, sess, r.decision) for (eng, _s, _y, _d, sess, _x), r in final.items())
        legacy_open = {(k[1], k[2], k[3]) for k, r in final.items() if k[0] == LEGACY and r.decision == OPENED}
        v2_open = {(k[1], k[2], k[3]) for k, r in final.items() if k[0] == V2 and r.decision in (OPENED, WOULD_OPEN)}
        def book(model, tag):
            rows = s.query(model).filter(model.session_date >= day_from, model.session_date <= day_to).all()
            closed = [t for t in rows if t.profit is not None and t.status not in ("OPEN", "PENDING_RECON")]
            return {"book": tag, "trades": len(rows), "closed": len(closed), "open": sum(1 for t in rows if t.status == "OPEN"),
                    "pending_recon": sum(1 for t in rows if t.status == "PENDING_RECON"),
                    "net_pnl": round(sum(t.profit for t in closed), 2),
                    "win_rate": round(100 * sum(1 for t in closed if t.profit > 0) / len(closed), 1) if closed else None}
        return {"from": str(day_from), "to": str(day_to),
                "per_strategy": {f"{eng}/{strat}": {**{k: v for k, v in p.items() if k != "reasons"}, "reasons": dict(p["reasons"])}
                                 for (eng, strat), p in sorted(per.items())},
                "by_session": {f"{e}/{se}/{d}": n for (e, se, d), n in sorted(by_session.items())},
                "overlap": {"both": len(legacy_open & v2_open), "legacy_only": len(legacy_open - v2_open),
                            "v2_only": len(v2_open - legacy_open)},
                "legacy_book": book(DemoTrade, "demo_trades (actual paper book)"),
                "v2_shadow_book": book(ShadowTrade, "intraday_shadow_trades (virtual, never placed)"),
                "note": "cumulative drawdown and costs are not in this report; fills are the documented paper-fill assumptions"}
    finally:
        s.close()
