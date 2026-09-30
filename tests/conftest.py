"""Shared pytest fixtures.

The integration tests run in either of two topologies:

* **standalone** - the fixture starts a :class:`FixAcceptor` in-process on an
  ephemeral port.  ``pytest`` alone is enough, which is what you want locally;
* **against an external acceptor** - if ``FIX_PORT`` names a port that is
  already listening, the tests connect to that instead.  This is what CI does,
  so the pipeline exercises a genuinely separate process over a real socket.
"""

from __future__ import annotations

import os
import socket
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fixengine.acceptor import FixAcceptor
from fixengine.initiator import FixInitiator

FIX_VERSION = os.environ.get("FIX_VERSION", "FIX.4.2")
LOG_DIR = os.environ.get("FIX_LOG_DIR", "logs")


def _port_is_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def acceptor_endpoint():
    """Yield ``(host, port)`` for a running acceptor, starting one if needed."""
    host = os.environ.get("FIX_HOST", "127.0.0.1")
    port = int(os.environ.get("FIX_PORT", "9876"))

    if _port_is_open(host, port):
        # An acceptor is already running (CI starts one in the background).
        yield host, port
        return

    os.makedirs(LOG_DIR, exist_ok=True)
    acceptor = FixAcceptor(host=host, port=0,           # 0 = pick a free port
                           begin_string=FIX_VERSION,
                           heartbeat_interval=30,
                           log_path=os.path.join(LOG_DIR, "acceptor.log"))
    bound_port = acceptor.start()
    acceptor.run_in_background()
    try:
        yield host, bound_port
    finally:
        acceptor.stop()


@pytest.fixture
def client(acceptor_endpoint, request):
    """A connected, logged-on initiator that logs out on teardown."""
    host, port = acceptor_endpoint
    os.makedirs(LOG_DIR, exist_ok=True)
    initiator = FixInitiator(
        host=host, port=port, comp_id="CLIENT", target_comp_id="BROKER",
        begin_string=FIX_VERSION,
        log_path=os.path.join(LOG_DIR, "initiator.log"))
    initiator.connect()
    initiator.logon()
    yield initiator
    # Teardown mirrors the protocol: Logout, then close the socket.  Leaving a
    # session half-open would leak the acceptor thread across tests.
    try:
        if initiator.session is not None and initiator.session.logged_on:
            initiator.logout()
    finally:
        initiator.disconnect()


@pytest.fixture
def raw_client(acceptor_endpoint):
    """An initiator that is connected but *not* logged on, for tests that
    drive the session layer themselves."""
    host, port = acceptor_endpoint
    os.makedirs(LOG_DIR, exist_ok=True)
    initiator = FixInitiator(
        host=host, port=port, begin_string=FIX_VERSION,
        log_path=os.path.join(LOG_DIR, "initiator.log"))
    initiator.connect()
    yield initiator
    initiator.disconnect()
