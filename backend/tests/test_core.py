from __future__ import annotations

import pytest

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.devices.transport import AuthError, ShellTransport, UnreachableError
from app.devices.zyxel_drivers import AP_PROMPT_RE


def test_password_hash_roundtrip():
    h = hash_password("s3cret-pass")
    assert h != "s3cret-pass"
    assert verify_password("s3cret-pass", h)
    assert not verify_password("wrong", h)


def test_jwt_roundtrip():
    token = create_access_token("admin", True)
    payload = decode_access_token(token)
    assert payload["sub"] == "admin"
    assert payload["admin"] is True


def test_jwt_invalid_token_rejected():
    import jwt

    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token("not-a-token")


def test_fernet_roundtrip():
    enc = encrypt_secret("device-password")
    assert enc != "device-password"
    assert decrypt_secret(enc) == "device-password"


def test_error_kinds():
    assert AuthError("x").kind == "auth"
    assert UnreachableError("x").kind == "unreachable"


def test_prompt_regexes():
    from app.devices.zyxel_drivers import (
        AP_PROMPT_RE,
        FIREWALL_PROMPT_RE,
        SWITCH_PROMPT_RE,
    )

    for prompt in ("sysname#", "sysname>", "sysname(config)#", "GS1350#"):
        assert SWITCH_PROMPT_RE.search(prompt + " "), prompt
    assert not SWITCH_PROMPT_RE.search("show running-config")

    for prompt in ("usgflex700h>", "usgflex500h running config#"):
        assert FIREWALL_PROMPT_RE.search(prompt), prompt

    for prompt in ("Router>", "Router#", "Router(config)#"):
        assert AP_PROMPT_RE.search(prompt + " "), prompt


def test_sanitize_strips_ansi_and_cr():
    from app.devices.transport import sanitize_terminal_output

    # Exact real-device artifact seen on XS1930-12HP: prompt + DECSC (ESC 7)
    assert sanitize_terminal_output("\r\nXS1930# \x1b7") == "\nXS1930# "
    # CSI color sequences
    assert sanitize_terminal_output("\x1b[32mOK\x1b[0m\r\n") == "OK\n"
    # Other 2-char escapes (ESC 8 / ESC M)
    assert sanitize_terminal_output("a\x1b8b\x1bMc") == "abc"


def test_transport_read_until_prompt():
    class FakeChannel:
        def __init__(self):
            self.sent = b""
            self.replies = [b"welcome banner\r\n"]
            self.closed = False

        def sendall(self, data: bytes) -> None:
            self.sent += data
            if b"show version" in data:
                self.replies.append(b"show version\r\nV7.40\r\nRouter# \x1b7")

        def recv(self, n: int) -> bytes:
            if self.replies:
                return self.replies.pop(0)
            raise TimeoutError

        def settimeout(self, t: float) -> None: ...

        def close(self) -> None:
            self.closed = True

    t = ShellTransport("127.0.0.1", 22, "u", "p")
    t._client = object()  # fake connected client
    t._channel = FakeChannel()
    t.sendline("show version")
    out = t.read_until(AP_PROMPT_RE, timeout=2)
    assert "V7.40" in out
    assert "\x1b" not in out
