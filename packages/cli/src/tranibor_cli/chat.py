# SPDX-License-Identifier: Apache-2.0
"""`choregos chat`: the workspace of ADR 0154 §2 in the terminal, and a bare `choregos` in one (§3).

This module is what `main` imports: it holds no Textual, so `choregos --version` and every other command
start as fast as before. The workspace itself is `workspace.py`, imported here when, and only when, the person
opens it (ADR 0154 §8).
"""

from __future__ import annotations

import sys
from typing import Annotated

import typer

from . import theme
from .client import NoOrganisationError


def interactive() -> bool:
    """Both standard input and output are terminals: what a bare `choregos` needs to open the workspace, so
    no script that pipes `choregos` changes behaviour."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def chat(
    assistant: Annotated[
        str | None,
        typer.Option("--assistant", "-a", help="the assistant's slug; the only one ready for you otherwise"),
    ] = None,
    conversation: Annotated[
        str | None, typer.Option("--conversation", "-c", help="resume this conversation (its id)")
    ] = None,
    org: Annotated[
        str | None, typer.Option("--org", help="the organisation; the profile's otherwise")
    ] = None,
    smoke: Annotated[
        bool, typer.Option("--smoke", hidden=True, help="start headless, check the parts, quit")
    ] = False,
) -> None:
    """The workspace: converse with an assistant, watch what runs, see what waits for you.

    One screen of tabs (conversation, activity, projects, decisions, artefacts, live), a header of
    what runs and what waits, and a context pane that follows the selection. The keys are in the footer.
    It needs a terminal; `choregos ask` and `choregos activity` are the scriptable forms.
    """
    open_workspace(org=org, assistant=assistant, conversation=conversation, smoke=smoke)


def open_workspace(
    *,
    org: str | None = None,
    assistant: str | None = None,
    conversation: str | None = None,
    smoke: bool = False,
) -> None:
    from . import main

    if not smoke and not interactive():
        main.fail("`choregos chat` needs a terminal; use `choregos ask` or `choregos activity` in a script")
    profile = main.client().profile
    try:
        organisation = profile.organisation(org)
    except NoOrganisationError as exc:
        main.fail(str(exc))
        return
    if smoke:
        from .workspace import smoke as headless  # Textual is imported here, and nowhere else

        typer.echo(headless(profile, organisation))
        return
    from .workspace import run  # Textual is imported here, and nowhere else

    run(profile, organisation, assistant=assistant, conversation=conversation, colour=theme.wants_colour())
