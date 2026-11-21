# SPDX-License-Identifier: Apache-2.0
"""`choregos live`: a workflow's flow in the terminal (S67-28, ADR 0187 §4).

Like `chat`, this module holds no Textual: `choregos --version` and every other command start as fast as
before. The screen is `live_app.py`, imported here when, and only when, the person opens it.
"""

from __future__ import annotations

import os
from typing import Annotated

import typer

from . import theme
from .chat import interactive
from .client import NoOrganisationError


def live(
    project: Annotated[
        str | None,
        typer.Option(
            "--project", "-p", help="the project's slug; the only one of the organisation otherwise"
        ),
    ] = None,
    workflow: Annotated[
        str | None,
        typer.Option("--workflow", "-w", help="the workflow's name; the project's default otherwise"),
    ] = None,
    org: Annotated[
        str | None, typer.Option("--org", help="the organisation; the profile's otherwise")
    ] = None,
    no_motion: Annotated[
        bool,
        typer.Option(
            "--no-motion", help="no spinner and no travelling token (also TRANIBOR_NO_MOTION, and NO_COLOR)"
        ),
    ] = False,
) -> None:
    """Watch a workflow's flow: its stations, the items parked in them, and the ones that move.

    Stations from left to right, side states (rejections, retries, waits) below, each item a token coloured
    by what it is doing: blue runs, amber waits for a person, red failed (with why). A token that changes
    station travels along its arrow. Enter opens an item, q quits. It needs a terminal, and writes nothing;
    `choregos activity` is the scriptable form.
    """
    from . import main
    from .client import ApiError

    if not interactive():
        main.fail(
            "`choregos live` needs a terminal; use `choregos activity` or `choregos items list` in a script"
        )
    api = main.client()
    try:
        organisation = api.profile.organisation(org)
    except NoOrganisationError as exc:
        main.fail(str(exc))
        return
    try:
        projects = api.get(f"/orgs/{organisation}/projects")["items"]
        chosen = _pick(projects, project)
        workflows = api.get_pages(f"/projects/{chosen['slug']}/workflows")
        name, is_default = _workflow(workflows, workflow)
    except ApiError as exc:
        main.fail(str(exc))
        return
    except LookupError as exc:
        main.fail(str(exc))
        return
    from .client import AsyncClient
    from .live_app import run  # Textual is imported here, and nowhere else

    run(
        AsyncClient(api.profile),
        organisation,
        chosen,
        name,
        is_default=is_default,
        colour=theme.wants_colour(),
        motion=not (no_motion or os.environ.get("TRANIBOR_NO_MOTION")),
    )


def _pick(projects: list[dict[str, object]], slug: str | None) -> dict[str, object]:
    if slug:
        found = next((p for p in projects if slug in (p["slug"], p["id"])), None)
        if found is None:
            raise LookupError(f"no project {slug!r} here ({_slugs(projects)})")
        return found
    if len(projects) == 1:
        return projects[0]
    raise LookupError(f"which project? --project one of: {_slugs(projects)}")


def _slugs(projects: list[dict[str, object]]) -> str:
    return ", ".join(str(p["slug"]) for p in projects) or "none"


def _workflow(workflows: list[dict[str, object]], name: str | None) -> tuple[str, bool]:
    if name:
        found = next((w for w in workflows if w["name"] == name), None)
        names = ", ".join(str(w["name"]) for w in workflows) or "none"
        if found is None:
            raise LookupError(f"no workflow {name!r} in this project ({names})")
        return str(found["name"]), bool(found.get("is_default"))
    default = next((w for w in workflows if w.get("is_default")), None)
    if default is None:
        raise LookupError("this project has no default workflow; pass --workflow")
    return str(default["name"]), True
