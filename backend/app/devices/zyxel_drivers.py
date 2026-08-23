from __future__ import annotations

import ftplib
import io
import logging
import os
import re

from app.devices.base import ZyxelDriver
from app.devices.tftp import (
    SingleFileTFTPServer,
    TFTPError,
    TFTPReceiveServer,
    random_filename,
    tftp_lock,
)
from app.devices.transport import DriverError, OperationFailedError, UnreachableError

log = logging.getLogger("zynk.drivers")

# New-generation Ethernet switch CLI (XS1930 / CX4800 style): prompts look
# like `sysname>` (exec) or `sysname#` (enable) or `sysname(config)#`.
SWITCH_PROMPT_RE = re.compile(r"^[A-Za-z0-9._-]+(?:\([^)]*\))?[>#][ ]*$", re.MULTILINE)

# USG FLEX H firewalls: `hostname>` and nested `hostname running config#`.
FIREWALL_PROMPT_RE = re.compile(r"^[A-Za-z0-9._-]+(?: running [a-z ]*)?[>#][ ]*$", re.MULTILINE)

# NWA/WAX/WBE access points: `Router>`, `Router#`, `Router(config)#`.
AP_PROMPT_RE = re.compile(r"^[A-Za-z0-9._-]+(?:\([^)]*\))?[>#][ ]*$", re.MULTILINE)

# ZLD firewalls (ATP / USG ZyWALL): `Router>`, `Router#`, `Router(config)#`
# and sub-modes like `Router(zone)#`, `Router(config-if-ge)#`.
ZLD_PROMPT_RE = re.compile(r"^[A-Za-z0-9._-]+(?:\([^)]*\))?[>#][ ]*$", re.MULTILINE)


