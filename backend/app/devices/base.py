from __future__ import annotations

import re
from dataclasses import dataclass

from app.devices.transport import (
    AuthError,
    DriverError,
    OperationFailedError,
    ShellTransport,
)


@dataclass
class ConnectionSpec:
    host: str
    port: int
    username: str
    password: str
    connect_timeout: float = 15.0
    command_timeout: float = 120.0
    # Switch revert (TFTP): how the device reaches us and how long a reboot may take.
    tftp_address: str | None = None  # None = auto-detect the source IP toward the device
    tftp_port: int = 69
    reboot_timeout: float = 300.0
    reboot_settle: float = 10.0  # grace period after SSH answers again


class ZyxelDriver:
    """Common interface for Zyxel device families.

    Subclasses define prompt patterns, pagination disabling and the
    family-specific config-pull / apply commands.
    """

    family: str = "generic"
    prompt_re: re.Pattern[str] = re.compile(r"[>#]\s*$")
    pager_strips: tuple[str, ...] = ()

    def __init__(self, spec: ConnectionSpec):
        self.spec = spec
        self.transport: ShellTransport | None = None
        self.base_prompt: str = ""
        self.detected_model: str | None = None  # from config header, if parsed
        self.pull_source: str = ""  # how the last get_config ran (e.g. ftp/cli)

    # -- helpers for tests ---------------------------------------------------
    def _make_transport(self) -> ShellTransport:
        return ShellTransport(
            host=self.spec.host,
            port=self.spec.port,
            username=self.spec.username,
            password=self.spec.password,
            connect_timeout=self.spec.connect_timeout,
        )

    # -- lifecycle -----------------------------------------------------------

    def connect(self) -> None:
        self.transport = self._make_transport()
        self.transport.connect()
        banner = self.transport.drain(2.0)
        self.base_prompt = self._detect_prompt(banner)
        self._disable_pager()

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    # -- primitives ----------------------------------------------------------

    def _detect_prompt(self, banner: str) -> str:
        """Nudge with a newline if needed and return the current prompt line."""
        text = banner
        if not self.prompt_re.search(text.splitlines()[-1] if text.splitlines() else ""):
            self.transport.sendline()
            text += self.transport.read_until(self.prompt_re, timeout=10)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return lines[-1] if lines else ""

    def _disable_pager(self) -> None:
        """Optional hook: disable CLI paging for the session."""

    def run(self, command: str, timeout: float | None = None) -> str:
        """Run a command and return cleaned output (echo + prompt removed)."""
        if self.transport is None:
            raise DriverError("Not connected")
        self.transport.sendline(command)
        raw = self.transport.read_until(
            self.prompt_re, timeout=timeout or self.spec.command_timeout
        )
        return self._clean_output(raw, command)

    def _clean_output(self, raw: str, command: str) -> str:
        lines = raw.splitlines()
        # Drop echoed command (first line(s)) — match loosely, ignoring leading
        # whitespace and possible prompt prefix on the same line.
        while lines and command.split()[0] not in lines[0]:
            lines.pop(0)
        if lines:
            lines.pop(0)  # the echo line itself
        # Drop trailing prompt line(s)
        while lines and (not lines[-1].strip() or self.prompt_re.search(lines[-1])):
            lines.pop()
        cleaned = []
        for ln in lines:
            if any(marker in ln for marker in self.pager_strips):
                continue
            cleaned.append(ln.rstrip())
        return "\n".join(cleaned).strip("\n") + ("\n" if cleaned else "")

    # -- interface -----------------------------------------------------------

    def get_config(self) -> str:
        raise NotImplementedError

    def check_alive(self) -> bool:
        raise NotImplementedError

    def apply_config(self, config_text: str) -> str:
        raise OperationFailedError(
            f"Config revert is not supported for family '{self.family}' yet (alpha)"
        )

    # -- error mapping ---------------------------------------------------------

    @staticmethod
    def raise_for_auth(username: str, host: str) -> None:  # pragma: no cover
        raise AuthError(f"Authentication failed for '{username}' on {host}")
