# SPDX-License-Identifier: Apache-2.0
"""The egress gate (S80-07f, ADR 0184 "Amendment: the egress gate"): nothing an agent controls runs
before the pod's egress NetworkPolicy is in force.

k3s enforces NetworkPolicies with kube-router, which builds a pod's firewall chain only after it has
read the pod's IP in the API and rewritten the whole table: a new pod's own deny is not in force for
the first 1.2 to 5.2 s of its containers (15/15 starts measured open on k3d, issue #1854), and for
as long as a failed sync lasts. Cilium programs the pod before its containers start (10/10 closed
from the first packet on dev, runc and gVisor).

The gate is the pod's FIRST init container. It tries, again and again, a direct TCP connection to a
destination the pod's policy forbids (an address outside the cluster, `1.1.1.1:443` by default), and
exits 0 only once `closed_streak` attempts in a row did not connect. Still open at the deadline: it
exits `Exit.EGRESS_OPEN`, and the pod never runs the agent. A connection refused, reset, unreachable
or timed out all count as "did not connect": a REJECT rule answers like a closed port, a DROP rule
like a dead host, and the gate cannot tell either from a destination that is merely down. Hence the
target must be a listener that accepts whenever the path is open, and a node with no route out (an
air-gapped site) passes at once, which is the same safe state: nothing leaves directly.

Standard library only, besides the exit code: it runs before anything else in the pod, from the
runner's image (or any image with this package), as `python -m tranibor_core.egress_gate`.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import socket
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .exit_codes import Exit

#: The init container's name, which the executors' diagnostics read (`initContainerStatuses`).
GATE_CONTAINER = "egress-gate"
#: The journal event the runner records from the gate's report.
REPORT_EVENT = "egress.gate"
#: Where the gate writes its report, on a volume the runner container mounts read-only.
REPORT_DIR = "/egress-gate"
REPORT_FILE = "report.json"
#: The variable that tells the runner where the report is.
REPORT_ENV = "TRANIBOR_EGRESS_GATE_REPORT"

DEFAULT_TARGET = "1.1.1.1:443"
DEFAULT_DEADLINE_S = 30.0
DEFAULT_CLOSED_STREAK = 3
DEFAULT_TIMEOUT_S = 1.0
DEFAULT_INTERVAL_S = 0.25

Connect = Callable[[tuple[str, int], float], Any]


@dataclass(frozen=True)
class EgressGate:
    """How the gate probes. The executor turns it into the init container's arguments."""

    target: str = DEFAULT_TARGET
    deadline_s: float = DEFAULT_DEADLINE_S
    closed_streak: int = DEFAULT_CLOSED_STREAK
    timeout_s: float = DEFAULT_TIMEOUT_S
    interval_s: float = DEFAULT_INTERVAL_S

    def __post_init__(self) -> None:
        parse_target(self.target)
        if self.deadline_s <= 0 or self.timeout_s <= 0 or self.interval_s < 0 or self.closed_streak < 1:
            raise ValueError(f"egress gate: invalid settings {self}")

    def args(self) -> list[str]:
        return [
            "--target",
            self.target,
            "--deadline",
            _number(self.deadline_s),
            "--closed-streak",
            str(self.closed_streak),
            "--timeout",
            _number(self.timeout_s),
            "--interval",
            _number(self.interval_s),
            "--report",
            f"{REPORT_DIR}/{REPORT_FILE}",
        ]


