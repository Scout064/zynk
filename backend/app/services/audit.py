from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import AuditLog


def audit(
    db: Session,
    action: str,
    target: str = "",
    detail: str = "",
    actor: str = "system",
    ok: bool = True,
) -> AuditLog:
    entry = AuditLog(actor=actor, action=action, target=target, detail=detail, ok=ok)
    db.add(entry)
    db.commit()
    return entry
