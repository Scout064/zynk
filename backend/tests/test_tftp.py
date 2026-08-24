from __future__ import annotations

import socket
import struct

import pytest

from app.devices.tftp import SingleFileTFTPServer, TFTPReceiveServer


def tftp_put(host: str, port: int, filename: str, data: bytes, blksize: int | None = None) -> None:
    """Minimal TFTP write client for tests: WRQ (octet), optional blksize."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(5.0)
    try:
        req = struct.pack("!H", 2) + filename.encode() + b"\x00octet\x00"
        if blksize:
            req += f"blksize\x00{blksize}\x00".encode()
        s.sendto(req, (host, port))
        peer: tuple | None = None
        effective_blksize = 512
        offset = 0
        block = 0  # last DATA block sent

        def send_next() -> bool:
            """Send the next data block; returns True if it was the last."""
            nonlocal offset, block
            assert peer is not None
            chunk = data[offset : offset + effective_blksize]
            s.sendto(struct.pack("!HH", 3, block + 1) + chunk, peer)
            offset += len(chunk)
            block += 1
            return len(chunk) < effective_blksize

        while True:
            pkt, src = s.recvfrom(2048)
            peer = peer or src
            opcode = struct.unpack("!H", pkt[:2])[0]
            if opcode == 6:  # OACK — ACK(0) then immediately start sending data
                effective_blksize = blksize or 512
                s.sendto(struct.pack("!HH", 4, 0), peer)
                if send_next():
                    return
                continue
            if opcode == 5:
                msg = pkt[4:].split(b"\x00")[0]
                raise RuntimeError(f"TFTP error: {msg.decode()}")
            if opcode == 4:  # ACK — send next block
                acked = struct.unpack("!H", pkt[2:4])[0]
                if acked == block and send_next():
                    return
    finally:
        s.close()


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


class TestTFTPReceiveServer:
    def test_receives_upload_default_blocksize(self):
        payload = bytes(range(256)) * 6  # 1536 bytes = 3 blocks
        srv = TFTPReceiveServer("upload.cfg", port=0)
        ip, port = srv.start()
        try:
            tftp_put("127.0.0.1", port, "upload.cfg", payload)
            assert srv.wait(timeout=5.0)
            assert srv.data == payload
        finally:
            srv.stop()

    def test_receives_upload_blksize(self):
        payload = b"z" * 3000
        srv = TFTPReceiveServer("big.cfg", port=0)
        ip, port = srv.start()
        try:
            tftp_put("127.0.0.1", port, "big.cfg", payload, blksize=1024)
            assert srv.wait(timeout=5.0)
            assert srv.data == payload
        finally:
            srv.stop()

    def test_rejects_wrong_filename(self):
        srv = TFTPReceiveServer("expected.cfg", port=0)
        ip, port = srv.start()
        try:
            with pytest.raises(RuntimeError, match="File not found"):
                tftp_put("127.0.0.1", port, "unexpected.cfg", b"data")
            assert srv.wait(timeout=5.0) is False
            assert "unexpected file" in (srv.error or "")
        finally:
            srv.stop()

    def test_no_upload_times_out(self):
        srv = TFTPReceiveServer("x.cfg", port=0, idle_timeout=0.5)
        srv.start()
        try:
            assert srv.wait(timeout=5.0) is False
            assert "no TFTP upload" in (srv.error or "")
        finally:
            srv.stop()
