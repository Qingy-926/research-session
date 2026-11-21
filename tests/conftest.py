"""Offline network guard shared by the public test suites.

Every test runs with non-loopback connects disabled. Loopback stays allowed
because the existing suites start local HTTP servers. Set
``RESEARCH_SESSION_ALLOW_LIVE=1`` only for explicit live-provider tests, which
must also require an explicit provider configuration (see CONTRIBUTING.md).
"""

import ipaddress
import os
import socket

import pytest

ALLOW_LIVE_ENV = "RESEARCH_SESSION_ALLOW_LIVE"
LOOPBACK_NAMES = {"localhost"}


def _is_local(address):
    """Return True for loopback, localhost and non-IP (AF_UNIX) addresses."""
    if not isinstance(address, tuple) or not address:
        return True
    host = address[0]
    if host is None or host == "":
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    if host.casefold() in LOOPBACK_NAMES or host.casefold().endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _refuse(address):
    raise RuntimeError(
        "external network disabled in offline tests; attempted %r. "
        "Set %s=1 only for explicit live tests." % (address, ALLOW_LIVE_ENV)
    )


@pytest.fixture(autouse=True)
def offline_network_guard(monkeypatch):
    if os.environ.get(ALLOW_LIVE_ENV) == "1":
        yield
        return

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create_connection = socket.create_connection

    def guarded_connect(self, address):
        if not _is_local(address):
            _refuse(address)
        return original_connect(self, address)

    def guarded_connect_ex(self, address):
        if not _is_local(address):
            _refuse(address)
        return original_connect_ex(self, address)

    def guarded_create_connection(address, *args, **kwargs):
        if not _is_local(address):
            _refuse(address)
        return original_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    yield
