# SPDX-License-Identifier: Apache-2.0
"""`choregos-outpost enrol | run | doctor`: the program an outpost's operator installs.

- `enrol` makes the instance's keys if it has none, presents an enrolment token to the door, and
  prints the fingerprint a person compares in the console before approving the instance.
- `run` polls the door and makes the calls its policy file allows. It does not start on an invalid
  policy, nor without an enrolled identity, nor when it cannot write its journal or its audit log.
- `doctor` checks what stands between this machine and a working outpost, one line per check.

Exit codes of `run`: 0 stopped on request; 2 it could not start (the policy, the identity, the
files); 3 the door no longer serves this daemon's protocol (upgrade it); 4 the instance or its
outpost was revoked; 5 the disk refused the journal or the audit log.
"""

from __future__ import annotations

import asyncio
import platform
import signal
import socket
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import httpx
import typer
from choregos_contracts.outpost import OutpostEnrolmentAnswer, OutpostPolicy

from . import PROTOCOL, __version__, doctor, identity, logs, policy, uplink
from .audit import AuditLog, AuditWriteError
from .daemon import Daemon, Exit
from .journal import Journal, JournalError
from .settings import DEFAULT_POLICY, DEFAULT_STATE_DIR, Settings, SettingsError
from .toolhost import ToolHost

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="The Choregos outpost daemon: it only dials out, and makes the calls its own policy allows.",
)

#: `run` could not start: the policy, the identity or the files are not usable.
CANNOT_START = 2
_SEQUENCE_FILE = "sequence"
_FINAL_WORDS = {
    Exit.UPGRADE_REQUIRED: "the door no longer serves this daemon's protocol: it stopped polling. Upgrade.",
    Exit.REVOKED: "the instance, or its outpost, was revoked: the daemon stopped.",
    Exit.CANNOT_RECORD: "the disk refused the journal or the audit log: the daemon stopped, and executed "
    "nothing it could not record.",
}


def _say(text: str) -> None:
    typer.echo(text)


def _fail(text: str, code: int = 1) -> typer.Exit:
    typer.echo(f"choregos-outpost: {text}", err=True)
    return typer.Exit(code)


def _version(value: bool) -> None:
    if value:
        _say(f"choregos-outpost {__version__} (protocol {PROTOCOL})")
        raise typer.Exit


@app.callback()
def main_options(
    context: typer.Context,
    policy_file: Annotated[
        Path, typer.Option("--policy", envvar="CHOREGOS_OUTPOST_POLICY", help="The policy file.")
    ] = DEFAULT_POLICY,
    state_dir: Annotated[
        Path,
        typer.Option(
            "--state-dir",
            envvar="CHOREGOS_OUTPOST_STATE_DIR",
            help="Where the identity and the journal live.",
        ),
    ] = DEFAULT_STATE_DIR,
    identity_dir: Annotated[
        Path | None,
        typer.Option(
            "--identity-dir",
            envvar="CHOREGOS_OUTPOST_IDENTITY_DIR",
            help="Where the keys live, when not under the state directory.",
        ),
    ] = None,
    ca_file: Annotated[
        Path | None,
        typer.Option(
            "--ca-file",
            envvar="CHOREGOS_OUTPOST_CA_FILE",
            help="An authority to trust besides the system's: an inspecting proxy's, a private one.",
        ),
    ] = None,
    proxy: Annotated[
        str | None,
        typer.Option(
            "--proxy",
            envvar="CHOREGOS_OUTPOST_PROXY",
            help="The outbound proxy towards the door; HTTPS_PROXY and NO_PROXY apply otherwise.",
        ),
    ] = None,
    log_level: Annotated[str, typer.Option("--log-level", envvar="CHOREGOS_OUTPOST_LOG_LEVEL")] = "INFO",
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Print the version and exit.")
    ] = False,
) -> None:
    context.obj = Settings(policy_file, state_dir, identity_dir, ca_file, proxy)
    logs.configure(log_level)


def _policy(settings: Settings) -> OutpostPolicy:
    try:
        return policy.load(settings.policy)
    except policy.PolicyError as error:
        typer.echo(f"choregos-outpost: {settings.policy} is not a valid policy:", err=True)
        for fault in error.faults:
            typer.echo(f"  - {fault}", err=True)
        raise typer.Exit(CANNOT_START) from error


def _http(settings: Settings, control_plane: str) -> httpx.AsyncClient:
    try:
        return uplink.client(verify=settings.trust(), proxy=settings.proxy_for(control_plane))
    except SettingsError as error:
        raise _fail(str(error), CANNOT_START) from error


def _facts() -> dict[str, str]:
    """Facts about this instance, for the person who approves it: a host name, a platform."""
    return {
        "hostname": socket.gethostname()[:256],
        "platform": f"{platform.system().lower()}/{platform.machine().lower()}"[:256],
    }


def _token(token: str | None, token_file: Path | None) -> str:
    if token_file is not None:
        try:
            return token_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise _fail(f"{token_file}: {error.strerror or error}", CANNOT_START) from error
    if token:
        return token.strip()
    if sys.stdin.isatty():
        return str(typer.prompt("Enrolment token", hide_input=True)).strip()
    raise _fail("no enrolment token: give `--token`, `--token-file` or CHOREGOS_OUTPOST_ENROLMENT_TOKEN", 2)