class ZyxelSwitchDriver(ZyxelDriver):
    """XS1930-12HP / CX4800-56F (and similar new-gen switches).

    `show running-config` outputs the full config unpaged (the paged variant
    is `show running-config page`).

    Revert: serve the snapshot over TFTP, `copy tftp config 1 <ip> <file>` to
    stage it in config slot 1, then `reload config 1` (warm reboot, confirmed
    with `y`) to apply. The device downloads FROM Zynk — Zynk must be reachable
    from the switch on UDP port 69.
    """

    family = "switch"
    prompt_re = SWITCH_PROMPT_RE
    RELOAD_CONFIRM_RE = re.compile(r"\[y/N\]\s*$", re.IGNORECASE)
    # Broad CLI-failure detection: Zyxel phrasings vary across firmware
    # ("Can not", "Illegal parameter", "Invalid input", "TFTP fail", ...).
    TFTP_ERROR_RE = re.compile(
        r"error|fail|can\s*not|can'?t|timed?\s*out|unreach|too long|not found|"
        r"illegal|invalid|denied|refused|no such|not exist",
        re.IGNORECASE,
    )

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

    def _tftp_address(self) -> str:
        """Address the switch should use to reach this Zynk instance."""
        if self.spec.tftp_address:
            return self.spec.tftp_address
        # Auto-detect: which local source IP would route toward the device?
        import socket as _socket

        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        try:
            s.connect((self.spec.host, self.spec.port))
            return s.getsockname()[0]
        except OSError as err:
            raise OperationFailedError(
                f"Could not determine TFTP source address toward {self.spec.host} "
                f"({err}). Set ZYNK_TFTP_PUBLIC_ADDRESS explicitly."
            ) from err
        finally:
            s.close()

    def _wait_for_reboot(self) -> None:
        """Block until the device's SSH port answers again (plus a settle delay)."""
        import socket as _socket
        import time as _time

        deadline = _time.monotonic() + self.spec.reboot_timeout
        while _time.monotonic() < deadline:
            try:
                with _socket.create_connection((self.spec.host, self.spec.port), timeout=3.0):
                    _time.sleep(self.spec.reboot_settle)  # let SSH finish initializing
                    return
            except OSError:
                _time.sleep(5.0)
        raise OperationFailedError(
            f"Switch did not come back within {self.spec.reboot_timeout:.0f}s after "
            "reload — verify it rebooted with the restored configuration."
        )

    def _probe_backup_commands(self) -> str:
        """Ask the switch which backup/TFTP commands its firmware actually has.

        Runs the documented `help` command (enable mode) — read-only — and
        returns the matching command lines. Used when the documented `copy`
        syntax is rejected. Absence of any `copy` command combined with the
        basic command set (import/reload/show) identifies the XS1930 series
        restricted basic CLI — full CLI configuration (incl. copy tftp
        config) requires the Access L3 license (guide §1.1, Table 4).
        """
        kw = re.compile(r"tftp|\bcopy\b|backup|restore|import|upload|download", re.IGNORECASE)
        try:
            out = self.run("help", timeout=60)
        except DriverError as err:
            return f"(help probe failed: {err})"
        has_copy = re.search(r"\bcopy\b", out, re.IGNORECASE)
        if not has_copy and re.search(r"\b(import|reload|show)\b", out, re.IGNORECASE):
            return (
                "restricted basic CLI detected — no 'copy' command at all. The XS1930 "
                "series ships with a restricted basic CLI; full CLI configuration "
                "(incl. copy tftp config) requires the Access L3 license from Zyxel "
                "(myzyxel.com, CLI guide §1.1). Config pull works, config restore "
                "does not. GS1350/CX4800 series have the full CLI without a license."
            )
        lines = [ln.strip() for ln in out.splitlines() if kw.search(ln) and ln.strip()]
        if lines:
            return "; ".join(lines[:20])
        raw = [ln.strip() for ln in out.splitlines() if ln.strip()][:8]
        return (
            "no tftp/copy/backup/restore commands in help; first lines of help "
            f"output: {'; '.join(raw) if raw else '(empty)'}"
        )

    def apply_config(self, config_text: str) -> str:
        """Restore a snapshot: TFTP staging into config slot 1 + reload config 1.

        Destructive: the switch warm-reboots with the staged configuration.
        """
        tftp_addr = self._tftp_address()
        filename = random_filename()
        log.info(
            "switch restore %s: serving %s via TFTP from %s (port %d)",
            self.spec.host,
            filename,
            tftp_addr,
            self.spec.tftp_port,
        )
        server = SingleFileTFTPServer(
            config_text.encode("utf-8"), filename, port=self.spec.tftp_port
        )
        with tftp_lock:
            try:
                server.start()
            except TFTPError as err:
                raise OperationFailedError(str(err)) from err
            try:
                out = self.run(f"copy tftp config 1 {tftp_addr} {filename}", timeout=180)
                if self.TFTP_ERROR_RE.search(out):
                    probe = self._probe_backup_commands()
                    raise OperationFailedError(
                        f"Switch rejected the TFTP restore: {out.strip()!r}. "
                        f"This firmware may not support the documented copy syntax; "
                        f"available backup/TFTP commands: {probe}"
                    )
                if not server.wait(timeout=180):
                    raise OperationFailedError(
                        f"TFTP transfer failed: {server.error}. "
                        f"Switch CLI output was: {out.strip()!r}"
                    )
            finally:
                server.stop()
            import time as _time

            _time.sleep(2.0)  # let the switch finish writing flash after last ACK

        # Reload with the staged config: confirm the [y/N] prompt, then the
        # connection drops while the switch reboots — that drop means success.
        self.transport.sendline("reload config 1")
        self.transport.read_until(self.RELOAD_CONFIRM_RE, timeout=30)
        self.transport.sendline("y")
        try:
            self.transport.read_until(self.prompt_re, timeout=60)
        except UnreachableError:
            pass  # connection dropped during reboot — expected
        except DriverError:
            pass  # some firmware reboots without a final prompt — tolerate

        self._wait_for_reboot()
        return out

    def test_tftp_path(self) -> tuple[bool, str]:
        """Verify the device can reach us on UDP 69 (TFTP) end to end.

        Runs the documented backup command `copy running-config tftp <ip> <file>`
        so the switch pushes its running config to a throwaway in-memory
        receiver. Nothing on the switch is modified.
        """
        tftp_addr = self._tftp_address()
        filename = f"zynktest{os.urandom(2).hex()}.cfg"
        log.info(
            "TFTP path test %s: expecting upload %s to %s",
            self.spec.host,
            filename,
            tftp_addr,
        )
        recv = TFTPReceiveServer(filename, port=self.spec.tftp_port, idle_timeout=30.0)
        with tftp_lock:
            try:
                recv.start()
            except TFTPError as err:
                return False, str(err)
            try:
                out = self.run(f"copy running-config tftp {tftp_addr} {filename}", timeout=90)
                if self.TFTP_ERROR_RE.search(out):
                    probe = self._probe_backup_commands()
                    return False, (
                        f"switch refused: {out.strip()!r}. This firmware may not "
                        f"support the documented copy syntax; available "
                        f"backup/TFTP commands: {probe}"
                    )
                if not recv.wait(timeout=90):
                    return False, f"{recv.error}; switch CLI output: {out.strip()!r}"
            finally:
                recv.stop()
        size = len(recv.data or b"")
        if size == 0:
            return False, "upload completed but was empty"
        return True, (
            f"TFTP path OK — switch pushed {size} bytes "
            f"to {tftp_addr}:{self.spec.tftp_port}/udp"
        )


class ZyxelFirewallDriver(ZyxelDriver):
    """USG FLEX 700H (uOS CLI).

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


class ZyxelZLDFirewallDriver(ZyxelDriver):
    """ATP / USG ZyWALL firewalls (ZLD CLI, e.g. ATP800).

    NOTE: ZLD-based devices (USG & ATP series) are END OF LIFE at Zyxel —
    no new firmware or support. Supported for existing installations only.

    Config via `show running-config` at the privilege prompt. Revert: FTP
    upload to /conf/ then `apply /conf/<file> ignore-error rollback` + `write`
    (single-line command per the ZLD CLI guide, unlike the AP's continuation
    line form). No pager commands are documented for ZLD.
    """

    family = "zld_firewall"
    prompt_re = ZLD_PROMPT_RE

    def _disable_pager(self) -> None:
        return

    def _ensure_enable(self) -> None:
        if self.base_prompt.endswith("#"):
            return
        self.transport.sendline("enable")
        text = self.transport.read_until(self.prompt_re, timeout=15)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        self.base_prompt = lines[-1] if lines else self.base_prompt
        if not self.base_prompt.endswith("#"):
            raise DriverError("Could not enter enable mode on ZLD firewall")

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
        """Push a snapshot back: FTP upload + apply (ignore-error rollback) + write."""
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
                f"Could not upload config to ZLD firewall (FTP must be enabled on the "
                f"device): {err}"
            ) from err

        out = self.run(f"apply /conf/{remote_name} ignore-error rollback", timeout=300)
        self.run("write", timeout=60)
        return out


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
