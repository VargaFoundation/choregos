# SPDX-License-Identifier: Apache-2.0
"""`choregos-outpost enrol | run | doctor`, as an operator types them.

The door is the fake one, served over HTTP on the loopback by a thread, and the commands run as they
would on a machine: they read a policy file, write a state directory, and print.
"""

from __future__ import annotations

import json
import re
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from choregos_contracts.outpost import FINGERPRINT_PATTERN
from choregos_outpost import __version__
from choregos_outpost import identity as identities
from choregos_outpost.cli import app
from choregos_outpost.fakes import FakeDoor
from typer.testing import CliRunner

from ._bench import policy_document, private_spellings, write_policy
from ._wire import Background, closed_port

FINGERPRINT = re.compile(FINGERPRINT_PATTERN.strip("^$"))


class Machine:
    """One operator's machine: a policy file, a state directory, and the door it dials."""

    def __init__(self, root: Path, background: Background) -> None:
        self.root = root
        self.door = FakeDoor()
        self.door.origin = background.serve(self.handle)
        self.rewrite: Any = None
        self.policy = write_policy(root, policy_document(self.door, root))
        self.state = root / "state"
        self.runner = CliRunner()

    async def handle(self, request: httpx.Request) -> httpx.Response:
        response = await self.door.handle(request)
        return self.rewrite(response) if self.rewrite else response

    def run(self, *arguments: str, **more: Any) -> Any:
        base = ["--policy", str(self.policy), "--state-dir", str(self.state)]
        return self.runner.invoke(app, [*base, *arguments], **more)


@pytest.fixture
def machine(tmp_path: Path) -> Iterator[Machine]:
    background = Background()
    try:
        yield Machine(tmp_path, background)
    finally:
        background.close()


#: A terminal's styling sequence (colour, bold), as the help prints them on a terminal.
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def test_the_command_has_three_verbs_and_says_its_version() -> None:
    runner = CliRunner()
    helped = runner.invoke(app, ["--help"])
    assert helped.exit_code == 0
    # The help is styled where the terminal is said to take it (a CI runner forces it): the verbs
    # are read from the text, without its escape sequences.
    text = ANSI.sub("", helped.output)
    for verb in ("enrol", "run", "doctor"):
        assert re.search(rf"^\W*{verb}\b", text, re.M), verb
    assert "only dials out" in " ".join(text.split())
    assert runner.invoke(app, ["--version"]).output.strip() == f"choregos-outpost {__version__} (protocol 1)"
    # Nothing that would make the daemon fetch or run something: no such verb exists (ADR 0147 §9).
    assert not re.search(r"upgrade|update|install|exec", text, re.I)


def test_enrol_makes_the_keys_and_prints_the_fingerprint_a_person_compares(machine: Machine) -> None:
    result = machine.run("enrol", "--token", machine.door.enrolment_token())
    assert result.exit_code == 0, result.output
    own = identities.load(machine.state / "identity")
    assert FINGERPRINT.findall(result.output) == [own.fingerprint]
    (instance,) = machine.door.instances.values()
    assert instance.status == "pending" and instance.fingerprint == own.fingerprint
    assert f"instance {instance.id} of outpost acme/factory-floor: pending" in result.output
    assert (
        "Approve the instance in the console only if the fingerprint shown there is this one."
        in result.output
    )
    # The person compares, and approves: with another fingerprint nothing is approved.
    with pytest.raises(ValueError, match="not this instance's"):
        machine.door.approve(instance.id, "SHA256:" + "A" * 43)
    machine.door.approve(instance.id, FINGERPRINT.findall(result.output)[0])
    # Keys only the daemon reads, and a record of the enrolment beside them.
    assert stat.S_IMODE((machine.state / "identity").stat().st_mode) == 0o700
    assert stat.S_IMODE((machine.state / "identity" / "signing.key").stat().st_mode) == 0o600
    enrolment = identities.load_enrolment(machine.state / "identity")
    assert enrolment is not None and enrolment.instance_id == instance.id
    assert (enrolment.organisation, enrolment.outpost) == ("acme", "factory-floor")
    assert not any(spelling in result.output for spelling in private_spellings(machine.state / "identity"))
    # What left the machine: the public halves, and two facts for the person who approves.
    sent = json.loads(machine.door.received[0][2])
    assert sent["signing_key"] == own.signing_key and sent["sealing_key"] == own.sealing_key
    assert set(sent["facts"]) == {"hostname", "platform"} and set(instance.facts) == {"hostname", "platform"}


def test_enrol_again_keeps_the_keys_it_has(machine: Machine) -> None:
    """A daemon whose first answer was lost on the way enrols again as the same instance."""
    first = machine.run("enrol", "--token", machine.door.enrolment_token(), "--no-facts")
    second = machine.run(
        "enrol", "--token-file", str(_file(machine.root / "token", machine.door.enrolment_token()))
    )
    assert first.exit_code == 0 and second.exit_code == 0, second.output
    assert FINGERPRINT.findall(first.output) == FINGERPRINT.findall(second.output)
    assert "The keys were made on this machine." in first.output
    assert "The keys this machine already had were enrolled." in second.output
    assert len(machine.door.instances) == 1
    assert json.loads(machine.door.received[0][2]).get("facts") == {}, "`--no-facts` sends none"


