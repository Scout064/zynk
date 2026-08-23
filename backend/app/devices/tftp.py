from __future__ import annotations

import logging
import os
import socket
import struct
import threading
import time

# Serialized access to the TFTP listen port (only one transfer at a time).
tftp_lock = threading.Lock()

DEFAULT_BLOCKSIZE = 512
ACK_TIMEOUT = 5.0
MAX_RETRIES = 5

log = logging.getLogger("zynk.tftp")

_OPCODES = {1: "RRQ", 2: "WRQ", 3: "DATA", 4: "ACK", 5: "ERROR", 6: "OACK"}


class TFTPError(Exception):
    pass


def _error_packet(code: int, msg: str) -> bytes:
    return struct.pack("!HH", 5, code) + msg.encode() + b"\x00"


def _parse_request(data: bytes) -> tuple[str, str, dict[str, str]]:
    parts = data[2:].split(b"\x00")
    filename = parts[0].decode("utf-8", errors="replace")
    mode = parts[1].decode("utf-8", errors="replace").lower() if len(parts) > 1 else ""
    options: dict[str, str] = {}
    rest = parts[2:]
    for i in range(0, len(rest) - 1, 2):
        options[rest[i].decode("utf-8", errors="replace").lower()] = rest[i + 1].decode(
            "utf-8", errors="replace"
        )
    return filename, mode, options


def _negotiate_blksize(options: dict[str, str]) -> int:
    if "blksize" in options:
        try:
            negotiated = int(options["blksize"])
            if 8 <= negotiated <= 65464:
                return negotiated
        except ValueError:
            pass
    return DEFAULT_BLOCKSIZE


