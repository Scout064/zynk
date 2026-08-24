from __future__ import annotations

import threading
import time

import pytest

from app.devices.base import ConnectionSpec
from app.devices.factory import make_driver
from app.devices.tftp import SingleFileTFTPServer
from app.devices.transport import (
    DriverError,
    OperationFailedError,
    ShellTransport,
    UnreachableError,
    sanitize_terminal_output,
)


class FakeTransport(ShellTransport):
    """Scriptable stand-in for a real SSH shell."""

    def __init__(self, responses: dict[str, str] | None = None, banner: str = "sysname# "):
        super().__init__("fake", 22, "u", "p")
        self.banner = banner
        self.responses = responses or {}
        self.sent: list[str] = []
        self.uploads: list[tuple[str, bytes]] = []

    def connect(self) -> None:
        self._client = object()
        self._channel = object()

    def drain(self, seconds: float = 1.0) -> str:
        return sanitize_terminal_output(self.banner)

    def sendline(self, line: str = "") -> None:
        self.sent.append(line)

    def read_until(self, pattern, timeout: float = 60.0) -> str:
        cmd = self.sent[-1] if self.sent else ""
        body = self.responses.get(cmd, "")
        tail = self.banner.strip().splitlines()[-1] if self.banner.strip() else "#"
        return sanitize_terminal_output(f"{cmd}\r\n{body}{tail} ")

    def close(self) -> None:
        self._client = None
        self._channel = None


@pytest.fixture
def spec() -> ConnectionSpec:
    return ConnectionSpec(host="fake", port=22, username="admin", password="pw")


SWITCH_CONFIG = """vlan 1
 name default
exit
interface port-channel 1
 speed-duplex auto
exit
"""

FIREWALL_CONFIG = """vrf main
    system
        name usg
    ..
..
"""

AP_CONFIG = """interface ge1
 ip dhcp
exit
"""


def make_fake_driver(family: str, responses: dict[str, str], banner: str):
    driver = make_driver(ConnectionSpec("fake", 22, "admin", "pw"), family)

    def _fake_transport(self):
        return FakeTransport(responses, banner)

    driver._make_transport = _fake_transport.__get__(driver, type(driver))
    return driver


class TestSwitchDriver:
    def test_get_config(self, spec):
        d = make_fake_driver(
            "switch",
            {"show running-config": SWITCH_CONFIG, "show version": "V4.90"},
            "sysname# ",
        )
        d.connect()
        assert d.base_prompt.strip() == "sysname#"
        cfg = d.get_config()
        assert "vlan 1" in cfg
        assert "show running-config" not in cfg
        assert "sysname#" not in cfg
        d.close()

    def test_prompt_with_ansi_escapes(self, spec):
        """Regression: XS1930-12HP emits `prompt# \\x1b7` (DECSC) after login."""
        d = make_fake_driver(
            "switch",
            {"show running-config": SWITCH_CONFIG, "show version": "V4.90"},
            "sysname# \x1b7",
        )
        d.connect()
        assert d.base_prompt.strip() == "sysname#"
        cfg = d.get_config()
        assert "vlan 1" in cfg
        assert "\x1b" not in cfg
        d.close()

    def test_apply_config_not_implemented_raises(self, spec):
        """Base class default still raises for families without revert."""
        from app.devices.base import ZyxelDriver

        class Bare(ZyxelDriver):
            family = "bare"

        with pytest.raises(OperationFailedError):
            Bare(spec).apply_config("x")

    def test_revert_diagnoses_model_from_snapshot(self, monkeypatch):
        """Revert parses the model from the restored config's own header."""
        monkeypatch.setattr("app.devices.zyxel_drivers.random_filename", lambda: "fixed.cfg")

        class CopyFailFake(FakeTransport):
            def read_until(self, pattern, timeout: float = 60.0):
                cmd = self.sent[-1] if self.sent else ""
                if cmd.startswith("copy tftp config"):
                    return f'{cmd}\r\n%Invalid command "copy"\r\nsysname# '
                if cmd == "?":
                    return "?\r\n  import\r\n  reload\r\n  show\r\nsysname# "
                return super().read_until(pattern, timeout)

        spec = ConnectionSpec(
            host="127.0.0.1",
            port=22,
            username="admin",
            password="pw",
            tftp_port=0,
        )
        d = make_driver(spec, "switch")
        d._make_transport = lambda: CopyFailFake()
        d.connect()
        assert d.detected_model is None  # no pull happened this session
        snapshot = "  Current configuration:\n\n; Product Name = XS1935-10\nvlan 1\n"
        with pytest.raises(OperationFailedError) as exc:
            d.apply_config(snapshot)
        msg = str(exc.value)
        # dynamic model from the snapshot header, not a hardcoded one
        assert "XS1935-10" in msg
        assert "XS1930" not in msg


