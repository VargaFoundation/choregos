# SPDX-License-Identifier: Apache-2.0
"""`choregos outposts`: an organisation's outposts and their instances (ADR 0147).

An outpost is a place a person enrols, of one organisation, that only dials out to the platform. These
commands are its administrator's (`outpost:manage`): they call the management routes
(`/orgs/{org}/outposts…`) and nothing else. The door an outpost dials is its daemon's business
(`choregos-outpost`), never this CLI's.

What the operator of the outpost needs is printed ready to use: the lines of the policy file, which
pin the door's keys (ADR 0148 §13), and the `choregos-outpost enrol` command. Those lines are printed
as they are and never wrapped: a token or a key cut by the width of a terminal is one nobody can paste.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from .client import Client, NoOrganisationError

outposts_app = typer.Typer(help="Outposts: places that only dial out to the platform, and their instances")
console = Console()

#: Where the daemon reads its policy unless told otherwise (`choregos-outpost --policy`).
POLICY_FILE = "/etc/choregos-outpost/policy.yaml"
#: Where a person approves an instance: the API takes no approval from a token.
CONSOLE_PAGE = "Administration → Outposts"

OrgOption = Annotated[str | None, typer.Option("--org", help="the organisation; the profile's otherwise")]
InstanceArgument = Annotated[
    str, typer.Argument(help="the instance's id, as `choregos outposts instances` shows it")
]


def _target(org: str | None) -> tuple[Client, str]:
    """The client every command of the CLI goes through, and the organisation it works in."""
    from . import main

    api = main.client()
    try:
        return api, api.profile.organisation(org)
    except NoOrganisationError as exc:
        main.fail(str(exc))
        raise


def _verbatim(text: str = "") -> None:
    """A line someone copies: no markup, no highlighting, and no wrapping at the terminal's width."""
    console.print(text, markup=False, highlight=False, soft_wrap=True)


def _done(text: str) -> None:
    console.print("[green]✓[/green] ", end="")
    _verbatim(text)


def door_keys_line(keys: list[str]) -> str:
    """The `door_keys` line of an outpost's policy file: the only keys whose calls the outpost makes."""
    return "door_keys: [" + ", ".join(f'"{key}"' for key in keys) + "]"


def control_plane_line(origin: str | None) -> str:
    """The `control_plane` line of an outpost's policy file, or a comment where the deployment declares
    no origin for its door: a line that looked like a value would be pasted as one."""
    if origin:
        return f"control_plane: {origin}"
    return "# control_plane: this deployment declares no origin for the outposts' door; ask its operator"


def _presence(online: bool, last_seen_at: str | None) -> str:
    if online:
        return "online"
    return f"last seen {last_seen_at}" if last_seen_at else "never seen"


def _names(values: list[str] | None) -> str:
    return ", ".join(values or []) or "—"


def _print_keys_to_pin(keys: list[str]) -> None:
    if not keys:
        _verbatim("No key to pin: the outposts' door has no signing key on this deployment.")
        return
    _verbatim(f"The door's keys to pin, in the outpost's policy file ({POLICY_FILE} by default):")
    _verbatim()
    _verbatim(door_keys_line(keys))


@outposts_app.command("create")
def create(
    slug: str,
    display_name: Annotated[str | None, typer.Option(help="what people read; the slug otherwise")] = None,
    org: OrgOption = None,
) -> None:
    """Create an outpost of the organisation, with no instance yet.

    An instance comes from an enrolment token (`choregos outposts token`) and a person's approval.
    Refused while the deployment keeps outposts switched off.
    """
    api, organisation = _target(org)
    body: dict[str, Any] = {"slug": slug}
    if display_name:
        body["display_name"] = display_name
    outpost = api.post(f"/orgs/{organisation}/outposts", json=body)
    _done(f"outpost `{outpost['slug']}` created: {outpost['status']}, no instance yet")
    _verbatim(f"  enrol its first instance with a token: choregos outposts token {outpost['slug']}")


@outposts_app.command("list")
def list_(org: OrgOption = None) -> None:
    """The organisation's outposts: status, instances and presence."""
    api, organisation = _target(org)
    table = Table(
        "outpost", "name", "status", "approved", "pending", "revoked", "presence", "endpoints", "connectors"
    )
    for outpost in api.get(f"/orgs/{organisation}/outposts")["items"]:
        instances = outpost["instances"]
        table.add_row(
            outpost["slug"],
            outpost["display_name"],
            outpost["status"],
            str(instances["active"]),
            str(instances["pending"]),
            str(instances["revoked"]),
            _presence(outpost["online"], outpost.get("last_seen_at")),
            _names(outpost.get("endpoints")),
            _names(outpost.get("connectors")),
        )
    console.print(table)


