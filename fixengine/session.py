"""Session layer: sequence numbers, the standard header, and socket framing.

FIX splits cleanly into two layers, and this module is the lower one:

* the **session layer** (Logon, Heartbeat, TestRequest, Logout, sequence
  numbers, resend) guarantees an ordered, gap-detected, authenticated stream
  between two CompIDs;
* the **application layer** (NewOrderSingle, ExecutionReport, ...) carries the
  business intent and assumes the session layer already did its job.

:class:`FixSession` wraps a connected socket and owns the session-layer
bookkeeping so the acceptor and initiator can talk in whole messages.
"""

from __future__ import annotations

import socket
import threading

from . import tags as T
from .logging_ import IN, OUT, FixLogger
from .message import FixMessage, FixStreamReader, utc_timestamp


class SessionClosed(Exception):
    """The peer closed the TCP connection (or we did)."""


class FixSession:
    """One FIX session over one TCP connection.

    Sequence numbers are per-session *and* per-direction: each side counts its
    own outbound messages starting at 1, and checks that inbound numbers arrive
    without gaps.  A gap means messages were lost and must be resent - we
    detect and report it rather than silently carrying on with bad state.
    """

    def __init__(self, sock: socket.socket, sender_comp_id: str,
                 target_comp_id: str, logger: FixLogger,
                 begin_string: str = "FIX.4.2"):
        self.sock = sock
        self.sender_comp_id = sender_comp_id
        self.target_comp_id = target_comp_id
        self.begin_string = begin_string
        self.log = logger

        self.out_seq_num = 1          # next MsgSeqNum we will send
        self.expected_in_seq_num = 1  # next MsgSeqNum we expect to receive
        self.logged_on = False

        self._reader = FixStreamReader()
        self._inbox: list[FixMessage] = []
        self._send_lock = threading.Lock()  # heartbeat timer vs. app thread

    # -- outbound -----------------------------------------------------------
    def build(self, msg_type: str) -> FixMessage:
        """Start a message with MsgType set; the header is added on send()."""
        return FixMessage().set(T.MsgType, msg_type)

    def send(self, msg: FixMessage) -> FixMessage:
        """Stamp the standard header, encode, and write to the socket.

        SenderCompID/TargetCompID/MsgSeqNum/SendingTime are applied here so no
        caller can forget them or get the sequence number out of step.

        Returns the message exactly as it went on the wire, including the
        generated BodyLength(9) and CheckSum(10).
        """
        with self._send_lock:
            header = FixMessage()
            header.set(T.MsgType, msg.msg_type)
            header.set(T.SenderCompID, self.sender_comp_id)
            header.set(T.TargetCompID, self.target_comp_id)
            header.set(T.MsgSeqNum, self.out_seq_num)
            header.set(T.SendingTime, utc_timestamp())
            for tag, value in msg.fields:
                if tag != T.MsgType:
                    header.set(tag, value)

            wire = header.encode(self.begin_string)
            try:
                self.sock.sendall(wire)
            except OSError as exc:
                raise SessionClosed(f"send failed: {exc}") from exc
            self.out_seq_num += 1

        # Log the decoded wire form rather than the message we assembled, so
        # outbound and inbound lines are byte-for-byte comparable with the
        # counterparty's own log - BeginString, BodyLength and CheckSum
        # included.  Decoding also re-validates the checksum we just produced.
        sent = FixMessage.decode(wire)
        self.log.message(OUT, sent)
        return sent

    # -- inbound ------------------------------------------------------------
    def receive(self, timeout: float | None = 5.0) -> FixMessage:
        """Return the next inbound message, blocking up to ``timeout`` seconds.

        Raises :class:`socket.timeout` if nothing arrives in time and
        :class:`SessionClosed` when the peer hangs up.
        """
        if self._inbox:
            return self._pop()

        self.sock.settimeout(timeout)
        while not self._inbox:
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                raise
            except OSError as exc:
                raise SessionClosed(f"recv failed: {exc}") from exc
            if not chunk:
                raise SessionClosed("peer closed the connection")
            self._inbox.extend(self._reader.feed(chunk))
        return self._pop()

    def receive_until(self, predicate, timeout: float = 5.0,
                      on_skip=None) -> FixMessage:
        """Read messages until ``predicate(msg)`` is true or ``timeout`` expires.

        Application code cares about ExecutionReports, not about the
        Heartbeats that may be interleaved with them, so the caller passes a
        predicate and optionally an ``on_skip`` hook for the rest.
        """
        import time
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout(
                    f"no matching message within {timeout:.1f}s")
            msg = self.receive(timeout=remaining)
            if predicate(msg):
                return msg
            if on_skip:
                on_skip(msg)

    def _pop(self) -> FixMessage:
        msg = self._inbox.pop(0)
        self.log.message(IN, msg)
        self._check_sequence(msg)
        return msg

    def _check_sequence(self, msg: FixMessage) -> None:
        """Detect sequence gaps.  A production engine would issue a
        ResendRequest (35=2) here; a mock logs loudly and keeps going."""
        seq = msg.get_int(T.MsgSeqNum)
        if seq is None:
            return
        if seq > self.expected_in_seq_num:
            self.log.event(
                f"!! sequence gap: expected {self.expected_in_seq_num}, got {seq}"
                " (a production engine would send a ResendRequest 35=2)")
        self.expected_in_seq_num = max(self.expected_in_seq_num, seq) + 1

    # -- lifecycle ----------------------------------------------------------
    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        finally:
            self.sock.close()
            self.logged_on = False
