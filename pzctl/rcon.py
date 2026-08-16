"""Minimal Source RCON client (the protocol Project Zomboid's RCON port speaks)."""

from __future__ import annotations

import socket
import struct
import threading

SERVERDATA_RESPONSE_VALUE = 0
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_AUTH = 3


class RconError(Exception):
    pass


class RconClient:
    """One-shot connection: connect, auth, run commands, close.

    PZ's RCON server is not tolerant of long-lived idle sockets, so callers
    generally use this as a context manager per command batch.
    """

    def __init__(self, host: str, port: int, password: str, timeout: float = 6.0):
        self.host = host
        self.port = int(port)
        self.password = password
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self._id = 0
        self._lock = threading.Lock()

    def __enter__(self) -> "RconClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> None:
        if not self.password:
            raise RconError("no RCON password configured")
        try:
            self.sock = socket.create_connection((self.host, self.port), self.timeout)
        except OSError as exc:
            raise RconError(f"cannot reach RCON at {self.host}:{self.port} ({exc})") from exc
        self.sock.settimeout(self.timeout)
        req_id = self._write(SERVERDATA_AUTH, self.password)
        while True:
            pkt_id, pkt_type, _ = self._read()
            if pkt_type == SERVERDATA_AUTH_RESPONSE:
                if pkt_id == -1 or pkt_id != req_id:
                    self.close()
                    raise RconError("RCON authentication failed (wrong password)")
                return

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def command(self, cmd: str) -> str:
        with self._lock:
            if self.sock is None:
                raise RconError("not connected")
            self._write(SERVERDATA_EXECCOMMAND, cmd)
            chunks: list[str] = []
            # Read the first packet at full timeout, then drain any continuation
            # packets quickly -- multi-packet responses have no explicit end marker.
            try:
                _, _, body = self._read()
                chunks.append(body)
            except socket.timeout as exc:
                raise RconError("timed out waiting for RCON response") from exc
            self.sock.settimeout(0.4)
            try:
                while True:
                    _, _, body = self._read()
                    chunks.append(body)
            except (socket.timeout, RconError, OSError):
                pass
            finally:
                if self.sock is not None:
                    self.sock.settimeout(self.timeout)
            return "".join(chunks).strip()

    # -- wire format -----------------------------------------------------

    def _write(self, pkt_type: int, body: str) -> int:
        assert self.sock is not None
        self._id += 1
        payload = struct.pack("<ii", self._id, pkt_type) + body.encode("utf-8") + b"\x00\x00"
        self.sock.sendall(struct.pack("<i", len(payload)) + payload)
        return self._id

    def _read(self) -> tuple[int, int, str]:
        assert self.sock is not None
        raw_len = self._recv_exact(4)
        (size,) = struct.unpack("<i", raw_len)
        if size < 10 or size > 8192:
            raise RconError(f"bogus RCON packet size {size}")
        payload = self._recv_exact(size)
        pkt_id, pkt_type = struct.unpack("<ii", payload[:8])
        body = payload[8:-2].decode("utf-8", errors="replace")
        return pkt_id, pkt_type, body

    def _recv_exact(self, count: int) -> bytes:
        assert self.sock is not None
        buf = b""
        while len(buf) < count:
            chunk = self.sock.recv(count - len(buf))
            if not chunk:
                raise RconError("RCON connection closed by server")
            buf += chunk
        return buf


def run_command(host: str, port: int, password: str, cmd: str, timeout: float = 6.0) -> str:
    with RconClient(host, port, password, timeout) as client:
        return client.command(cmd)
