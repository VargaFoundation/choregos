"""CLI `choregos` : projets, tickets, runs, trains, findings, workflow, dev."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from .client import ApiError, Client, Profile

app = typer.Typer(add_completion=True, help="Choregos — un ticket entre, une prod maîtrisée sort.")
projects_app = typer.Typer(help="Projets")
items_app = typer.Typer(help="Tickets")
runs_app = typer.Typer(help="Runs")
trains_app = typer.Typer(help="Release trains")
findings_app = typer.Typer(help="Findings")
workflow_app = typer.Typer(help="Workflows")
dev_app = typer.Typer(help="Environnement de développement")
app.add_typer(projects_app, name="projects")
app.add_typer(items_app, name="items")
app.add_typer(runs_app, name="runs")
app.add_typer(trains_app, name="trains")
app.add_typer(findings_app, name="findings")
app.add_typer(workflow_app, name="workflow")
app.add_typer(dev_app, name="dev")

console = Console()


def client() -> Client:
    return Client()


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
    """Enregistre le profil de connexion (`~/.config/choregos/config.json`)."""
    profile = Profile(api_url=api_url, token=token, org=org)
    path = profile.save()
    try:
        me = Client(profile).get("/me")
    except ApiError as exc:
        fail(f"connexion refusée : {exc}")
        return
    console.print(f"[green]✓[/green] connecté comme {me['email']} — profil écrit dans {path}")


@app.command()
def whoami() -> None:
    """Affiche l'utilisateur courant et ses rôles."""
    me = client().get("/me")
    table = Table("organisation", "projet", "rôle", title=me["email"])
    for membership in me["memberships"]:
        table.add_row(membership["org"], membership.get("project_slug") or "—", membership["role"])
    console.print(table)


# ───────────────────────────── projets ─────────────────────────────


@projects_app.command("list")
def projects_list(org: Annotated[str | None, typer.Option()] = None) -> None:
    api = client()
    organization = org or api.profile.org
    page = api.get(f"/orgs/{organization}/projects")
    table = Table("slug", "nom", "état", "tickets actifs", "coût du mois (€)", "trains")
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
    repo: Annotated[str, typer.Option(help="URL du dépôt applicatif")],
    name: Annotated[str | None, typer.Option()] = None,
    template: Annotated[str | None, typer.Option("--template")] = None,
    org: Annotated[str | None, typer.Option()] = None,
    language: Annotated[str, typer.Option()] = "python",
) -> None:
    """Crée un projet ; `--template` déclenche ensuite le provisioning."""
    api = client()
    organization = org or api.profile.org
    payload = {
        "slug": slug,
        "name": name or slug,
        "template_ref": template,
        "config": {
            "slug": slug,
            "org": organization,
            "repo": {"url": repo, "default_branch": "main", "language": language},
        },
    }
    project = api.post(f"/orgs/{organization}/projects", json=payload)
    console.print(f"[green]✓[/green] projet {project['slug']} créé ({project['id']})")
    if template:
        status = api.post(f"/projects/{project['id']}/provision", json={})
        console.print(f"provisioning démarré : {status['status']}")


@projects_app.command("provision")
def projects_provision(project: str, follow: Annotated[bool, typer.Option("--follow")] = False) -> None:
    """Lance (ou relance) le provisioning, et le suit en direct avec `--follow`."""
    api = client()
    status = api.post(f"/projects/{project}/provision", json={})
    console.print(f"provisioning : {status['status']}")
    if not follow:
        return
    for event in api.stream_sse(f"/projects/{project}/provision"):
        data = event.get("data", event)
        console.print(f"· {data.get('step', '?')} — {data.get('status', '')}")


# ───────────────────────────── tickets ─────────────────────────────


@items_app.command("list")
def items_list(project: str, state: Annotated[str | None, typer.Option()] = None) -> None:
    page = client().get(f"/projects/{project}/work-items", params={"state": state} if state else None)
    table = Table("ticket", "titre", "état", "taille", "coût (€)", "PR")
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


