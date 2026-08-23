from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from app.db.base import session_scope
from app.db.models import Device, Schedule, ScheduleScope, SnapshotSource
from app.services.backup import pull_config

log = logging.getLogger("zynk.scheduler")

scheduler = AsyncIOScheduler(timezone="UTC")


def resolve_devices(db: Session, schedule: Schedule) -> list[Device]:
    q = db.query(Device).filter(Device.enabled.is_(True))
    if schedule.scope == ScheduleScope.DEVICES.value:
        q = q.filter(Device.id.in_(schedule.targets or []))
    elif schedule.scope == ScheduleScope.TAGS.value:
        devices = q.all()
        wanted = set(schedule.targets or [])
        devices = [d for d in devices if wanted.intersection(d.tags or [])]
        return devices
    return q.all()


async def run_backup_job(schedule_id: str) -> None:
    db = session_scope()
    try:
        schedule = db.get(Schedule, schedule_id)
        if schedule is None or not schedule.enabled:
            return
        devices = resolve_devices(db, schedule)
        actor = f"schedule:{schedule.name}"
        for device in devices:
            try:
                result = await asyncio.to_thread(
                    pull_config, db, device, SnapshotSource.SCHEDULED, actor
                )
                log.info(
                    "scheduled pull %s/%s: %s",
                    schedule.name,
                    device.name,
                    "saved" if result.saved else result.message,
                )
            except Exception:
                log.exception("scheduled pull failed for %s", device.name)
        schedule.last_run = datetime.now(UTC)
        db.commit()
    finally:
        db.close()


def sync_jobs(db: Session) -> None:
    """Reconcile APScheduler jobs with the Schedule table."""
    schedules = db.query(Schedule).all()
    wanted: set[str] = set()
    for s in schedules:
        job_id = f"schedule-{s.id}"
        wanted.add(job_id)
        trigger = CronTrigger.from_crontab(s.cron, timezone="UTC")
        existing = scheduler.get_job(job_id)
        if existing is not None:
            scheduler.modify_job(job_id, trigger=trigger)
            if not s.enabled:
                scheduler.pause_job(job_id)
            else:
                scheduler.resume_job(job_id)
        else:
            scheduler.add_job(
                run_backup_job,
                trigger,
                id=job_id,
                args=[s.id],
                coalesce=True,
                max_instances=1,
            )
            if not s.enabled:
                scheduler.pause_job(job_id)
    for job in scheduler.get_jobs():
        if job.id.startswith("schedule-") and job.id not in wanted:
            scheduler.remove_job(job.id)


def next_run_for(schedule: Schedule) -> datetime | None:
    job = scheduler.get_job(f"schedule-{schedule.id}")
    if job is None:
        return None
    nr = job.next_run_time
    return nr if isinstance(nr, datetime) else None


def start_scheduler() -> None:
    if not scheduler.running:
        scheduler.start()


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
