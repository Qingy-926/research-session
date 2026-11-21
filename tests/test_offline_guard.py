"""Checks for the offline network guard in tests/conftest.py."""

import os
import socket

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RESEARCH_SESSION_ALLOW_LIVE") == "1",
    reason="live mode disables the offline guard",
)


def test_external_connect_is_refused():
    with pytest.raises(RuntimeError, match="external network disabled in offline tests"):
        socket.create_connection(("203.0.113.1", 80), timeout=0.01)
    sock = socket.socket()
    try:
        with pytest.raises(RuntimeError, match="external network disabled in offline tests"):
            sock.connect(("203.0.113.1", 80))
    finally:
        sock.close()


def test_loopback_connect_is_still_allowed():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        with socket.create_connection(server.getsockname(), timeout=1) as client:
            accepted, _ = server.accept()
            accepted.close()
            assert client.getpeername()[0] == "127.0.0.1"
    finally:
        server.close()
