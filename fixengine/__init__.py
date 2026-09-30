"""A dependency-free FIX 4.2 / 4.4 engine: codec, session layer, acceptor and
initiator.

    from fixengine import FixAcceptor, FixInitiator

Everything is plain sockets and the standard library, so the whole thing runs
anywhere Python does - including a CI runner with no build toolchain.
"""

from .acceptor import DEFAULT_HOST, DEFAULT_PORT, FixAcceptor
from .initiator import FixInitiator, next_cl_ord_id
from .message import SOH, FixMessage, FixParseError, FixStreamReader, utc_timestamp
from .session import FixSession, SessionClosed
from .enums import ExecType, MsgTypes, OrdStatus, OrdType, Side

__version__ = "1.0.0"

__all__ = [
    "FixAcceptor", "FixInitiator", "FixSession", "FixMessage",
    "FixStreamReader", "FixParseError", "SessionClosed", "MsgTypes",
    "ExecType", "OrdStatus", "Side", "OrdType", "SOH", "utc_timestamp",
    "next_cl_ord_id", "DEFAULT_HOST", "DEFAULT_PORT", "__version__",
]
