# FIX Engine — Order Lifecycle Demo

A lightweight, dependency-free **FIX 4.2 / 4.4** engine in pure Python: a mock
broker (**acceptor**), a client (**initiator**), and a pytest suite that drives
the full order lifecycle over a real TCP socket — wired to GitHub Actions.

Everything is standard-library sockets. `pytest` is the only dependency, and it
is only needed to run the tests.

```
Initiator (CLIENT)                          Acceptor (BROKER)
        │                                          │
        │  35=A   Logon ─────────────────────────► │
        │ ◄───────────────────────── Logon   35=A  │
        │                                          │
        │  35=D   NewOrderSingle ────────────────► │
        │ ◄──────────── ExecutionReport 39=0  35=8 │   acknowledged
        │ ◄──────────── ExecutionReport 39=2  35=8 │   filled
        │                                          │
        │  35=1   TestRequest ───────────────────► │
        │ ◄───────────────────── Heartbeat   35=0  │
        │                                          │
        │  35=5   Logout ────────────────────────► │
        │ ◄──────────────────────── Logout   35=5  │
```

---

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest
```

That is enough on its own: if nothing is listening on port 9876, the test
fixture starts an acceptor in-process on an ephemeral port.

---

## Live demo — two terminals

**Terminal 1 — the mock broker.** Leave this running and visible; every
message that crosses the wire is printed here.

```bash
python scripts/run_acceptor.py --port 9876 --fix-version FIX.4.2
```

**Terminal 2 — the client.** Sends a limit order and walks the lifecycle.

```bash
python scripts/run_initiator.py --symbol AAPL --side buy --qty 100 --price 150.25 --test-request
```

Terminal 2 prints, in order:

```
[INITIATOR] --> 8=FIX.4.2|9=73|35=A|49=CLIENT|56=BROKER|34=1|52=...|98=0|108=30|141=Y|10=184|
[INITIATOR] <-- 8=FIX.4.2|9=73|35=A|49=BROKER|56=CLIENT|34=1|52=...|98=0|108=30|141=Y|10=172|
[INITIATOR] --> 8=FIX.4.2|9=...|35=D|...|11=ORD55267001|55=AAPL|54=1|38=100|40=2|44=150.25|59=0|10=...|
[INITIATOR] <-- 8=FIX.4.2|9=197|35=8|...|37=BRK-000001|11=ORD55267001|17=EXEC-000001|150=0|39=0|151=100|14=0|10=101|
[INITIATOR] <-- 8=FIX.4.2|9=209|35=8|...|37=BRK-000001|11=ORD55267001|17=EXEC-000002|150=2|39=2|32=100|31=150.25|151=0|14=100|10=188|
[INITIATOR] ORDER ORD55267001 -> ack BRK-000001 -> filled 100 @ 150.25 (CumQty=100, LeavesQty=0)
```

Each raw line is followed by an annotated one that names every tag and decodes
the enums, which is what makes the protocol readable on a screen share:

```
MsgType(35)=8[ExecutionReport] OrdStatus(39)=2[Filled] Side(54)=1[Buy] LastPx(31)=150.25 ...
```

Pass `--raw-only` to either script to suppress the annotations and show nothing
but the wire format. Sessions are also written to `logs/acceptor.log` and
`logs/initiator.log`.

**Terminal 3 (optional) — run the tests against that same live acceptor**, so
the broker terminal shows the test traffic arriving in real time:

```bash
FIX_PORT=9876 pytest -v -s
```

### Useful variations

```bash
# FIX 4.4 instead of 4.2 (drops tag 20, reports fills as 150=F)
python scripts/run_acceptor.py --fix-version FIX.4.4
python scripts/run_initiator.py --fix-version FIX.4.4

# A market order — no Price(44) on the wire; the venue supplies the fill price
python scripts/run_initiator.py --symbol TSLA --side sell --qty 50

