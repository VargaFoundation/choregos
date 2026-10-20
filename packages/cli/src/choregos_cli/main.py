# SPDX-License-Identifier: Apache-2.0
"""The `choregos` CLI: projects, work items, runs, trains, findings, workflows, dev."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
from rich.console import Console
from rich.table import Table

from .client import ApiError, Client, Profile
from .knowledge import knowledge_app

app = typer.Typer(
    add_completion=True, help="Choregos — a work item comes in, a controlled release comes out."
)
projects_app = typer.Typer(help="Projects")
items_app = typer.Typer(help="Work items")
runs_app = typer.Typer(help="Runs")
trains_app = typer.Typer(help="Release trains")
findings_app = typer.Typer(help="Findings")
workflow_app = typer.Typer(help="Workflows")
dev_app = typer.Typer(help="Development environment")
orgs_app = typer.Typer(help="Organisations")
tokens_app = typer.Typer(help="API tokens")
admin_app = typer.Typer(help="Platform administration")
secrets_app = typer.Typer(help="Sealed connector secrets")
admin_app.add_typer(secrets_app, name="secrets")
app.add_typer(orgs_app, name="orgs")
app.add_typer(tokens_app, name="tokens")
app.add_typer(admin_app, name="admin")
app.add_typer(projects_app, name="projects")
app.add_typer(items_app, name="items")
app.add_typer(runs_app, name="runs")
app.add_typer(trains_app, name="trains")
app.add_typer(findings_app, name="findings")
app.add_typer(workflow_app, name="workflow")
app.add_typer(dev_app, name="dev")
app.add_typer(knowledge_app, name="knowledge")

console = Console()


class TransportSafeClient(Client):
    """The same client, but an unreachable API becomes a red line and exit code 1."""

    def _unreachable(self, exc: httpx.TransportError) -> None:
        reason = str(exc) or type(exc).__name__
        fail(
            f"cannot reach {self.profile.api_url}: {reason} — "
            "run `choregos login --api-url …` to point the CLI at a running API"
        )

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            return super().request(method, path, **kwargs)
        except httpx.TransportError as exc:  # connect error, timeout, dropped connection…
            self._unreachable(exc)
            return None  # `fail` raises; for the type checker

    def stream_sse(self, path: str, **kwargs: Any) -> Any:
        try:
            yield from super().stream_sse(path, **kwargs)
        except httpx.TransportError as exc:
            self._unreachable(exc)


class _ReadableClient(TransportSafeClient):
    """Also: an API error becomes a red line and exit code 1."""

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            return super().request(method, path, **kwargs)
        except ApiError as exc:
            fail(str(exc))
            return None


def client() -> Client:
    return _ReadableClient()


def fail(message: str) -> None:
    console.print(f"[red]✗[/red] {message}")
    raise typer.Exit(1)


# ───────────────────────────── session ─────────────────────────────


@app.command()
def login(
    api_url: Annotated[str, typer.Option(prompt=True)] = "http://localhost:8000",
    token: Annotated[str, typer.Option(prompt=True, hide_input=True)] = "",
    org: Annotated[str, typer.Option()] = "varga",
) -> None:
    """Save the connection profile (`~/.config/choregos/config.json`).

    The CLI cannot issue the first token (`tokens create` itself needs one): get it from the
    console, under `/admin` (API tokens), then pass it with `--token`.
    """
    profile = Profile(api_url=api_url, token=token, org=org)
    path = profile.save()
    try:
        me = _ReadableClient(profile).get("/me")
    except ApiError as exc:
        fail(f"sign-in refused: {exc}")
        return
    console.print(f"[green]✓[/green] signed in as {me['email']} — profile written to {path}")


@app.command()
def whoami() -> None:
    """Show the current user and their roles."""
    me = client().get("/me")
    table = Table("organisation", "project", "role", title=me["email"])
    for membership in me["memberships"]:
        table.add_row(membership["org"], membership.get("project_slug") or "—", membership["role"])
    console.print(table)


# ───────────────────────────── organisations and tokens ─────────────────────────────


@orgs_app.command("list")
def orgs_list() -> None:
    table = Table("organisation", "name", "my role")
    for org in client().get("/orgs"):
        table.add_row(org["slug"], org["name"], org.get("role") or "—")
    console.print(table)


@orgs_app.command("create")
def orgs_create(slug: str, name: Annotated[str | None, typer.Option()] = None) -> None:
    """Create an organisation (requires being an admin of an organisation already)."""
    org = client().post("/orgs", json={"slug": slug, "name": name or slug})
    console.print(f"[green]✓[/green] organisation {org['slug']} created, you are its admin")


@secrets_app.command("rewrap")
def secrets_rewrap() -> None:
    """Re-wrap every sealed secret under the active key, after a key rotation (the new key first in
    CHOREGOS_SECRETS_KEKS, the API restarted). The values are not read out nor changed."""
    result = client().post("/platform/secrets/rewrap")
    console.print(f"[green]✓[/green] {result['rewrapped']} secret(s) re-wrapped under the active key")


@tokens_app.command("create")
def tokens_create(
    name: Annotated[str, typer.Option(prompt=True)],
    expires_in_days: Annotated[int, typer.Option()] = 90,
    scope: Annotated[
        list[str] | None,
        typer.Option(help="`*` (REST API and CLI, the default), or `mcp:read` / `mcp:write` (MCP gate)"),
    ] = None,
    project: Annotated[
        str | None, typer.Option(help="`org:slug`: limits an MCP token to one project")
    ] = None,
) -> None:
    """Issue an API token for the caller. The secret is shown HERE, once."""
    body: dict[str, object] = {"name": name, "expires_in_days": expires_in_days, "scopes": scope or ["*"]}
    if project:
        body["project"] = project
    token = client().post("/me/tokens", json=body)
    scopes = ", ".join(token.get("scopes") or ["*"])
    console.print(
        f"[green]✓[/green] token `{token['name']}` ({scopes}"
        f"{', project ' + token['project'] if token.get('project') else ''}"
        f", expires {token.get('expires_at') or 'never'})"
    )
    console.print(token["token"])


@tokens_app.command("list")
def tokens_list() -> None:
    table = Table("id", "name", "scope", "project", "created", "expires", "last used", "client")
    for token in client().get("/me/tokens"):
        table.add_row(
            token["id"],
            token["name"],
            ", ".join(token.get("scopes") or ["*"]),
            token.get("project") or "—",
            token["created_at"],
            token.get("expires_at") or "—",
            token.get("last_used_at") or "—",
            token.get("last_client") or "—",
        )
    console.print(table)


@tokens_app.command("revoke")
def tokens_revoke(token_id: str) -> None:
    client().delete(f"/me/tokens/{token_id}")
    console.print(f"[green]✓[/green] token {token_id} revoked")


# ───────────────────────────── projects ─────────────────────────────


@projects_app.command("list")
def projects_list(org: Annotated[str | None, typer.Option()] = None) -> None:
    api = client()
    organization = org or api.profile.org
    page = api.get(f"/orgs/{organization}/projects")
    table = Table("slug", "name", "status", "active work items", "cost this month (€)", "trains")
    for project in page["items"]:
        stats = project.get("stats", {})
        table.add_row(
            project["slug"],
            project["name"],
            project["status"],
            str(stats.get("active_work_items", 0)),
            f"{stats.get('cost_month_eur', 0):.2f}",
            str(stats.get("trains_pending", 0)),
        )
    console.print(table)


@projects_app.command("create")
def projects_create(
    slug: str,
    repo: Annotated[
        str | None,
        typer.Option(help="URL of the application repository (optional: work without code has none)"),
    ] = None,
    name: Annotated[str | None, typer.Option()] = None,
    template: Annotated[str | None, typer.Option("--template")] = None,
    org: Annotated[str | None, typer.Option()] = None,
    language: Annotated[str, typer.Option()] = "python",
    tracker: Annotated[
        str | None,
        typer.Option(
            help="`internal`: requests are filed in Choregos. Without `--repo` the default is `internal`; "
            "with `--repo` it is written only when given"
        ),
    ] = None,
) -> None:
    """Create a project; `--template` then starts the provisioning.

    Without `--repo`, the project has no repository (ADR 0012): the agent works in an empty
    directory, and requests are filed with `choregos items create`.
    """
    api = client()
    organization = org or api.profile.org
    config: dict[str, Any] = {"slug": slug, "org": organization}
    if repo:
        config["repo"] = {"url": repo, "default_branch": "main", "language": language}
    payload = {"slug": slug, "name": name or slug, "template_ref": template, "config": config}
    project = api.post(f"/orgs/{organization}/projects", json=payload)
    console.print(f"[green]✓[/green] project {project['slug']} created ({project['id']})")
    # `--tracker` is honoured with or without `--repo` (#704): it used to be dropped with one, and
    # the project was born with no tracker. With `--repo` and no `--tracker`, the template or a
    # later `connectors` call says which tracker the repository uses.
    tracker = tracker or (None if repo else "internal")
    if tracker:
        api.put(f"/projects/{project['id']}/connectors/tracker", json={"type": tracker, "config": {}})
        if tracker == "internal":
            console.print(f"  tracker `{tracker}`: file requests with `choregos items create {slug} …`")
        else:
            console.print(f"  tracker `{tracker}`: set its configuration in the project's connectors")
    if template:
        status = api.post(f"/projects/{project['id']}/provision", json={})
        console.print(f"provisioning started: {status['status']}")


@projects_app.command("provision")
def projects_provision(project: str, follow: Annotated[bool, typer.Option("--follow")] = False) -> None:
    """Start (or restart) the provisioning, and follow it live with `--follow`."""
    api = client()
    status = api.post(f"/projects/{project}/provision", json={})
    console.print(f"provisioning: {status['status']}")
    if not follow:
        return
    for event in api.stream_sse(f"/projects/{project}/provision"):
        data = event.get("data", event)
        console.print(f"· {data.get('step', '?')} — {data.get('status', '')}")


@projects_app.command("readiness")
def projects_readiness(project: str) -> None:
    """What the project still lacks to run, and who can fix each item. Exits 1 while something is to do."""
    report = client().get(f"/projects/{project}/readiness")
    summary = report["summary"]
    table = Table("state", "item", "detail", "fixed by")
    for item in report["items"]:
        if item["state"] == "not_needed":
            continue
        table.add_row(item["state"], item["title"], item["detail"], item["fix"]["by"])
    console.print(table)
    console.print(f"{summary['done']} done, {summary['check']} to check, {summary['todo']} to do")
    if not report["ready"]:
        raise typer.Exit(1)


# ───────────────────────────── work items ─────────────────────────────


@items_app.command("list")
def items_list(project: str, state: Annotated[str | None, typer.Option()] = None) -> None:
    page = client().get(f"/projects/{project}/work-items", params={"state": state} if state else None)
    table = Table("work item", "title", "state", "size", "cost (€)", "PR")
    for item in page["items"]:
        totals = item.get("totals", {})
        table.add_row(
            item["tracker_key"],
            item["title"][:60],
            item.get("state_display") or item["state"],
            item.get("size") or "—",
            f"{totals.get('cost_eur', 0):.2f}",
            item.get("pr_url") or "—",
        )
    console.print(table)


def _fields(pairs: list[str]) -> dict[str, Any]:
    """`key=value`; the value is read as JSON when it is JSON (number, boolean, list), otherwise as text."""
    fields: dict[str, Any] = {}
    for pair in pairs:
        key, equals, value = pair.partition("=")
        if not equals or not key:
            fail(f"--field {pair!r}: expected `key=value`")
        try:
            fields[key] = json.loads(value)
        except json.JSONDecodeError:
            fields[key] = value
    return fields


@items_app.command("create")
def items_create(
    project: str,
    title: Annotated[str, typer.Option(prompt=True)],
    body: Annotated[str, typer.Option()] = "",
    size: Annotated[str | None, typer.Option(help="S, M, L or XL")] = None,
    no_start: Annotated[
        bool, typer.Option("--no-start", help="file it without starting the interpreter")
    ] = False,
    workflow: Annotated[
        str | None,
        typer.Option(
            help="the workflow the request is born in; otherwise the project's routing, then its default"
        ),
    ] = None,
    label: Annotated[
        list[str] | None, typer.Option("--label", help="a label (repeatable), read by the routing")
    ] = None,
    field: Annotated[
        list[str] | None,
        typer.Option("--field", help="`key=value` (repeatable), validated by the workflow's `inputs`"),
    ] = None,
) -> None:
    """File a request in Choregos (a project with the internal tracker) and start its interpreter."""
    request: dict[str, Any] = {"title": title, "body": body, "size": size, "start": not no_start}
    if workflow:
        request["workflow"] = workflow
    if label:
        request["labels"] = list(label)
    if field:
        request["fields"] = _fields(list(field))
    item = client().post(f"/projects/{project}/work-items", json=request)
    console.print(
        f"[green]✓[/green] {item['tracker_key']} created — state `{item['state']}`"
        + ("" if no_start else f", interpreter {item.get('temporal_wf_id')}")
    )


@items_app.command("show")
def items_show(item_id: str) -> None:
    """A work item in detail: state, costs, timeline."""
    api = client()
    item = api.get(f"/work-items/{item_id}")
    console.print(f"[bold]{item['tracker_key']}[/bold] — {item['title']}")
    console.print(f"state: {item.get('state_display') or item['state']} · size {item.get('size') or '—'}")
    totals = item.get("totals", {})
    console.print(f"cost: {totals.get('cost_eur', 0):.2f} € ({totals.get('runs', 0)} runs)")
    timeline = api.get(f"/work-items/{item_id}/timeline")
    table = Table("when", "what", "detail")
    for entry in timeline[-15:]:
        table.add_row(entry["ts"][:19], entry["title"][:40], (entry.get("detail") or "")[:60])
    console.print(table)


def _decide(item_id: str, kind: str, reason: str | None = None, answer: str | None = None) -> None:
    payload: dict[str, Any] = {"kind": kind}
    if reason:
        payload["reason"] = reason
    if answer:
        payload["answer"] = answer
    client().post(f"/work-items/{item_id}/decisions", json=payload)
    console.print(f"[green]✓[/green] decision `{kind}` sent")


@items_app.command("approve")
def items_approve(item_id: str, reason: Annotated[str | None, typer.Option()] = None) -> None:
    _decide(item_id, "approve", reason)


@items_app.command("reject")
def items_reject(item_id: str, reason: Annotated[str, typer.Option(prompt=True)]) -> None:
    _decide(item_id, "reject", reason)


@items_app.command("answer")
def items_answer(item_id: str, answer: Annotated[str, typer.Argument()]) -> None:
    _decide(item_id, "answer", answer=answer)


@items_app.command("action")
def items_action(
    item_id: str,
    action: Annotated[str, typer.Argument(help="pause|resume|stop|rerun_stage|mark_agent_ready")],
    top_up: Annotated[
        float | None,
        typer.Option("--top-up", help="rerun_stage: USD granted on top of a spent budget (audited)"),
    ] = None,
) -> None:
    body: dict[str, Any] = {"action": action}
    if top_up is not None:
        body["top_up_usd"] = top_up
    client().post(f"/work-items/{item_id}/actions", json=body)
    console.print(f"[green]✓[/green] action `{action}` sent")


# ───────────────────────────── runs ─────────────────────────────


@runs_app.command("list")
def runs_list(item_id: str) -> None:
    runs = client().get(f"/work-items/{item_id}/runs")
    table = Table("run", "stage", "attempt", "status", "backend · model", "cost ($)")
    for run in runs:
        table.add_row(
            run["id"][:12],
            run["stage_role"],
            str(run.get("attempt", 1)),
            run["status"],
            f"{run.get('backend') or '—'} · {(run.get('model') or '—').split('/')[-1]}",
            f"{run.get('cost_usd', 0):.2f}",
        )
    console.print(table)


@runs_app.command("tail")
def runs_tail(run_id: str) -> None:
    """Follow a run's ACP journal live (SSE)."""
    api = client()
    console.print(f"[dim]following run {run_id} — Ctrl-C to stop[/dim]")
    try:
        for event in api.stream_sse(f"/runs/{run_id}/events"):
            kind = event.get("type", "")
            payload = event.get("payload", event.get("data", {}))
            console.print(
                f"[dim]{event.get('seq', '')}[/dim] {kind} {json.dumps(payload, ensure_ascii=False)[:160]}"
            )
    except KeyboardInterrupt:
        console.print("\ninterrupted")


