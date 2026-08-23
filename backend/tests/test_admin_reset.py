from __future__ import annotations

from fastapi.testclient import TestClient

import app.core.config as config_mod
from app.api.deps import bootstrap_admin
from app.core.security import hash_password, verify_password
from app.db.base import init_db, session_scope
from app.db.models import User
from app.main import app


def restart() -> None:
    """Simulate an app restart: fresh settings cache + fresh engine."""
    config_mod.get_settings.cache_clear()
    import app.db.base as db_base

    db_base._engine = None
    db_base._session_factory = None
    init_db()


def _admin_hash() -> str:
    db = session_scope()
    try:
        return db.query(User).filter(User.username == "admin").one().password_hash
    finally:
        db.close()


def test_first_run_creates_admin_with_env_password(monkeypatch):
    monkeypatch.setenv("ZYNK_INITIAL_ADMIN_PASSWORD", "first-run-pw")
    restart()
    db = session_scope()
    try:
        assert bootstrap_admin(db) is None  # not generated -> no return
    finally:
        db.close()
    assert verify_password("first-run-pw", _admin_hash())


def test_second_run_is_noop_without_flag(monkeypatch):
    monkeypatch.setenv("ZYNK_INITIAL_ADMIN_PASSWORD", "pw-one")
    restart()
    db = session_scope()
    bootstrap_admin(db)
    db.close()

    # restart with a different env password: must NOT change anything
    monkeypatch.setenv("ZYNK_INITIAL_ADMIN_PASSWORD", "pw-two")
    restart()
    db = session_scope()
    try:
        assert bootstrap_admin(db) is None
    finally:
        db.close()
    assert verify_password("pw-one", _admin_hash())
    assert not verify_password("pw-two", _admin_hash())


def test_force_reset_rewrites_password(monkeypatch):
    monkeypatch.setenv("ZYNK_INITIAL_ADMIN_PASSWORD", "old-pw-123")
    restart()
    db = session_scope()
    bootstrap_admin(db)
    db.close()

    monkeypatch.setenv("ZYNK_FORCE_ADMIN_RESET", "true")
    monkeypatch.setenv("ZYNK_INITIAL_ADMIN_PASSWORD", "new-pw-456")
    restart()
    db = session_scope()
    try:
        assert bootstrap_admin(db) is None
    finally:
        db.close()

    db = session_scope()
    try:
        admins = db.query(User).filter(User.username == "admin").count()
        assert admins == 1  # reset, not duplicated
    finally:
        db.close()
    assert not verify_password("old-pw-123", _admin_hash())
    assert verify_password("new-pw-456", _admin_hash())


def test_force_reset_generates_password_when_none_given(monkeypatch):
    monkeypatch.setenv("ZYNK_INITIAL_ADMIN_PASSWORD", "initial-pw")
    restart()
    db = session_scope()
    bootstrap_admin(db)
    db.close()

    monkeypatch.delenv("ZYNK_INITIAL_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("ZYNK_FORCE_ADMIN_RESET", "true")
    restart()
    db = session_scope()
    try:
        generated = bootstrap_admin(db)
    finally:
        db.close()
    assert generated is not None
    assert not verify_password("initial-pw", _admin_hash())
    assert verify_password(generated, _admin_hash())


def test_force_reset_end_to_end_login(monkeypatch):
    """Boot the real app lifespan with the flag and log in with the new password."""
    monkeypatch.setenv("ZYNK_INITIAL_ADMIN_PASSWORD", "reset-e2e-pw")
    with TestClient(app) as client:
        pass  # first start creates admin

    # simulate a lost password: force reset with a new one
    monkeypatch.setenv("ZYNK_FORCE_ADMIN_RESET", "true")
    monkeypatch.setenv("ZYNK_INITIAL_ADMIN_PASSWORD", "e2e-new-pw")
    restart()
    with TestClient(app) as client:
        ok = client.post("/api/auth/token", data={"username": "admin", "password": "e2e-new-pw"})
        assert ok.status_code == 200
        bad = client.post("/api/auth/token", data={"username": "admin", "password": "reset-e2e-pw"})
        assert bad.status_code == 401
    restart()  # clean engine state for the next test


def test_manual_hash_still_valid(monkeypatch):
    restart()
    db = session_scope()
    try:
        db.add(User(username="manual", password_hash=hash_password("manual-pw")))
        db.commit()
        u = db.query(User).filter(User.username == "manual").one()
        assert verify_password("manual-pw", u.password_hash)
    finally:
        db.close()
