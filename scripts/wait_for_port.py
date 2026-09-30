#!/usr/bin/env python3
"""Block until a TCP port accepts connections (or give up).

CI needs this because starting the acceptor in the background returns long
before the listening socket is bound - racing straight into pytest produces a
flaky "connection refused".  Used instead of `nc`, which is not guaranteed to
be installed and whose flags differ between the BSD and GNU builds.

    python scripts/wait_for_port.py 127.0.0.1 9876 --timeout 30
"""

from __future__ import annotations

import argparse
import socket
import sys
import time


def wait_for_port(host: str, port: int, timeout: float = 30.0,
                  interval: float = 0.25) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(interval)
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Wait for a TCP port to open")
    parser.add_argument("host", nargs="?", default="127.0.0.1")
    parser.add_argument("port", nargs="?", type=int, default=9876)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    if wait_for_port(args.host, args.port, args.timeout):
        print(f"{args.host}:{args.port} is accepting connections")
        return 0
    print(f"timed out after {args.timeout:g}s waiting for "
          f"{args.host}:{args.port}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
