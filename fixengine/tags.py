"""FIX tag numbers used by this engine.

FIX is a tag=value protocol: every field is an integer tag, an '=', and a
value, terminated by SOH (ASCII 0x01).  Tag numbers below 5000 are defined by
the FIX specification; this module only declares the subset the engine needs.

Field *values* (what a tag may legally contain) live in :mod:`fixengine.enums`,
kept separate so ``tags.Side`` is unambiguously the number 54 while
``enums.Side.Buy`` is unambiguously the value "1".

Keeping the numbers in one place means the rest of the codebase reads like the
protocol documentation (``msg.get(T.ClOrdID)``) instead of like magic numbers.
"""

# --- Standard header -------------------------------------------------------
BeginString = 8        # "FIX.4.2" / "FIX.4.4" - always the first field
BodyLength = 9         # byte count of the body - always the second field
MsgType = 35           # message type discriminator, always the third field
SenderCompID = 49      # who sent this message
TargetCompID = 56      # who it is addressed to
MsgSeqNum = 34         # per-session, per-direction sequence number (starts at 1)
SendingTime = 52       # UTC timestamp the message left the sender
PossDupFlag = 43       # Y when a message is a possible duplicate (resends)

# --- Standard trailer ------------------------------------------------------
CheckSum = 10          # modulo-256 checksum, always the last field

# --- Session level (admin) messages ---------------------------------------
HeartBtInt = 108       # heartbeat interval in seconds, negotiated on Logon
EncryptMethod = 98     # 0 = None. Encryption is handled by TLS in practice.
ResetSeqNumFlag = 141  # Y = both sides reset sequence numbers to 1
TestReqID = 112        # echoed back in the Heartbeat answering a TestRequest
Text = 58              # free-form human readable text (reject reasons, etc.)
RefSeqNum = 45         # sequence number of the message being referenced
RefMsgType = 372       # MsgType of the message being referenced
BusinessRejectReason = 380

# --- NewOrderSingle / ExecutionReport -------------------------------------
ClOrdID = 11           # client-assigned order id, unique per day per session
OrderID = 37           # broker-assigned order id
ExecID = 17            # unique id of this execution report
ExecTransType = 20     # FIX 4.2 only: 0 = New. Removed in FIX 4.4.
ExecType = 150         # what this report *is* (new, fill, cancel, ...)
OrdStatus = 39         # what the order *is* now (new, partially filled, ...)
Symbol = 55            # instrument, e.g. "AAPL"
Side = 54              # 1 = Buy, 2 = Sell
OrderQty = 38          # quantity ordered
OrdType = 40           # 1 = Market, 2 = Limit
Price = 44             # limit price (absent on market orders)
TimeInForce = 59       # 0 = Day, 1 = GTC, 3 = IOC ...
TransactTime = 60      # when the business transaction occurred
LastShares = 32        # quantity of *this* fill (renamed LastQty in FIX 4.4)
LastPx = 31            # price of *this* fill
LeavesQty = 151        # quantity still open on the book
CumQty = 14            # quantity filled so far across all fills
AvgPx = 6              # average price across all fills
OrdRejReason = 103

# Human readable names, used only by the pretty-printer in fixengine.logging_.
TAG_NAMES = {
    6: "AvgPx", 8: "BeginString", 9: "BodyLength", 10: "CheckSum",
    11: "ClOrdID", 14: "CumQty", 17: "ExecID", 20: "ExecTransType",
    31: "LastPx", 32: "LastShares", 34: "MsgSeqNum", 35: "MsgType",
    37: "OrderID", 38: "OrderQty", 39: "OrdStatus", 40: "OrdType",
    43: "PossDupFlag", 44: "Price", 45: "RefSeqNum", 49: "SenderCompID",
    52: "SendingTime", 54: "Side", 55: "Symbol", 56: "TargetCompID",
    58: "Text", 59: "TimeInForce", 60: "TransactTime", 98: "EncryptMethod",
    103: "OrdRejReason", 108: "HeartBtInt", 112: "TestReqID",
    141: "ResetSeqNumFlag", 150: "ExecType", 151: "LeavesQty",
    372: "RefMsgType", 380: "BusinessRejectReason",
}

# Enum value -> label, for the annotated log format.
VALUE_NAMES = {
    35: {"0": "Heartbeat", "1": "TestRequest", "2": "ResendRequest",
         "3": "Reject", "4": "SequenceReset", "5": "Logout",
         "8": "ExecutionReport", "9": "OrderCancelReject", "A": "Logon",
         "D": "NewOrderSingle", "F": "OrderCancelRequest",
         "j": "BusinessMessageReject"},
    39: {"0": "New", "1": "PartiallyFilled", "2": "Filled", "4": "Cancelled",
         "8": "Rejected"},
    150: {"0": "New", "1": "PartialFill", "2": "Fill", "4": "Cancelled",
          "8": "Rejected", "F": "Trade"},
    54: {"1": "Buy", "2": "Sell"},
    40: {"1": "Market", "2": "Limit", "3": "Stop", "4": "StopLimit"},
    59: {"0": "Day", "1": "GTC", "3": "IOC", "4": "FOK"},
    20: {"0": "New", "1": "Cancel", "2": "Correct", "3": "Status"},
}
