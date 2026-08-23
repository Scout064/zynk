from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine = None
_session_factory = None


def get_engine():
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            f"sqlite:///{settings.db_path}",
            connect_args={"check_same_thread": False},
        )
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def init_db() -> None:
    from app.db import models  # noqa: F401  (register mappings)

    models.Base.metadata.create_all(get_engine())


def session_scope() -> Session:
    get_engine()
    return _session_factory()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    db = session_scope()
    try:
        yield db
    finally:
        db.close()