@runs_app.command("diff")
def runs_diff(run_id: str) -> None:
    diff = client().get(f"/runs/{run_id}/diff")
    table = Table("file", "+", "-", "scope")
    for file in diff["files"]:
        table.add_row(
            file["path"],
            str(file["additions"]),
            str(file["deletions"]),
            "✓" if file.get("in_scope", True) else "[red]out of scope[/red]",
        )
    console.print(table)


# ───────────────────────────── trains ─────────────────────────────


@trains_app.command("status")
def trains_status(project: str, env: Annotated[str, typer.Option()] = "prod") -> None:
    status = client().get(f"/projects/{project}/trains/{env}")
    console.print(f"[bold]{project} → {env}[/bold] : {status['status']}")
    console.print(
        f"batch: {status['batch_size']} work item(s) · frozen: {'yes' if status['frozen'] else 'no'}"
    )
    for key in status.get("pending_items", []):
        console.print(f"  · {key}")
    if status.get("freeze_reason"):
        console.print(f"[yellow]freeze reason: {status['freeze_reason']}[/yellow]")


@trains_app.command("depart")
def trains_depart(project: str, env: Annotated[str, typer.Option()] = "prod") -> None:
    client().post(f"/projects/{project}/trains/{env}/depart")
    console.print("[green]✓[/green] departure requested")


