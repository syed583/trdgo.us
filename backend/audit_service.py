"""
Admin audit log: an append-only record of every privileged action.

Each mutating admin endpoint calls `record(...)` after it succeeds, so the team
can always answer "who did this, to what, and when". Reads come back newest
first, with optional filters, for the admin Audit Log page.

Logging must never break the action it records, so `record` swallows its own
errors (a failed write is logged to stderr, not raised).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from database import SessionLocal
from models_user import AuditLog


def record(actor: str, action: str, target: Optional[str] = None,
           detail: Optional[str] = None, ip: Optional[str] = None,
           actor_role: Optional[str] = None) -> None:
    """Append one audit row. Never raises."""
    db = SessionLocal()
    try:
        db.add(AuditLog(
            actor=(actor or "?")[:40],
            actor_role=(actor_role or None),
            action=(action or "")[:48],
            target=(target or None) and str(target)[:80],
            detail=(detail or None) and str(detail)[:2000],
            ip=(ip or None) and str(ip)[:64],
        ))
        db.commit()
    except Exception as exc:  # noqa: BLE001 - auditing must not break the action
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        print(f"audit: failed to record {action!r} by {actor!r} ({exc})")
    finally:
        db.close()


def recent(limit: int = 100, actor: Optional[str] = None,
           action: Optional[str] = None, target: Optional[str] = None,
           days: Optional[int] = None) -> list[dict]:
    """Newest-first audit rows, with optional filters. Capped at 500."""
    limit = max(1, min(int(limit or 100), 500))
    db = SessionLocal()
    try:
        q = db.query(AuditLog)
        if actor:
            q = q.filter(AuditLog.actor == actor.strip().lower())
        if action:
            q = q.filter(AuditLog.action.like(f"{action.strip()}%"))
        if target:
            q = q.filter(AuditLog.target == target.strip())
        if days:
            since = datetime.now(timezone.utc) - timedelta(days=int(days))
            q = q.filter(AuditLog.at >= since)
        rows = q.order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(limit).all()
        return [{
            "id": r.id,
            "actor": r.actor,
            "actor_role": r.actor_role,
            "action": r.action,
            "target": r.target,
            "detail": r.detail,
            "ip": r.ip,
            "at": r.at.isoformat() if r.at else None,
        } for r in rows]
    finally:
        db.close()


def for_target(target: str, limit: int = 50) -> list[dict]:
    """Audit rows about one entity (e.g. a username), for its detail view."""
    return recent(limit=limit, target=target)