class _TFTPBase:
    """Shared lifecycle for the single-shot TFTP servers."""

    def __init__(self, filename: str, bind: str, port: int, idle_timeout: float):
        self.filename = filename.lstrip("./")
        self.bind = bind
        self.port = port
        self.idle_timeout = idle_timeout
        self._done = threading.Event()
        self._error: str | None = None
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None

    def start(self) -> tuple[str, int]:
        """Bind and start serving. Returns the actual (ip, port)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.bind, self.port))
        except OSError as err:
            sock.close()
            raise TFTPError(
                f"cannot bind TFTP listener on {self.bind}:{self.port}/udp ({err}). "
                "Switch revert needs UDP port 69 reachable from the device: map 69/udp "
                "in Docker and set ZYNK_TFTP_PUBLIC_ADDRESS; on Linux non-root requires "
                "CAP_NET_BIND_SERVICE."
            ) from err
        self._sock = sock
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return sock.getsockname()

    def stop(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._done.set()

    def wait(self, timeout: float = 60.0) -> bool:
        """Block until the single transfer finished successfully."""
        if not self._done.wait(timeout):
            self._error = self._error or f"TFTP transfer timed out after {timeout:.0f}s"
            return False
        return self._error is None

    @property
    def error(self) -> str | None:
        return self._error


class SingleFileTFTPServer(_TFTPBase):
    """Minimal TFTP server that serves exactly one file to one client, once.

    Handles RRQ in octet mode with optional blksize negotiation (RFC 2348).
    Runs in a background thread; the caller waits for completion via wait().
    """

    def __init__(
        self,
        data: bytes,
        filename: str,
        bind: str = "0.0.0.0",
        port: int = 69,
        idle_timeout: float = 120.0,
    ):
        super().__init__(filename, bind, port, idle_timeout)
        self.data = data

    def _serve(self) -> None:
        sock = self._sock
        assert sock is not None
        try:
            sock.settimeout(self.idle_timeout)
            while not self._done.is_set():
                try:
                    data, addr = sock.recvfrom(2048)
                except TimeoutError:
                    self._error = "no TFTP request arrived (device never connected)"
                    return
                opcode = struct.unpack("!H", data[:2])[0]
                log.info(
                    "TFTP listen :%d got %s from %s",
                    self.port,
                    _OPCODES.get(opcode, opcode),
                    addr,
                )
                if opcode == 1:  # RRQ
                    self._handle_rrq(sock, addr, data)
                    return  # single-shot
                if opcode == 2:  # WRQ — not supported here
                    sock.sendto(_error_packet(4, "Write requests not supported"), addr)
                    continue
                sock.sendto(_error_packet(4, "Illegal TFTP operation"), addr)
        except OSError as err:
            self._error = f"TFTP server error: {err}"
        finally:
            self._done.set()

    def _handle_rrq(self, listen_sock: socket.socket, addr: tuple, data: bytes) -> None:
        try:
            filename, mode, options = _parse_request(data)
        except (IndexError, struct.error):
            listen_sock.sendto(_error_packet(4, "Malformed request"), addr)
            self._error = "malformed TFTP request"
            return

        if mode not in ("octet", "netascii"):
            listen_sock.sendto(_error_packet(0, f"Unsupported mode '{mode}'"), addr)
            self._error = f"unsupported TFTP mode '{mode}'"
            return

        requested = filename.lstrip("./")
        if requested != self.filename:
            listen_sock.sendto(_error_packet(1, "File not found"), addr)
            self._error = f"device requested unexpected file '{filename}'"
            return

        blksize = _negotiate_blksize(options)
        log.info(
            "TFTP RRQ from %s: serving %d bytes (blksize %d)",
            addr[0],
            len(self.data),
            blksize,
        )

        session = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        session.bind((self.bind if self.bind != "0.0.0.0" else "", 0))
        session.settimeout(ACK_TIMEOUT)
        try:
            if blksize != DEFAULT_BLOCKSIZE:
                # OACK with the negotiated block size; client replies ACK(0)
                session.sendto(
                    struct.pack("!H", 6) + f"blksize\x00{blksize}\x00".encode(), addr
                )
                if not self._await_ack(session, addr, 0):
                    self._error = "client did not ACK the blksize negotiation"
                    return
            offset = 0
            block = 1
            while True:
                chunk = self.data[offset : offset + blksize]
                packet = struct.pack("!HH", 3, block) + chunk
                sent = False
                for _ in range(MAX_RETRIES):
                    session.sendto(packet, addr)
                    if self._await_ack(session, addr, block):
                        sent = True
                        break
                if not sent:
                    self._error = (
                        f"TFTP transfer stalled at block {block} (no ACK from {addr[0]})"
                    )
                    return
                offset += blksize
                block += 1
                if len(chunk) < blksize:
                    log.info(
                        "TFTP transfer to %s complete (%d bytes)", addr[0], len(self.data)
                    )
                    return  # final block sent and acknowledged
        finally:
            session.close()

    def _await_ack(self, session: socket.socket, addr: tuple, block: int) -> bool:
        """Wait for ACK of `block`; DATA retransmission is handled by caller."""
        end = time.monotonic() + ACK_TIMEOUT
        while time.monotonic() < end:
            try:
                data, src = session.recvfrom(2048)
            except TimeoutError:
                return False
            if src[0] != addr[0]:
                continue  # stray packet from another host
            opcode = struct.unpack("!H", data[:2])[0]
            if opcode == 4:
                acked = struct.unpack("!H", data[2:4])[0]
                if acked == block:
                    return True
                # duplicate/old ACK — ignore and keep waiting
        return False


def random_filename() -> str:
    # Keep it short: ZyNOS-based firmwares may impose filename length limits.
    return f"zynk{os.urandom(3).hex()}.cfg"


class TFTPReceiveServer(_TFTPBase):
    """Minimal single-shot TFTP receiver: accepts one WRQ and collects the file.

    Used for connectivity tests — the device pushes to us via
    `copy running-config tftp <ip> <file>`. The received bytes are available
    via .data once wait() returns True.
    """

    def __init__(
        self,
        filename: str,
        bind: str = "0.0.0.0",
        port: int = 69,
        idle_timeout: float = 120.0,
        max_size: int = 50 * 1024 * 1024,
    ):
        super().__init__(filename, bind, port, idle_timeout)
        self.max_size = max_size
        self._received: bytearray | None = None

    @property
    def data(self) -> bytes | None:
        return bytes(self._received) if self._received is not None else None

    def _serve(self) -> None:
        sock = self._sock
        assert sock is not None
        try:
            sock.settimeout(self.idle_timeout)
            while not self._done.is_set():
                try:
                    data, addr = sock.recvfrom(2048)
                except TimeoutError:
                    self._error = "no TFTP upload arrived (device never connected)"
                    return
                opcode = struct.unpack("!H", data[:2])[0]
                log.info(
                    "TFTP listen :%d got %s from %s",
                    self.port,
                    _OPCODES.get(opcode, opcode),
                    addr,
                )
                if opcode == 2:  # WRQ
                    self._handle_wrq(sock, addr, data)
                    return  # single-shot
                if opcode == 1:  # RRQ — not supported here
                    sock.sendto(_error_packet(4, "Read requests not supported"), addr)
                    continue
                sock.sendto(_error_packet(4, "Illegal TFTP operation"), addr)
        except OSError as err:
            self._error = f"TFTP server error: {err}"
        finally:
            self._done.set()

    def _handle_wrq(self, listen_sock: socket.socket, addr: tuple, data: bytes) -> None:
        try:
            filename, mode, options = _parse_request(data)
        except (IndexError, struct.error):
            listen_sock.sendto(_error_packet(4, "Malformed request"), addr)
            self._error = "malformed TFTP request"
            return

        if mode not in ("octet", "netascii"):
            listen_sock.sendto(_error_packet(0, f"Unsupported mode '{mode}'"), addr)
            self._error = f"unsupported TFTP mode '{mode}'"
            return

        requested = filename.lstrip("./")
        if requested != self.filename:
            listen_sock.sendto(_error_packet(1, "File not found"), addr)
            self._error = f"device announced unexpected file '{filename}'"
            return

        blksize = _negotiate_blksize(options)
        log.info("TFTP WRQ from %s: receiving (blksize %d)", addr[0], blksize)

        session = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        session.bind((self.bind if self.bind != "0.0.0.0" else "", 0))
        session.settimeout(ACK_TIMEOUT)
        received = bytearray()
        try:
            if blksize != DEFAULT_BLOCKSIZE:
                session.sendto(
                    struct.pack("!H", 6) + f"blksize\x00{blksize}\x00".encode(), addr
                )
            else:
                session.sendto(struct.pack("!HH", 4, 0), addr)  # plain ACK(0)

            expected_block = 1
            while True:
                end = time.monotonic() + ACK_TIMEOUT
                got = False
                while time.monotonic() < end:
                    try:
                        pkt, src = session.recvfrom(2048)
                    except TimeoutError:
                        break
                    if src[0] != addr[0]:
                        continue
                    opcode = struct.unpack("!H", pkt[:2])[0]
                    if opcode == 5:
                        msg = pkt[4:].split(b"\x00")[0].decode(errors="replace")
                        self._error = f"device sent TFTP error: {msg}"
                        return
                    if opcode == 3:  # DATA
                        block = struct.unpack("!H", pkt[2:4])[0]
                        if block == expected_block:
                            received += pkt[4:]
                            if len(received) > self.max_size:
                                session.sendto(
                                    _error_packet(3, "Disk full or allocation exceeded"),
                                    addr,
                                )
                                self._error = "upload exceeded size limit"
                                return
                            session.sendto(struct.pack("!HH", 4, block), addr)
                            expected_block += 1
                            got = True
                            if len(pkt) - 4 < blksize:
                                self._received = received
                                log.info(
                                    "TFTP upload from %s complete (%d bytes)",
                                    addr[0],
                                    len(received),
                                )
                                return
                        elif block == expected_block - 1:
                            # retransmission of the previous block — re-ACK
                            session.sendto(struct.pack("!HH", 4, block), addr)
                        break
                if not got:
                    self._error = (
                        f"TFTP upload stalled at block {expected_block} "
                        f"(no data from {addr[0]})"
                    )
                    return
        finally:
            session.close()
