# SPDX-License-Identifier: Apache-2.0
"""`choregos-admin`: the gestures that come before the first token.

The first API token cannot come from the API: calling `POST /me/tokens` takes a session, and a
session comes from an IdP or from the front end. A new installation, from a terminal, needs a
server-side gesture: this one. It talks to the database directly, like the migrations, and is
used for nothing else.

    choregos-admin tokens create --email admin@example.org --name bootstrap
    choregos-admin orgs bootstrap   # replays the bootstrap (CHOREGOS_BOOTSTRAP_*) by hand
    choregos-admin audit verify     # walks every audit chain, reports the first break (ADR 0182)
    choregos-admin audit checkpoint --key-file - < audit-checkpoint.pem
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any

import typer
from sqlalchemy import select
from tranibor_core import utcnow

app = typer.Typer(help=__doc__, no_args_is_help=True)
tokens_app = typer.Typer(help="API tokens", no_args_is_help=True)
orgs_app = typer.Typer(help="Organisations", no_args_is_help=True)
app.add_typer(tokens_app, name="tokens")
app.add_typer(orgs_app, name="orgs")
audit_app = typer.Typer(help="The audit log's hash chain (ADR 0182)", no_args_is_help=True)
app.add_typer(audit_app, name="audit")


@tokens_app.command("create")
def tokens_create(
    email: Annotated[str, typer.Option(help="the owning user (created if absent)")],
    name: Annotated[str, typer.Option()] = "bootstrap",
    expires_in_days: Annotated[int, typer.Option()] = 30,
) -> None:
    """Mint an API token and print it ONCE.

    No role: those come from the bootstrap or from an admin.
    """

    async def _run() -> str:
        from .db.models import ApiToken, User
        from .db.session import session_scope
        from .security import generate_api_token

        async with session_scope(orgs="*") as session:
            user = (
                await session.execute(select(User).where(User.email == email.lower()))
            ).scalar_one_or_none()
            if user is None:
                user = User(email=email.lower(), display_name=email.split("@", 1)[0])
                session.add(user)
                await session.flush()
            raw, digest = generate_api_token()
            session.add(
                ApiToken(
                    user_id=user.id,
                    name=name,
                    hash=digest,
                    scopes=["*"],
                    expires_at=utcnow() + timedelta(days=expires_in_days),
                )
            )
        return raw

    typer.echo(asyncio.run(_run()))


@orgs_app.command("bootstrap")
def orgs_bootstrap() -> None:
    """Replay the bootstrap (`CHOREGOS_BOOTSTRAP_ORG`, `CHOREGOS_BOOTSTRAP_ADMINS`). Idempotent."""
    from .bootstrap import bootstrap_installation
    from .config import get_settings

    created = asyncio.run(bootstrap_installation(get_settings()))
    organisations = ", ".join(created["organisations"]) or "none"
    admins = ", ".join(created["administrateurs"]) or "none"
    typer.echo(f"organisations created: {organisations}; admins: {admins}")


#: `verify`'s and `checkpoint`'s exit codes: a chain is broken, or there is no chain to read.
BROKEN, NO_CHAIN = 1, 2

Orgs = Annotated[
    list[str] | None, typer.Option("--org", help="an organisation's slug; repeat it (default: all)")
]
Platform = Annotated[
    bool, typer.Option("--platform/--no-platform", help="the platform's chain too (sign-ins, tokens)")
]


def _postgres_or_exit() -> None:
    from .config import get_settings

    if get_settings().is_sqlite:
        typer.echo("SQLite keeps no audit chain: the chain is PostgreSQL's (ADR 0182)", err=True)
        raise typer.Exit(NO_CHAIN)


def _line(report: Any) -> str:
    """One chain, one line."""
    if report.first_break is not None:
        where = f" (entry id {report.first_break.entry_id})" if report.first_break.entry_id else ""
        return f"{report.label}: BROKEN at entry {report.first_break.seq}{where}: {report.first_break.reason}"
    if report.problems:
        return f"{report.label}: NOT VERIFIED: " + "; ".join(report.problems)
    said = [f"{report.label}: intact, {report.entries} entries"]
    if report.genesis is not None:
        said.append(
            f"entries 1 to {report.genesis.seq} chained by the migration on "
            f"{report.genesis.created_at:%Y-%m-%d} (evidence from then on, not before)"
        )
    if report.matched:
        last = report.matched[-1]
        said.append(f"{len(report.matched)} signed checkpoint(s), the last at entry {last.seq}")
    return "; ".join(said)


async def verify_chains(
    orgs: list[str], platform: bool, trusted: list[str], anchor_files: list[Path]
) -> list[Any]:
    """Every chain asked for, walked under the platform's scope."""
    from .audit_chain import chains, read_anchors, verify
    from .db.session import session_scope

    anchors = [
        item
        for path in anchor_files
        for item in read_anchors(
            (sys.stdin.read() if str(path) == "-" else path.read_text(encoding="utf-8")).splitlines(),
            "stdin" if str(path) == "-" else str(path),
        )
    ]
    async with session_scope(orgs="*") as session:
        return [
            await verify(session, chain, label, trusted=trusted, anchors=anchors)
            for chain, label in await chains(session, orgs, platform)
        ]


