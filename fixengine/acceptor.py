"""FIX Acceptor - a mock broker / exchange simulator.

In FIX terminology the **acceptor** listens for connections and the
**initiator** dials out; the buy-side firm is normally the initiator and the
broker is the acceptor.  This module is the broker side.

Behaviour implemented here:

* answers Logon (35=A) with a Logon, echoing the negotiated HeartBtInt;
* answers Heartbeat (35=0) and TestRequest (35=1), and emits its own
  Heartbeats when the session goes quiet;
* answers Logout (35=5) with a Logout and closes the connection;
* for every NewOrderSingle (35=D) emits two ExecutionReports (35=8):
  an acknowledgement (OrdStatus New, 39=0) and then a complete fill
  (OrdStatus Filled, 39=2).

The two-report sequence is the point: a real venue always acknowledges before
it fills, and an order management system that assumes a single terminal report
will lose track of working orders.
"""

from __future__ import annotations

import itertools
import socket
import threading
import time

from . import tags as T
from .logging_ import FixLogger
from .message import FixMessage, format_decimal, utc_timestamp
from .session import FixSession, SessionClosed
from .enums import ExecType, MsgTypes, OrdStatus, OrdType

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876

# Price used to fill market orders, which carry no Price(44) of their own.
# A real venue would cross against its book; a mock needs a deterministic
# number so the tests can assert on it.
DEFAULT_MARKET_PRICE = 100.00


class OrderBook:
    """Thread-safe id generation and order bookkeeping for the mock broker."""

    def __init__(self):
        self._order_ids = itertools.count(1)
        self._exec_ids = itertools.count(1)
        self._lock = threading.Lock()
        self.orders: dict[str, dict] = {}   # ClOrdID -> order snapshot

    def next_order_id(self) -> str:
        with self._lock:
            return f"BRK-{next(self._order_ids):06d}"

    def next_exec_id(self) -> str:
        with self._lock:
            return f"EXEC-{next(self._exec_ids):06d}"

    def record(self, cl_ord_id: str, snapshot: dict) -> None:
        with self._lock:
            self.orders[cl_ord_id] = snapshot


