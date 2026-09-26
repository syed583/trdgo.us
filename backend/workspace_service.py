"""
Personal workspace: watchlist, alerts, trade journal, strategy configuration.

All of it is persisted in PostgreSQL and owned by the operator. None of it
feeds the scoring engine, and none of it can place an order.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import live_market_service as market
from database import SessionLocal
from models import Company, EarningsEvent
from models_user import AlertRule, JournalEntry, StrategySetting, WatchlistItem


def _f(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


# ---------------------------------------------------------------------------
# watchlist
# ---------------------------------------------------------------------------


def _owner(owner: Optional[str]) -> Optional[str]:
    o = (owner or "").strip().lower()
    # The admin's own list is the legacy rows stored with owner NULL, so map
    # "admin" (and no owner) to None; everyone else is scoped by their username.
    return None if o in ("", "admin") else o


def list_watchlist(with_quotes: bool = True, owner: Optional[str] = None) -> dict:
    who = _owner(owner)
    db = SessionLocal()
    try:
        q = db.query(WatchlistItem)
        q = q.filter(WatchlistItem.owner.is_(None)) if who is None \
            else q.filter(WatchlistItem.owner == who)
        items = (
            q.order_by(WatchlistItem.sort_order.asc(), WatchlistItem.id.asc())
            .all()
        )
        rows = [{
            "id": i.id,
            "symbol": i.symbol,
            "note": i.note,
            "added": i.created_at.isoformat() if i.created_at else None,
            "price": None, "change": None, "change_percent": None,
            "next_earnings": None, "status": "OK",
        } for i in items]

        for row in rows:
            company = (db.query(Company)
                       .filter(Company.symbol == row["symbol"]).first())
            if company:
                event = (db.query(EarningsEvent)
                         .filter(EarningsEvent.company_id == company.id)
                         .filter(EarningsEvent.status == "scheduled")
                         .order_by(EarningsEvent.earnings_date.asc()).first())
                row["company"] = company.company_name
                if event and event.earnings_date:
                    row["next_earnings"] = event.earnings_date.isoformat()
                    row["next_earnings_label"] = event.earnings_date.strftime("%b %d, %Y")
    finally:
        db.close()

    if with_quotes and rows:
        batch = market.get_batch([r["symbol"] for r in rows][:25])
        quotes = batch.get("symbols", {})
        for row in rows:
            hit = quotes.get(row["symbol"])
            if hit:
                row["price"] = hit.get("price")
                row["change"] = hit.get("change")
                row["change_percent"] = hit.get("change_percent")
                row["status"] = hit.get("status", "OK")

    return {"rows": rows, "count": len(rows), "status": "OK", "source": "DATABASE"}


def add_watchlist(symbol: str, note: Optional[str] = None,
                  owner: Optional[str] = None) -> dict:
    who = _owner(owner)
    # A watchlist row is read straight back into a provider URL and a cache
    # key, so an invalid symbol is refused at write time rather than stored
    # and replayed. See input_validation.is_symbol.
    import input_validation as validate

    symbol = (symbol or "").strip().upper()
    if not validate.is_symbol(symbol):
        return {"status": "INVALID", "detail": "Symbol required"}

    # A note is free text but is stored and shown back; keep it short so it
    # cannot be used to stuff the row with an unbounded payload.
    if note is not None:
        note = str(note)[:280]

    db = SessionLocal()
    try:
        owner_filter = (WatchlistItem.owner.is_(None) if who is None
                        else WatchlistItem.owner == who)
        existing = (db.query(WatchlistItem)
                    .filter(WatchlistItem.symbol == symbol)
                    .filter(owner_filter).first())
        if existing:
            return {"status": "EXISTS", "symbol": symbol, "id": existing.id}

        highest = db.query(WatchlistItem).filter(owner_filter).count()
        item = WatchlistItem(symbol=symbol, note=note, sort_order=highest,
                             owner=who)
        db.add(item)
        db.commit()
        db.refresh(item)
        return {"status": "OK", "symbol": symbol, "id": item.id}
    finally:
        db.close()


def remove_watchlist(symbol: str, owner: Optional[str] = None) -> dict:
    symbol = symbol.strip().upper()
    who = _owner(owner)
    db = SessionLocal()
    try:
        q = db.query(WatchlistItem).filter(WatchlistItem.symbol == symbol)
        q = q.filter(WatchlistItem.owner.is_(None)) if who is None \
            else q.filter(WatchlistItem.owner == who)
        deleted = q.delete()
        db.commit()
        return {"status": "OK" if deleted else "NOT_FOUND", "symbol": symbol}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# alerts
# ---------------------------------------------------------------------------

ALERT_KINDS = {
    "PRICE": "Last price",
    "SCORE": "US-Stock Reader direction score",
    "CONFIDENCE": "Confidence score",
    "EXPECTED_MOVE": "Option-implied expected move %",
    "CHANGE_PERCENT": "Daily change %",
}


def list_alerts() -> dict:
    db = SessionLocal()
    try:
        rules = db.query(AlertRule).order_by(AlertRule.id.desc()).all()
        rows = [{
            "id": r.id,
            "symbol": r.symbol,
            "kind": r.kind,
            "kind_label": ALERT_KINDS.get(r.kind, r.kind),
            "comparator": r.comparator,
            "threshold": _f(r.threshold),
            "note": r.note,
            "active": r.active,
            "last_value": _f(r.last_value),
            "last_triggered_at": (r.last_triggered_at.isoformat()
                                  if r.last_triggered_at else None),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rules]
    finally:
        db.close()
    return {"rows": rows, "count": len(rows), "kinds": ALERT_KINDS,
            "status": "OK", "source": "DATABASE"}


def create_alert(symbol: str, kind: str, comparator: str,
                 threshold: float, note: Optional[str] = None) -> dict:
    symbol = symbol.strip().upper()
    kind = kind.strip().upper()
    if kind not in ALERT_KINDS:
        return {"status": "INVALID", "detail": f"Unknown alert kind: {kind}"}
    if comparator not in (">=", "<="):
        return {"status": "INVALID", "detail": "Comparator must be >= or <="}

    db = SessionLocal()
    try:
        rule = AlertRule(symbol=symbol, kind=kind, comparator=comparator,
                         threshold=threshold, note=note, active=True)
        db.add(rule)
        db.commit()
        db.refresh(rule)
        return {"status": "OK", "id": rule.id}
    finally:
        db.close()


def delete_alert(alert_id: int) -> dict:
    db = SessionLocal()
    try:
        deleted = db.query(AlertRule).filter(AlertRule.id == alert_id).delete()
        db.commit()
        return {"status": "OK" if deleted else "NOT_FOUND", "id": alert_id}
    finally:
        db.close()


def toggle_alert(alert_id: int, active: bool) -> dict:
    db = SessionLocal()
    try:
        rule = db.query(AlertRule).filter(AlertRule.id == alert_id).first()
        if not rule:
            return {"status": "NOT_FOUND", "id": alert_id}
        rule.active = active
        db.commit()
        return {"status": "OK", "id": alert_id, "active": active}
    finally:
        db.close()


def evaluate_alerts() -> dict:
    """
    Check every active rule against current values.

    Evaluated on demand rather than on a timer: this is a personal tool and a
    background poller would hold market-data lines open all day.
    """
    listing = list_alerts()
    rules = [r for r in listing["rows"] if r["active"]]
    if not rules:
        return {"rows": [], "triggered": 0, "status": "OK"}

    symbols = sorted({r["symbol"] for r in rules})
    batch = market.get_batch(symbols)
    quotes = batch.get("symbols", {})

    # Score-based rules need the scoring engine; only fetch for the symbols
    # that actually use one.
    score_symbols = {r["symbol"] for r in rules
                     if r["kind"] in ("SCORE", "CONFIDENCE", "EXPECTED_MOVE")}
    scores: dict[str, dict] = {}
    if score_symbols:
        import live_score_service as scoring
        for sym in sorted(score_symbols):
            try:
                scores[sym] = scoring.get_trdgo_score(sym)
            except Exception:  # noqa: BLE001
                scores[sym] = {}

    out = []
    triggered = 0
    db = SessionLocal()
    try:
        for rule in rules:
            quote = quotes.get(rule["symbol"], {})
            value: Optional[float] = None
            status = "OK"

            if rule["kind"] == "PRICE":
                value = quote.get("price")
            elif rule["kind"] == "CHANGE_PERCENT":
                value = quote.get("change_percent")
            elif rule["kind"] in ("SCORE", "CONFIDENCE"):
                final = (scores.get(rule["symbol"]) or {}).get("final") or {}
                value = (final.get("direction_score") if rule["kind"] == "SCORE"
                         else final.get("confidence_score"))
            elif rule["kind"] == "EXPECTED_MOVE":
                try:
                    import live_options_analytics as opt
                    import live_options_service as options
                    chain = options.load_chain(rule["symbol"])
                    if chain.get("status") == "OK":
                        value = opt.get_metrics(chain).get("expected_move_percent")
                    else:
                        status = chain.get("status", "DATA_UNAVAILABLE")
                except Exception:  # noqa: BLE001
                    status = "DATA_UNAVAILABLE"

            if value is None:
                fired = False
                if status == "OK":
                    status = "DATA_UNAVAILABLE"
            else:
                fired = (value >= rule["threshold"] if rule["comparator"] == ">="
                         else value <= rule["threshold"])

            if fired:
                triggered += 1
                row = db.query(AlertRule).filter(AlertRule.id == rule["id"]).first()
                if row:
                    row.last_triggered_at = datetime.now(timezone.utc)
                    row.last_value = value
                    db.commit()

            out.append({**rule, "current_value": value,
                        "triggered": fired, "data_status": status})
    finally:
        db.close()

    return {"rows": out, "triggered": triggered, "checked": len(out),
            "status": "OK", "source": "IBKR+DATABASE"}


# ---------------------------------------------------------------------------
# trade journal
# ---------------------------------------------------------------------------


def list_journal(limit: int = 200) -> dict:
    db = SessionLocal()
    try:
        entries = (db.query(JournalEntry)
                   .order_by(JournalEntry.trade_date.desc(),
                             JournalEntry.id.desc())
                   .limit(limit).all())
        rows = [{
            "id": e.id,
            "trade_date": e.trade_date.isoformat() if e.trade_date else None,
            "symbol": e.symbol,
            "direction": e.direction,
            "entry_price": _f(e.entry_price),
            "exit_price": _f(e.exit_price),
            "stop_price": _f(e.stop_price),
            "target_price": _f(e.target_price),
            "quantity": _f(e.quantity),
            "result": e.result,
            "pnl": _f(e.pnl),
            "notes": e.notes,
            "score_at_entry": _f(e.score_at_entry),
            "confidence_at_entry": _f(e.confidence_at_entry),
        } for e in entries]
    finally:
        db.close()

    closed = [r for r in rows if r["pnl"] is not None]
    wins = [r for r in closed if r["pnl"] > 0]
    losses = [r for r in closed if r["pnl"] < 0]
    gross_win = sum(r["pnl"] for r in wins)
    gross_loss = abs(sum(r["pnl"] for r in losses))

    stats = {
        "total": len(rows),
        "closed": len(closed),
        "open": len(rows) - len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "net_pnl": round(sum(r["pnl"] for r in closed), 2) if closed else None,
        "avg_win": round(gross_win / len(wins), 2) if wins else None,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else None,
        "profit_factor": (round(gross_win / gross_loss, 2)
                          if gross_loss > 0 else None),
    }
    return {"rows": rows, "stats": stats, "status": "OK", "source": "DATABASE"}


def create_journal(payload: dict) -> dict:
    try:
        trade_date = (datetime.strptime(payload["trade_date"], "%Y-%m-%d").date()
                      if payload.get("trade_date")
                      else datetime.now(market.EASTERN).date())
    except ValueError:
        return {"status": "INVALID", "detail": "trade_date must be YYYY-MM-DD"}

    symbol = str(payload.get("symbol", "")).strip().upper()
    if not symbol:
        return {"status": "INVALID", "detail": "symbol required"}

    direction = str(payload.get("direction", "LONG")).upper()
    if direction not in ("LONG", "SHORT"):
        return {"status": "INVALID", "detail": "direction must be LONG or SHORT"}

    entry = payload.get("entry_price")
    exit_ = payload.get("exit_price")
    qty = payload.get("quantity") or 1

    pnl = None
    result = "OPEN"
    if entry is not None and exit_ is not None:
        delta = (float(exit_) - float(entry)) * (1 if direction == "LONG" else -1)
        pnl = round(delta * float(qty), 2)
        result = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "FLAT"

    db = SessionLocal()
    try:
        entry_row = JournalEntry(
            trade_date=trade_date, symbol=symbol, direction=direction,
            entry_price=entry, exit_price=exit_,
            stop_price=payload.get("stop_price"),
            target_price=payload.get("target_price"),
            quantity=qty, result=result, pnl=pnl,
            notes=payload.get("notes"),
            score_at_entry=payload.get("score_at_entry"),
            confidence_at_entry=payload.get("confidence_at_entry"),
        )
        db.add(entry_row)
        db.commit()
        db.refresh(entry_row)
        return {"status": "OK", "id": entry_row.id, "pnl": pnl, "result": result}
    finally:
        db.close()


def delete_journal(entry_id: int) -> dict:
    db = SessionLocal()
    try:
        deleted = db.query(JournalEntry).filter(JournalEntry.id == entry_id).delete()
        db.commit()
        return {"status": "OK" if deleted else "NOT_FOUND", "id": entry_id}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# strategy configuration
# ---------------------------------------------------------------------------

# Mirrors the weights hard-coded in trdgo_score_service. These are the
# operator's personal view of the gates; the scoring engine itself is not
# rewritten from here.
STRATEGY_DEFAULTS: dict[str, Any] = {
    "min_score_long": 70,
    "min_score_short": 30,
    "min_confidence": 60,
    "weight_fundamentals": 20,
    "weight_estimates": 25,
    "weight_technicals": 20,
    "weight_earnings_history": 15,
    "weight_options": 15,
    "weight_market_environment": 5,
    "max_risk_per_trade_pct": 1.0,
    "max_open_positions": 5,
    "allow_long": True,
    "allow_short": False,
}

STRATEGY_META = {
    "min_score_long": {"label": "Minimum score for a long", "min": 0, "max": 100, "unit": ""},
    "min_score_short": {"label": "Maximum score for a short", "min": 0, "max": 100, "unit": ""},
    "min_confidence": {"label": "Confidence gate", "min": 0, "max": 100, "unit": ""},
    "weight_fundamentals": {"label": "Fundamentals weight", "min": 0, "max": 40, "unit": "pts"},
    "weight_estimates": {"label": "Estimate revisions weight", "min": 0, "max": 40, "unit": "pts"},
    "weight_technicals": {"label": "Technical weight", "min": 0, "max": 40, "unit": "pts"},
    "weight_earnings_history": {"label": "Earnings history weight", "min": 0, "max": 40, "unit": "pts"},
    "weight_options": {"label": "Options weight", "min": 0, "max": 40, "unit": "pts"},
    "weight_market_environment": {"label": "Market environment weight", "min": 0, "max": 20, "unit": "pts"},
    "max_risk_per_trade_pct": {"label": "Max risk per trade", "min": 0.1, "max": 10, "unit": "%"},
    "max_open_positions": {"label": "Max open positions", "min": 1, "max": 50, "unit": ""},
    "allow_long": {"label": "Allow long setups", "type": "bool"},
    "allow_short": {"label": "Allow short setups", "type": "bool"},
}


def get_strategy() -> dict:
    db = SessionLocal()
    try:
        stored = {s.key: s.value for s in db.query(StrategySetting).all()}
    finally:
        db.close()

    values = dict(STRATEGY_DEFAULTS)
    for key, raw in stored.items():
        if key not in values:
            continue
        try:
            values[key] = json.loads(raw)
        except (TypeError, ValueError):
            values[key] = raw

    weights = {k: v for k, v in values.items() if k.startswith("weight_")}
    return {
        "values": values,
        "defaults": STRATEGY_DEFAULTS,
        "meta": STRATEGY_META,
        "weight_total": sum(float(v) for v in weights.values()),
        "customised": sorted(stored.keys()),
        "note": (
            "Personal configuration only. Component weights shown here mirror "
            "the scoring engine's defaults; editing them does not rewrite the "
            "engine and no setting can place an order."
        ),
        "status": "OK",
        "source": "DATABASE",
    }


def update_strategy(updates: dict) -> dict:
    applied, rejected = {}, {}

    db = SessionLocal()
    try:
        for key, value in (updates or {}).items():
            if key not in STRATEGY_DEFAULTS:
                rejected[key] = "unknown setting"
                continue

            meta = STRATEGY_META.get(key, {})
            if meta.get("type") == "bool":
                value = bool(value)
            else:
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    rejected[key] = "not a number"
                    continue
                lo, hi = meta.get("min"), meta.get("max")
                if lo is not None and value < lo:
                    rejected[key] = f"below minimum {lo}"
                    continue
                if hi is not None and value > hi:
                    rejected[key] = f"above maximum {hi}"
                    continue
                if float(value).is_integer() and key != "max_risk_per_trade_pct":
                    value = int(value)

            row = (db.query(StrategySetting)
                   .filter(StrategySetting.key == key).first())
            if row:
                row.value = json.dumps(value)
            else:
                db.add(StrategySetting(key=key, value=json.dumps(value)))
            applied[key] = value
        db.commit()
    finally:
        db.close()

    return {"status": "OK" if applied else "NO_CHANGES",
            "applied": applied, "rejected": rejected,
            "config": get_strategy()}


def reset_strategy() -> dict:
    db = SessionLocal()
    try:
        db.query(StrategySetting).delete()
        db.commit()
    finally:
        db.close()
    return {"status": "OK", "config": get_strategy()}