async def checkpoint_chains(
    orgs: list[str], platform: bool, private_pem: str, trusted: list[str]
) -> list[Any]:
    """Each chain asked for, verified then signed at its last entry, in one transaction."""
    from tranibor_core import utcnow

    from .audit_chain import chains, checkpoint
    from .db.session import session_scope

    async with session_scope(orgs="*") as session:
        moment = utcnow()
        return [
            await checkpoint(session, chain, label, private_pem, at=moment, trusted=trusted)
            for chain, label in await chains(session, orgs, platform)
        ]


@audit_app.command("verify")
def audit_verify(
    org: Orgs = None,
    platform: Platform = True,
    trust: Annotated[
        list[str] | None,
        typer.Option(help="a public key whose signed checkpoints are trusted (base64url); repeat it"),
    ] = None,
    anchor: Annotated[
        list[Path] | None,
        typer.Option(
            help="a file of checkpoints kept outside the database, as `checkpoint` prints them; `-` for "
            "standard input"
        ),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="one JSON object per chain")] = False,
) -> None:
    """Walk each audit chain and report its first break. Exit 1 if one is broken.

    The walk recomputes every hash from the columns, checks each entry links to the one before it, and
    compares the chain with its head, its stored checkpoints and the anchors given.
    """
    _postgres_or_exit()
    try:
        reports = asyncio.run(verify_chains(org or [], platform, trust or [], anchor or []))
    except (LookupError, ValueError, OSError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(NO_CHAIN) from None
    for report in reports:
        typer.echo(json.dumps(report.as_dict()) if as_json else _line(report))
    if not all(report.intact for report in reports):
        raise typer.Exit(BROKEN)


@audit_app.command("checkpoint")
def audit_checkpoint(
    key_file: Annotated[
        str,
        typer.Option(help="the Ed25519 private key (PEM, PKCS#8) that signs, `-` for standard input"),
    ],
    org: Orgs = None,
    platform: Platform = True,
    trust: Annotated[
        list[str] | None,
        typer.Option(help="former public keys whose checkpoints are trusted (a rotation); repeat it"),
    ] = None,
) -> None:
    """Verify each audit chain, sign its last entry and keep the checkpoint; print one JSON line per
    checkpoint, to be kept OUTSIDE the database (`verify --anchor`). A broken chain is not signed: exit 1.

    The key is read here and nowhere else: give it on standard input from where it is kept, so that the
    API's pods never hold it.
    """
    _postgres_or_exit()
    private_pem = sys.stdin.read() if key_file == "-" else Path(key_file).read_text(encoding="utf-8")
    try:
        results = asyncio.run(checkpoint_chains(org or [], platform, private_pem, trust or []))
    except (LookupError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(NO_CHAIN) from None
    for report, signed in results:
        if signed is not None:
            typer.echo(json.dumps(signed.as_anchor(report.label)))
        elif not report.intact:
            typer.echo(_line(report), err=True)
    if not all(report.intact for report, _ in results):
        raise typer.Exit(BROKEN)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
