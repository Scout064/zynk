from __future__ import annotations

import platform
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.version import (
    APP_NAME,
    LICENSE,
    REPOSITORY,
    SUPPORTED_FAMILIES,
    __version__,
)
from app.db.base import get_db
from app.db.models import AuditLog, ConfigSnapshot, Device, Schedule, User

router = APIRouter(prefix="/api", tags=["about"])


@router.get("/about")
def about(
    request: Request,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Version, runtime and usage information (authenticated)."""
    started_at = getattr(request.app.state, "started_at", None)
    uptime = round((datetime.now(UTC) - started_at).total_seconds(), 1) if started_at else None
    stats = {
        "devices": db.query(func.count(Device.id)).scalar() or 0,
        "devices_enabled": db.query(func.count(Device.id)).filter(Device.enabled.is_(True)).scalar()
        or 0,
        "snapshots": db.query(func.count(ConfigSnapshot.id)).scalar() or 0,
        "schedules": db.query(func.count(Schedule.id)).scalar() or 0,
        "audit_entries": db.query(func.count(AuditLog.id)).scalar() or 0,
    }
    return {
        "name": APP_NAME,
        "version": __version__,
        "python_version": platform.python_version(),
        "license": LICENSE,
        "repository": REPOSITORY,
        "api_docs": "/docs",
        "started_at": started_at.isoformat() if started_at else None,  # already aware
        "uptime_seconds": uptime,
        "stats": stats,
        "families": SUPPORTED_FAMILIES,
    }
