from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.base import get_db
from app.db.models import AuditLog, Device, User

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status")
def overall_status(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    devices = db.query(Device).order_by(Device.name).all()
    out = []
    for d in devices:
        item = {
            "device_id": d.id,
            "name": d.name,
            "family": d.family,
            "enabled": d.enabled,
            "reachable": None,
            "latency_ms": None,
            "last_checked": None,
        }
        if d.status is not None:
            item["reachable"] = d.status.reachable
            item["latency_ms"] = d.status.latency_ms
            item["last_checked"] = d.status.last_checked.isoformat()
        out.append(item)
    online = sum(1 for i in out if i["reachable"] is True)
    return {"online": online, "offline": len(out) - online, "devices": out}


@router.get("/audit")
def audit_log(
    limit: int = 100,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    limit = min(max(limit, 1), 500)
    entries = db.query(AuditLog).order_by(AuditLog.ts.desc()).limit(limit).all()
    return [
        {
            "id": e.id,
            "ts": e.ts.isoformat(),
            "actor": e.actor,
            "action": e.action,
            "target": e.target,
            "detail": e.detail,
            "ok": e.ok,
        }
        for e in entries
    ]