@app.command()
def enrol(
    context: typer.Context,
    token: Annotated[
        str | None,
        typer.Option("--token", envvar="CHOREGOS_OUTPOST_ENROLMENT_TOKEN", help="The enrolment token."),
    ] = None,
    token_file: Annotated[
        Path | None, typer.Option("--token-file", help="A file that holds the token.")
    ] = None,
    no_facts: Annotated[
        bool,
        typer.Option("--no-facts", help="Send no fact about this machine (its host name, its platform)."),
    ] = False,
) -> None:
    """Enrol this instance with a one-time token, and print the fingerprint to compare."""
    settings: Settings = context.obj
    loaded = _policy(settings)
    secret = _token(token, token_file)
    try:
        own, new = identity.load_or_create(settings.identity_dir)
    except identity.IdentityError as error:
        raise _fail(str(error), CANNOT_START) from error

    async def present() -> OutpostEnrolmentAnswer:
        async with _http(settings, loaded.control_plane) as http:
            door = uplink.Uplink(loaded.control_plane, own, http)
            return await door.enrol(secret, {} if no_facts else _facts())

    try:
        answer = asyncio.run(present())
    except uplink.DoorRefusedError as refused:
        raise _fail(f"the door refused the enrolment: {refused}") from refused
    except uplink.DoorUnreachableError as error:
        raise _fail(f"the door at {loaded.control_plane} did not answer: {error}") from error
    try:
        identity.save_enrolment(
            settings.identity_dir,
            identity.Enrolment(
                instance_id=answer.instance_id,
                organisation=answer.organisation,
                outpost=answer.outpost,
                control_plane=loaded.control_plane,
                enrolled_at=datetime.now(UTC).isoformat(),
            ),
        )
    except identity.IdentityError as error:
        raise _fail(str(error), CANNOT_START) from error
    _say(
        f"Enrolled as instance {answer.instance_id} of outpost {answer.organisation}/{answer.outpost}: "
        "pending."
    )
    _say("The keys were made on this machine." if new else "The keys this machine already had were enrolled.")
    _say("")
    _say("Fingerprint of this instance's signing key:")
    _say("")
    _say(f"    {own.fingerprint}")
    _say("")
    _say("Approve the instance in the console only if the fingerprint shown there is this one.")
    _say("Then start the daemon: choregos-outpost run")
    if answer.fingerprint != own.fingerprint:
        # The fingerprint printed above is this machine's own. What the answer echoes is not signed.
        typer.echo(
            f"choregos-outpost: WARNING: the answer echoed another fingerprint ({answer.fingerprint}). "
            "Something between this machine and the door rewrote the enrolment: do not approve it.",
            err=True,
        )
    if answer.protocol.minimum > PROTOCOL:
        typer.echo(
            f"choregos-outpost: WARNING: the door serves protocol {answer.protocol.minimum} and above; this "
            f"daemon speaks {PROTOCOL}. Upgrade it before it runs.",
            err=True,
        )


async def _serve(
    settings: Settings, loaded: OutpostPolicy, own: identity.Identity, instance_id: str, grace_s: float
) -> int:
    journal = Journal(settings.journal_dir)
    audit = AuditLog(Path(loaded.audit.file))
    try:
        await journal.open(time.time())
        await audit.check()
    except (JournalError, AuditWriteError) as error:
        typer.echo(f"choregos-outpost: {error}", err=True)
        journal.close()
        return int(Exit.CANNOT_RECORD)
    door = uplink.Uplink(
        loaded.control_plane,
        own,
        _http(settings, loaded.control_plane),
        instance_id=instance_id,
        sequence=uplink.Sequence(settings.journal_dir / _SEQUENCE_FILE),
    )
    daemon = Daemon(
        policy=loaded,
        identity=own,
        instance_id=instance_id,
        uplink=door,
        journal=journal,
        audit=audit,
        tools=ToolHost(loaded, verify=settings.trust()),
        grace_s=grace_s,
    )
    loop = asyncio.get_running_loop()
    for wanted in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(wanted, daemon.stop)
    try:
        result = await daemon.run()
    finally:
        await door.aclose()
        journal.close()
    if result in _FINAL_WORDS:
        typer.echo(f"choregos-outpost: {_FINAL_WORDS[result]}", err=True)
    return int(result)


@app.command()
def run(
    context: typer.Context,
    grace_s: Annotated[
        float,
        typer.Option(
            "--grace", envvar="CHOREGOS_OUTPOST_GRACE_S", help="Seconds calls in progress have to finish."
        ),
    ] = 20.0,
) -> None:
    """Poll the door and make the calls the policy file allows, until told to stop."""
    settings: Settings = context.obj
    loaded = _policy(settings)
    try:
        own = identity.load(settings.identity_dir)
        enrolment = identity.load_enrolment(settings.identity_dir)
        settings.trust()
    except (identity.IdentityError, SettingsError) as error:
        raise _fail(str(error), CANNOT_START) from error
    if enrolment is None:
        raise _fail(
            f"{settings.identity_dir} holds keys that never enrolled: run `choregos-outpost enrol`", 2
        )
    raise typer.Exit(asyncio.run(_serve(settings, loaded, own, enrolment.instance_id, grace_s)))


@app.command("doctor")
def doctor_command(
    context: typer.Context,
    as_json: Annotated[bool, typer.Option("--json", help="Print the checks as JSON.")] = False,
) -> None:
    """Check the policy, the path to the door, the clock and each endpoint; exit 1 if one fails."""
    checks = asyncio.run(doctor.diagnose(context.obj))
    _say(doctor.as_json(checks) if as_json else doctor.render(checks))
    if any(check.failed for check in checks):
        raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