@trains_app.command("freeze")
def trains_freeze(
    project: str,
    reason: Annotated[str, typer.Option(prompt=True)],
    env: Annotated[str, typer.Option()] = "prod",
) -> None:
    client().post(f"/projects/{project}/trains/{env}/freeze", json={"reason": reason})
    console.print("[green]✓[/green] train frozen")


@trains_app.command("unfreeze")
def trains_unfreeze(project: str, env: Annotated[str, typer.Option()] = "prod") -> None:
    client().post(f"/projects/{project}/trains/{env}/unfreeze")
    console.print("[green]✓[/green] train unfrozen")


@trains_app.command("approve")
def trains_approve(release_id: str, note: Annotated[str | None, typer.Option()] = None) -> None:
    client().post(f"/releases/{release_id}/approve", json={"note": note})
    console.print("[green]✓[/green] release approved")


# ───────────────────────────── findings ─────────────────────────────


@findings_app.command("list")
def findings_list(project: str, status: Annotated[str | None, typer.Option()] = None) -> None:
    page = client().get(f"/projects/{project}/findings", params={"status": status} if status else None)
    table = Table("id", "severity", "type", "title", "status", "work item")
    for finding in page["items"]:
        table.add_row(
            finding["id"][:8],
            finding["severity"],
            finding["type"],
            finding["title"][:50],
            finding["status"],
            finding.get("created_work_item_key") or "—",
        )
    console.print(table)


