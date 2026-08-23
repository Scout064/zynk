from __future__ import annotations

import difflib
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import ConfigSnapshot, Device, DeviceFamily, SnapshotSource
from app.devices.base import ConnectionSpec, ZyxelDriver
from app.devices.factory import make_driver
from app.services import gitstore
from app.services.audit import audit

# Common noise to strip from pulled configs (pager remnants, banners).
_NOISE_PATTERNS = (
    re.compile(r"^#\s*more\s*#", re.IGNORECASE),
    re.compile(r"^--\s*more\s*--", re.IGNORECASE),
    re.compile(r"^Press any key to continue", re.IGNORECASE),
    re.compile(r"^\s*:\s*$"),
)


def normalize_config(text: str) -> str:
    lines = []
    for ln in text.splitlines():
        if any(p.search(ln) for p in _NOISE_PATTERNS):
            continue
        lines.append(ln.rstrip())
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n" if lines else ""


def config_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def make_connection_spec(device: Device) -> ConnectionSpec:
    from app.core.crypto import decrypt_secret

    settings = get_settings()
    return ConnectionSpec(
        host=device.host,
        port=device.port,
        username=device.username,
        password=decrypt_secret(device.password_enc),
        connect_timeout=settings.ssh_connect_timeout_seconds,
        command_timeout=settings.ssh_command_timeout_seconds,
    )


@dataclass
class PullResult:
    ok: bool
    saved: bool = False
    snapshot_id: str | None = None
    config_hash: str | None = None
    message: str = ""


def pull_config(
    db: Session,
    device: Device,
    source: SnapshotSource = SnapshotSource.MANUAL,
    actor: str = "system",
) -> PullResult:
    """Connect, pull, dedup, persist. Always audited."""
    try:
        driver: ZyxelDriver = make_driver(make_connection_spec(device), device.family)
    except Exception as err:  # unknown family etc.
        audit(db, "pull", device.name, f"driver error: {err}", actor=actor, ok=False)
        return PullResult(ok=False, message=str(err))

    try:
        driver.connect()
        raw = driver.get_config()
    except Exception as err:
        driver.close()
        kind = getattr(err, "kind", "error")
        audit(db, "pull", device.name, f"{kind}: {err}", actor=actor, ok=False)
        return PullResult(ok=False, message=f"[{kind}] {err}")
    finally:
        driver.close()

    text = normalize_config(raw)
    if not text.strip():
        audit(db, "pull", device.name, "empty config returned", actor=actor, ok=False)
        return PullResult(ok=False, message="Device returned an empty configuration")

    digest = config_hash(text)
    latest = (
        db.query(ConfigSnapshot)
        .filter(ConfigSnapshot.device_id == device.id)
        .order_by(ConfigSnapshot.ts.desc())
        .first()
    )
    if latest is not None and latest.config_hash == digest:
        audit(
            db,
            "pull",
            device.name,
            f"unchanged (hash {digest[:12]})",
            actor=actor,
            ok=True,
        )
        return PullResult(ok=True, saved=False, config_hash=digest, message="unchanged")

    settings = get_settings()
    ts = datetime.now(UTC)
    rel = f"{gitstore.device_dir(device.id)}/{ts.strftime('%Y%m%d-%H%M%S')}-{digest[:8]}.cfg"
    abs_path = settings.config_repo_dir / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(text, encoding="utf-8")

    commit = None
    try:
        gitstore.ensure_repo()
        commit = gitstore.commit_file(rel, f"{device.name}: config backup ({source.value})")
    except gitstore.GitStoreError:
        commit = None  # git is optional storage; DB remains source of truth

    snap = ConfigSnapshot(
        device_id=device.id,
        ts=ts,
        source=source.value,
        config_hash=digest,
        size_bytes=len(text.encode()),
        git_commit=commit,
        rel_path=rel,
        message="config changed" if latest is not None else "initial backup",
    )
    db.add(snap)
    db.commit()

    audit(
        db,
        "pull",
        device.name,
        f"new snapshot {snap.id[:8]} ({len(text)} bytes, commit {commit or 'n/a'})",
        actor=actor,
        ok=True,
    )
    return PullResult(ok=True, saved=True, snapshot_id=snap.id, config_hash=digest, message="saved")


def snapshot_text(snapshot: ConfigSnapshot) -> str:
    return gitstore.read_file(snapshot.rel_path)


def diff_snapshots(a: ConfigSnapshot, b: ConfigSnapshot) -> str:
    text_a = snapshot_text(a).splitlines(keepends=True)
    text_b = snapshot_text(b).splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            text_a,
            text_b,
            fromfile=f"{a.ts:%Y-%m-%d %H:%M} ({a.id[:8]})",
            tofile=f"{b.ts:%Y-%m-%d %H:%M} ({b.id[:8]})",
        )
    )


def export_device_history(device: Device) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for snap in device.snapshots:
            zf.writestr(
                f"{device.name}/{snap.ts:%Y%m%d-%H%M%S}-{snap.config_hash[:8]}.cfg",
                snapshot_text(snap),
            )
    buf.seek(0)
    return buf


def revert_to_snapshot(
    db: Session, device: Device, snapshot: ConfigSnapshot, actor: str
) -> PullResult:
    """Destructive: push an old config back to the device, then re-pull to confirm."""
    text = snapshot_text(snapshot)
    try:
        driver = make_driver(make_connection_spec(device), device.family)
    except Exception as err:
        audit(db, "revert", device.name, f"driver error: {err}", actor=actor, ok=False)
        return PullResult(ok=False, message=str(err))

    try:
        driver.connect()
        driver.apply_config(text)
    except Exception as err:
        driver.close()
        kind = getattr(err, "kind", "error")
        audit(db, "revert", device.name, f"{kind}: {err}", actor=actor, ok=False)
        return PullResult(ok=False, message=f"[{kind}] {err}")
    finally:
        driver.close()

    audit(
        db,
        "revert",
        device.name,
        f"applied snapshot {snapshot.id[:8]} ({snapshot.ts:%Y-%m-%d %H:%M} UTC)",
        actor=actor,
        ok=True,
    )
    # Confirm the applied state with a fresh pull.
    result = pull_config(db, device, source=SnapshotSource.POST_REVERT, actor=actor)
    return result


SUPPORTED_FAMILIES = {f.value for f in DeviceFamily}