@outposts_app.command("show")
def show(slug: str, org: OrgOption = None) -> None:
    """One outpost, and the door's keys to pin in its policy file."""
    api, organisation = _target(org)
    outpost = api.get(f"/orgs/{organisation}/outposts/{slug}")
    instances = outpost["instances"]
    _verbatim(f"{outpost['slug']} — {outpost['display_name']}")
    _verbatim(
        f"status: {outpost['status']} · epoch {outpost['epoch']} · "
        f"capabilities: {_names(outpost.get('capabilities'))}"
    )
    _verbatim(f"presence: {_presence(outpost['online'], outpost.get('last_seen_at'))}")
    _verbatim(
        f"instances: {instances['active']} approved, {instances['pending']} pending, "
        f"{instances['revoked']} revoked"
    )
    _verbatim(f"endpoints announced: {_names(outpost.get('endpoints'))}")
    _verbatim(f"connectors that route to it: {_names(outpost.get('connectors'))}")
    _verbatim()
    _print_keys_to_pin(list(outpost.get("door_keys") or []))


@outposts_app.command("pause")
def pause(slug: str, org: OrgOption = None) -> None:
    """Pause the outpost: nothing more is handed to it, and what its instances hold may finish.

    This is how a healthy outpost is held or retired; `revoke` answers a key that may be stolen.
    """
    api, organisation = _target(org)
    outpost = api.post(f"/orgs/{organisation}/outposts/{slug}/pause")
    _done(f"outpost `{outpost['slug']}` is {outpost['status']}: nothing more is handed to it")


@outposts_app.command("resume")
def resume(slug: str, org: OrgOption = None) -> None:
    """Resume a paused outpost."""
    api, organisation = _target(org)
    outpost = api.post(f"/orgs/{organisation}/outposts/{slug}/resume")
    _done(f"outpost `{outpost['slug']}` is {outpost['status']}")


@outposts_app.command("revoke")
def revoke(slug: str, org: OrgOption = None) -> None:
    """Revoke the outpost and every instance of it. This is final.

    Nothing its instances had in flight is taken, and a write among it ends in doubt. Connectors that
    route to it are refused until they are routed elsewhere.
    """
    api, organisation = _target(org)
    outpost = api.post(f"/orgs/{organisation}/outposts/{slug}/revoke")
    _done(
        f"outpost `{outpost['slug']}` is {outpost['status']}, with every instance of it "
        f"(epoch {outpost['epoch']}). This is final."
    )


@outposts_app.command("delete")
def delete(slug: str, org: OrgOption = None) -> None:
    """Delete a revoked outpost, with its instances and what is left of its calls.

    Refused while connectors still route to it (`choregos outposts show` names them), and while it is
    not revoked.
    """
    api, organisation = _target(org)
    api.delete(f"/orgs/{organisation}/outposts/{slug}")
    _done(f"outpost `{slug}` deleted, with its instances and what was left of its calls")


@outposts_app.command("token")
def token(
    slug: str,
    expires_in_minutes: Annotated[
        int | None, typer.Option(help="how long the token lives: 1 to 15 minutes, 15 by default")
    ] = None,
    org: OrgOption = None,
) -> None:
    """Issue an enrolment token for one instance of the outpost. The token is shown HERE, once.

    Single use, valid 15 minutes at most. It is printed with what the outpost's operator needs: the
    lines of the policy file (the door's origin and the door's keys to pin) and the
    `choregos-outpost enrol` command to run there. The instance it enrols is born pending, and is
    handed nothing until a person approves it in the console.
    """
    api, organisation = _target(org)
    body = {} if expires_in_minutes is None else {"expires_in_minutes": expires_in_minutes}
    created = api.post(f"/orgs/{organisation}/outposts/{slug}/enrolments", json=body)
    _done(f"enrolment token for `{slug}`: single use, expires {created['expires_at']}")
    _verbatim()
    _verbatim("The token, shown once:")
    _verbatim()
    _verbatim(created["token"])
    _verbatim()
    _verbatim(f"1. In the outpost's policy file ({POLICY_FILE} by default), before the daemon starts:")
    _verbatim()
    _verbatim(control_plane_line(created.get("control_plane")))
    _verbatim(door_keys_line(list(created["door_keys"])))
    _verbatim()
    _verbatim("2. On the outpost's machine, before the token expires:")
    _verbatim()
    _verbatim(f"choregos-outpost enrol --token {created['token']}")
    _verbatim()
    _verbatim(
        "3. `enrol` prints the fingerprint of the instance's signing key. Approve the pending instance in "
        f"the console ({CONSOLE_PAGE} → {slug}) only if the fingerprint shown there is that one."
    )