class TestSwitchRestore:
    """Integration: driver + real TFTP server + scripted SSH reload flow."""

    TFTP_TEST_PORT = 6969  # unprivileged fixed port; driver + test client agree on it

    class RestoreFake(FakeTransport):
        def __init__(self):
            super().__init__({}, "sysname# ")

        def read_until(self, pattern, timeout: float = 60.0):
            cmd = self.sent[-1] if self.sent else ""
            if cmd.startswith("copy tftp config"):
                return f"{cmd}\r\n\r\nTransfer success.\r\nsysname# "
            if cmd == "reload config 1":
                return (
                    "reload config 1\r\nDo you really want to reboot system with "
                    "configuration file 1? [y/N]"
                )
            if cmd == "y":
                raise UnreachableError("Connection closed by device")
            return super().read_until(pattern, timeout)

    def _fetch_thread(self, filename: str, received: list):
        """TFTP client with retry until the driver's server is listening."""

        def run():
            from tests.test_tftp import tftp_get

            for _ in range(50):
                try:
                    received.append(
                        tftp_get("127.0.0.1", TestSwitchRestore.TFTP_TEST_PORT, filename)
                    )
                    return
                except (RuntimeError, OSError, TimeoutError):
                    time.sleep(0.1)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t

    def test_full_restore_flow(self, monkeypatch):
        import socket as sk

        monkeypatch.setattr(
            "app.devices.zyxel_drivers.random_filename",
            lambda: "fixed_restore.cfg",
        )

        # SSH listener so the post-reboot probe finds the "switch back up"
        listener = sk.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        ssh_probe_port = listener.getsockname()[1]

        received: list[bytes] = []
        self._fetch_thread("fixed_restore.cfg", received)

        spec = ConnectionSpec(
            host="127.0.0.1",
            port=ssh_probe_port,
            username="admin",
            password="pw",
            tftp_port=self.TFTP_TEST_PORT,
            reboot_timeout=10.0,
            reboot_settle=0.0,
        )
        d = make_driver(spec, "switch")
        d._make_transport = lambda: self.RestoreFake()
        d.connect()

        result = d.apply_config(SWITCH_CONFIG)
        listener.close()

        assert "Transfer success." in result
        assert received and received[0].decode() == SWITCH_CONFIG  # TFTP payload intact
        # exact CLI sequence issued
        assert "copy tftp config 1 127.0.0.1 fixed_restore.cfg" in d.transport.sent
        assert "reload config 1" in d.transport.sent
        assert "y" in d.transport.sent

    def test_copy_error_surfaces(self, monkeypatch):
        class CopyFailFake(self.RestoreFake):
            def read_until(self, pattern, timeout: float = 60.0):
                cmd = self.sent[-1] if self.sent else ""
                if cmd.startswith("copy tftp config"):
                    return f"{cmd}\r\nTFTP: error code 1 (file not found)\r\nsysname# "
                return super().read_until(pattern, timeout)

        spec = ConnectionSpec(
            host="127.0.0.1",
            port=22,
            username="admin",
            password="pw",
            tftp_port=self.TFTP_TEST_PORT,
        )
        d = make_driver(spec, "switch")
        d._make_transport = lambda: CopyFailFake()
        d.connect()
        with pytest.raises(OperationFailedError, match="rejected the TFTP restore"):
            d.apply_config(SWITCH_CONFIG)

    def test_no_request_error_includes_cli_output(self, monkeypatch):
        """The 'no TFTP request' error must include what the switch CLI said."""

        class SilentFailFake(FakeTransport):
            def read_until(self, pattern, timeout: float = 60.0):
                cmd = self.sent[-1] if self.sent else ""
                if cmd.startswith("copy tftp config"):
                    # benign-looking output that matches no error keyword
                    return f"{cmd}\r\nConfiguration file transfer initiated.\r\nsysname# "
                return super().read_until(pattern, timeout)

        monkeypatch.setattr("app.devices.zyxel_drivers.random_filename", lambda: "tiny.cfg")
        # force a short idle timeout so the test fails fast
        real_server = SingleFileTFTPServer

        def fast_server(data, filename, **kw):
            return real_server(data, filename, idle_timeout=0.5, **kw)

        monkeypatch.setattr("app.devices.zyxel_drivers.SingleFileTFTPServer", fast_server)
        spec = ConnectionSpec(
            host="127.0.0.1",
            port=22,
            username="admin",
            password="pw",
            tftp_port=0,  # ephemeral port nothing will reach -> no request
        )
        d = make_driver(spec, "switch")
        d._make_transport = lambda: SilentFailFake()
        d.connect()
        with pytest.raises(OperationFailedError) as exc:
            d.apply_config(SWITCH_CONFIG)
        msg = str(exc.value)
        assert "no TFTP request arrived" in msg
        # the CLI output we previously discarded must now be visible
        assert "Configuration file transfer initiated." in msg

    def test_can_not_phrasing_is_detected(self, monkeypatch):
        """Zyxel's two-word 'Can not' failure phrasing must be caught."""

        class CanNotFake(FakeTransport):
            def read_until(self, pattern, timeout: float = 60.0):
                cmd = self.sent[-1] if self.sent else ""
                if cmd.startswith("copy tftp config"):
                    return f"{cmd}\r\nCan not open TFTP connection\r\nsysname# "
                return super().read_until(pattern, timeout)

        monkeypatch.setattr("app.devices.zyxel_drivers.random_filename", lambda: "tiny.cfg")
        spec = ConnectionSpec(
            host="127.0.0.1",
            port=22,
            username="admin",
            password="pw",
            tftp_port=0,
        )
        d = make_driver(spec, "switch")
        d._make_transport = lambda: CanNotFake()
        d.connect()
        with pytest.raises(OperationFailedError, match="rejected the TFTP restore"):
            d.apply_config(SWITCH_CONFIG)

    def test_tftp_path_test_success(self, monkeypatch):
        """test_tftp_path: switch pushes config to our WRQ receiver."""
        from app.devices.tftp import TFTPReceiveServer

        class PushFake(FakeTransport):
            def read_until(self, pattern, timeout: float = 60.0):
                cmd = self.sent[-1] if self.sent else ""
                if cmd.startswith("copy running-config tftp"):
                    # simulate: switch uploads, then prints success
                    return f"{cmd}\r\nTransfer success.\r\nsysname# "
                return super().read_until(pattern, timeout)

        def fake_receiver(filename: str, **kwargs):
            class R(TFTPReceiveServer):
                def __init__(self, fn, **kw):
                    super().__init__(fn, **kw)
                    self._received = bytearray(b"pushed-config-bytes")

                def wait(self, timeout: float = 60.0):
                    return True

            return R(filename, **kwargs)

        monkeypatch.setattr("app.devices.zyxel_drivers.TFTPReceiveServer", fake_receiver)
        spec = ConnectionSpec(
            host="127.0.0.1",
            port=22,
            username="admin",
            password="pw",
            tftp_port=self.TFTP_TEST_PORT,
        )
        d = make_driver(spec, "switch")
        d._make_transport = lambda: PushFake()
        d.connect()

        ok, msg = d.test_tftp_path()
        assert ok, msg
        assert "TFTP path OK" in msg
        assert any(s.startswith("copy running-config tftp 127.0.0.1") for s in d.transport.sent)

    def test_tftp_path_test_refused(self, monkeypatch):
        class RefuseFake(FakeTransport):
            def read_until(self, pattern, timeout: float = 60.0):
                cmd = self.sent[-1] if self.sent else ""
                if cmd.startswith("copy running-config tftp"):
                    return f'{cmd}\r\n%Invalid command "copy"\r\nsysname# '
                if cmd == "show running-config":
                    return (
                        f"{cmd}\r\n  Building configuration...\r\n"
                        f"; Product Name = XS1930-12HP\r\n"
                        f"; Firmware Version = V4.80(ABQF.4)\r\n"
                        f"; Service Status = Not Licensed\r\n"
                        f"vlan 1\r\nsysname# "
                    )
                if cmd == "?":
                    # exact basic-CLI command set observed on a real
                    # unlicensed XS1930-12HP (V4.80): no `copy`, has `import`
                    return (
                        "?\r\n  Commands available:\r\n\r\n  boot\r\n"
                        "  cable-diagnostics\r\n  clear\r\n  disable\r\n  erase\r\n"
                        "  exit\r\n  igmp-flush\r\n  import\r\n  locator-led\r\n"
                        "  logout\r\n  mac-flush\r\n  no\r\n  ping\r\n  ping6\r\n"
                        "  release\r\n  reload\r\n  renew\r\n  reset\r\n"
                        "  restart\r\n  service-register\r\n  show\r\n"
                        "  traceroute\r\n  traceroute6\r\nsysname# "
                    )
                return super().read_until(pattern, timeout)

        spec = ConnectionSpec(
            host="127.0.0.1",
            port=22,
            username="admin",
            password="pw",
            tftp_port=self.TFTP_TEST_PORT,
        )
        d = make_driver(spec, "switch")
        d._make_transport = lambda: RefuseFake()
        d.connect()
        ok, msg = d.test_tftp_path()
        assert not ok
        assert "switch refused" in msg
        assert "%Invalid command" in msg
        # model-aware license explanation from the support matrix
        assert "restricted basic CLI" in msg
        assert "XS1930" in msg
        assert "XS1930-12HP" in msg
        assert "license" in msg
        # stale series-specific text from the old message is gone
        assert "Access L3 license" not in msg

    def test_tftp_path_test_doubled_prompt_probe(self, monkeypatch):
        """Regression: XS1930 prints the prompt TWICE after a '?' listing.

        The doubled prompt (`XS1930# XS1930# `) can make the prompt-wait time
        out; the probe must still diagnose from the listing text it received.
        """
        from app.devices.transport import TimeoutError_

        class DoubledPromptFake(FakeTransport):
            def read_until(self, pattern, timeout: float = 60.0):
                cmd = self.sent[-1] if self.sent else ""
                if cmd.startswith("copy running-config tftp"):
                    return f'{cmd}\r\n%Invalid command "copy"\r\nsysname# '
                if cmd == "show running-config":
                    return (
                        f"{cmd}\r\n; Product Name = XS1930-12HP\r\n"
                        f"; Service Status = Not Licensed\r\nvlan 1\r\nsysname# "
                    )
                if cmd == "?":
                    # exact behavior observed on the real unlicensed
                    # XS1930-12HP: listing, then the prompt printed twice
                    # (DECSC escapes stripped by the transport layer)
                    raise TimeoutError_(
                        "Timed out waiting for prompt; last output: "
                        "' Service register\\r\\n show Show system information\\r\\n"
                        " traceroute Exec traceroute\\r\\n traceroute6 Exec IPv6 "
                        "traceroute\\r\\nXS1930# XS1930# '"
                    )
                return super().read_until(pattern, timeout)

        spec = ConnectionSpec(
            host="127.0.0.1",
            port=22,
            username="admin",
            password="pw",
            tftp_port=self.TFTP_TEST_PORT,
        )
        d = make_driver(spec, "switch")
        d._make_transport = lambda: DoubledPromptFake()
        d.connect()
        ok, msg = d.test_tftp_path()
        assert not ok
        assert "switch refused" in msg
        # diagnosis extracted from the timed-out probe's partial output,
        # model-aware via the config header
        assert "restricted basic CLI" in msg
        assert "XS1930-12HP" in msg
        assert "license" in msg

    def test_tftp_path_test_partial_copy_support(self, monkeypatch):
        """Device has some `copy` subcommands but rejects the TFTP one."""

        class PartialCopyFake(FakeTransport):
            def read_until(self, pattern, timeout: float = 60.0):
                cmd = self.sent[-1] if self.sent else ""
                if cmd.startswith("copy running-config tftp"):
                    return f"{cmd}\r\nInvalid input detected\r\nsysname# "
                if cmd == "?":
                    return (
                        "?\r\n  copy running-config custom-default\r\n"
                        "  backup config tftp\r\n  restore config tftp\r\n"
                        "  show running-config\r\nsysname# "
                    )
                return super().read_until(pattern, timeout)

        spec = ConnectionSpec(
            host="127.0.0.1",
            port=22,
            username="admin",
            password="pw",
            tftp_port=self.TFTP_TEST_PORT,
        )
        d = make_driver(spec, "switch")
        d._make_transport = lambda: PartialCopyFake()
        d.connect()
        ok, msg = d.test_tftp_path()
        assert not ok
        assert "backup config tftp" in msg  # keyword listing, not license text
        assert "Access L3 license" not in msg