def gate_from_env(environ: Mapping[str, str] | None = None) -> EgressGate | None:
    """The deployment's gate (the chart's `runner.egressGate`), or `None` when it is off.

    `TRANIBOR_RUNNER_EGRESS_GATE` is `true` or `false`; the target and the deadline have their own
    variables. The deployment's, never a connector's: a project cannot turn it off."""
    env = os.environ if environ is None else environ
    if env.get("TRANIBOR_RUNNER_EGRESS_GATE", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    return EgressGate(
        target=env.get("TRANIBOR_RUNNER_EGRESS_GATE_TARGET", "").strip() or DEFAULT_TARGET,
        deadline_s=float(env.get("TRANIBOR_RUNNER_EGRESS_GATE_DEADLINE", "").strip() or DEFAULT_DEADLINE_S),
    )


def parse_target(text: str) -> tuple[str, int]:
    """`host:port` or `[v6]:port` into its parts; `ValueError` on anything else."""
    host, sep, port = text.strip().rpartition(":")
    host = host.removeprefix("[").removesuffix("]")
    if not sep or not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError(f"egress gate: the target must be host:port, not {text!r}")
    return host, int(port)


@dataclass
class Report:
    """What the gate saw, printed as one JSON line, written to its report file and journaled."""

    target: str
    outcome: Literal["closed", "open"]
    waited_s: float
    attempts: int
    open_attempts: int
    #: Why the last attempt did not connect (`ECONNREFUSED`, `timeout`, ...); empty if it connected.
    last_error: str
    deadline_s: float
    closed_streak: int
    #: Seconds since the gate started at which each attempt connected: the window it saw.
    open_at_s: list[float] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps({"event": REPORT_EVENT, **asdict(self)}, sort_keys=True)


def attempt(target: tuple[str, int], timeout_s: float, connect: Connect) -> str:
    """One direct connection. `""` if it connected (the path is open), else why it did not."""
    try:
        connect(target, timeout_s).close()
    except TimeoutError:
        return "timeout"
    except OSError as exc:
        return errno.errorcode.get(exc.errno or 0, type(exc).__name__)
    return ""


def wait_until_closed(
    gate: EgressGate,
    *,
    connect: Connect = socket.create_connection,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Report:
    """Probes until `closed_streak` attempts in a row did not connect, or the deadline passes.

    Every attempt that connects resets the streak: a policy that is applied, lifted and applied
    again (a sync that programmed the pod with the default chain first) is not taken for closed."""
    target = parse_target(gate.target)
    started = clock()
    attempts = streak = 0
    open_at: list[float] = []
    error = ""
    while True:
        attempts += 1
        error = attempt(target, gate.timeout_s, connect)
        elapsed = clock() - started
        if error:
            streak += 1
        else:
            streak = 0
            open_at.append(round(elapsed, 3))
        if streak >= gate.closed_streak or elapsed >= gate.deadline_s:
            break
        sleep(gate.interval_s)
    return Report(
        target=gate.target,
        outcome="closed" if streak >= gate.closed_streak else "open",
        waited_s=round(clock() - started, 3),
        attempts=attempts,
        open_attempts=len(open_at),
        last_error=error,
        deadline_s=gate.deadline_s,
        closed_streak=gate.closed_streak,
        open_at_s=open_at,
    )


def main(argv: Sequence[str] | None = None, *, connect: Connect = socket.create_connection) -> int:
    parser = argparse.ArgumentParser(
        prog="egress-gate",
        description="Exit 0 once direct connections to TARGET fail in a row; 115 if it stays open.",
    )
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE_S)
    parser.add_argument("--closed-streak", type=int, default=DEFAULT_CLOSED_STREAK)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    parser.add_argument("--report", type=Path, default=None)
    options = parser.parse_args(argv)
    try:
        gate = EgressGate(
            target=options.target,
            deadline_s=options.deadline,
            closed_streak=options.closed_streak,
            timeout_s=options.timeout,
            interval_s=options.interval,
        )
    except ValueError as exc:
        parser.error(str(exc))
    report = wait_until_closed(gate, connect=connect)
    line = report.to_json()
    print(line, flush=True)
    if options.report is not None:
        try:
            options.report.write_text(line + "\n", encoding="utf-8")
        except OSError as exc:  # the report is for the journal; the verdict is the exit code
            print(f"egress gate: report not written ({exc})", file=sys.stderr, flush=True)
    if report.outcome == "closed":
        return int(Exit.OK)
    print(
        f"egress gate: {gate.target} still reachable directly after {report.waited_s}s: the pod's egress "
        "NetworkPolicy is not in force, the agent does not start",
        file=sys.stderr,
        flush=True,
    )
    return int(Exit.EGRESS_OPEN)


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


if __name__ == "__main__":  # pragma: no cover - the container's entry point
    sys.exit(main())
