from __future__ import annotations

import pytest

from app.devices.base import ConnectionSpec
from app.devices.factory import make_driver
from app.devices.transport import DriverError, ShellTransport, sanitize_terminal_output


class FakeTransport(ShellTransport):
    """Scriptable stand-in for a real SSH shell."""

    def __init__(self, responses: dict[str, str] | None = None, banner: str = "sysname# "):
        super().__init__("fake", 22, "u", "p")
        self.banner = banner
        self.responses = responses or {}
        self.sent: list[str] = []

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

    def test_apply_not_supported(self, spec):
        from app.devices.transport import OperationFailedError

        d = make_fake_driver("switch", {}, "sysname# ")
        with pytest.raises(OperationFailedError):
            d.apply_config("x")

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


class TestFactory:
    def test_unknown_family(self):
        with pytest.raises(DriverError):
            make_driver(ConnectionSpec("x", 22, "u", "p"), "router")

    def test_all_families(self):
        for fam in ("switch", "firewall", "ap"):
            d = make_driver(ConnectionSpec("x", 22, "u", "p"), fam)
            assert d.family == fam
