"""End-to-end order lifecycle tests against the FIX acceptor.

Each test drives a real TCP socket and asserts on the FIX fields that a
counterparty's conformance suite would check.
"""

from __future__ import annotations

import socket

import pytest

from fixengine import tags as T
from fixengine.initiator import next_cl_ord_id
from fixengine.message import SOH, FixMessage
from fixengine.enums import ExecType, MsgTypes, OrdStatus, OrdType, Side


# ---------------------------------------------------------------------------
# Session layer
# ---------------------------------------------------------------------------
def test_logon_round_trip(raw_client):
    """Logon (35=A) must be answered by a Logon echoing the heartbeat interval."""
    reply = raw_client.logon()

    assert reply.msg_type == MsgTypes.Logon
    assert reply.get(T.SenderCompID) == "BROKER"
    assert reply.get(T.TargetCompID) == "CLIENT"
    assert reply.get(T.EncryptMethod) == "0"
    assert reply.get_int(T.HeartBtInt) == raw_client.heartbeat_interval
    # The first message of a reset session is always MsgSeqNum 1.
    assert reply.get_int(T.MsgSeqNum) == 1

    raw_client.logout()


def test_test_request_is_answered_with_matching_heartbeat(client):
    """TestRequest (35=1) -> Heartbeat (35=0) echoing TestReqID (112).

    This is the liveness check every FIX engine relies on when a session goes
    quiet, and the echoed id is what correlates answer to question.
    """
    heartbeat = client.test_request("PING-42")

    assert heartbeat.msg_type == MsgTypes.Heartbeat
    assert heartbeat.get(T.TestReqID) == "PING-42"


def test_logout_round_trip(raw_client):
    """Logout (35=5) must be acknowledged before the socket goes away."""
    raw_client.logon()
    reply = raw_client.logout("End of demo")

    assert reply is not None
    assert reply.msg_type == MsgTypes.Logout


# ---------------------------------------------------------------------------
# Application layer - the core order flow
# ---------------------------------------------------------------------------
def test_new_order_single_returns_new_then_filled(client):
    """The canonical flow: 35=D in, two 35=8 out (39=0 then 39=2)."""
    cl_ord_id = next_cl_ord_id("DEMO")
    client.send_new_order_single(symbol="AAPL", side=Side.Buy, quantity=100,
                                 price=150.25, cl_ord_id=cl_ord_id)

    ack = client.await_execution_report(cl_ord_id, OrdStatus.New)
    fill = client.await_execution_report(cl_ord_id, OrdStatus.Filled)

    # --- acknowledgement: live on the book, nothing traded yet -------------
    assert ack.msg_type == MsgTypes.ExecutionReport
    assert ack.get(T.ExecType) == ExecType.New
    assert ack.get(T.OrdStatus) == OrdStatus.New
    assert ack.get(T.ClOrdID) == cl_ord_id
    assert ack.get(T.OrderID), "broker must assign an OrderID (37)"
    assert ack.get(T.ExecID), "every report needs a unique ExecID (17)"
    assert ack.get_float(T.CumQty) == 0.0
    assert ack.get_float(T.LeavesQty) == 100.0

    # --- fill: fully executed, nothing left open --------------------------
    assert fill.get(T.OrdStatus) == OrdStatus.Filled
    assert fill.get(T.ClOrdID) == cl_ord_id
    assert fill.get(T.OrderID) == ack.get(T.OrderID), "same order, same OrderID"
    assert fill.get(T.ExecID) != ack.get(T.ExecID), "ExecID is per report"
    assert fill.get(T.Symbol) == "AAPL"
    assert fill.get(T.Side) == Side.Buy
    assert fill.get_float(T.LastShares) == 100.0
    assert fill.get_float(T.LastPx) == 150.25
    assert fill.get_float(T.CumQty) == 100.0
    assert fill.get_float(T.LeavesQty) == 0.0
    assert fill.get_float(T.AvgPx) == 150.25
    assert fill.get(T.TransactTime), "TransactTime (60) is mandatory on 35=8"


def test_execute_order_helper_reports_sell_side(client):
    """A sell order echoes Side=2 back on both reports."""
    order, ack, fill = client.execute_order(
        symbol="MSFT", side=Side.Sell, quantity=250, price=410.5)

    assert order.get(T.Side) == Side.Sell
    assert ack.get(T.Side) == Side.Sell
    assert fill.get(T.Side) == Side.Sell
    assert fill.get_float(T.LastPx) == 410.5
    assert fill.get_float(T.CumQty) == 250.0


def test_market_order_fills_at_the_mock_reference_price(client):
    """A market order carries no Price(44); the venue supplies the fill price."""
    from fixengine.acceptor import DEFAULT_MARKET_PRICE

    order = client.send_new_order_single(symbol="TSLA", side=Side.Buy,
                                         quantity=50, price=None)
    assert order.get(T.OrdType) == OrdType.Market
    assert T.Price not in order, "a market order must not carry Price(44)"

    cl_ord_id = order.get(T.ClOrdID)
    client.await_execution_report(cl_ord_id, OrdStatus.New)
    fill = client.await_execution_report(cl_ord_id, OrdStatus.Filled)

    assert fill.get_float(T.LastPx) == DEFAULT_MARKET_PRICE
    assert fill.get_float(T.LeavesQty) == 0.0