@findings_app.command("action")
def findings_action(
    finding_id: str,
    action: Annotated[str, typer.Argument(help="create_ticket|mark_duplicate|dismiss|agent_ready")],
) -> None:
    client().post(f"/findings/{finding_id}/actions", json={"action": action})
    console.print(f"[green]✓[/green] `{action}` applied")


# ───────────────────────────── workflow ─────────────────────────────


@workflow_app.command("list")
def workflow_list(project: str) -> None:
    """A project's workflows: the active version of each, the default, the open work items."""
    workflows = client().get(f"/projects/{project}/workflows")
    table = Table("workflow", "version", "default", "open work items", "published by")
    for definition in workflows:
        table.add_row(
            definition["name"],
            f"v{definition['version']}",
            "✓" if definition.get("is_default") else "",
            str(definition.get("open_items", 0)),
            definition.get("created_by") or "—",
        )
    console.print(table)


@workflow_app.command("push")
def workflow_push(
    project: str,
    path: Annotated[Path, typer.Argument()] = Path(".choregos/workflow.yaml"),
    base_version: Annotated[
        int | None,
        typer.Option("--base-version", help="the active version you read: 409 if another one replaced it"),
    ] = None,
) -> None:
    """Publish the next version of a workflow, under the name its `metadata.name` gives.

    The project's other workflows do not move, and a work item in progress finishes on its version.
    """
    import yaml

    source = path.read_text(encoding="utf-8")
    try:
        document = yaml.safe_load(source) or {}
    except yaml.YAMLError as exc:
        fail(f"{path}: unreadable YAML ({exc})")
        return
    name = (document.get("metadata") or {}).get("name") if isinstance(document, dict) else None
    if not name:
        fail(f"{path}: `metadata.name` missing — it is the workflow's name in the project")
    body: dict[str, Any] = {"yaml": source}
    if base_version is not None:
        body["base_version"] = base_version
    published = client().put(f"/projects/{project}/workflows/{name}", json=body)
    console.print(f"[green]✓[/green] {published['name']} v{published['version']} published in {project}")


