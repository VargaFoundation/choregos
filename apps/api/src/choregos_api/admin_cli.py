# SPDX-License-Identifier: Apache-2.0
"""`choregos-admin`: the gestures that come before the first token.

The first API token cannot come from the API: calling `POST /me/tokens` takes a session, and a
session comes from an IdP or from the front end. A new installation, from a terminal, needs a
server-side gesture: this one. It talks to the database directly, like the migrations, and is
used for nothing else.

    choregos-admin tokens create --email admin@example.org --name bootstrap
    choregos-admin orgs bootstrap   # replays the bootstrap (CHOREGOS_BOOTSTRAP_*) by hand
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Annotated

import typer
from choregos_core import utcnow
from sqlalchemy import select

app = typer.Typer(help=__doc__, no_args_is_help=True)
tokens_app = typer.Typer(help="API tokens", no_args_is_help=True)
orgs_app = typer.Typer(help="Organisations", no_args_is_help=True)
app.add_typer(tokens_app, name="tokens")
app.add_typer(orgs_app, name="orgs")


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
    from .amorcage import amorcer
    from .config import get_settings

    created = asyncio.run(amorcer(get_settings()))
    organisations = ", ".join(created["organisations"]) or "none"
    admins = ", ".join(created["administrateurs"]) or "none"
    typer.echo(f"organisations created: {organisations}; admins: {admins}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