class TestFirewallDriver:
    def test_get_config_disables_pager(self):
        d = make_fake_driver(
            "firewall",
            {
                "cliconfig pager enabled false": "",
                "show config running | no-pager": FIREWALL_CONFIG,
            },
            "usg700h> ",
        )
        d.connect()
        cfg = d.get_config()
        assert "vrf main" in cfg
        assert "no-pager" not in cfg
        d.close()


class TestFirewallRestore:
    """uOS firewall restore: SFTP upload + dry-run + cmd config-apply (no reboot)."""

    APPLY_OK = "configuration-apply\r\n    ok\r\n        message OK\r\n        ..\r\n    ..\r\n"

    class FirewallFake(FakeTransport):
        def __init__(self):
            super().__init__({}, "usgflex700h> ")
            self.mode_prompts = {
                "": "usgflex700h> ",
                "edit running": "usgflex700h running config# ",
            }

        def read_until(self, pattern, timeout: float = 60.0):
            cmd = self.sent[-1] if self.sent else ""
            body = self.responses.get(cmd, "")
            # prompt reflects the current mode
            prompt = (
                "usgflex700h running config# "
                if any(s in ("edit running",) or s.startswith("cmd ") for s in self.sent)
                else "usgflex700h> "
            )
            if cmd == "edit running":
                return f"{cmd}\r\n{prompt}"
            return sanitize_terminal_output(f"{cmd}\r\n{body}{prompt}")

    def test_full_restore_flow(self, monkeypatch):
        monkeypatch.setattr("app.devices.zyxel_drivers.os.urandom", lambda n: b"\xab\xcd\xef")
        uploaded: list[tuple[str, str, bytes]] = []

        def fake_sftp(host, port, user, pw, remote, data, **kw):
            uploaded.append(("sftp", remote, data))

        monkeypatch.setattr("app.devices.zyxel_drivers.sftp_upload_dedicated", fake_sftp)
        d = make_fake_driver("firewall", {}, "usgflex700h> ")
        fake = self.FirewallFake()
        fake.responses = {
            "cmd config-apply option dry-run zynkabcdef.conf": self.APPLY_OK,
            "cmd config-apply zynkabcdef.conf": self.APPLY_OK,
        }
        d._make_transport = lambda: fake
        d.connect()

        out = d.apply_config(FIREWALL_CONFIG)

        assert "message OK" in out
        # upload went to /conf/ with a compliant filename via SFTP
        assert uploaded == [("sftp", "/conf/zynkabcdef.conf", FIREWALL_CONFIG.encode())]
        # command sequence: enter mode, dry-run first, then apply
        assert "edit running" in fake.sent
        dry_idx = fake.sent.index("cmd config-apply option dry-run zynkabcdef.conf")
        apply_idx = fake.sent.index("cmd config-apply zynkabcdef.conf")
        assert dry_idx < apply_idx
        assert "reload" not in " ".join(fake.sent)  # no reboot involved
        d.close()

    def test_sftp_garbage_falls_back_to_scp(self, monkeypatch):
        """Real-device regression: 'Garbage packet received' on SFTP -> SCP."""
        monkeypatch.setattr("app.devices.zyxel_drivers.os.urandom", lambda n: b"\xab\xcd\xef")
        from paramiko.ssh_exception import SSHException

        def garbage(*a, **kw):
            raise SSHException("Garbage packet received")

        def fake_scp(host, port, user, pw, remote, data, **kw):
            return ("scp", remote, data)

        monkeypatch.setattr("app.devices.zyxel_drivers.sftp_upload_dedicated", garbage)
        monkeypatch.setattr(
            "app.devices.zyxel_drivers.scp_upload_dedicated", fake_scp
        )
        d = make_fake_driver("firewall", {}, "usgflex700h> ")
        fake = self.FirewallFake()
        fake.responses = {
            "cmd config-apply option dry-run zynkabcdef.conf": self.APPLY_OK,
            "cmd config-apply zynkabcdef.conf": self.APPLY_OK,
        }
        d._make_transport = lambda: fake
        d.connect()
        out = d.apply_config(FIREWALL_CONFIG)
        assert "message OK" in out  # restore completed via SCP
        d.close()

    def test_sftp_and_scp_fail_fall_back_to_ftp(self, monkeypatch):
        monkeypatch.setattr("app.devices.zyxel_drivers.os.urandom", lambda n: b"\xab\xcd\xef")

        def fail(*a, **kw):
            raise DriverError("nope")

        monkeypatch.setattr("app.devices.zyxel_drivers.sftp_upload_dedicated", fail)
        monkeypatch.setattr("app.devices.zyxel_drivers.scp_upload_dedicated", fail)

        import ftplib

        stored: dict[str, bytes] = {}

        class FakeFTP:
            def __init__(self, timeout=None): ...
            def connect(self, host, port): ...
            def login(self, u, p): ...
            def set_pasv(self, v): ...
            def storbinary(self, cmd, data):
                stored[cmd] = data.read()

            def quit(self): ...

        monkeypatch.setattr(ftplib, "FTP", FakeFTP)
        d = make_fake_driver("firewall", {}, "usgflex700h> ")
        fake = self.FirewallFake()
        fake.responses = {
            "cmd config-apply option dry-run zynkabcdef.conf": self.APPLY_OK,
            "cmd config-apply zynkabcdef.conf": self.APPLY_OK,
        }
        d._make_transport = lambda: fake
        d.connect()
        out = d.apply_config(FIREWALL_CONFIG)
        assert "message OK" in out  # restore completed via FTP
        assert stored["STOR /conf/zynkabcdef.conf"] == FIREWALL_CONFIG.encode()
        d.close()

    def test_all_transfers_fail_gives_guidance(self, monkeypatch):
        monkeypatch.setattr("app.devices.zyxel_drivers.os.urandom", lambda n: b"\xab\xcd\xef")

        def fail(*a, **kw):
            raise DriverError("Garbage packet received")

        monkeypatch.setattr("app.devices.zyxel_drivers.sftp_upload_dedicated", fail)
        monkeypatch.setattr("app.devices.zyxel_drivers.scp_upload_dedicated", fail)

        import ftplib

        class RefusedFTP:
            def __init__(self, timeout=None): ...
            def connect(self, host, port):
                raise ConnectionRefusedError("refused")

        monkeypatch.setattr(ftplib, "FTP", RefusedFTP)
        d = make_fake_driver("firewall", {}, "usgflex700h> ")
        fake = self.FirewallFake()
        d._make_transport = lambda: fake
        d.connect()
        with pytest.raises(OperationFailedError) as exc:
            d.apply_config(FIREWALL_CONFIG)
        msg = str(exc.value)
        assert "all transfer methods failed" in msg
        assert "Garbage packet received" in msg  # original error included
        # actionable guidance with the exact device commands
        assert "vrf main ftp-server enabled true" in msg
        assert "commit" in msg
        # the destructive apply must never have been sent
        assert not any(s.startswith("cmd config-apply") for s in fake.sent)

    def test_dry_run_failure_blocks_apply(self, monkeypatch):
        monkeypatch.setattr("app.devices.zyxel_drivers.os.urandom", lambda n: b"\xab\xcd\xef")
        monkeypatch.setattr(
            "app.devices.zyxel_drivers.sftp_upload_dedicated",
            lambda *a, **kw: None,
        )
        d = make_fake_driver("firewall", {}, "usgflex700h> ")
        fake = self.FirewallFake()
        fake.responses = {
            "cmd config-apply option dry-run zynkabcdef.conf": (
                "configuration-apply\r\n    error\r\n        message syntax error line 42\r\n"
            ),
        }
        d._make_transport = lambda: fake
        d.connect()

        with pytest.raises(OperationFailedError, match="dry-run.*syntax error"):
            d.apply_config(FIREWALL_CONFIG)
        # the destructive apply must never have been sent
        assert not any(s == "cmd config-apply zynkabcdef.conf" for s in fake.sent)

    def test_apply_failure_surfaces(self, monkeypatch):
        monkeypatch.setattr("app.devices.zyxel_drivers.os.urandom", lambda n: b"\xab\xcd\xef")
        monkeypatch.setattr(
            "app.devices.zyxel_drivers.sftp_upload_dedicated",
            lambda *a, **kw: None,
        )
        d = make_fake_driver("firewall", {}, "usgflex700h> ")
        fake = self.FirewallFake()
        fake.responses = {
            "cmd config-apply option dry-run zynkabcdef.conf": self.APPLY_OK,
            "cmd config-apply zynkabcdef.conf": (
                "configuration-apply\r\n    error\r\n        message file not found\r\n"
            ),
        }
        d._make_transport = lambda: fake
        d.connect()

        with pytest.raises(OperationFailedError, match="config-apply failed.*file not found"):
            d.apply_config(FIREWALL_CONFIG)