@workflow_app.command("validate")
def workflow_validate(
    path: Annotated[Path, typer.Argument()] = Path(".choregos/workflow.yaml"),
    remote: Annotated[
        bool, typer.Option("--remote", help="validate through the API rather than locally")
    ] = False,
) -> None:
    """Validate a workflow. Without `--remote`, the validation is local and offline."""
    source = path.read_text(encoding="utf-8")
    if remote:
        report = client().post("/workflows/validate", json={"yaml": source})
    else:
        from choregos_core import parse_workflow

        try:
            _, result = parse_workflow(source, strict=False)
            report = {
                "valid": result.valid,
                "errors": [issue.to_dict() for issue in result.errors],
                "warnings": [issue.to_dict() for issue in result.warnings],
            }
        except Exception as exc:  # syntax errors: already located
            console.print(f"[red]✗[/red] {exc}")
            raise typer.Exit(1) from exc

    for warning in report.get("warnings", []):
        console.print(f"[yellow]![/yellow] {warning['message']} ({warning.get('path') or ''})")
    if report["valid"]:
        console.print(f"[green]✓[/green] {path} is valid")
        return
    for error in report["errors"]:
        where = f"line {error['line']}" if error.get("line") else (error.get("path") or "")
        console.print(f"[red]✗[/red] {error['message']} — {where}")
    raise typer.Exit(1)


