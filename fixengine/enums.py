"""Legal values for FIX enumerated fields.

Kept apart from :mod:`fixengine.tags` so the two never collide: ``tags.Side``
is the tag *number* 54, ``enums.Side.Buy`` is the *value* "1" that goes in it.
"""


# MsgType (tag 35) values.  Single characters are admin/common messages;
# lower-case letters were allocated later as the spec grew.
class MsgTypes:
    Heartbeat = "0"
    TestRequest = "1"
    ResendRequest = "2"
    Reject = "3"
    SequenceReset = "4"
    Logout = "5"
    ExecutionReport = "8"
    OrderCancelReject = "9"
    Logon = "A"
    NewOrderSingle = "D"
    OrderCancelRequest = "F"
    BusinessMessageReject = "j"


class ExecType:
    """Tag 150 - the *event* being reported."""
    New = "0"
    PartialFill = "1"     # FIX 4.2 only; 4.4 uses Trade ("F")
    Fill = "2"            # FIX 4.2 only; 4.4 uses Trade ("F")
    Cancelled = "4"
    Rejected = "8"
    Trade = "F"           # FIX 4.4 replacement for PartialFill/Fill


class OrdStatus:
    """Tag 39 - the *state* of the order after the event."""
    New = "0"
    PartiallyFilled = "1"
    Filled = "2"
    Cancelled = "4"
    Rejected = "8"


class Side:
    Buy = "1"
    Sell = "2"


class OrdType:
    Market = "1"
    Limit = "2"
