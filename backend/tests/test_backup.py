from __future__ import annotations

import pytest

from app.core.crypto import encrypt_secret
from app.db.base import init_db, session_scope
from app.db.models import Device
from app.services import backup, gitstore

CONFIG_V1 = "interface port-channel 1\n speed auto\nexit\n"
CONFIG_V2 = "interface port-channel 1\n speed 1g\nexit\n"


@pytest.fixture
def db():
    init_db()
    gitstore.ensure_repo()
    s = session_scope()
    yield s
    s.close()


def make_device(db, name="sw1", family="switch") -> Device:
    d = Device(
        name=name,
        host="10.0.0.1",
        port=22,
        family=family,
        username="admin",
        password_enc=encrypt_secret("pw"),
    )
    db.add(d)
    db.commit()
    return d


class TestNormalize:
    def test_strips_pager_artifacts(self):
        raw = "line1\n-- more --\nline2\nPress any key to continue\nline3\n"
        out = backup.normalize_config(raw)
        assert out == "line1\nline2\nline3\n"

    def test_trailing_whitespace(self):
        assert backup.normalize_config("a  \nb\n\n\n") == "a\nb\n"


class TestPull:
    def test_pull_saves_and_dedups(self, db, monkeypatch):
        device = make_device(db)
        outputs = iter([CONFIG_V1, CONFIG_V1, CONFIG_V2])

        class FakeDriver:
            family = "switch"

            def connect(self): ...

            def get_config(self):
                return next(outputs)

            def close(self): ...

        monkeypatch.setattr(backup, "make_driver", lambda spec, family: FakeDriver())

        r1 = backup.pull_config(db, device)
        assert r1.ok and r1.saved
        r2 = backup.pull_config(db, device)
        assert r2.ok and not r2.saved  # dedup: unchanged config
        r3 = backup.pull_config(db, device)
        assert r3.ok and r3.saved  # changed

        snaps = device.snapshots
        assert len(snaps) == 2
        assert snaps[0].config_hash != snaps[1].config_hash
        assert snaps[0].git_commit  # git storage engaged
        # history is newest-first
        assert snaps[0].size_bytes == len(CONFIG_V2.encode())

    def test_pull_failure_audited(self, db, monkeypatch):
        from app.devices.transport import AuthError

        device = make_device(db)

        class BadDriver:
            def connect(self):
                raise AuthError("nope")

            def close(self): ...

        monkeypatch.setattr(backup, "make_driver", lambda spec, family: BadDriver())
        result = backup.pull_config(db, device)
        assert not result.ok
        assert "auth" in result.message

        from app.db.models import AuditLog

        entry = db.query(AuditLog).filter(AuditLog.action == "pull").one()
        assert not entry.ok


class TestDiffExport:
    def test_diff(self, db, monkeypatch):
        device = make_device(db)
        outputs = iter([CONFIG_V1, CONFIG_V2])

        class FakeDriver:
            def connect(self): ...

            def get_config(self):
                return next(outputs)

            def close(self): ...

        monkeypatch.setattr(backup, "make_driver", lambda spec, family: FakeDriver())
        backup.pull_config(db, device)
        backup.pull_config(db, device)
        a, b = device.snapshots[1], device.snapshots[0]
        diff = backup.diff_snapshots(a, b)
        assert "- speed auto" in diff
        assert "+ speed 1g" in diff

    def test_export_zip(self, db, monkeypatch):
        import io
        import zipfile

        device = make_device(db)

        class FakeDriver:
            def connect(self): ...

            def get_config(self):
                return CONFIG_V1

            def close(self): ...

        monkeypatch.setattr(backup, "make_driver", lambda spec, family: FakeDriver())
        backup.pull_config(db, device)
        buf = backup.export_device_history(device)
        with zipfile.ZipFile(io.BytesIO(buf.read())) as zf:
            assert len(zf.namelist()) == 1
            assert zf.read(zf.namelist()[0]).decode() == CONFIG_V1


class TestGitStore:
    def test_commit_and_read(self):
        rel = "dev1/test.cfg"
        from app.core.config import get_settings

        target = get_settings().config_repo_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(CONFIG_V1)
        sha = gitstore.commit_file(rel, "test commit")
        assert sha
        assert gitstore.read_file(rel) == CONFIG_V1
        target.write_text(CONFIG_V2)
        sha2 = gitstore.commit_file(rel, "second commit")
        assert sha2 != sha
        assert gitstore.read_file_at(sha, rel) == CONFIG_V1
        assert gitstore.read_file(rel) == CONFIG_V2