class TestZLDFirewallDriver:
    def test_get_config(self):
        d = make_fake_driver(
            "zld_firewall",
            {"show running-config": FIREWALL_CONFIG, "show version": "V4.32"},
            "Router# ",
        )
        d.connect()
        cfg = d.get_config()
        assert "vrf main" in cfg
        assert "show running-config" not in cfg
        d.close()

    def test_get_config_enters_enable(self):
        class EnablingFake(FakeTransport):
            def read_until(self, pattern, timeout: float = 60.0):
                cmd = self.sent[-1] if self.sent else ""
                if cmd == "enable":
                    return "enable\r\nRouter# "
                return super().read_until(pattern, timeout)

        def _ft(self):
            return EnablingFake({"show running-config": FIREWALL_CONFIG}, "Router> ")

        d = make_fake_driver("zld_firewall", {"show running-config": FIREWALL_CONFIG}, "Router> ")
        d._make_transport = _ft.__get__(d, type(d))
        d.connect()
        cfg = d.get_config()
        assert "vrf main" in cfg
        assert d.base_prompt.strip().endswith("#")

    def test_apply_config_ftp_and_apply(self, monkeypatch):
        import ftplib

        d = make_fake_driver("zld_firewall", {}, "Router# ")
        d.connect()
        sent: list[str] = []
        d.run = lambda cmd, timeout=None: (sent.append(cmd), "")[1]

        class FakeFTP:
            def __init__(self, timeout=None): ...
            def connect(self, host, port): ...
            def login(self, u, p): ...
            def set_pasv(self, v): ...
            def storbinary(self, cmd, data):
                sent.append(cmd)

            def quit(self): ...

        monkeypatch.setattr(ftplib, "FTP", FakeFTP)
        out = d.apply_config("vlan 1\n")
        assert any("STOR /conf/zynk_restore_" in c for c in sent)
        assert any(
            c.startswith("apply /conf/zynk_restore_") and "ignore-error rollback" in c for c in sent
        )
        assert "write" in sent
        assert out == ""