@items_app.command("show")
def items_show(item_id: str) -> None:
    """Détail d'un ticket : état, coûts, timeline."""
    api = client()
    item = api.get(f"/work-items/{item_id}")
    console.print(f"[bold]{item['tracker_key']}[/bold] — {item['title']}")
    console.print(f"état : {item.get('state_display') or item['state']} · taille {item.get('size') or '—'}")
    totals = item.get("totals", {})
    console.print(f"coût : {totals.get('cost_eur', 0):.2f} € ({totals.get('runs', 0)} runs)")
    timeline = api.get(f"/work-items/{item_id}/timeline")
    table = Table("quand", "quoi", "détail")
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
    console.print(f"[green]✓[/green] décision `{kind}` transmise")


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
) -> None:
    client().post(f"/work-items/{item_id}/actions", json={"action": action})
    console.print(f"[green]✓[/green] action `{action}` transmise")


# ───────────────────────────── runs ─────────────────────────────


@runs_app.command("list")
def runs_list(item_id: str) -> None:
    runs = client().get(f"/work-items/{item_id}/runs")
    table = Table("run", "étape", "tentative", "état", "backend · modèle", "coût ($)")
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
    """Suit le journal ACP d'un run en direct (SSE)."""
    api = client()
    console.print(f"[dim]suivi du run {run_id} — Ctrl-C pour arrêter[/dim]")
    try:
        for event in api.stream_sse(f"/runs/{run_id}/events"):
            kind = event.get("type", "")
            payload = event.get("payload", event.get("data", {}))
            console.print(
                f"[dim]{event.get('seq', '')}[/dim] {kind} {json.dumps(payload, ensure_ascii=False)[:160]}"
            )
    except KeyboardInterrupt:
        console.print("\ninterrompu")


@runs_app.command("diff")
def runs_diff(run_id: str) -> None:
    diff = client().get(f"/runs/{run_id}/diff")
    table = Table("fichier", "+", "-", "périmètre")
    for file in diff["files"]:
        table.add_row(
            file["path"],
            str(file["additions"]),
            str(file["deletions"]),
            "✓" if file.get("in_scope", True) else "[red]hors périmètre[/red]",
        )
    console.print(table)


# ───────────────────────────── trains ─────────────────────────────


@trains_app.command("status")
def trains_status(project: str, env: Annotated[str, typer.Option()] = "prod") -> None:
    status = client().get(f"/projects/{project}/trains/{env}")
    console.print(f"[bold]{project} → {env}[/bold] : {status['status']}")
    console.print(f"lot : {status['batch_size']} ticket(s) · gelé : {'oui' if status['frozen'] else 'non'}")
    for key in status.get("pending_items", []):
        console.print(f"  · {key}")
    if status.get("freeze_reason"):
        console.print(f"[yellow]motif du gel : {status['freeze_reason']}[/yellow]")


@trains_app.command("depart")
def trains_depart(project: str, env: Annotated[str, typer.Option()] = "prod") -> None:
    client().post(f"/projects/{project}/trains/{env}/depart")
    console.print("[green]✓[/green] départ demandé")


@trains_app.command("freeze")
def trains_freeze(
    project: str,
    reason: Annotated[str, typer.Option(prompt=True)],
    env: Annotated[str, typer.Option()] = "prod",
) -> None:
    client().post(f"/projects/{project}/trains/{env}/freeze", json={"reason": reason})
    console.print("[green]✓[/green] train gelé")


@trains_app.command("unfreeze")
def trains_unfreeze(project: str, env: Annotated[str, typer.Option()] = "prod") -> None:
    client().post(f"/projects/{project}/trains/{env}/unfreeze")
    console.print("[green]✓[/green] train dégelé")


@trains_app.command("approve")
def trains_approve(release_id: str, note: Annotated[str | None, typer.Option()] = None) -> None:
    client().post(f"/releases/{release_id}/approve", json={"note": note})
    console.print("[green]✓[/green] release approuvée")