def test_multiple_orders_get_distinct_ids_and_sequence_numbers(client):
    """Sequence numbers increase monotonically and ids never repeat."""
    results = [client.execute_order(symbol="IBM", side=Side.Buy,
                                    quantity=10 * (i + 1), price=100 + i)
               for i in range(3)]

    order_ids = {fill.get(T.OrderID) for _, _, fill in results}
    exec_ids = [r.get(T.ExecID) for _, ack, fill in results for r in (ack, fill)]
    seq_nums = [fill.get_int(T.MsgSeqNum) for _, _, fill in results]

    assert len(order_ids) == 3, "each order gets its own OrderID (37)"
    assert len(set(exec_ids)) == len(exec_ids), "ExecID (17) must be unique"
    assert seq_nums == sorted(seq_nums), "MsgSeqNum (34) must not go backwards"


def test_execution_report_matches_the_negotiated_fix_version(client):
    """The two FIX versions disagree about how a fill is expressed.

    FIX 4.2 requires ExecTransType(20) and reports a complete fill as
    ExecType(150)=2 (Fill).  FIX 4.4 dropped tag 20 entirely and collapsed
    Fill/PartialFill into ExecType=F (Trade), leaving OrdStatus(39) alone to
    say whether the order is done.  Sending 4.2 shapes on a 4.4 session is a
    routine cause of counterparty rejections.
    """
    _, ack, fill = client.execute_order(symbol="AAPL", side=Side.Buy,
                                        quantity=100, price=150.25)

    if client.begin_string == "FIX.4.2":
        assert ack.get(T.ExecTransType) == "0", "tag 20 is mandatory in FIX 4.2"
        assert fill.get(T.ExecType) == ExecType.Fill        # 150=2
    else:
        assert T.ExecTransType not in ack, "tag 20 was removed in FIX 4.4"
        assert fill.get(T.ExecType) == ExecType.Trade       # 150=F
    # OrdStatus means the same thing in both versions.
    assert fill.get(T.OrdStatus) == OrdStatus.Filled


def test_invalid_order_is_rejected_not_filled(client):
    """A malformed order comes back as 39=8 (Rejected), not as a fill.

    Rejections belong on the application layer: the session itself is healthy,
    so the venue answers with an ExecutionReport rather than a session Reject.
    """
    cl_ord_id = next_cl_ord_id("BAD")
    client.send_new_order_single(symbol="AAPL", side=Side.Buy, quantity=0,
                                 price=10.0, cl_ord_id=cl_ord_id)

    report = client.await_execution_report(cl_ord_id, OrdStatus.Rejected)

    assert report.get(T.ExecType) == ExecType.Rejected
    assert report.get(T.Text), "a rejection should explain itself in Text (58)"


def test_unsupported_message_type_is_business_rejected(client):
    """An application message the venue does not implement -> 35=j."""
    session = client.session
    cancel = session.build(MsgTypes.OrderCancelRequest)
    cancel.set(T.ClOrdID, next_cl_ord_id("CXL"))
    cancel.set(T.Symbol, "AAPL")
    cancel.set(T.Side, Side.Buy)
    sent = session.send(cancel)

    reject = session.receive_until(
        lambda m: m.msg_type == MsgTypes.BusinessMessageReject, timeout=5.0)

    assert reject.get(T.RefMsgType) == MsgTypes.OrderCancelRequest
    assert reject.get_int(T.RefSeqNum) == sent.get_int(T.MsgSeqNum)


# ---------------------------------------------------------------------------
# Wire-level checks - the bytes the counterparty actually sees
# ---------------------------------------------------------------------------
def test_execution_report_is_wire_valid(client):
    """Re-encoding a received report must reproduce a valid BodyLength and
    CheckSum, which is the strongest evidence that our codec is correct."""
    _, _, fill = client.execute_order(symbol="AAPL", side=Side.Buy,
                                      quantity=100, price=150.25)

    wire = fill.encode(client.begin_string)
    text = wire.decode()

    assert text.startswith(f"8={client.begin_string}{SOH}9=")
    assert text.endswith(SOH)
    assert f"{SOH}10=" in text
    # decode() validates the checksum, so a round trip proves the framing.
    assert FixMessage.decode(wire).get(T.ClOrdID) == fill.get(T.ClOrdID)


def test_session_log_records_pipe_delimited_fix(client):
    """The pipe-delimited log is part of the deliverable, so it is asserted on.

    Reading the file rather than stdout means this also proves the artifact CI
    uploads is genuinely useful, and it keeps working under ``pytest -s``.
    """
    log_path = client.log.path
    assert log_path, "the initiator fixture should log to a file"
    with open(log_path, encoding="utf-8") as handle:
        handle.seek(0, 2)
        offset = handle.tell()          # ignore traffic from earlier tests

    order, _, fill = client.execute_order(symbol="AAPL", side=Side.Buy,
                                          quantity=100, price=150.25)

    with open(log_path, encoding="utf-8") as handle:
        handle.seek(offset)
        written = handle.read()

    assert "|35=D|" in written, "the outbound order should be logged raw"
    assert f"11={order.get(T.ClOrdID)}|" in written
    assert "|35=8|" in written, "ExecutionReports should be logged raw"
    assert f"|31={fill.get(T.LastPx)}|" in written, "fill price should be visible"
    assert "ExecutionReport" in written, "the annotated form should name the message"


def test_no_response_when_nothing_is_sent(client):
    """A quiet session stays quiet: the acceptor must not invent traffic."""
    with pytest.raises(socket.timeout):
        client.session.receive(timeout=1.0)
