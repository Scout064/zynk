from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.crypto import encrypt_secret
from app.db.base import init_db, session_scope
from app.db.models import Device
from app.main import app


@pytest.fixture(autouse=True)
def _bootstrap(monkeypatch):
    monkeypatch.setenv("ZYNK_INITIAL_ADMIN_PASSWORD", "admin-test-pw")
    init_db()
    from app.api.deps import bootstrap_admin

    db = session_scope()
    try:
        bootstrap_admin(db)
    finally:
        db.close()
    yield


@pytest.fixture
def client(_bootstrap) -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    resp = client.post("/api/auth/token", data={"username": "admin", "password": "admin-test-pw"})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def make_device_row(name="sw1", family="switch") -> str:
    db = session_scope()
    try:
        d = Device(
            name=name,
            host="10.0.0.9",
            family=family,
            username="admin",
            password_enc=encrypt_secret("pw"),
        )
        db.add(d)
        db.commit()
        return d.id
    finally:
        db.close()


class TestAuth:
    def test_login_bad_credentials(self, client: TestClient):
        resp = client.post("/api/auth/token", data={"username": "admin", "password": "nope"})
        assert resp.status_code == 401

    def test_endpoints_require_auth(self, client: TestClient):
        for path in ("/api/devices", "/api/status", "/api/audit", "/api/schedules"):
            assert client.get(path).status_code == 401, path

    def test_me(self, client: TestClient, auth_headers):
        resp = client.get("/api/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["username"] == "admin"


class TestDevices:
    def test_crud(self, client: TestClient, auth_headers):
        payload = {
            "name": "Core-SW",
            "host": "10.0.0.2",
            "port": 22,
            "family": "switch",
            "model": "XS1930-12HP",
            "username": "admin",
            "password": "secret",
            "tags": ["core"],
        }
        resp = client.post("/api/devices", json=payload, headers=auth_headers)
        assert resp.status_code == 201, resp.text
        dev = resp.json()
        assert dev["name"] == "Core-SW"
        assert dev["snapshot_count"] == 0
        assert "password" not in dev and "password_enc" not in dev

        resp = client.get("/api/devices", headers=auth_headers)
        assert len(resp.json()) == 1

        payload["name"] = "Core-SW-2"
        payload["password"] = ""
        resp = client.put(f"/api/devices/{dev['id']}", json=payload, headers=auth_headers)
        assert resp.json()["name"] == "Core-SW-2"

        resp = client.delete(f"/api/devices/{dev['id']}", headers=auth_headers)
        assert resp.status_code == 204
        assert client.get("/api/devices", headers=auth_headers).json() == []

    def test_invalid_family_rejected(self, client: TestClient, auth_headers):
        payload = {
            "name": "x",
            "host": "1.2.3.4",
            "family": "toaster",
            "username": "admin",
        }
        resp = client.post("/api/devices", json=payload, headers=auth_headers)
        assert resp.status_code == 422


class TestSchedules:
    def test_crud_and_validation(self, client: TestClient, auth_headers):
        bad = client.post(
            "/api/schedules",
            json={"name": "bad", "cron": "not-cron"},
            headers=auth_headers,
        )
        assert bad.status_code == 422

        resp = client.post(
            "/api/schedules",
            json={"name": "nightly", "cron": "0 2 * * *", "scope": "all"},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        sched = resp.json()
        assert sched["next_run"], "scheduler should compute next run"

        listing = client.get("/api/schedules", headers=auth_headers)
        assert len(listing.json()) == 1

        resp = client.put(
            f"/api/schedules/{sched['id']}",
            json={"name": "nightly", "cron": "30 3 * * *", "scope": "all", "enabled": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["cron"] == "30 3 * * *"

        assert (
            client.delete(f"/api/schedules/{sched['id']}", headers=auth_headers).status_code == 204
        )


class TestAbout:
    def test_about_requires_auth(self, client: TestClient):
        assert client.get("/api/about").status_code == 401

    def test_about(self, client: TestClient, auth_headers):
        make_device_row("about-sw")
        resp = client.get("/api/about", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Zynk"
        assert data["version"] == "0.4.0"
        assert data["license"] == "MIT"
        assert data["repository"].startswith("https://github.com/")
        assert data["stats"]["devices"] == 1
        assert data["stats"]["snapshots"] == 0
        assert {f["family"] for f in data["families"]} == {
            "switch",
            "firewall",
            "zld_firewall",
            "ap",
        }
        zld = next(f for f in data["families"] if f["family"] == "zld_firewall")
        assert zld["eol"] is True
        assert zld["revert_supported"] is True
        assert data["started_at"] is not None
        assert data["uptime_seconds"] is not None

    def test_health_includes_version(self, client: TestClient):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["version"] == "0.4.0"


class TestStatusAndAudit:
    def test_status_lists_devices(self, client: TestClient, auth_headers):
        make_device_row("edge-sw")
        resp = client.get("/api/status", headers=auth_headers)
        data = resp.json()
        assert data["devices"][0]["name"] == "edge-sw"
        assert data["devices"][0]["reachable"] is None
        assert data["devices"][0]["last_checked"] is None  # never checked
        assert data["interval_seconds"] == 300  # 5-minute default

    def test_status_persists_until_next_check(self, client: TestClient, auth_headers):
        """Device keeps last known state between polls (no reset when not probed)."""
        import socket

        device_id = make_device_row("stale-sw")
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            db = session_scope()
            try:
                from app.db.models import Device

                dev = db.get(Device, device_id)
                dev.host = "127.0.0.1"
                dev.port = port
                db.commit()
            finally:
                db.close()
            r1 = client.post(f"/api/devices/{device_id}/check", headers=auth_headers)
            assert r1.json()["reachable"] is True
        finally:
            srv.close()
        # Port now closed, but WITHOUT a new check the old state must persist
        data = client.get("/api/status", headers=auth_headers).json()
        entry = next(d for d in data["devices"] if d["device_id"] == device_id)
        assert entry["reachable"] is True
        assert entry["last_checked"] is not None
        # After an actual check it flips to offline
        r2 = client.post(f"/api/devices/{device_id}/check", headers=auth_headers)
        assert r2.json()["reachable"] is False

    def test_audit_log_records_logins(self, client: TestClient, auth_headers):
        resp = client.get("/api/audit", headers=auth_headers)
        entries = resp.json()
        assert any(e["action"] == "login" and e["ok"] for e in entries)


class TestSnapshotEndpoints:
    def test_history_flow(self, client: TestClient, auth_headers, monkeypatch):
        from app.services import backup as backup_mod

        device_id = make_device_row("hist-sw")
        outputs = iter(["conf a\n", "conf a\n", "conf b\n"])

        class FakeDriver:
            family = "switch"

            def connect(self): ...

            def get_config(self):
                return next(outputs)

            def close(self): ...

        monkeypatch.setattr(backup_mod, "make_driver", lambda spec, family: FakeDriver())

        r1 = client.post(f"/api/devices/{device_id}/pull", headers=auth_headers)
        assert r1.status_code == 200 and r1.json()["saved"]
        r2 = client.post(f"/api/devices/{device_id}/pull", headers=auth_headers)
        assert r2.json()["saved"] is False  # dedup
        r3 = client.post(f"/api/devices/{device_id}/pull", headers=auth_headers)
        assert r3.json()["saved"]

        snaps = client.get(f"/api/devices/{device_id}/snapshots", headers=auth_headers).json()
        assert len(snaps) == 2

        raw = client.get(f"/api/snapshots/{snaps[0]['id']}", headers=auth_headers)
        assert raw.text == "conf b\n"

        diff = client.get(
            "/api/diff",
            params={"a": snaps[1]["id"], "b": snaps[0]["id"]},
            headers=auth_headers,
        )
        assert "-conf a" in diff.text
        assert "+conf b" in diff.text

        dl = client.get(f"/api/snapshots/{snaps[0]['id']}/download", headers=auth_headers)
        assert "attachment" in dl.headers["content-disposition"]

    def test_pull_failure_surfaces_detail(self, client: TestClient, auth_headers):
        device_id = make_device_row("dead-sw")

        class DeadDriver:
            def connect(self):
                from app.devices.transport import AuthError

                raise AuthError("Authentication failed for user 'admin'")

            def close(self): ...

        import app.services.backup as backup_mod

        orig = backup_mod.make_driver
        backup_mod.make_driver = lambda spec, family: DeadDriver()
        try:
            resp = client.post(f"/api/devices/{device_id}/pull", headers=auth_headers)
        finally:
            backup_mod.make_driver = orig
        assert resp.status_code == 502
        assert "auth" in resp.json()["detail"]
