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


def _connect_client(
    host: str,
    port: int,
    username: str,
    password: str,
    connect_timeout: float = 15.0,
) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        port=port,
        username=username,
        password=password,
        timeout=connect_timeout,
        banner_timeout=30.0,
        auth_timeout=connect_timeout,
        allow_agent=False,
        look_for_keys=False,
    )
    return client


def sftp_upload_dedicated(
    host: str,
    port: int,
    username: str,
    password: str,
    remote_path: str,
    data: bytes,
    connect_timeout: float = 15.0,
) -> None:
    """Upload via SFTP on a dedicated SSH connection (no shell channel).

    Some embedded SSH servers mis-handle multiple concurrent channels: opening
    an SFTP session while an interactive shell is active yields garbage packets.
    A fresh, shell-less connection avoids that entirely.
    """
    import io

    client = _connect_client(host, port, username, password, connect_timeout)
    try:
        sftp = client.open_sftp()
        try:
            sftp.putfo(io.BytesIO(data), remote_path)
        finally:
            sftp.close()
    finally:
        client.close()


def scp_upload_dedicated(
    host: str,
    port: int,
    username: str,
    password: str,
    remote_path: str,
    data: bytes,
    connect_timeout: float = 15.0,
) -> None:
    """Upload via SCP (OpenSSH 'scp -t' sink protocol) on a dedicated connection.

    Used when the SFTP subsystem is unavailable (some firmwares only support
    plain SCP over exec channels). Implements the minimal documented sink
    handshake: C-mode line, data, zero byte acknowledgements.
    """
    client = _connect_client(host, port, username, password, connect_timeout)
    try:
        chan = client.get_transport().open_session(timeout=connect_timeout)
        chan.exec_command(f"scp -t {remote_path.rsplit('/', 1)[0] or '/'}")
        # wait for the sink's initial OK (single NUL) — read the banner byte
        _scp_expect_ok(chan)
        header = f"C0644 {len(data)} {remote_path.rsplit('/', 1)[-1]}\n".encode()
        chan.sendall(header)
        _scp_expect_ok(chan)
        chan.sendall(data)
        chan.sendall(b"\x00")
        _scp_expect_ok(chan)
        chan.close()
    finally:
        client.close()


def _scp_expect_ok(chan) -> None:
    """Read one SCP protocol status byte; raise with stderr text on failure."""
    import time as _time

    deadline = _time.monotonic() + 15.0
    buf = b""
    while _time.monotonic() < deadline:
        if chan.recv_ready():
            buf += chan.recv(4096)
            if buf[:1] in (b"\x00", b"\x01", b"\x02"):
                if buf[0:1] == b"\x00":
                    return
                # 1/2 = warning/fatal: rest of buffer is the message
                msg = buf[1:].decode(errors="replace").strip()
                raise DriverError(f"SCP refused: {msg}")
        if chan.exit_status_ready() and not chan.recv_ready():
            break
        _time.sleep(0.05)
    stderr = b""
    if chan.recv_stderr_ready():
        stderr = chan.recv_stderr(4096)
    raise DriverError(
        "SCP handshake failed" + (f": {stderr.decode(errors='replace').strip()}" if stderr else "")
    )


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
