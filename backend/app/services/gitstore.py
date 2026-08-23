from __future__ import annotations

import subprocess

from app.core.config import get_settings


class GitStoreError(Exception):
    pass


def _git(*args: str, check: bool = True) -> str:
    repo = get_settings().config_repo_dir
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if check and proc.returncode != 0:
        raise GitStoreError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def ensure_repo() -> None:
    repo = get_settings().config_repo_dir
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        _git("init", "-q")
    _git("config", "user.name", "Zynk")
    _git("config", "user.email", "zynk@localhost")
    _git("config", "commit.gpgsign", "false")
    gitignore = repo / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n!.gitignore\n!*/\n!*/*.cfg\n")


def device_dir(device_id: str) -> str:
    return device_id


def commit_file(rel_path: str, message: str) -> str | None:
    """Stage and commit one file; returns the commit sha (None if nothing changed)."""
    ensure_repo()
    _git("add", "--", rel_path)
    staged = _git("status", "--porcelain", "--untracked-files=all")
    if not staged.strip():
        return None
    _git("commit", "-q", "-m", message, "--", rel_path)
    return _git("rev-parse", "HEAD").strip()


def read_file(rel_path: str) -> str:
    path = get_settings().config_repo_dir / rel_path
    return path.read_text(encoding="utf-8", errors="replace")


def read_file_at(commit: str, rel_path: str) -> str:
    return _git("show", f"{commit}:{rel_path}")
