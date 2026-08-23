from __future__ import annotations

import re
import time

import paramiko

# Terminal escape sequences some Zyxel firmwares emit around the prompt
# (e.g. ESC 7 / DECSC "save cursor" on XS1930 switches) or in output.
ANSI_ESCAPE_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"  # OSC sequences (window title etc.)
    r"|\x1b\[[^a-zA-Z]*[a-zA-Z]"  # CSI sequences (colors, cursor movement)
    r"|\x1b."  # 2-char escapes: ESC 7, ESC 8, ESC M, ...
)


def sanitize_terminal_output(text: str) -> str:
    """Remove ANSI escape sequences and CR characters from terminal output."""
    return ANSI_ESCAPE_RE.sub("", text).replace("\r", "")


class DriverError(Exception):
    """Base error for device interaction failures."""

    kind = "error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class UnreachableError(DriverError):
    kind = "unreachable"


class AuthError(DriverError):
    kind = "auth"


class TimeoutError_(DriverError):
    kind = "timeout"


class UnsupportedCommandError(DriverError):
    kind = "unsupported"


class OperationFailedError(DriverError):
    kind = "failed"


class ShellTransport:
    """Thin expect-like wrapper over a Paramiko interactive shell.

    Keeps a rolling receive buffer; callers wait for a prompt regex to appear
    at the end of the buffer. The PTY is created wide to avoid line wrapping.
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        connect_timeout: float = 15.0,
        banner_timeout: float = 30.0,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.connect_timeout = connect_timeout
        self.banner_timeout = banner_timeout
        self._client: paramiko.SSHClient | None = None
        self._channel: paramiko.Channel | None = None
        self._buffer = b""

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=self.connect_timeout,
                banner_timeout=self.banner_timeout,
                auth_timeout=self.connect_timeout,
                allow_agent=False,
                look_for_keys=False,
            )
        except paramiko.AuthenticationException as err:
            client.close()
            raise AuthError(
                f"Authentication failed for user '{self.username}' on {self.host}:{self.port}"
            ) from err
        except (paramiko.SSHException, OSError) as err:
            client.close()
            msg = str(err)
            if "timed out" in msg.lower() or "timeout" in msg.lower():
                raise TimeoutError_(f"Connection to {self.host}:{self.port} timed out") from err
            raise UnreachableError(f"Cannot reach {self.host}:{self.port} ({msg})") from err
        self._client = client
        try:
            self._channel = client.invoke_shell(term="vt100", width=511, height=511)
            self._channel.settimeout(1.0)
        except paramiko.SSHException as err:
            self.close()
            raise UnsupportedCommandError(
                f"{self.host} did not provide an interactive shell: {err}"
            ) from err

    def close(self) -> None:
        if self._channel is not None:
            try:
                self._channel.close()
            except OSError:
                pass
            self._channel = None
        if self._client is not None:
            try:
                self._client.close()
            except OSError:
                pass
            self._client = None

    @property
    def connected(self) -> bool:
        return self._client is not None

    # -- I/O ---------------------------------------------------------------

    def _recv_some(self) -> bytes:
        assert self._channel is not None
        try:
            data = self._channel.recv(4096)
        except TimeoutError:
            return b""
        except OSError as err:
            raise UnreachableError(f"Connection lost: {err}") from err
        if data == b"":
            raise UnreachableError("Connection closed by device")
        return data

    def send(self, text: str) -> None:
        assert self._channel is not None
        self._buffer = b""
        self._channel.sendall(text.encode("utf-8"))

    def sendline(self, line: str = "") -> None:
        self.send(line + "\n")

    def read_until(self, pattern: re.Pattern[str], timeout: float = 60.0) -> str:
        """Read until `pattern` matches; returns sanitized output."""
        deadline = time.monotonic() + timeout
        while True:
            text = sanitize_terminal_output(self._buffer.decode("utf-8", errors="replace"))
            if pattern.search(text):
                return text
            if time.monotonic() > deadline:
                raw = self._buffer.decode("utf-8", errors="replace")
                raise TimeoutError_(f"Timed out waiting for prompt; last output: {raw[-200:]!r}")
            self._buffer += self._recv_some()

    def drain(self, seconds: float = 1.0) -> str:
        """Consume any pending output for a short period."""
        deadline = time.monotonic() + seconds
        out = b""
        while time.monotonic() < deadline:
            try:
                out += self._recv_some()
            except TimeoutError_:
                break
        return sanitize_terminal_output(out.decode("utf-8", errors="replace"))
