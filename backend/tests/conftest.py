from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def temp_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Point the app's data dir at a temp dir and reset caches between tests."""
    data = tmp_path / "zynk-data"
    os.environ["ZYNK_DATA_DIR"] = str(data)

    import app.core.config as config_mod

    config_mod.get_settings.cache_clear()
    yield data
    config_mod.get_settings.cache_clear()

    import app.core.crypto as crypto_mod

    crypto_mod._fernet.cache_clear()

    import app.db.base as db_base

    db_base._engine = None
    db_base._session_factory = None