@workflow_app.command("show")
def workflow_show(
    path: Annotated[Path, typer.Argument()] = Path(".choregos/workflow.yaml"),
    mermaid: Annotated[bool, typer.Option("--mermaid")] = False,
    process: Annotated[bool, typer.Option("--process", help="each transition in plain words")] = False,
) -> None:
    """Show the workflow: states, transitions, a Mermaid diagram, or the process in plain words."""
    from choregos_core import parse_workflow, to_mermaid, to_process

    workflow, _ = parse_workflow(path.read_text(encoding="utf-8"), strict=False)
    if mermaid:
        console.print(to_mermaid(workflow))
        return
    if process:
        for step in to_process(workflow):
            console.print(f"- {step['sentence']}")
        return
    table = Table("state", "label", "outgoing actor", "gates")
    for name, state in workflow.states.items():
        transitions = workflow.transitions_from(name)
        actor = transitions[0].by or (transitions[0].via or "—") if transitions else "—"
        gates = ", ".join(transitions[0].gate_names()) if transitions else ""
        table.add_row(name, state.display, str(actor), gates)
    console.print(table)


@workflow_app.command("templates")
def workflow_templates() -> None:
    from choregos_core.dsl import load_template, template_names

    for name in template_names():
        workflow = load_template(name)
        console.print(
            f"[bold]{name}[/bold] v{workflow.metadata.version} — "
            f"{len(workflow.states)} states, {len(workflow.transitions)} transitions"
        )
        console.print(f"  {workflow.metadata.description or ''}")


# ───────────────────────────── dev ─────────────────────────────


def _make(target: str) -> None:
    """Delegate to the matching `make` target, from the repository's root."""
    import shutil
    import subprocess

    make = shutil.which("make")
    if make is None:
        fail("`make` is not found: run the target by hand")
        return
    subprocess.run([make, target], check=False)  # noqa: S603 - resolved path


@dev_app.command("up")
def dev_up() -> None:
    """Bring the development environment up (kind + Tilt)."""
    _make("dev-up")


@dev_app.command("down")
def dev_down() -> None:
    _make("dev-down")


@dev_app.command("seed")
def dev_seed() -> None:
    """Seed the local environment: organisation, demo project, work items."""
    _make("dev-seed")


@dev_app.command("demo")
def dev_demo() -> None:
    """Play the offline demonstration (no service needed)."""
    try:
        from choregos_orchestrator.demo import main as demo_main
    except ImportError:
        # The CLI no longer depends on the orchestrator (it shipped the whole server — SQLAlchemy,
        # Temporal, asyncpg — for this one command). The offline demo needs a checkout of the
        # repository: `make demo` from the root.
        fail("the offline demo needs the repository: run `make demo` from a checkout of choregos")
        return

    demo_main()


def main() -> None:
    try:
        app()
    except ApiError as exc:
        fail(str(exc))


if __name__ == "__main__":
    main()