# ───────────────────────────── findings ─────────────────────────────


@findings_app.command("list")
def findings_list(project: str, status: Annotated[str | None, typer.Option()] = None) -> None:
    page = client().get(f"/projects/{project}/findings", params={"status": status} if status else None)
    table = Table("id", "sévérité", "type", "titre", "état", "ticket")
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
    console.print(f"[green]✓[/green] `{action}` appliqué")


# ───────────────────────────── workflow ─────────────────────────────


@workflow_app.command("validate")
def workflow_validate(
    path: Annotated[Path, typer.Argument()] = Path(".choregos/workflow.yaml"),
    remote: Annotated[bool, typer.Option("--remote", help="valider via l'API plutôt qu'en local")] = False,
) -> None:
    """Valide un workflow. Sans `--remote`, la validation est locale et hors ligne."""
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
        except Exception as exc:  # erreurs de syntaxe : déjà localisées
            console.print(f"[red]✗[/red] {exc}")
            raise typer.Exit(1) from exc

    for warning in report.get("warnings", []):
        console.print(f"[yellow]![/yellow] {warning['message']} ({warning.get('path') or ''})")
    if report["valid"]:
        console.print(f"[green]✓[/green] {path} est valide")
        return
    for error in report["errors"]:
        where = f"ligne {error['line']}" if error.get("line") else (error.get("path") or "")
        console.print(f"[red]✗[/red] {error['message']} — {where}")
    raise typer.Exit(1)


@workflow_app.command("show")
def workflow_show(
    path: Annotated[Path, typer.Argument()] = Path(".choregos/workflow.yaml"),
    mermaid: Annotated[bool, typer.Option("--mermaid")] = False,
) -> None:
    """Affiche le workflow : états, transitions, ou diagramme Mermaid."""
    from choregos_core import parse_workflow, to_mermaid

    workflow, _ = parse_workflow(path.read_text(encoding="utf-8"), strict=False)
    if mermaid:
        console.print(to_mermaid(workflow))
        return
    table = Table("état", "libellé", "acteur sortant", "gates")
    for name, state in workflow.states.items():
        transitions = workflow.transitions_from(name)
        actor = transitions[0].by or (transitions[0].via or "—") if transitions else "—"
        gates = ", ".join(transitions[0].gate_names()) if transitions else ""
        table.add_row(name, state.display, str(actor), gates)
    console.print(table)


@workflow_app.command("templates")
def workflow_templates() -> None:
    from choregos_core.dsl import TEMPLATE_NAMES, load_template

    for name in TEMPLATE_NAMES:
        workflow = load_template(name)
        console.print(
            f"[bold]{name}[/bold] v{workflow.metadata.version} — "
            f"{len(workflow.states)} états, {len(workflow.transitions)} transitions"
        )
        console.print(f"  {workflow.metadata.description or ''}")


# ───────────────────────────── dev ─────────────────────────────


def _make(target: str) -> None:
    """Délègue à la cible `make` correspondante, depuis la racine du dépôt."""
    import shutil
    import subprocess

    make = shutil.which("make")
    if make is None:
        fail("`make` est introuvable : lancez la cible à la main")
        return
    subprocess.run([make, target], check=False)  # noqa: S603 - chemin résolu


@dev_app.command("up")
def dev_up() -> None:
    """Monte l'environnement de développement (kind + Tilt)."""
    _make("dev-up")


@dev_app.command("down")
def dev_down() -> None:
    _make("dev-down")


@dev_app.command("seed")
def dev_seed() -> None:
    """Peuple l'environnement local : organisation, projet de démonstration, tickets."""
    _make("dev-seed")


@dev_app.command("demo")
def dev_demo() -> None:
    """Joue la démonstration hors ligne (aucun service requis)."""
    from choregos_orchestrator.demo import main as demo_main

    demo_main()


def main() -> None:
    try:
        app()
    except ApiError as exc:
        fail(str(exc))


if __name__ == "__main__":
    main()
