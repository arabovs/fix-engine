"""FIX Initiator - the client side (buy-side / order sender).

The initiator dials the acceptor, logs on, sends orders and consumes the
resulting ExecutionReports.  It is deliberately synchronous: an interview demo
and a pytest assertion both read far better as a straight line of
send-then-expect than as a callback soup.
"""

from __future__ import annotations

import itertools
import socket
import time

from . import tags as T
from .logging_ import FixLogger
from .message import FixMessage, format_decimal, utc_timestamp
from .session import FixSession, SessionClosed
from .enums import MsgTypes, OrdStatus, OrdType, Side

_clord_counter = itertools.count(1)


def next_cl_ord_id(prefix: str = "ORD") -> str:
    """ClOrdID(11) must be unique per session per day; the timestamp keeps
    reruns of the demo from colliding with each other."""
    return f"{prefix}{int(time.time()) % 100000}{next(_clord_counter):03d}"


class FixInitiator:
    """A FIX client session against a single acceptor."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9876,
                 comp_id: str = "CLIENT", target_comp_id: str = "BROKER",
                 begin_string: str = "FIX.4.2", heartbeat_interval: int = 30,
                 log_path: str | None = None, annotate: bool = True,
                 connect_timeout: float = 5.0):
        self.host = host
        self.port = port
        self.comp_id = comp_id
        self.target_comp_id = target_comp_id
        self.begin_string = begin_string
        self.heartbeat_interval = heartbeat_interval
        self.connect_timeout = connect_timeout
        self.log = FixLogger("INITIATOR", path=log_path, annotate=annotate)
        self.session: FixSession | None = None

    # -- connection ---------------------------------------------------------
    def connect(self) -> "FixInitiator":
        sock = socket.create_connection((self.host, self.port),
                                        timeout=self.connect_timeout)
        # Orders are small and latency-sensitive: Nagle's algorithm would
        # hold a message back waiting for more data to coalesce, which is
        # exactly the wrong trade-off on an order entry socket.
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.session = FixSession(sock, sender_comp_id=self.comp_id,
                                  target_comp_id=self.target_comp_id,
                                  logger=self.log,
                                  begin_string=self.begin_string)
        self.log.event(f"connected to {self.host}:{self.port}")
        return self

    def _require_session(self) -> FixSession:
        if self.session is None:
            raise RuntimeError("not connected - call connect() first")
        return self.session

    # -- session layer ------------------------------------------------------
    def logon(self, reset_seq_num: bool = True, timeout: float = 5.0) -> FixMessage:
        """Send Logon (35=A) and wait for the acceptor's Logon back.

        Until that round trip completes, no application message may be sent:
        the counterparty is entitled to ignore or reject anything that arrives
        before the session is established.
        """
        session = self._require_session()
        logon = session.build(MsgTypes.Logon)
        logon.set(T.EncryptMethod, "0")            # 0 = None
        logon.set(T.HeartBtInt, self.heartbeat_interval)
        if reset_seq_num:
            logon.set(T.ResetSeqNumFlag, "Y")
        session.send(logon)

        reply = session.receive_until(
            lambda m: m.msg_type in (MsgTypes.Logon, MsgTypes.Logout),
            timeout=timeout)
        if reply.msg_type == MsgTypes.Logout:
            raise SessionClosed(
                f"logon rejected: {reply.get(T.Text, 'no reason given')}")
        session.logged_on = True
        self.log.event("logon complete")
        return reply

    def heartbeat(self, test_req_id: str | None = None) -> FixMessage:
        """Send a Heartbeat (35=0), optionally answering a TestRequest."""
        session = self._require_session()
        msg = session.build(MsgTypes.Heartbeat)
        if test_req_id:
            msg.set(T.TestReqID, test_req_id)
        return session.send(msg)

    def test_request(self, test_req_id: str = "TEST1",
                     timeout: float = 5.0) -> FixMessage:
        """Send a TestRequest (35=1) and wait for the Heartbeat echoing it.

        This is how a FIX engine proves the session is still alive when no
        traffic has flowed for longer than the heartbeat interval.
        """
        session = self._require_session()
        msg = session.build(MsgTypes.TestRequest)
        msg.set(T.TestReqID, test_req_id)
        session.send(msg)
        return session.receive_until(
            lambda m: (m.msg_type == MsgTypes.Heartbeat
                       and m.get(T.TestReqID) == test_req_id),
            timeout=timeout)

    def logout(self, text: str = "Normal logout",
               timeout: float = 5.0) -> FixMessage | None:
        """Send Logout (35=5) and wait for the acknowledging Logout.

        A clean logout matters: dropping the TCP connection instead leaves the
        counterparty's sequence numbers in an unresolved state.
        """
        session = self._require_session()
        msg = session.build(MsgTypes.Logout)
        msg.set(T.Text, text)
        session.send(msg)
        try:
            reply = session.receive_until(
                lambda m: m.msg_type == MsgTypes.Logout, timeout=timeout)
        except (socket.timeout, SessionClosed):
            self.log.event("no Logout acknowledgement - peer closed first")
            reply = None
        session.logged_on = False
        return reply

    def disconnect(self) -> None:
        if self.session is not None:
            self.session.close()
            self.session = None
        self.log.event("disconnected")
        self.log.close()

    # -- application layer --------------------------------------------------
    def send_new_order_single(self, symbol: str, side: str, quantity,
                              price=None, ord_type: str | None = None,
                              cl_ord_id: str | None = None,
                              time_in_force: str = "0") -> FixMessage:
        """Send a NewOrderSingle (35=D).

        Returns the message as sent (header included), so the caller can read
        back the generated ClOrdID and correlate the ExecutionReports.
        """
        session = self._require_session()
        if ord_type is None:
            ord_type = OrdType.Limit if price is not None else OrdType.Market

        order = session.build(MsgTypes.NewOrderSingle)
        order.set(T.ClOrdID, cl_ord_id or next_cl_ord_id())
        order.set(T.Symbol, symbol)
        order.set(T.Side, side)
        order.set(T.TransactTime, utc_timestamp())
        order.set(T.OrderQty, format_decimal(quantity))
        order.set(T.OrdType, ord_type)
        if price is not None:
            # Price(44) is required for a limit order and must be absent on a
            # market order; sending it anyway is a common rejection cause.
            order.set(T.Price, format_decimal(price))
        order.set(T.TimeInForce, time_in_force)
        return session.send(order)

    def await_execution_report(self, cl_ord_id: str,
                               ord_status: str | None = None,
                               timeout: float = 5.0) -> FixMessage:
        """Wait for the next ExecutionReport (35=8) for ``cl_ord_id``.

        Heartbeats and TestRequests may be interleaved with business traffic,
        so they are answered rather than treated as a protocol error.
        """
        session = self._require_session()

        def wanted(msg: FixMessage) -> bool:
            return (msg.msg_type == MsgTypes.ExecutionReport
                    and msg.get(T.ClOrdID) == cl_ord_id
                    and (ord_status is None or msg.get(T.OrdStatus) == ord_status))

        def handle_admin(msg: FixMessage) -> None:
            if msg.msg_type == MsgTypes.TestRequest:
                self.heartbeat(msg.get(T.TestReqID))

        return session.receive_until(wanted, timeout=timeout,
                                     on_skip=handle_admin)

    def execute_order(self, symbol: str, side: str = Side.Buy, quantity=100,
                      price=None, timeout: float = 5.0):
        """Convenience: send an order and collect the ack and the fill.

        Returns ``(sent_order, ack_report, fill_report)``.
        """
        order = self.send_new_order_single(symbol, side, quantity, price)
        cl_ord_id = order.get(T.ClOrdID)
        ack = self.await_execution_report(cl_ord_id, OrdStatus.New, timeout)
        fill = self.await_execution_report(cl_ord_id, OrdStatus.Filled, timeout)
        return order, ack, fill

    # -- context manager ----------------------------------------------------
    def __enter__(self) -> "FixInitiator":
        self.connect()
        self.logon()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self.session is not None and self.session.logged_on:
                self.logout()
        finally:
            self.disconnect()
