from __future__ import annotations

import anyio
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.db.base import get_db
from app.db.models import Device, DeviceFamily, User, iso
from app.devices.base import ConnectionSpec, ZyxelDriver
from app.devices.factory import make_driver
from app.devices.transport import DriverError
from app.devices.zyxel_drivers import ZyxelSwitchDriver
from app.services import backup
from app.services.audit import audit
from app.services.status import check_now

router = APIRouter(prefix="/api/devices", tags=["devices"])

FAMILIES = [f.value for f in DeviceFamily]


class DeviceIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    family: str
    model: str = ""
    username: str = Field(min_length=1, max_length=64)
    password: str = ""
    tags: list[str] = []
    enabled: bool = True
    notes: str = ""

    @field_validator("family")
    @classmethod
    def check_family(cls, v: str) -> str:
        if v not in FAMILIES:
            raise ValueError(f"family must be one of {FAMILIES}")
        return v


class DeviceOut(BaseModel):
    id: str
    name: str
    host: str
    port: int
    family: str
    model: str
    username: str
    tags: list[str]
    enabled: bool
    notes: str
    snapshot_count: int = 0
    last_snapshot_ts: str | None = None
    status: dict | None = None

    model_config = {"from_attributes": True}


def _device_out(d: Device) -> DeviceOut:
    latest = d.snapshots[0] if d.snapshots else None
    status = None
    if d.status is not None:
        status = {
            "reachable": d.status.reachable,
            "latency_ms": d.status.latency_ms,
            "last_checked": iso(d.status.last_checked),
        }
    return DeviceOut(
        id=d.id,
        name=d.name,
        host=d.host,
        port=d.port,
        family=d.family,
        model=d.model,
        username=d.username,
        tags=d.tags or [],
        enabled=d.enabled,
        notes=d.notes,
        snapshot_count=len(d.snapshots),
        last_snapshot_ts=iso(latest.ts) if latest else None,
        status=status,
    )


def _get_device(db: Session, device_id: str) -> Device:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@router.get("", response_model=list[DeviceOut])
def list_devices(
    tag: str | None = None,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List devices, optionally filtered by tag (`?tag=core`)."""
    devices = db.query(Device).order_by(Device.name).all()
    if tag:
        devices = [d for d in devices if tag in (d.tags or [])]
    return [_device_out(d) for d in devices]


@router.post("", response_model=DeviceOut, status_code=201)
def create_device(
    body: DeviceIn,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    device = Device(
        name=body.name,
        host=body.host,
        port=body.port,
        family=body.family,
        model=body.model,
        username=body.username,
        password_enc=encrypt_secret(body.password) if body.password else "",
        tags=body.tags,
        enabled=body.enabled,
        notes=body.notes,
    )
    db.add(device)
    db.commit()
    audit(db, "device.create", device.name, f"{body.family} @ {body.host}", actor=_user.username)
    return _device_out(device)


@router.get("/{device_id}", response_model=DeviceOut)
def get_device(
    device_id: str, _user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return _device_out(_get_device(db, device_id))


@router.put("/{device_id}", response_model=DeviceOut)
def update_device(
    device_id: str,
    body: DeviceIn,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    device = _get_device(db, device_id)
    device.name = body.name
    device.host = body.host
    device.port = body.port
    device.family = body.family
    device.model = body.model
    device.username = body.username
    if body.password:
        device.password_enc = encrypt_secret(body.password)
    device.tags = body.tags
    device.enabled = body.enabled
    device.notes = body.notes
    db.commit()
    audit(db, "device.update", device.name, "", actor=_user.username)
    return _device_out(device)


@router.delete("/{device_id}", status_code=204)
def delete_device(
    device_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    device = _get_device(db, device_id)
    name = device.name
    db.delete(device)
    db.commit()
    audit(db, "device.delete", name, "", actor=_user.username)


@router.post("/{device_id}/test")
async def test_device(
    device_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Test connection + authentication (runs `show version`-equivalent)."""
    device = _get_device(db, device_id)
    settings = get_settings()
    spec = ConnectionSpec(
        host=device.host,
        port=device.port,
        username=device.username,
        password=decrypt_secret(device.password_enc),
        connect_timeout=settings.ssh_connect_timeout_seconds,
        command_timeout=settings.ssh_command_timeout_seconds,
        tftp_address=settings.tftp_public_address,
        tftp_port=settings.tftp_port,
        reboot_timeout=float(settings.switch_reboot_timeout_seconds),
    )

    def _probe() -> dict:
        driver: ZyxelDriver = make_driver(spec, device.family)
        try:
            driver.connect()
            alive = driver.check_alive()
            result = {"ok": alive, "message": "Connection and authentication OK", "tftp": None}
            if alive and isinstance(driver, ZyxelSwitchDriver):
                # Verify the TFTP path used for config restores (switch pushes
                # its running config to a throwaway receiver; nothing modified).
                tftp_ok, tftp_msg = driver.test_tftp_path()
                result["tftp"] = {"ok": tftp_ok, "message": tftp_msg}
            return result
        except DriverError as err:
            return {"ok": False, "message": f"[{err.kind}] {err}", "tftp": None}
        except Exception as err:  # unexpected driver bugs
            return {"ok": False, "message": f"error: {err}", "tftp": None}
        finally:
            driver.close()

    result = await anyio.to_thread.run_sync(_probe)
    detail = result["message"]
    if result.get("tftp"):
        detail += f" | TFTP: {result['tftp']['message']}"
    audit(
        db,
        "device.test",
        device.name,
        detail,
        actor=_user.username,
        ok=result["ok"],
    )
    return result


@router.post("/{device_id}/pull")
async def pull_now(
    device_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    device = _get_device(db, device_id)
    result = await anyio.to_thread.run_sync(
        lambda: backup.pull_config(db, device, actor=_user.username)
    )
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.message)
    return {
        "ok": True,
        "saved": result.saved,
        "snapshot_id": result.snapshot_id,
        "hash": result.config_hash,
        "message": result.message,
    }


@router.post("/{device_id}/check")
async def check_status(
    device_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    device = _get_device(db, device_id)
    status = await check_now(db, device)
    return {
        "reachable": status.reachable,
        "latency_ms": status.latency_ms,
        "last_checked": iso(status.last_checked),
    }
