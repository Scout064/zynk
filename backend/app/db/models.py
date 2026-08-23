from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class DeviceFamily(enum.StrEnum):
    SWITCH = "switch"
    FIREWALL = "firewall"
    AP = "ap"


class SnapshotSource(enum.StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    POST_REVERT = "post_revert"


class ScheduleScope(enum.StrEnum):
    ALL = "all"
    DEVICES = "devices"
    TAGS = "tags"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), index=True)
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=22)
    family: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(128), default="")
    username: Mapped[str] = mapped_column(String(64))
    password_enc: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    snapshots: Mapped[list[ConfigSnapshot]] = relationship(
        back_populates="device",
        order_by="ConfigSnapshot.ts.desc()",
        cascade="all, delete-orphan",
    )
    status: Mapped[DeviceStatus | None] = relationship(
        back_populates="device", uselist=False, cascade="all, delete-orphan"
    )


class ConfigSnapshot(Base):
    __tablename__ = "config_snapshots"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    source: Mapped[str] = mapped_column(String(16), default=SnapshotSource.MANUAL.value)
    config_hash: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    git_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rel_path: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(String(255), default="")

    device: Mapped[Device] = relationship(back_populates="snapshots")


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128))
    cron: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(16), default=ScheduleScope.ALL.value)
    targets: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DeviceStatus(Base):
    __tablename__ = "device_status"

    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    last_checked: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    reachable: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    method: Mapped[str] = mapped_column(String(16), default="tcp")

    device: Mapped[Device] = relationship(back_populates="status")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(255), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
