"""Console/file logging for FIX sessions.

Two formats are emitted for every message:

* the **raw** pipe-delimited form, which is what you paste into a support
  ticket or compare against a counterparty's log;
* an **annotated** form that names each tag and decodes the enums, which is
  what makes a live demo readable.

Named ``logging_`` with a trailing underscore so it never shadows the
standard library ``logging`` module for anything else on the path.
"""

from __future__ import annotations

import sys
import threading

from .message import FixMessage
from .tags import TAG_NAMES, VALUE_NAMES

# Direction markers, borrowed from the convention used by most FIX engines.
OUT = "-->"   # we sent it
IN = "<--"    # we received it

_write_lock = threading.Lock()   # sessions run on their own threads


class FixLogger:
    """Writes session traffic to stdout and, optionally, to a log file."""

    def __init__(self, name: str, path: str | None = None, annotate: bool = True,
                 stream=None):
        self.name = name
        self.path = path
        self.annotate = annotate
        # Resolved lazily at write time, not captured here: pytest swaps
        # sys.stdout out from under us, and a logger built during fixture
        # setup would otherwise keep writing to the pre-capture stream.
        self._stream = stream
        self._file = open(path, "a", buffering=1, encoding="utf-8") if path else None

    # -- plumbing -----------------------------------------------------------
    def _emit(self, line: str) -> None:
        stream = self._stream if self._stream is not None else sys.stdout
        with _write_lock:
            print(line, file=stream, flush=True)
            if self._file:
                self._file.write(line + "\n")

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None

    # -- public API ---------------------------------------------------------
    def event(self, text: str) -> None:
        """Log a non-protocol event (bind, accept, disconnect, ...)."""
        self._emit(f"[{self.name}] {text}")

    def message(self, direction: str, msg: FixMessage) -> None:
        """Log one FIX message in raw and annotated form."""
        self._emit(f"[{self.name}] {direction} {msg.to_pipe()}")
        if self.annotate:
            self._emit(f"[{self.name}]     {annotate(msg)}")


def annotate(msg: FixMessage) -> str:
    """``MsgType(35)=D[NewOrderSingle] ClOrdID(11)=ORD123 ...``"""
    parts = []
    for tag, value in msg.fields:
        name = TAG_NAMES.get(tag, "?")
        label = VALUE_NAMES.get(tag, {}).get(value)
        rendered = f"{value}[{label}]" if label else value
        parts.append(f"{name}({tag})={rendered}")
    return " ".join(parts)
