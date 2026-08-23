from __future__ import annotations

import ftplib
import io
import os
import re

from app.devices.base import ZyxelDriver
from app.devices.transport import DriverError, OperationFailedError

# New-generation Ethernet switch CLI (XS1930 / CX4800 style): prompts look
# like `sysname>` (exec) or `sysname#` (enable) or `sysname(config)#`.
SWITCH_PROMPT_RE = re.compile(r"^[A-Za-z0-9._-]+(?:\([^)]*\))?[>#][ ]*$", re.MULTILINE)

# USG FLEX H firewalls: `hostname>` and nested `hostname running config#`.
FIREWALL_PROMPT_RE = re.compile(r"^[A-Za-z0-9._-]+(?: running [a-z ]*)?[>#][ ]*$", re.MULTILINE)

# NWA/WAX/WBE access points: `Router>`, `Router#`, `Router(config)#`.
AP_PROMPT_RE = re.compile(r"^[A-Za-z0-9._-]+(?:\([^)]*\))?[>#][ ]*$", re.MULTILINE)


class ZyxelSwitchDriver(ZyxelDriver):
    """XS1930-12HP / CX4800-56F (and similar new-gen switches).

    `show running-config` outputs the full config unpaged (the paged variant
    is `show running-config page`). Config restore via TFTP + reload is not
    part of the alpha.
    """

    family = "switch"
    prompt_re = SWITCH_PROMPT_RE

    def _disable_pager(self) -> None:
        # New-gen switch CLI does not page `show running-config` output.
        return

    def get_config(self) -> str:
        out = self.run("show running-config")
        if not out.strip():
            raise DriverError("Device returned an empty configuration")
        return out

    def check_alive(self) -> bool:
        out = self.run("show version", timeout=30)
        return bool(out.strip())

    def apply_config(self, config_text: str) -> str:
        raise OperationFailedError(
            "Switch config revert requires TFTP staging + reload and is not enabled in this "
            "alpha (commands are documented: copy tftp config / reload config)"
        )


class ZyxelFirewallDriver(ZyxelDriver):
    """USG FLEX 700H (ZLD-style CLI).

    Full running config via `show config running | no-pager`; pager disabled
    session-wide with `cliconfig pager enabled false`.
    """

    family = "firewall"
    prompt_re = FIREWALL_PROMPT_RE

    def _disable_pager(self) -> None:
        self.run("cliconfig pager enabled false", timeout=15)

    def get_config(self) -> str:
        out = self.run("show config running | no-pager")
        if not out.strip():
            raise DriverError("Device returned an empty configuration")
        return out

    def check_alive(self) -> bool:
        out = self.run("show system-info", timeout=30)
        return bool(out.strip())

    def apply_config(self, config_text: str) -> str:
        raise OperationFailedError(
            "Firewall config revert requires staging the file on the device "
            "(cmd config-apply) and is not enabled in this alpha"
        )


class ZyxelAPDriver(ZyxelDriver):
    """WBE660S (NWA/IAP/WAX/WBE series CLI).

    Config via `show running-config` at the enable prompt. Revert: upload the
    snapshot to /conf/ over FTP, then `apply running-config /conf/<file>
    ignore error rollback` followed by `write`.
    """

    family = "ap"
    prompt_re = AP_PROMPT_RE

    def _disable_pager(self) -> None:
        return

    def _ensure_enable(self) -> None:
        if self.base_prompt.endswith("#"):
            return
        # Send enable and wait for prompt change; password prompt not expected
        # when already authenticated as admin over SSH.
        self.transport.sendline("enable")
        text = self.transport.read_until(self.prompt_re, timeout=15)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        self.base_prompt = lines[-1] if lines else self.base_prompt
        if not self.base_prompt.endswith("#"):
            raise DriverError("Could not enter enable mode on AP")

    def get_config(self) -> str:
        self._ensure_enable()
        out = self.run("show running-config")
        if not out.strip():
            raise DriverError("Device returned an empty configuration")
        return out

    def check_alive(self) -> bool:
        out = self.run("show version", timeout=30)
        return bool(out.strip())

    def apply_config(self, config_text: str) -> str:
        """Push a snapshot back to the AP: FTP upload + apply + write."""

        self._ensure_enable()
        remote_name = f"zynk_restore_{os.urandom(4).hex()}.conf"
        try:
            ftp = ftplib.FTP(timeout=30)
            ftp.connect(self.spec.host, 21)
            ftp.login(self.spec.username, self.spec.password)
            ftp.set_pasv(True)
            data = config_text.encode("utf-8")
            ftp.storbinary(f"STOR /conf/{remote_name}", io.BytesIO(data))
            ftp.quit()
        except (ftplib.all_errors, OSError) as err:
            raise OperationFailedError(
                f"Could not upload config to AP (FTP must be enabled on the device): {err}"
            ) from err

        self.run("configure terminal", timeout=30)
        # Follow the documented transcript: `apply running-config /conf/<file>`
        # followed by the `ignore error rollback` continuation line, then write.
        self.transport.sendline(f"apply running-config /conf/{remote_name}")
        self.transport.drain(5.0)
        self.transport.sendline("ignore error rollback")
        out = self.transport.read_until(self.prompt_re, timeout=300)
        self.run("write", timeout=60)
        return out
