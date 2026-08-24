from __future__ import annotations

import ftplib
import io
import logging
import os
import re

from app.devices.base import ZyxelDriver
from app.devices.switch_support import license_message
from app.devices.tftp import (
    SingleFileTFTPServer,
    TFTPError,
    TFTPReceiveServer,
    random_filename,
    tftp_lock,
)
from app.devices.transport import (
    DriverError,
    OperationFailedError,
    TimeoutError_,
    UnreachableError,
    scp_upload_dedicated,
    sftp_upload_dedicated,
)

log = logging.getLogger("zynk.drivers")

# New-generation Ethernet switch CLI (XS1930 / CX4800 style): prompts look
# like `sysname>` (exec) or `sysname#` (enable) or `sysname(config)#`.
# After `?` listings the XS1930 prints the prompt TWICE on one line
# (`XS1930# XS1930# `), so allow a prompt to be followed by another prompt
# on the same line as well as line-anchored.
_PROMPT_TOKEN = r"[A-Za-z0-9._-]+(?:\([^)]*\))?[>#][ ]*"
SWITCH_PROMPT_RE = re.compile(
    rf"(?:^|\s)(?P<prompt>{_PROMPT_TOKEN})" rf"(?:\n|$|(?={_PROMPT_TOKEN}))",
    re.MULTILINE,
)

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
    # Config header lines, e.g. `; Product Name = XS1930-12HP`
    PRODUCT_NAME_RE = re.compile(r";\s*Product Name\s*=\s*(\S+)", re.IGNORECASE)
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
        # Remember the model from the config header (e.g. XS1930-12HP) so
        # license diagnostics can be model-specific.
        self._remember_model(out)
        return out

    def _remember_model(self, config_text: str) -> None:
        m = self.PRODUCT_NAME_RE.search(config_text)
        if m:
            self.detected_model = m.group(1)

    def _license_hint(self) -> str:
        model = self.detected_model or ""
        msg = license_message(model)
        if msg:
            return msg
        if model:
            return (
                f"no 'copy' command found in the '?' listing for model {model}. "
                f"This is unexpected — the {model} series should have the full CLI. "
                f"Check the device (license state, firmware) or report this."
            )
        return (
            "no 'copy' command in the '?' listing and no model detected — "
            "the model is parsed from the config header "
            "(; Product Name = ...); run a config pull first."
        )

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

        Runs `?` (enable mode) — read-only — and returns the matching command
        lines. Used when the documented `copy` syntax is rejected. Absence of
        any `copy` command combined with the basic command set
        (import/reload/show) identifies a restricted basic CLI — the
        model-specific explanation comes from the switch CLI support matrix
        (switch_cli_support.csv).
        """
        out = ""
        timeout_err = None
        try:
            out = self.run("?", timeout=30)
        except TimeoutError_ as err:
            # The XS1930 doubles its prompt after a `?` listing
            # (`prompt# prompt# `) which may not match cleanly — but the
            # listing itself arrived; parse what we got.
            timeout_err = err
            out = getattr(err, "message", "")
            out = out.rsplit("last output:", 1)[-1].strip().strip("'")
        except DriverError as err:
            return f"(probe with '?' failed: {err})"
        if not out.strip():
            return f"(probe with '?' failed: {timeout_err or 'empty output'})"

        def diagnose(text: str) -> str | None:
            has_copy = re.search(r"\bcopy\b", text, re.IGNORECASE)
            if not has_copy and re.search(r"\b(import|reload|show)\b", text, re.IGNORECASE):
                return f"restricted basic CLI detected — {self._license_hint()}"
            return None

        diagnosis = diagnose(out)
        if diagnosis:
            return diagnosis
        kw = re.compile(r"tftp|\bcopy\b|backup|restore|import|upload|download", re.IGNORECASE)
        lines = [ln.strip() for ln in out.splitlines() if kw.search(ln) and ln.strip()]
        if lines:
            return "; ".join(lines[:20])
        raw = [ln.strip() for ln in out.splitlines() if ln.strip()][:8]
        return (
            "no tftp/copy/backup/restore commands in '?' output; first lines: "
            f"{'; '.join(raw) if raw else '(empty)'}"
        )

    def apply_config(self, config_text: str) -> str:
        """Restore a snapshot: TFTP staging into config slot 1 + reload config 1.

        Destructive: the switch warm-reboots with the staged configuration.
        """
        # Model for diagnostics comes from the snapshot's own config header
        # (this session never ran get_config).
        self._remember_model(config_text)
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
                    # Capture the model from the config header (if unknown yet)
                    # so the diagnosis is model-specific.
                    if not self.detected_model:
                        try:
                            self._remember_model(self.run("show running-config", timeout=60))
                        except DriverError:
                            pass
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

    Revert (per guide §2.6.5 + File Manager commands): SFTP-upload the
    snapshot to /conf/ over the existing SSH connection, validate with
    `cmd config-apply option dry-run <file>`, then `cmd config-apply <file>`
    — applies immediately WITHOUT a reboot; success shows an `ok / message OK`
    response tree. (A `copy-reboot` option exists for reboot-apply, not used.)
    """

    family = "firewall"
    prompt_re = FIREWALL_PROMPT_RE
    # Error keywords across uOS responses (config-apply failures print
    # error trees rather than single-line messages).
    APPLY_ERROR_RE = re.compile(
        r"error|fail|can\s*not|can'?t|denied|refused|not found|invalid|" r"timed?\s*out|unsupport",
        re.IGNORECASE,
    )

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

    def _ensure_running_config_mode(self) -> None:
        """Enter `edit running` mode (prompt `<host> running config#`)."""
        if self.base_prompt.rstrip().endswith("#") and "running" in self.base_prompt:
            return
        out = self.run("edit running", timeout=30)
        # The only response is the new prompt; confirm we are in the mode by
        # looking at the echoed transcript's last prompt-ish line.
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        tail = lines[-1] if lines else ""
        if "running" not in out and not tail:
            # tolerate drivers/firmwares that echo nothing; verify via prompt
            self.transport.sendline()
            text = self.transport.read_until(self.prompt_re, timeout=15)
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        self.base_prompt = lines[-1] if lines else self.base_prompt

    def _upload_snapshot(self, remote_path: str, config_text: str) -> str:
        """Upload the snapshot to /conf/, trying SFTP -> SCP -> FTP.

        Real-device finding (USG FLEX H): opening SFTP alongside the active
        shell channel can yield 'Garbage packet received'; some firmwares
        don't support the SFTP subsystem at all. The dedicated-connection
        attempts avoid the channel-multiplexing issue; FTP is the documented
        uOS file-upload service (used for firmware uploads) as last resort.

        Returns which transport succeeded ("sftp" / "scp" / "ftp").
        """
        errors: list[str] = []
        for method in ("sftp", "scp"):
            fn = sftp_upload_dedicated if method == "sftp" else scp_upload_dedicated
            try:
                fn(
                    self.spec.host,
                    self.spec.port,
                    self.spec.username,
                    self.spec.password,
                    remote_path,
                    config_text.encode("utf-8"),
                )
                return method
            except DriverError as err:
                errors.append(f"{method}: {err}")
            except Exception as err:  # paramiko garbage packets etc.
                errors.append(f"{method}: {err.__class__.__name__}: {err}")
        # FTP fallback — same pattern as the AP/ZLD drivers
        import ftplib
        import io

        try:
            ftp = ftplib.FTP(timeout=30)
            ftp.connect(self.spec.host, 21)
            ftp.login(self.spec.username, self.spec.password)
            ftp.set_pasv(True)
            ftp.storbinary(f"STOR {remote_path}", io.BytesIO(config_text.encode("utf-8")))
            ftp.quit()
            return "ftp"
        except (*ftplib.all_errors, OSError) as err:
            errors.append(f"ftp: {err}")
        raise OperationFailedError(
            "Could not upload the config to the firewall — all transfer methods "
            f"failed ({'; '.join(errors)}). If SFTP/SCP are not available on this "
            "firmware, enable the FTP server on the device and retry: "
            "'vrf main ftp-server enabled true' + 'commit' (disable again "
            "afterwards if desired)."
        )

    def apply_config(self, config_text: str) -> str:
        """Restore a snapshot: upload + dry-run validation + config-apply.

        Applies immediately without a reboot; the post-revert confirmation
        pull in the backup service verifies the applied state.
        """
        import os

        # /conf/ file name: <=76 chars, must end with .conf (guide constraints)
        name = f"zynk{os.urandom(3).hex()}.conf"
        remote = f"/conf/{name}"

        self._ensure_running_config_mode()
        method = self._upload_snapshot(remote, config_text)
        log.info("firewall restore %s: uploaded via %s", self.spec.host, method)

        # Pre-flight: validate the file without applying it.
        dry = self.run(f"cmd config-apply option dry-run {name}", timeout=180)
        if self.APPLY_ERROR_RE.search(dry) and "ok" not in dry.lower():
            raise OperationFailedError(f"Firewall rejected the config (dry-run): {dry.strip()!r}")

        out = self.run(f"cmd config-apply {name}", timeout=300)
        lowered = out.lower()
        if "ok" not in lowered or self.APPLY_ERROR_RE.search(out):
            raise OperationFailedError(f"Firewall config-apply failed: {out.strip()!r}")
        return out


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
