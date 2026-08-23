from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


def _read_or_create_secret(path: Path) -> str:
    """Load a secret from disk, creating a random one on first run."""
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(48)
    path.write_text(value + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ZYNK_", env_file=".env", extra="ignore")

    app_name: str = "Zynk"
    data_dir: Path = REPO_ROOT / "data"
    host: str = "0.0.0.0"
    port: int = 8000
    access_token_ttl_minutes: int = 720
    status_poll_interval_seconds: int = 300
    ssh_connect_timeout_seconds: int = 15
    ssh_command_timeout_seconds: int = 120
    device_status_history: int = 500
    initial_admin_password: str | None = None
    force_admin_reset: bool = False

    @property
    def db_path(self) -> Path:
        return self.data_dir / "zynk.db"

    @property
    def config_repo_dir(self) -> Path:
        return self.data_dir / "configs"

    @property
    def secret_key_path(self) -> Path:
        return self.data_dir / "secret.key"

    @property
    def jwt_secret(self) -> str:
        return _read_or_create_secret(self.secret_key_path)

    @property
    def fernet_key_path(self) -> Path:
        return self.data_dir / "fernet.key"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_repo_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
