from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.base import get_db
from app.db.models import AuditLog, Device, User, iso

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status")
def overall_status(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Device online/offline status.

    The backend probes every enabled device every ZYNK_STATUS_POLL_INTERVAL_SECONDS
    (default 300s). Status is persisted between checks — a device keeps its last
    known state until the next check completes. `last_checked` shows how fresh the
    reading is (null = never checked).
    """
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
            item["last_checked"] = iso(d.status.last_checked)
        out.append(item)
    online = sum(1 for i in out if i["reachable"] is True)
    return {
        "online": online,
        "offline": len(out) - online,
        "interval_seconds": get_settings().status_poll_interval_seconds,
        "devices": out,
    }


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
            "ts": iso(e.ts),
            "actor": e.actor,
            "action": e.action,
            "target": e.target,
            "detail": e.detail,
            "ok": e.ok,
        }
        for e in entries
    ]
