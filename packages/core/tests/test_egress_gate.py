"""The egress gate (S80-07f, #1854): it lets the pod go on only once a direct connection out is refused
`closed_streak` times in a row, and fails the pod when the way out is still open at its deadline."""

from __future__ import annotations

import errno
import json
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from tranibor_core.egress_gate import (
    DEFAULT_TARGET,
    EgressGate,
    gate_from_env,
    main,
    parse_target,
    wait_until_closed,
)
from tranibor_core.exit_codes import Exit, explanation


class _Path:
    """A scripted way out: each attempt takes the next answer, then the last one forever. `True`
    connects; an exception class or an errno is how the attempt fails. Time advances by `cost` per
    attempt and by what the gate sleeps."""

    def __init__(self, *answers: Any, cost: float = 0.01) -> None:
        self.answers = list(answers)
        self.cost = cost
        self.now = 0.0
        self.tried: list[tuple[str, int]] = []

    def connect(self, target: tuple[str, int], timeout: float) -> Any:
        self.tried.append(target)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if answer is TimeoutError:
            self.now += timeout
            raise TimeoutError("timed out")
        self.now += self.cost
        if answer is True:
            return socket.socket()  # closed at once by the gate
        raise OSError(answer, errno.errorcode.get(answer, "?"))

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _wait(path: _Path, **settings: Any) -> Any:
    return wait_until_closed(EgressGate(**settings), connect=path.connect, clock=path.clock, sleep=path.sleep)


def test_open_then_closed_lets_the_pod_go_on_after_the_window() -> None:
    """What kube-router does to a new pod: open first, then its chain arrives and refuses."""
    path = _Path(True, True, True, errno.ECONNREFUSED)

    report = _wait(path)

    assert report.outcome == "closed"
    assert report.open_attempts == 3
    assert report.attempts == 6, "three refusals in a row after the window"
    assert report.last_error == "ECONNREFUSED"
    assert report.waited_s > 0
    assert report.open_at_s == sorted(report.open_at_s) and len(report.open_at_s) == 3
    assert path.tried[0] == ("1.1.1.1", 443)


def test_still_open_at_the_deadline_fails_the_pod() -> None:
    path = _Path(True)

    report = _wait(path, deadline_s=5)

    assert report.outcome == "open"
    assert report.waited_s >= 5
    assert report.open_attempts == report.attempts
    assert report.last_error == ""


@pytest.mark.parametrize(
    "refusal",
    [errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ECONNRESET, TimeoutError],
    ids=["refused (REJECT)", "host unreachable", "no route (air-gapped)", "reset", "timeout (DROP)"],
)
def test_every_way_of_not_connecting_counts_as_closed(refusal: Any) -> None:
    """A REJECT answers like a closed port, Cilium's DROP like a dead host, an air-gapped node has no
    route: none of them lets anything out, and the gate cannot tell one from another."""
    report = _wait(_Path(refusal))

    assert report.outcome == "closed"
    assert report.attempts == 3
    assert report.open_attempts == 0


def test_a_timeout_costs_its_whole_delay_and_still_closes() -> None:
    report = _wait(_Path(TimeoutError), timeout_s=1.0, interval_s=0.25)

    assert report.outcome == "closed"
    assert report.last_error == "timeout"
    assert report.waited_s == pytest.approx(3 * 1.0 + 2 * 0.25)


def test_an_attempt_that_connects_resets_the_streak() -> None:
    """Closed, then open again (a sync that gave the pod the default chain), then closed: the first
    refusals are not enough, the streak counts from the last connection."""
    path = _Path(errno.ECONNREFUSED, errno.ECONNREFUSED, True, errno.ECONNREFUSED)

    report = _wait(path)

    assert report.outcome == "closed"
    assert report.attempts == 6
    assert report.open_attempts == 1


def test_a_streak_short_of_its_count_at_the_deadline_is_not_closed() -> None:
    path = _Path(True, True, errno.ECONNREFUSED, errno.ECONNREFUSED, cost=1.0)

    report = _wait(path, deadline_s=3.5, interval_s=0)

    assert report.outcome == "open"


