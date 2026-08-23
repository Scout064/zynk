from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

REDACTED = "********"


@lru_cache
def _fernet() -> Fernet:
    path = get_settings().fernet_key_path
    if path.exists():
        key = path.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(key)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return Fernet(key)


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as err:
        raise ValueError("Stored credential could not be decrypted (key changed?)") from err