class TestAPDriver:
    def test_get_config(self):
        d = make_fake_driver(
            "ap",
            {"show running-config": AP_CONFIG, "show version": "V7.40"},
            "Router# ",
        )
        d.connect()
        cfg = d.get_config()
        assert "interface ge1" in cfg
        d.close()

    def test_enable_transition(self):
        class EnablingFake(FakeTransport):
            def read_until(self, pattern, timeout: float = 60.0):
                cmd = self.sent[-1] if self.sent else ""
                if cmd == "enable":
                    return "enable\r\nRouter# "
                return super().read_until(pattern, timeout)

        def _ft(self):
            return EnablingFake({"show running-config": AP_CONFIG}, "Router> ")

        d = make_fake_driver("ap", {"show running-config": AP_CONFIG}, "Router> ")
        d._make_transport = _ft.__get__(d, type(d))
        d.connect()
        cfg = d.get_config()
        assert d.base_prompt.strip().endswith("#")
        assert "interface ge1" in cfg


class TestSwitchSupportMatrix:
    def test_series_extraction(self):
        from app.devices.switch_support import series_from_model

        assert series_from_model("XS1930-12HP") == "XS1930"
        assert series_from_model("XS1930") == "XS1930"
        assert series_from_model("CX4800-56F") == "CX4800"
        assert series_from_model("XMG1930-30HP") == "XMG1930"  # longest match
        assert series_from_model("GS1350-12HP") == "GS1350"
        assert series_from_model("") is None
        assert series_from_model("UnknownModel") is None

    def test_license_message_restricted_with_license(self):
        from app.devices.switch_support import license_message

        msg = license_message("XS1930-12HP")
        assert msg and "XS1930" in msg and "XS1930-12HP" in msg
        assert "license" in msg and "myzyxel.com" in msg

    def test_license_message_is_model_dynamic(self):
        """The message must reflect the actually-detected model."""
        from app.devices.switch_support import license_message

        for model, series in [
            ("XS1930-12HP", "XS1930"),
            ("XS1935-10", "XS1935"),
            ("XMG1930-30HP", "XMG1930"),
        ]:
            msg = license_message(model)
            assert msg, model
            assert f"the model {model}" in msg, model
            assert msg.startswith(f"The {series} series"), model

        # EOL series gets the EOL note
        assert "End of Life" in license_message("XMG1930-30HP")
        assert "End of Life" not in license_message("XS1930-12HP")

    def test_license_message_restricted_no_license(self):
        from app.devices.switch_support import license_message

        msg = license_message("GS1900-24")
        assert msg and "GS1900" in msg
        assert "no license can unlock" in msg

    def test_full_cli_models_have_no_message(self):
        from app.devices.switch_support import license_message

        assert license_message("GS1350-12HP") is None
        assert license_message("CX4800-56F") is None
        assert license_message("GS2220-28") is None
        assert license_message("") is None
        assert license_message("Unknown-Model") is None


class TestFactory:
    def test_unknown_family(self):
        with pytest.raises(DriverError):
            make_driver(ConnectionSpec("x", 22, "u", "p"), "router")

    def test_all_families(self):
        for fam in ("switch", "firewall", "zld_firewall", "ap"):
            d = make_driver(ConnectionSpec("x", 22, "u", "p"), fam)
            assert d.family == fam