def test_main_says_closed_with_exit_0_and_writes_its_report(tmp_path: Path, capsys: Any) -> None:
    report_file = tmp_path / "report.json"
    path = _Path(errno.ECONNREFUSED)

    code = main(
        ["--target", "203.0.113.7:443", "--interval", "0", "--report", str(report_file)], connect=path.connect
    )

    assert code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["event"] == "egress.gate"
    assert printed["outcome"] == "closed"
    assert json.loads(report_file.read_text()) == printed


def test_main_fails_with_its_own_exit_code_when_the_way_out_stays_open(capsys: Any) -> None:
    path = _Path(True)

    code = main(["--deadline", "0.05", "--interval", "0"], connect=path.connect)

    assert code == Exit.EGRESS_OPEN == 115
    assert "not in force" in capsys.readouterr().err
    assert explanation(115) and "egress" in (explanation(115) or "")


def test_an_unwritable_report_does_not_change_the_verdict(tmp_path: Path) -> None:
    code = main(
        ["--interval", "0", "--report", str(tmp_path / "missing" / "report.json")],
        connect=_Path(errno.ECONNREFUSED).connect,
    )
    assert code == 0


# ------------------------------------------------------------------ against real sockets


@pytest.fixture
def listening() -> Iterator[int]:
    server = socket.create_server(("127.0.0.1", 0))
    server.settimeout(0.1)
    try:
        yield server.getsockname()[1]
    finally:
        server.close()


def _closed_port() -> int:
    probe = socket.create_server(("127.0.0.1", 0))
    port: int = probe.getsockname()[1]
    probe.close()
    return port


def test_a_real_listener_reads_open_and_fails_the_pod(listening: int) -> None:
    code = main(["--target", f"127.0.0.1:{listening}", "--deadline", "0.3", "--interval", "0.05"])
    assert code == Exit.EGRESS_OPEN


def test_a_real_refusal_reads_closed() -> None:
    code = main(["--target", f"127.0.0.1:{_closed_port()}", "--deadline", "5", "--interval", "0"])
    assert code == 0


# ------------------------------------------------------------------ settings


def test_targets_are_host_and_port() -> None:
    assert parse_target("1.1.1.1:443") == ("1.1.1.1", 443)
    assert parse_target("[2606:4700:4700::1111]:443") == ("2606:4700:4700::1111", 443)
    assert parse_target("example.org:80") == ("example.org", 80)
    for wrong in ("1.1.1.1", ":443", "1.1.1.1:http", "1.1.1.1:0", "1.1.1.1:70000"):
        with pytest.raises(ValueError):
            parse_target(wrong)


def test_invalid_settings_are_refused() -> None:
    with pytest.raises(ValueError):
        EgressGate(closed_streak=0)
    with pytest.raises(ValueError):
        EgressGate(deadline_s=0)
    with pytest.raises(SystemExit):
        main(["--target", "nowhere"])


def test_the_deployment_turns_it_on_and_names_target_and_deadline() -> None:
    assert gate_from_env({}) is None
    assert gate_from_env({"CHOREGOS_RUNNER_EGRESS_GATE": "false"}) is None
    gate = gate_from_env({"CHOREGOS_RUNNER_EGRESS_GATE": "true"})
    assert gate == EgressGate()
    assert gate is not None and gate.target == DEFAULT_TARGET
    gate = gate_from_env(
        {
            "CHOREGOS_RUNNER_EGRESS_GATE": "true",
            "CHOREGOS_RUNNER_EGRESS_GATE_TARGET": "10.0.0.9:8443",
            "CHOREGOS_RUNNER_EGRESS_GATE_DEADLINE": "12.5",
        }
    )
    assert gate == EgressGate(target="10.0.0.9:8443", deadline_s=12.5)
    assert gate.args()[:4] == ["--target", "10.0.0.9:8443", "--deadline", "12.5"]
