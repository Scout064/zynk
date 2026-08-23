from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.base import get_db
from app.db.models import ConfigSnapshot, Device, User
from app.services import backup
from app.services.audit import audit

router = APIRouter(prefix="/api", tags=["configs"])


class SnapshotOut(BaseModel):
    id: str
    device_id: str
    ts: str
    source: str
    config_hash: str
    size_bytes: int
    git_commit: str | None
    message: str


class RevertIn(BaseModel):
    confirm: bool = False


def _snap_out(s: ConfigSnapshot) -> SnapshotOut:
    return SnapshotOut(
        id=s.id,
        device_id=s.device_id,
        ts=s.ts.isoformat(),
        source=s.source,
        config_hash=s.config_hash,
        size_bytes=s.size_bytes,
        git_commit=s.git_commit,
        message=s.message,
    )


def _get_device(db: Session, device_id: str) -> Device:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


def _get_snapshot(db: Session, snapshot_id: str) -> ConfigSnapshot:
    snap = db.get(ConfigSnapshot, snapshot_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return snap


@router.get("/devices/{device_id}/snapshots", response_model=list[SnapshotOut])
def list_snapshots(
    device_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    device = _get_device(db, device_id)
    return [_snap_out(s) for s in device.snapshots]


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(
    snapshot_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    snap = _get_snapshot(db, snapshot_id)
    return PlainTextResponse(
        backup.snapshot_text(snap),
        media_type="text/plain; charset=utf-8",
    )


@router.get("/snapshots/{snapshot_id}/download")
def download_snapshot(
    snapshot_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    snap = _get_snapshot(db, snapshot_id)
    device = db.get(Device, snap.device_id)
    audit(
        db, "export", device.name if device else snap.device_id, snap.id[:8], actor=_user.username
    )
    filename = f"{device.name if device else 'device'}_{snap.ts:%Y%m%d-%H%M%S}.cfg"
    return PlainTextResponse(
        backup.snapshot_text(snap),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/diff")
def diff(
    a: str,
    b: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    snap_a = _get_snapshot(db, a)
    snap_b = _get_snapshot(db, b)
    if snap_a.device_id != snap_b.device_id:
        raise HTTPException(status_code=400, detail="Snapshots belong to different devices")
    return PlainTextResponse(backup.diff_snapshots(snap_a, snap_b))


@router.get("/devices/{device_id}/export")
def export_history(
    device_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    device = _get_device(db, device_id)
    audit(db, "export", device.name, "full history zip", actor=_user.username)
    buf = backup.export_device_history(device)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{device.name}-history.zip"'},
    )


@router.post("/snapshots/{snapshot_id}/revert")
async def revert(
    snapshot_id: str,
    body: RevertIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Destructive: push this snapshot's config back to the device."""
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Revert requires explicit confirmation")
    snap = _get_snapshot(db, snapshot_id)
    device = _get_device(db, snap.device_id)

    import anyio

    result = await anyio.to_thread.run_sync(
        lambda: backup.revert_to_snapshot(db, device, snap, user.username)
    )
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.message)
    return {
        "ok": True,
        "confirm_pull_saved": result.saved,
        "message": result.message,
    }
