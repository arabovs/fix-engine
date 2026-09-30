"""Unit tests for the codec: framing, BodyLength, CheckSum, stream reassembly.

These run without a socket and are where a codec bug should surface first.
"""

from __future__ import annotations

import pytest

from fixengine import tags as T
from fixengine.message import (SOH, FixMessage, FixParseError, FixStreamReader,
                               utc_timestamp)

# A known-good FIX 4.2 NewOrderSingle, pipe-delimited for readability.
SAMPLE = ("8=FIX.4.2|9=74|35=D|49=CLIENT|56=BROKER|34=2|"
          "52=20240101-12:00:00.000|11=ORD123|55=AAPL|54=1|38=100|10=000|")


def test_encode_computes_body_length_and_checksum():
    msg = FixMessage()
    msg.set(T.MsgType, "D").set(T.SenderCompID, "CLIENT")
    msg.set(T.TargetCompID, "BROKER").set(T.MsgSeqNum, 2)
    msg.set(T.SendingTime, "20240101-12:00:00.000")
    msg.set(T.ClOrdID, "ORD123").set(T.Symbol, "AAPL")

    text = msg.encode("FIX.4.2").decode()
    body = text.split(f"{SOH}", 2)[2]           # everything after 8= and 9=
    body_without_trailer = body[: body.rfind("10=")]

    stated_length = int(text.split(f"{SOH}")[1].split("=")[1])
    assert stated_length == len(body_without_trailer.encode())

    # The checksum covers every byte up to and including the SOH before "10=".
    covered = text[: text.rfind(f"{SOH}10=") + 1].encode()
    assert text.endswith(f"10={sum(covered) % 256:03d}{SOH}")


def test_encode_puts_the_framing_fields_where_the_spec_requires():
    wire = FixMessage([(T.ClOrdID, "X1"), (T.MsgType, "D")]).encode("FIX.4.4")
    tags = [f.split("=")[0] for f in wire.decode().split(SOH) if f]

    assert tags[0] == "8"      # BeginString first
    assert tags[1] == "9"      # BodyLength second
    assert tags[2] == "35"     # MsgType third, even though it was set last
    assert tags[-1] == "10"    # CheckSum last


def test_encode_rejects_a_message_without_msg_type():
    with pytest.raises(FixParseError, match="MsgType"):
        FixMessage([(T.ClOrdID, "X1")]).encode()


def test_decode_round_trips_through_the_wire():
    original = FixMessage([(T.MsgType, "D"), (T.ClOrdID, "ORD1"),
                           (T.Symbol, "AAPL"), (T.OrderQty, "100")])
    decoded = FixMessage.decode(original.encode("FIX.4.2"))

    assert decoded.msg_type == "D"
    assert decoded.get(T.ClOrdID) == "ORD1"
    assert decoded.get_int(T.OrderQty) == 100
    assert decoded.get(T.BeginString) == "FIX.4.2"


def test_decode_accepts_pipe_delimited_text_for_fixtures():
    msg = FixMessage.decode(SAMPLE, validate_checksum=False)

    assert msg.msg_type == "D"
    assert msg.get(T.Symbol) == "AAPL"
    assert msg.get(T.Side) == "1"


def test_decode_detects_a_corrupt_checksum():
    wire = FixMessage([(T.MsgType, "0")]).encode().decode()
    corrupt = wire[: wire.rfind("10=")] + f"10=999{SOH}"

    with pytest.raises(FixParseError, match="checksum"):
        FixMessage.decode(corrupt)


def test_decode_rejects_a_non_numeric_tag():
    with pytest.raises(FixParseError, match="non-numeric"):
        FixMessage.decode(f"8=FIX.4.2{SOH}bogus=1{SOH}", validate_checksum=False)


def test_to_pipe_is_the_human_readable_log_format():
    msg = FixMessage([(T.MsgType, "D"), (T.ClOrdID, "ORD123")])
    assert msg.to_pipe() == "35=D|11=ORD123|"


def test_get_all_preserves_repeating_field_order():
    msg = FixMessage([(T.MsgType, "8"), (T.Symbol, "A"), (T.Symbol, "B")])
    assert msg.get_all(T.Symbol) == ["A", "B"]


def test_utc_timestamp_has_millisecond_precision():
    stamp = utc_timestamp()
    date, _, clock = stamp.partition("-")

    assert len(date) == 8 and date.isdigit()
    assert len(clock) == len("HH:MM:SS.mmm")
    assert clock[8] == "."


# ---------------------------------------------------------------------------
# Stream reassembly - TCP does not preserve message boundaries
# ---------------------------------------------------------------------------
def _wire(cl_ord_id: str) -> bytes:
    return FixMessage([(T.MsgType, "D"), (T.ClOrdID, cl_ord_id)]).encode()


def test_reader_returns_nothing_until_a_message_is_complete():
    reader = FixStreamReader()
    wire = _wire("ORD1")

    assert reader.feed(wire[:10]) == []
    assert reader.feed(wire[10:-1]) == []       # one byte short
    assert [m.get(T.ClOrdID) for m in reader.feed(wire[-1:])] == ["ORD1"]


def test_reader_splits_several_messages_from_one_read():
    reader = FixStreamReader()
    blob = _wire("ORD1") + _wire("ORD2") + _wire("ORD3")

    got = reader.feed(blob)

    assert [m.get(T.ClOrdID) for m in got] == ["ORD1", "ORD2", "ORD3"]
    assert reader.pending == b""


def test_reader_handles_a_message_split_across_arbitrary_chunks():
    reader = FixStreamReader()
    blob = _wire("ORD1") + _wire("ORD2")
    received = []

    for i in range(0, len(blob), 7):            # deliberately ragged chunks
        received.extend(reader.feed(blob[i:i + 7]))

    assert [m.get(T.ClOrdID) for m in received] == ["ORD1", "ORD2"]


def test_reader_resynchronises_after_leading_garbage():
    reader = FixStreamReader()

    got = reader.feed(b"noise-before-the-session" + _wire("ORD9"))

    assert [m.get(T.ClOrdID) for m in got] == ["ORD9"]


@pytest.mark.parametrize("value,expected", [
    (100, "100"), (100.0, "100"), (150.25, "150.25"), ("42", "42"),
    (0, "0"), (0.5, "0.5"), ("MKT", "MKT"), (None, "None"),
])
def test_format_decimal_renders_fix_numbers_as_text(value, expected):
    from fixengine.message import format_decimal
    assert format_decimal(value) == expected