def _file(path: Path, content: str) -> Path:
    path.write_text(content + "\n", encoding="utf-8")
    return path


def test_enrol_prints_the_fingerprint_of_its_own_key_never_the_one_the_answer_echoes(
    machine: Machine,
) -> None:
    """The enrolment's answer is not signed: whoever sits on the path can rewrite what it echoes."""
    echoed = "SHA256:" + "B" * 43

    def rewritten(response: httpx.Response) -> httpx.Response:
        if response.status_code != 201:
            return response
        headers = {"Outpost-Nonce": response.headers["Outpost-Nonce"]}
        return httpx.Response(201, json=response.json() | {"fingerprint": echoed}, headers=headers)

    machine.rewrite = rewritten
    result = machine.run("enrol", "--token", machine.door.enrolment_token())
    assert result.exit_code == 0
    own = identities.load(machine.state / "identity")
    printed = result.stdout.split("Fingerprint of this instance's signing key:")[1]
    assert FINGERPRINT.findall(printed) == [own.fingerprint] and echoed not in result.stdout
    assert "WARNING" in result.stderr and "do not approve" in result.stderr


def test_enrol_says_why_the_door_refused(machine: Machine) -> None:
    refused = machine.run("enrol", "--token", "a-token-nobody-issued-000")
    assert refused.exit_code == 1 and "invalid_enrolment_token" in refused.stderr
    machine.door.serve(current=3, minimum=2)
    old = machine.run("enrol", "--token", machine.door.enrolment_token())
    assert old.exit_code == 1 and "protocol_unsupported" in old.stderr
    no_token = machine.run("enrol")
    assert no_token.exit_code == 2 and "no enrolment token" in no_token.stderr


@pytest.mark.parametrize("verb", ["enrol", "run"])
def test_the_daemon_does_not_start_on_an_invalid_policy_door_keys_included(
    machine: Machine, verb: str
) -> None:
    document = policy_document(machine.door, machine.root)
    del document["door_keys"]
    write_policy(machine.root, document)
    result = machine.run(verb, *(["--token", machine.door.enrolment_token()] if verb == "enrol" else []))
    assert result.exit_code == 2
    assert "is not a valid policy" in result.stderr and "door_keys: is required" in result.stderr
    assert not (machine.state / "identity").exists() and machine.door.received == []

    write_policy(machine.root, policy_document(machine.door, machine.root) | {"door_keys": ["not-a-key"]})
    result = machine.run(verb, *(["--token", machine.door.enrolment_token()] if verb == "enrol" else []))
    assert result.exit_code == 2 and "door_keys.0" in result.stderr and machine.door.received == []


def test_run_does_not_start_without_an_enrolled_identity_it_can_call_its_own(machine: Machine) -> None:
    nothing = machine.run("run")
    assert nothing.exit_code == 2 and "choregos-outpost enrol" in nothing.stderr
    identities.create(machine.state / "identity")
    never_enrolled = machine.run("run")
    assert never_enrolled.exit_code == 2 and "never enrolled" in never_enrolled.stderr
    (machine.state / "identity" / "signing.key").chmod(0o644)
    exposed = machine.run("run")
    assert exposed.exit_code == 2 and "open to others" in exposed.stderr
    assert not any(verb == "poll" for verb, _, _ in machine.door.received)


def test_doctor_prints_one_line_per_check_and_fails_when_one_does(machine: Machine) -> None:
    # The alias's server is not there (nothing listens on its port): that one check fails, and the
    # others say what they saw.
    nowhere = f"http://127.0.0.1:{closed_port()}/mcp"
    write_policy(machine.root, policy_document(machine.door, machine.root, url=nowhere))
    result = machine.run("doctor")
    assert result.exit_code == 1
    lines = {
        line.split()[1]: line for line in result.output.splitlines() if line.startswith(("ok", "skipped"))
    }
    assert set(lines) == {"policy", "identity", "records", "dns", "proxy", "tls", "door", "clock"}
    assert (
        lines["policy"].startswith("ok")
        and lines["door"].startswith("ok")
        and lines["clock"].startswith("ok")
    )
    assert lines["tls"].startswith("skipped") and "plain `http`" in lines["tls"]
    assert (
        "FAILED  endpoint tracker" in result.output and "-> check its `url` and its `auth`" in result.output
    )
    as_json = json.loads(machine.run("doctor", "--json").output)
    assert as_json["ok"] is False and [check["name"] for check in as_json["checks"]][-1] == "endpoint tracker"
    # It took nobody's place at the door: it signed nothing.
    assert machine.door.received and all(claims is None for _, claims, _ in machine.door.received)

    write_policy(machine.root, policy_document(machine.door, machine.root) | {"door_keys": []})
    broken = machine.run("doctor")
    assert broken.exit_code == 1 and "FAILED  policy" in broken.output and "door_keys" in broken.output
