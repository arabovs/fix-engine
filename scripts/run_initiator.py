#!/usr/bin/env python3
"""Run a demo FIX client against the acceptor.

    python scripts/run_initiator.py --symbol AAPL --side buy --qty 100 --price 150.25

Walks the full lifecycle - Logon, NewOrderSingle, ExecutionReport(New),
ExecutionReport(Filled), TestRequest/Heartbeat, Logout - printing every
message in both raw and annotated form.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fixengine import tags as T
from fixengine.initiator import FixInitiator
from fixengine.enums import Side

SIDES = {"buy": Side.Buy, "sell": Side.Sell}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="FIX initiator (demo client)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9876)
    parser.add_argument("--comp-id", default="CLIENT")
    parser.add_argument("--target-comp-id", default="BROKER")
    parser.add_argument("--fix-version", default="FIX.4.2",
                        choices=["FIX.4.2", "FIX.4.4"])
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--side", default="buy", choices=sorted(SIDES))
    parser.add_argument("--qty", type=float, default=100)
    parser.add_argument("--price", type=float, default=None,
                        help="limit price; omit to send a market order")
    parser.add_argument("--orders", type=int, default=1,
                        help="how many orders to send")
    parser.add_argument("--test-request", action="store_true",
                        help="also exercise TestRequest/Heartbeat")
    parser.add_argument("--log-file", default="logs/initiator.log")
    parser.add_argument("--raw-only", action="store_true")
    args = parser.parse_args(argv)

    if args.log_file:
        os.makedirs(os.path.dirname(os.path.abspath(args.log_file)), exist_ok=True)

    client = FixInitiator(host=args.host, port=args.port, comp_id=args.comp_id,
                          target_comp_id=args.target_comp_id,
                          begin_string=args.fix_version,
                          log_path=args.log_file, annotate=not args.raw_only)

    with client:                      # connect + logon, logout + close on exit
        for _ in range(args.orders):
            order, ack, fill = client.execute_order(
                symbol=args.symbol, side=SIDES[args.side],
                quantity=args.qty, price=args.price)
            client.log.event(
                f"ORDER {order.get(T.ClOrdID)} -> ack {ack.get(T.OrderID)}"
                f" -> filled {fill.get(T.LastShares)} @ {fill.get(T.LastPx)}"
                f" (CumQty={fill.get(T.CumQty)}, LeavesQty={fill.get(T.LeavesQty)})")
        if args.test_request:
            client.test_request("DEMO-TEST-1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
