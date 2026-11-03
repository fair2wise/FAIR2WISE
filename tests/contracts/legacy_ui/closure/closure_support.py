"""Helpers for the T18C closure tests (unique module name; see tests/graph/graph_contexts.py)."""
from __future__ import annotations

import socket
from typing import Any

#: The developer's own servers; never connectable from a test.
FORBIDDEN_PORTS = frozenset({8011, 8765, 8002, 8004, 5057})

_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
_REAL_CREATE_CONNECTION = socket.create_connection


class NetworkRefused(OSError):
    """Outbound connection outside 127.0.0.1 or to a forbidden port."""


def _check(address: Any) -> None:
    if not isinstance(address, tuple):
        return  # AF_UNIX
    host, port = address[0], address[1]
    if host not in ("127.0.0.1", "localhost", "::1") or port in FORBIDDEN_PORTS:
        raise NetworkRefused(f"T18C closure: connection to {address!r} is not allowed")


def loopback_guard(monkeypatch: Any) -> None:
    """Allow 127.0.0.1 except the forbidden ports; refuse everything else."""

    def connect(self, address, *a):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _check(address)
        return _REAL_CONNECT(self, address, *a)

    def connect_ex(self, address, *a):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _check(address)
        return _REAL_CONNECT_EX(self, address, *a)

    def create_connection(address, *a, **k):
        _check(address)
        return _REAL_CREATE_CONNECTION(address, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "create_connection", create_connection)


class _ClosedPort:
    """Stands in for the socket :func:`dead_port` used to return (``getsockname`` / ``close``)."""

    def __init__(self, port: int) -> None:
        self._port = port

    def getsockname(self) -> tuple[str, int]:
        return ("127.0.0.1", self._port)

    def close(self) -> None:
        """Nothing to release."""


def dead_port() -> _ClosedPort:
    """A just-released ephemeral 127.0.0.1 port: connections to it are refused at once.

    (A bound but never-listening socket is not refused on macOS: every connect waits for its
    timeout, about 8 s each, which made the UI04 test take 30-50 s.)
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    finally:
        sock.close()
    return _ClosedPort(port)