# A burst of orders, to show sequence numbers advancing
python scripts/run_initiator.py --orders 5
```

---

## Project structure

```
fix-engine/
├── fixengine/                  # the engine (no third-party dependencies)
│   ├── __init__.py             # public API: FixAcceptor, FixInitiator, ...
│   ├── tags.py                 # tag numbers (8, 35, 11, 55, ...) + display names
│   ├── enums.py                # legal field values (MsgTypes, OrdStatus, Side, ...)
│   ├── message.py              # codec: BodyLength, CheckSum, TCP stream framing
│   ├── session.py              # session layer: header, sequence numbers, I/O
│   ├── acceptor.py             # mock broker: logon, heartbeat, order matching
│   ├── initiator.py            # client: logon, order entry, report handling
│   └── logging_.py             # raw pipe-delimited + annotated log formats
├── scripts/
│   ├── run_acceptor.py         # terminal 1 — the mock broker
│   ├── run_initiator.py        # terminal 2 — the demo client
│   └── wait_for_port.py        # CI gate: block until the listener is bound
├── tests/
│   ├── conftest.py             # fixtures: in-process or external acceptor
│   ├── test_message.py         # unit: framing, checksum, stream reassembly
│   └── test_order_flow.py      # integration: the full lifecycle over TCP
├── .github/workflows/fix-ci.yml
├── requirements.txt
├── pytest.ini
└── README.md
```

### Layering

The engine splits along the same seam the protocol does:

| Layer | Module | Responsibility |
|---|---|---|
| Codec | `message.py` | tag=value ⇄ bytes, `BodyLength(9)`, `CheckSum(10)`, stream framing |
| Session | `session.py` | standard header, sequence numbers, socket read/write |
| Application | `acceptor.py`, `initiator.py` | orders and executions |

`FixStreamReader` is the piece people most often miss: TCP is a byte stream,
so one `recv()` may return half a message or three and a half. Messages are
framed on `BodyLength(9)`, exactly as the specification intends — not by
scanning for a terminator.

---

## FIX tag reference

A FIX message is `tag=value` pairs separated by **SOH** (`0x01`), shown as `|`
in logs and below.

### Standard header and trailer

| Tag | Name | Notes |
|---|---|---|
| `8` | BeginString | `FIX.4.2` / `FIX.4.4`. **Always first.** |
| `9` | BodyLength | Bytes between the SOH after tag 9 and the SOH before tag 10. **Always second.** |
| `35` | MsgType | Message discriminator. **Always third.** |
| `49` | SenderCompID | Who sent it (`CLIENT`) |
| `56` | TargetCompID | Who it is for (`BROKER`) |
| `34` | MsgSeqNum | Per-session, per-direction counter starting at 1 |
| `52` | SendingTime | UTC, `YYYYMMDD-HH:MM:SS.sss` |
| `10` | CheckSum | Sum of all preceding bytes mod 256, 3 digits. **Always last.** |

### Message types (tag 35)

| Value | Message | Handled by this engine |
|---|---|---|
| `A` | Logon | Negotiates `HeartBtInt(108)`; `ResetSeqNumFlag(141)=Y` restarts numbering |
| `0` | Heartbeat | Emitted when the session is idle; echoes `TestReqID` when answering a TestRequest |
| `1` | TestRequest | Liveness probe; the reply must echo `TestReqID(112)` |
| `5` | Logout | Acknowledged before the socket closes |
| `D` | NewOrderSingle | Order entry |
| `8` | ExecutionReport | Acknowledgement, fill, and rejection |
| `j` | BusinessMessageReject | Returned for application messages the mock does not implement |

### NewOrderSingle — `35=D`

| Tag | Name | Example | Notes |
|---|---|---|---|
| `11` | ClOrdID | `ORD55267001` | Client-assigned, unique per session per day |
| `55` | Symbol | `AAPL` | Instrument |
| `54` | Side | `1` | `1`=Buy, `2`=Sell |
| `38` | OrderQty | `100` | Quantity ordered |
| `40` | OrdType | `2` | `1`=Market, `2`=Limit |
| `44` | Price | `150.25` | Required on a limit order, **must be absent on a market order** |
| `59` | TimeInForce | `0` | `0`=Day, `1`=GTC, `3`=IOC |
| `60` | TransactTime | `20240101-12:00:00.000` | When the business event occurred |

### ExecutionReport — `35=8`

| Tag | Name | Notes |
|---|---|---|
| `37` | OrderID | Broker-assigned; **same across every report for one order** |
| `11` | ClOrdID | Echoed back so the client can correlate |
| `17` | ExecID | Unique per report — the ack and the fill differ here |
| `150` | ExecType | The *event*: `0`=New, `2`=Fill (4.2), `F`=Trade (4.4), `8`=Rejected |
| `39` | OrdStatus | The *state*: `0`=New, `1`=PartiallyFilled, `2`=Filled, `8`=Rejected |
| `32` | LastShares / LastQty | Quantity of **this** fill |
| `31` | LastPx | Price of **this** fill |
| `14` | CumQty | Filled so far across all fills |
| `151` | LeavesQty | Still open on the book — `0` means the order is done |
| `6` | AvgPx | Average price across all fills |
| `20` | ExecTransType | **FIX 4.2 only** (`0`=New); removed in 4.4 |

**`150` versus `39` is the distinction worth being precise about.** `ExecType`
says what *happened* (an event), `OrdStatus` says what the order *is* now (a
state). They coincide on the ack (`150=0`, `39=0`) and diverge as soon as a
cancel or a partial fill enters the picture.

### Why two reports per order

The acceptor answers every `35=D` with **two** `35=8` messages:

| # | ExecType | OrdStatus | CumQty | LeavesQty | Meaning |
|---|---|---|---|---|---|
| 1 | `150=0` New | `39=0` New | `0` | `100` | Accepted, live on the book, nothing traded |
| 2 | `150=2` Fill | `39=2` Filled | `100` | `0` | Fully executed — terminal state |

A venue always acknowledges before it fills. An OMS that assumes one terminal
report per order loses track of working orders — which is the bug this two-step
flow exists to demonstrate. Partial fills would simply add reports with `39=1`
and a non-zero `LeavesQty` before the terminal `39=2`.

### FIX 4.2 versus 4.4

The two versions share their framing and differ in their *dictionaries*:

| | FIX 4.2 | FIX 4.4 |
|---|---|---|
| `ExecTransType(20)` | Mandatory on `35=8` | Removed |
| Complete fill | `150=2` (Fill) | `150=F` (Trade) |
| Tag `32` | `LastShares` | `LastQty` (renamed, same tag) |

Both are exercised by the CI matrix, and `test_execution_report_matches_the_negotiated_fix_version`
pins the difference.

---

## Tests

```bash
pytest                 # everything
pytest -v -s           # stream the raw FIX traffic live
pytest tests/test_message.py     # codec only, no sockets
FIX_VERSION=FIX.4.4 pytest       # run the suite as FIX 4.4
FIX_PORT=9876 pytest             # use an acceptor you started yourself
```

| Env var | Default | Purpose |
|---|---|---|
| `FIX_HOST` | `127.0.0.1` | Acceptor host |
| `FIX_PORT` | `9876` | If already listening, tests use it; otherwise one is started in-process |
| `FIX_VERSION` | `FIX.4.2` | BeginString for both sides |
| `FIX_LOG_DIR` | `logs` | Where session logs are written |

**`tests/test_order_flow.py`** — integration over a real socket: logon round
trip, TestRequest/Heartbeat correlation, logout, New→Filled, sell side, market
orders, id uniqueness and sequence monotonicity, order rejection, business
reject, version-specific report shape, wire validity, and log format.

**`tests/test_message.py`** — the codec: `BodyLength`/`CheckSum` computation,
mandated field order, corrupt-checksum detection, and stream reassembly across
ragged chunk boundaries.

---

## CI/CD — `.github/workflows/fix-ci.yml`

Runs on every `push` and `pull_request`, on `ubuntu-latest`, across a matrix of
**FIX 4.2 / 4.4 × Python 3.11 / 3.12**.

1. Install dependencies (pip cache keyed on `requirements.txt`).
2. Start the acceptor with `nohup ... &` so it outlives the step.
3. **Gate on the port**, via `scripts/wait_for_port.py`. Starting a process is
   not the same as its socket being bound; without this gate pytest races the
   listener and fails with `connection refused`. A socket probe is used rather
   than `nc`, which is not guaranteed to be installed and whose flags differ
   between the BSD and GNU builds.
4. Run `pytest -v -s`, streaming the raw pipe-delimited FIX into the job log.
5. Print the acceptor's side of the session in a collapsible group.
6. Stop the acceptor with `SIGTERM` so it flushes its logs.
7. Upload `logs/` via `actions/upload-artifact@v4` with `if: always()` — a
   failing run is exactly when the raw wire traffic is worth reading. Artifact
   names include the matrix values, since v4 requires them to be unique.

---

## Deliberate simplifications

Honest scope, since this is a demo rather than a production engine:

- **Sequence gaps are detected and logged, not repaired.** A real engine
  answers a gap with a ResendRequest (`35=2`) and replays from a persistent
  store; here the gap is reported loudly and the session continues.
- **No message store**, so sequence numbers do not survive a restart —
  which is why the initiator logs on with `ResetSeqNumFlag(141)=Y`.
- **No authentication or TLS.** A real acceptor checks CompIDs, credentials
  (`553`/`554`) and source IP at logon, under TLS.
- **Every order fills immediately and completely** at the limit price, or at
  `DEFAULT_MARKET_PRICE` for a market order. There is no book and no matching.
- **Cancel/replace is not implemented** (`35=F`/`35=G` return a
  BusinessMessageReject), and repeating groups are parsed positionally but no
  message using them is modelled.