@outposts_app.command("instances")
def instances(slug: str, org: OrgOption = None) -> None:
    """The outpost's instances, pending ones first, each with the fingerprint of its signing key."""
    api, organisation = _target(org)
    listed = api.get(f"/orgs/{organisation}/outposts/{slug}/instances")["items"]
    if not listed:
        _verbatim(f"no instance yet: enrol one with `choregos outposts token {slug}`")
        return
    for instance in listed:
        facts = " ".join(f"{name}={value}" for name, value in sorted((instance.get("facts") or {}).items()))
        _verbatim(f"{instance['status']}  {instance['id']}")
        _verbatim(f"  fingerprint: {instance['fingerprint']}")
        _verbatim(
            f"  daemon {instance['daemon_version']}, protocol {instance['protocol']} · "
            f"{_presence(instance['online'], instance.get('last_seen_at'))} · "
            f"{instance['in_flight']} call(s) in flight"
        )
        _verbatim(f"  endpoints: {_names(instance.get('endpoints'))}")
        _verbatim(f"  what it said of itself: {facts or '—'}")
        _verbatim(f"  enrolled {instance['enrolled_at']}")
        if instance.get("approved_at"):
            _verbatim(f"  approved {instance['approved_at']} by {instance.get('approved_by') or '—'}")
        if instance.get("revoked_at"):
            _verbatim(f"  revoked {instance['revoked_at']} by {instance.get('revoked_by') or '—'}")
    if any(instance["status"] == "pending" for instance in listed):
        _verbatim()
        _verbatim(
            f"A pending instance is approved in the console ({CONSOLE_PAGE} → {slug}), against the "
            "fingerprint its daemon printed."
        )


@outposts_app.command("approve")
def approve(
    slug: str,
    instance: InstanceArgument,
    fingerprint: Annotated[
        str,
        typer.Option(
            "--fingerprint",
            help="the fingerprint `choregos-outpost enrol` printed on the outpost's machine, as read there",
        ),
    ],
    org: OrgOption = None,
) -> None:
    """Approve a pending instance against the fingerprint its daemon printed.

    The API takes an approval from a person's session that carries a recent sign-in, and refuses an
    API token (403). The CLI signs in with a token: this command is therefore refused, and prints the
    API's refusal as it is. Approve the instance in the console, under Administration → Outposts.
    """
    api, organisation = _target(org)
    approved = api.post(
        f"/orgs/{organisation}/outposts/{slug}/instances/{instance}/approve",
        json={"fingerprint": fingerprint},
    )
    _done(
        f"instance {approved['id']} of `{slug}` is {approved['status']}, "
        f"approved by {approved.get('approved_by') or '—'}"
    )


@outposts_app.command("revoke-instance")
def revoke_instance(
    slug: str,
    instance: InstanceArgument,
    org: OrgOption = None,
) -> None:
    """Revoke one instance of the outpost, pending or approved. This is final.

    The outpost's epoch moves: nothing in flight at the outpost is taken, from this instance or
    another. Its key does not come back for as long as the outpost exists.
    """
    api, organisation = _target(org)
    revoked = api.post(f"/orgs/{organisation}/outposts/{slug}/instances/{instance}/revoke")
    _done(f"instance {revoked['id']} of `{slug}` is {revoked['status']}. This is final.")


@outposts_app.command("calls")
def calls(
    slug: str,
    state: Annotated[
        str | None,
        typer.Option(help="pending, claimed, answered, refused, expired, timed_out or in_doubt"),
    ] = None,
    limit: Annotated[int, typer.Option(help="how many calls to read, 200 at most")] = 50,
    cursor: Annotated[str | None, typer.Option(help="where the previous page stopped")] = None,
    org: OrgOption = None,
) -> None:
    """The recent calls routed to the outpost, newest first: their state and the code of a refusal.

    Digests only: the platform keeps no arguments and no result of a call.
    """
    api, organisation = _target(org)
    params: dict[str, str] = {"limit": str(limit)}
    if state:
        params["state"] = state
    if cursor:
        params["cursor"] = cursor
    page = api.get(f"/orgs/{organisation}/outposts/{slug}/calls", params=params)
    table = Table(
        "when", "call", "endpoint", "connector", "state", "outcome", "refusal", "write", "arguments"
    )
    for call in page["items"]:
        table.add_row(
            call["created_at"],
            call.get("tool") or "(tool listing)",
            call["endpoint"],
            call.get("connector") or "—",
            call["state"],
            call.get("outcome") or "—",
            call.get("refusal") or "—",
            "write" if call.get("write") else "read",
            (call.get("arguments_sha256") or "—")[:12],
        )
    console.print(table)
    _verbatim("arguments: the first 12 characters of their SHA-256, as the outpost's own audit log writes it")
    meta = page.get("meta") or {}
    if meta.get("has_more") and meta.get("next_cursor"):
        _verbatim(f"older calls: choregos outposts calls {slug} --cursor {meta['next_cursor']}")