class FixAcceptor:
    """Threaded TCP listener speaking FIX 4.2 / 4.4."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 comp_id: str = "BROKER", begin_string: str = "FIX.4.2",
                 heartbeat_interval: int = 30, log_path: str | None = None,
                 annotate: bool = True):
        self.host = host
        self.port = port
        self.comp_id = comp_id
        self.begin_string = begin_string
        self.heartbeat_interval = heartbeat_interval
        self.log = FixLogger("ACCEPTOR", path=log_path, annotate=annotate)
        self.book = OrderBook()

        self._server: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> int:
        """Bind and listen.  Returns the bound port (useful when port=0)."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Without SO_REUSEADDR a restarted acceptor hits "address already in
        # use" for the duration of TIME_WAIT - painful during a live demo.
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(5)
        srv.settimeout(0.5)           # so serve_forever() can notice stop()
        self._server = srv
        self.port = srv.getsockname()[1]
        self.log.event(f"listening on {self.host}:{self.port} "
                       f"as {self.comp_id} ({self.begin_string})")
        return self.port

    def serve_forever(self) -> None:
        """Accept connections until :meth:`stop` is called."""
        if self._server is None:
            self.start()
        assert self._server is not None
        while not self._stop.is_set():
            try:
                conn, addr = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.log.event(f"connection accepted from {addr[0]}:{addr[1]}")
            thread = threading.Thread(
                target=self._serve_session, args=(conn,),
                name=f"fix-session-{addr[1]}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def run_in_background(self) -> threading.Thread:
        """Start the listener on a daemon thread (used by the test fixtures)."""
        if self._server is None:
            self.start()
        thread = threading.Thread(target=self.serve_forever,
                                  name="fix-acceptor", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        self.log.event("acceptor stopped")
        self.log.close()

    # -- one client session -------------------------------------------------
    def _serve_session(self, conn: socket.socket) -> None:
        session = FixSession(conn, sender_comp_id=self.comp_id,
                             target_comp_id="UNKNOWN", logger=self.log,
                             begin_string=self.begin_string)
        last_sent = time.monotonic()
        try:
            while not self._stop.is_set():
                try:
                    # Waking up on a timeout is what lets us emit our own
                    # Heartbeats: FIX requires traffic at least every
                    # HeartBtInt seconds or the peer will disconnect us.
                    msg = session.receive(timeout=1.0)
                except socket.timeout:
                    if (session.logged_on
                            and time.monotonic() - last_sent >= self.heartbeat_interval):
                        session.send(session.build(MsgTypes.Heartbeat))
                        last_sent = time.monotonic()
                    continue

                keep_going = self._dispatch(session, msg)
                last_sent = time.monotonic()
                if not keep_going:
                    break
        except SessionClosed as exc:
            self.log.event(f"session ended: {exc}")
        except Exception as exc:                      # pragma: no cover
            self.log.event(f"!! session error: {exc!r}")
        finally:
            session.close()
            self.log.event("session socket closed")

    def _dispatch(self, session: FixSession, msg: FixMessage) -> bool:
        """Handle one inbound message.  Returns False to end the session."""
        msg_type = msg.msg_type

        if msg_type == MsgTypes.Logon:
            self._on_logon(session, msg)
        elif msg_type == MsgTypes.Heartbeat:
            pass  # a Heartbeat needs no reply; receiving it is the point
        elif msg_type == MsgTypes.TestRequest:
            # The reply MUST echo TestReqID(112) so the peer can match it.
            reply = session.build(MsgTypes.Heartbeat)
            reply.set(T.TestReqID, msg.get(T.TestReqID))
            session.send(reply)
        elif msg_type == MsgTypes.Logout:
            self._on_logout(session, msg)
            return False
        elif msg_type == MsgTypes.NewOrderSingle:
            self._on_new_order_single(session, msg)
        else:
            self._on_unsupported(session, msg)
        return True

    # -- session-level handlers --------------------------------------------
    def _on_logon(self, session: FixSession, msg: FixMessage) -> None:
        """Accept the Logon and mirror the heartbeat interval back.

        A real acceptor authenticates here (CompID whitelist, optional
        Username/Password in 553/554, IP checks) and rejects with a Logout
        carrying Text(58) if anything fails.
        """
        session.target_comp_id = msg.get(T.SenderCompID, "CLIENT")
        negotiated = msg.get_int(T.HeartBtInt, self.heartbeat_interval)
        self.heartbeat_interval = negotiated

        # ResetSeqNumFlag(141)=Y asks both sides to restart numbering at 1.
        if msg.get(T.ResetSeqNumFlag) == "Y":
            session.out_seq_num = 1
            session.expected_in_seq_num = msg.get_int(T.MsgSeqNum, 1) + 1
            self.log.event("ResetSeqNumFlag=Y - sequence numbers reset to 1")

        reply = session.build(MsgTypes.Logon)
        reply.set(T.EncryptMethod, "0")          # 0 = None; TLS does the crypto
        reply.set(T.HeartBtInt, negotiated)
        if msg.get(T.ResetSeqNumFlag) == "Y":
            reply.set(T.ResetSeqNumFlag, "Y")
        session.send(reply)
        session.logged_on = True
        self.log.event(f"logon accepted for {session.target_comp_id} "
                       f"(HeartBtInt={negotiated}s)")

    def _on_logout(self, session: FixSession, msg: FixMessage) -> None:
        reply = session.build(MsgTypes.Logout)
        reply.set(T.Text, "Logout acknowledged")
        session.send(reply)
        session.logged_on = False
        self.log.event("logout acknowledged")

    def _on_unsupported(self, session: FixSession, msg: FixMessage) -> None:
        """Reject business messages we do not implement.

        BusinessMessageReject(35=j) is the correct answer to a *supported
        session* carrying an *unsupported application* message; session-level
        problems use Reject(35=3) instead.
        """
        reject = session.build(MsgTypes.BusinessMessageReject)
        reject.set(T.RefSeqNum, msg.get(T.MsgSeqNum))
        reject.set(T.RefMsgType, msg.msg_type)
        reject.set(T.BusinessRejectReason, "3")   # 3 = Unsupported message type
        reject.set(T.Text, f"MsgType {msg.msg_type} not supported by this mock")
        session.send(reject)

    # -- application-level handler -----------------------------------------
    def _on_new_order_single(self, session: FixSession, msg: FixMessage) -> None:
        """Acknowledge, then fully fill, an incoming order.

        The mock always fills in one print.  Partial fills would simply mean
        emitting extra reports with 39=1 (PartiallyFilled) and a non-zero
        LeavesQty before the terminal 39=2.
        """
        cl_ord_id = msg.get(T.ClOrdID)
        symbol = msg.get(T.Symbol)
        side = msg.get(T.Side)
        order_qty = msg.get_float(T.OrderQty, 0.0)
        ord_type = msg.get(T.OrdType, OrdType.Limit)
        price = msg.get_float(T.Price)

        # Minimal validation - a venue rejects rather than guesses.
        missing = [name for name, value in
                   (("ClOrdID(11)", cl_ord_id), ("Symbol(55)", symbol),
                    ("Side(54)", side)) if not value]
        if missing or order_qty <= 0:
            reason = ", ".join(missing) or "OrderQty(38) must be positive"
            self._reject_order(session, msg, f"Invalid order: {reason}")
            return

        fill_price = price if price is not None else DEFAULT_MARKET_PRICE
        if ord_type == OrdType.Market:
            fill_price = DEFAULT_MARKET_PRICE

        order_id = self.book.next_order_id()
        self.book.record(cl_ord_id, {
            "order_id": order_id, "symbol": symbol, "side": side,
            "qty": order_qty, "price": fill_price, "status": OrdStatus.New,
        })

        # 1) Acknowledgement: the order is live on the book, nothing traded.
        ack = self._execution_report(
            session, msg, order_id=order_id,
            exec_type=ExecType.New, ord_status=OrdStatus.New,
            last_qty=0.0, last_px=0.0,
            cum_qty=0.0, leaves_qty=order_qty, avg_px=0.0,
        )
        session.send(ack)
        self.log.event(f"order {cl_ord_id} acknowledged as {order_id}")

        # 2) Fill: the whole quantity traded, so LeavesQty drops to zero and
        #    OrdStatus becomes Filled - a terminal state for this order.
        fill = self._execution_report(
            session, msg, order_id=order_id,
            exec_type=self._fill_exec_type(), ord_status=OrdStatus.Filled,
            last_qty=order_qty, last_px=fill_price,
            cum_qty=order_qty, leaves_qty=0.0, avg_px=fill_price,
        )
        session.send(fill)
        self.book.orders[cl_ord_id]["status"] = OrdStatus.Filled
        self.log.event(f"order {cl_ord_id} filled {order_qty:g} @ {fill_price:g}")

    def _fill_exec_type(self) -> str:
        """FIX 4.2 reports a fill as ExecType=2 (Fill).  FIX 4.4 retired the
        separate Fill/PartialFill values in favour of ExecType=F (Trade),
        with OrdStatus(39) alone distinguishing partial from complete."""
        return ExecType.Fill if self.begin_string == "FIX.4.2" else ExecType.Trade

    def _execution_report(self, session: FixSession, order: FixMessage,
                          order_id: str, exec_type: str, ord_status: str,
                          last_qty: float, last_px: float, cum_qty: float,
                          leaves_qty: float, avg_px: float) -> FixMessage:
        """Build a 35=8 that echoes the order's identity back to the client."""
        report = session.build(MsgTypes.ExecutionReport)
        report.set(T.OrderID, order_id)
        report.set(T.ClOrdID, order.get(T.ClOrdID))
        report.set(T.ExecID, self.book.next_exec_id())
        if self.begin_string == "FIX.4.2":
            # ExecTransType(20) is mandatory in 4.2 and was removed in 4.4.
            report.set(T.ExecTransType, "0")      # 0 = New
        report.set(T.ExecType, exec_type)
        report.set(T.OrdStatus, ord_status)
        report.set(T.Symbol, order.get(T.Symbol))
        report.set(T.Side, order.get(T.Side))
        report.set(T.OrderQty, format_decimal(order.get_float(T.OrderQty, 0.0)))
        report.set(T.LastShares, format_decimal(last_qty))   # LastQty in FIX 4.4
        report.set(T.LastPx, format_decimal(last_px))
        report.set(T.LeavesQty, format_decimal(leaves_qty))
        report.set(T.CumQty, format_decimal(cum_qty))
        report.set(T.AvgPx, format_decimal(avg_px))
        if order.get(T.Price) is not None:
            report.set(T.Price, order.get(T.Price))
        report.set(T.TransactTime, utc_timestamp())
        return report

    def _reject_order(self, session: FixSession, order: FixMessage,
                      text: str) -> None:
        """An order the venue will not accept comes back as an
        ExecutionReport with OrdStatus=Rejected, not as a session Reject."""
        report = session.build(MsgTypes.ExecutionReport)
        report.set(T.OrderID, "NONE")
        report.set(T.ClOrdID, order.get(T.ClOrdID, "UNKNOWN"))
        report.set(T.ExecID, self.book.next_exec_id())
        if self.begin_string == "FIX.4.2":
            report.set(T.ExecTransType, "0")
        report.set(T.ExecType, ExecType.Rejected)
        report.set(T.OrdStatus, OrdStatus.Rejected)
        report.set(T.Symbol, order.get(T.Symbol, "N/A"))
        report.set(T.Side, order.get(T.Side, "1"))
        report.set(T.OrderQty, order.get(T.OrderQty, "0"))
        report.set(T.LeavesQty, "0")
        report.set(T.CumQty, "0")
        report.set(T.AvgPx, "0")
        report.set(T.OrdRejReason, "0")           # 0 = Broker option
        report.set(T.Text, text)
        report.set(T.TransactTime, utc_timestamp())
        session.send(report)
        self.log.event(f"order rejected: {text}")
