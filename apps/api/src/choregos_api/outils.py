"""`choregos-admin` : les gestes qui précèdent le premier jeton.

Le premier jeton d'API ne peut pas venir de l'API : il faut une session pour appeler
`POST /me/tokens`, et une session vient d'un IdP ou du front. Une installation neuve,
depuis un terminal, a besoin d'un geste côté serveur — celui-ci. Il parle à la base
directement, comme les migrations, et ne sert qu'à ça.

    choregos-admin tokens create --email admin@example.org --name bootstrap
    choregos-admin orgs bootstrap   # rejoue l'amorçage (CHOREGOS_BOOTSTRAP_*) à la main
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Annotated

import typer
from choregos_core import utcnow
from sqlalchemy import select

app = typer.Typer(help=__doc__, no_args_is_help=True)
tokens_app = typer.Typer(help="Jetons d'API", no_args_is_help=True)
orgs_app = typer.Typer(help="Organisations", no_args_is_help=True)
app.add_typer(tokens_app, name="tokens")
app.add_typer(orgs_app, name="orgs")


@tokens_app.command("create")
def tokens_create(
    email: Annotated[str, typer.Option(help="l'utilisateur porteur (créé s'il n'existe pas)")],
    name: Annotated[str, typer.Option()] = "bootstrap",
    expires_in_days: Annotated[int, typer.Option()] = 30,
) -> None:
    """Frappe un jeton d'API et l'affiche UNE fois.

    Sans rôle : ceux-ci viennent de l'amorçage ou d'un admin.
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
    """Rejoue l'amorçage (`CHOREGOS_BOOTSTRAP_ORG`, `CHOREGOS_BOOTSTRAP_ADMINS`). Idempotent."""
    from .amorcage import amorcer
    from .config import get_settings

    crees = asyncio.run(amorcer(get_settings()))
    organisations = ", ".join(crees["organisations"]) or "aucune"
    admins = ", ".join(crees["administrateurs"]) or "aucun"
    typer.echo(f"organisations créées : {organisations} ; admins : {admins}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
