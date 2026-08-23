from __future__ import annotations

import socket
import struct

import pytest

from app.devices.tftp import SingleFileTFTPServer


def tftp_get(host: str, port: int, filename: str, blksize: int | None = None) -> bytes:
    """Minimal TFTP client for tests: RRQ (octet), optional blksize, returns data."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(5.0)
    try:
        req = struct.pack("!H", 1) + filename.encode() + b"\x00octet\x00"
        if blksize:
            req += f"blksize\x00{blksize}\x00".encode()
        s.sendto(req, (host, port))
        data = b""
        expected_block = 1
        effective_blksize = 512
        peer: tuple | None = None  # TFTP data comes from a per-session port
        while True:
            pkt, src = s.recvfrom(2048)
            peer = peer or src
            opcode = struct.unpack("!H", pkt[:2])[0]
            if opcode == 6:  # OACK
                s.sendto(struct.pack("!HH", 4, 0), peer)
                effective_blksize = blksize or 512
                continue
            if opcode == 5:  # ERROR
                code = struct.unpack("!H", pkt[2:4])[0]
                msg = pkt[4:].split(b"\x00")[0]
                raise RuntimeError(f"TFTP error {code}: {msg.decode()}")
            if opcode == 3:  # DATA
                block = struct.unpack("!H", pkt[2:4])[0]
                if block == expected_block:
                    data += pkt[4:]
                    expected_block += 1
                assert peer is not None
                s.sendto(struct.pack("!HH", 4, block), peer)
                if len(pkt) - 4 < effective_blksize:
                    return data
    finally:
        s.close()


class TestSingleFileTFTPServer:
    def test_serves_file_default_blocksize(self):
        payload = b"a" * 1500  # 3 blocks at 512
        srv = SingleFileTFTPServer(payload, "restore.cfg", port=0)
        ip, port = srv.start()
        try:
            got = tftp_get("127.0.0.1", port, "restore.cfg")
            assert got == payload
            assert srv.wait(timeout=5.0)
        finally:
            srv.stop()

    def test_serves_file_blksize_negotiation(self):
        payload = b"b" * 2500
        srv = SingleFileTFTPServer(payload, "big.cfg", port=0)
        ip, port = srv.start()
        try:
            got = tftp_get("127.0.0.1", port, "big.cfg", blksize=1024)
            assert got == payload
            assert srv.wait(timeout=5.0)
        finally:
            srv.stop()

    def test_rejects_wrong_filename(self):
        srv = SingleFileTFTPServer(b"data", "expected.cfg", port=0)
        ip, port = srv.start()
        try:
            with pytest.raises(RuntimeError, match="File not found"):
                tftp_get("127.0.0.1", port, "other.cfg")
            assert srv.wait(timeout=5.0) is False
            assert "unexpected file" in (srv.error or "")
        finally:
            srv.stop()

    def test_no_request_times_out(self):
        srv = SingleFileTFTPServer(b"data", "x.cfg", port=0, idle_timeout=0.5)
        srv.start()
        try:
            assert srv.wait(timeout=5.0) is False
            assert "no TFTP request" in (srv.error or "")
        finally:
            srv.stop()

    def test_single_shot_second_rrq_not_served(self):
        payload = b"tiny"
        srv = SingleFileTFTPServer(payload, "one.cfg", port=0)
        ip, port = srv.start()
        try:
            assert tftp_get("127.0.0.1", port, "one.cfg") == payload
            assert srv.wait(timeout=5.0)
        finally:
            srv.stop()
