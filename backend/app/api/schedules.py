from __future__ import annotations

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.base import get_db
from app.db.models import Schedule, ScheduleScope, User, iso
from app.scheduler import jobs
from app.services.audit import audit

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    cron: str = Field(min_length=9, max_length=64)
    scope: str = ScheduleScope.ALL.value
    targets: list[str] = []
    enabled: bool = True

    @field_validator("cron")
    @classmethod
    def check_cron(cls, v: str) -> str:
        try:
            CronTrigger.from_crontab(v)
        except ValueError as err:
            raise ValueError(f"Invalid cron expression: {err}") from err
        return v.strip()

    @field_validator("scope")
    @classmethod
    def check_scope(cls, v: str) -> str:
        if v not in {s.value for s in ScheduleScope}:
            raise ValueError("scope must be all, devices or tags")
        return v


class ScheduleOut(BaseModel):
    id: str
    name: str
    cron: str
    scope: str
    targets: list[str]
    enabled: bool
    last_run: str | None = None
    next_run: str | None = None


def _sched_out(s: Schedule) -> ScheduleOut:
    nr = jobs.next_run_for(s)
    return ScheduleOut(
        id=s.id,
        name=s.name,
        cron=s.cron,
        scope=s.scope,
        targets=s.targets or [],
        enabled=s.enabled,
        last_run=iso(s.last_run),
        next_run=iso(nr),
    )


def _sync(db: Session) -> None:
    jobs.sync_jobs(db)
    db.commit()


@router.get("", response_model=list[ScheduleOut])
def list_schedules(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [_sched_out(s) for s in db.query(Schedule).order_by(Schedule.name).all()]


@router.post("", response_model=ScheduleOut, status_code=201)
def create_schedule(
    body: ScheduleIn,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    s = Schedule(
        name=body.name,
        cron=body.cron,
        scope=body.scope,
        targets=body.targets,
        enabled=body.enabled,
    )
    db.add(s)
    db.commit()
    _sync(db)
    audit(db, "schedule.create", s.name, s.cron, actor=_user.username)
    return _sched_out(s)


@router.put("/{schedule_id}", response_model=ScheduleOut)
def update_schedule(
    schedule_id: str,
    body: ScheduleIn,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    s = db.get(Schedule, schedule_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    s.name = body.name
    s.cron = body.cron
    s.scope = body.scope
    s.targets = body.targets
    s.enabled = body.enabled
    db.commit()
    _sync(db)
    audit(db, "schedule.update", s.name, s.cron, actor=_user.username)
    return _sched_out(s)


@router.delete("/{schedule_id}", status_code=204)
def delete_schedule(
    schedule_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    s = db.get(Schedule, schedule_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    name = s.name
    db.delete(s)
    db.commit()
    _sync(db)
    audit(db, "schedule.delete", name, "", actor=_user.username)


@router.post("/{schedule_id}/run")
async def run_now(
    schedule_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    s = db.get(Schedule, schedule_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    import anyio

    await anyio.to_thread.run_sync(lambda: None)  # yield
    await jobs.run_backup_job(schedule_id)
    db.expire_all()
    s = db.get(Schedule, schedule_id)
    return {"ok": True, "last_run": iso(s.last_run) if s else None}
