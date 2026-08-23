from __future__ import annotations

import asyncio
import time

from sqlalchemy.orm import Session

from app.db.models import Device, DeviceStatus, utcnow
from app.services.audit import audit


async def probe_device(device: Device, timeout: float = 5.0) -> tuple[bool, float | None]:
    """TCP probe of the SSH port; returns (reachable, latency_ms)."""
    start = time.perf_counter()
    try:
        fut = asyncio.open_connection(device.host, device.port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
        latency = (time.perf_counter() - start) * 1000.0
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        return True, round(latency, 1)
    except (TimeoutError, OSError):
        return False, None


def update_status(
    db: Session, device: Device, reachable: bool, latency_ms: float | None
) -> DeviceStatus:
    status = device.status
    if status is None:
        status = DeviceStatus(device_id=device.id)
        db.add(status)
    status.reachable = reachable
    status.latency_ms = latency_ms
    status.method = "tcp"
    status.last_checked = utcnow()
    db.commit()
    return status


async def check_now(db: Session, device: Device) -> DeviceStatus:
    reachable, latency = await probe_device(device)
    return update_status(db, device, reachable, latency)


async def poll_all(db: Session, actor_for_failures: bool = False) -> None:
    devices = db.query(Device).filter(Device.enabled.is_(True)).all()
    results = await asyncio.gather(*(probe_device(d) for d in devices))
    for dev, (reachable, latency) in zip(devices, results, strict=True):
        update_status(db, dev, reachable, latency)
        if actor_for_failures and not reachable:
            audit(db, "status", dev.name, "device unreachable", ok=False)
