#!/usr/bin/env python3
"""Run the mock broker (FIX acceptor).

    python scripts/run_acceptor.py --port 9876 --fix-version FIX.4.2
"""

from __future__ import annotations

import argparse
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fixengine.acceptor import DEFAULT_HOST, DEFAULT_PORT, FixAcceptor


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="FIX acceptor (mock broker)")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--comp-id", default="BROKER",
                        help="our SenderCompID (49)")
    parser.add_argument("--fix-version", default="FIX.4.2",
                        choices=["FIX.4.2", "FIX.4.4"],
                        help="BeginString (8)")
    parser.add_argument("--heartbeat", type=int, default=30,
                        help="default HeartBtInt (108) in seconds")
    parser.add_argument("--log-file", default="logs/acceptor.log")
    parser.add_argument("--raw-only", action="store_true",
                        help="print only pipe-delimited FIX, no annotations")
    args = parser.parse_args(argv)

    if args.log_file:
        os.makedirs(os.path.dirname(os.path.abspath(args.log_file)), exist_ok=True)

    acceptor = FixAcceptor(host=args.host, port=args.port, comp_id=args.comp_id,
                           begin_string=args.fix_version,
                           heartbeat_interval=args.heartbeat,
                           log_path=args.log_file, annotate=not args.raw_only)

    def shutdown(signum, frame):
        acceptor.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    acceptor.start()
    try:
        acceptor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        acceptor.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
