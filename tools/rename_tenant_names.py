#!/usr/bin/env python3
"""Re-publish a tenant's French names as English versions (S39-11c).

    uv run python tools/rename_tenant_names.py --api https://choregos.example --org acme \
        --token-file ~/.choregos-admin-token                  # dry run: prints the plan, writes nothing
    uv run python tools/rename_tenant_names.py ... --apply    # executes it

It talks to the public API only, with the bearer token of an organisation administrator. It
**never deletes, never rewrites a published version and never renames a project slug** (a slug is a
URL). What it does, each step idempotent (a second run finds nothing to do):

1. skills of the `joiners-leavers` template, under their English slugs, when absent;
2. agents of the template, under their English slugs, when absent;
3. organisation connectors: each French one is copied under its English name (type, kind, config,
   secret references verbatim), its operations' policy and groups copied, the projects' tightenings too;
4. workflows: a project's HR workflow that still has the French marker state is re-published from the
   template as its next version, its routing label renamed; the labels of hand-made workflows are
   translated and published as the next version (state ids are never touched);
5. project display names;
6. the French agents move to `deprecated` (they still run; retiring them is a person's gesture).

A work item is pinned to the workflow version it was born on: publishing a new version never
moves it. Even so, a workflow that has open work items is left alone unless `--allow-open`; the
report at the end lists those items with their console URL in every case.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_MAPPING = pathlib.Path(__file__).with_suffix(".yaml")
DEFAULT_TEMPLATE = ROOT / "templates" / "joiners-leavers"
API = "/api/v1"


@dataclass
class Step:
    """One thing the script would do: what, on which object, and how to do it."""

    kind: str
    target: str
    detail: str
    run: Callable[[], Awaitable[None]]


@dataclass
class Report:
    steps: list[Step] = field(default_factory=list)
    open_items: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


class Failure(Exception):  # noqa: N818 - a refused API call, reported as is
    pass


class Tenant:
    """The public API of one organisation, through an `httpx.AsyncClient` (already authenticated)."""

    def __init__(self, client: httpx.AsyncClient, org: str, console: str = "") -> None:
        self.http, self.org, self.console = client, org, console.rstrip("/")

    async def call(self, method: str, path: str, *ok: int, **kwargs: Any) -> Any:
        response = await self.http.request(method, f"{API}{path}", **kwargs)
        if response.status_code not in (ok or (200, 201, 204)):
            raise Failure(f"{method} {path}: {response.status_code} {response.text[:300]}")
        return response.json() if response.content else None

    async def find(self, path: str) -> Any | None:
        response = await self.http.get(f"{API}{path}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise Failure(f"GET {path}: {response.status_code} {response.text[:300]}")
        return response.json()


# ───────────────────────────────────────── the plan ─────────────────────────────────────────


def _skill_files(folder: pathlib.Path) -> dict[str, str]:
    return {
        str(p.relative_to(folder)): p.read_text(encoding="utf-8")
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def _skill_name(files: dict[str, str]) -> str:
    header = yaml.safe_load(files["SKILL.md"].split("---", 2)[1]) or {}
    return str(header["name"])


async def _plan_skills(tenant: Tenant, template: pathlib.Path, report: Report) -> None:
    for folder in sorted((template / "skills").iterdir()):
        files = _skill_files(folder)
        name = _skill_name(files)
        if await tenant.find(f"/orgs/{tenant.org}/skills/{name}") is not None:
            continue

        async def create(files: dict[str, str] = files) -> None:
            await tenant.call("POST", f"/orgs/{tenant.org}/skills", 201, json={"files": files})

        report.steps.append(Step("skill", name, "install version 1 from the template", create))


async def _plan_agents(tenant: Tenant, template: pathlib.Path, report: Report) -> list[str]:
    slugs: list[str] = []
    for path in sorted((template / "agents").glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        slugs.append(document["slug"])
        if await tenant.find(f"/orgs/{tenant.org}/agents/{document['slug']}") is not None:
            continue

        async def create(document: dict[str, Any] = document) -> None:
            await tenant.call("POST", f"/orgs/{tenant.org}/agents", 201, json=document)

        report.steps.append(Step("agent", document["slug"], "create version 1 from the template", create))
    return slugs


async def _projects(tenant: Tenant) -> list[dict[str, Any]]:
    page = await tenant.call("GET", f"/orgs/{tenant.org}/projects")
    return list(page["items"] if isinstance(page, dict) else page)


def _policy_differences(
    old: dict[str, Any], new: dict[str, Any] | None, renamed: dict[str, str]
) -> list[tuple[str, dict[str, Any]]]:
    """The operations of `old` whose policy or groups the copy does not carry yet."""
    known = {o["name"]: o for o in (new or {}).get("operations", [])}
    changes = []
    for operation in old["operations"]:
        target = known.get(renamed.get(operation["name"], operation["name"]))
        if target is None:
            continue
        patch = {
            key: operation[key]
            for key in ("policy", "groups")
            if operation.get(key) is not None and operation[key] != target.get(key)
        }
        if patch:
            changes.append((target["name"], patch))
    return changes


async def _plan_connectors(tenant: Tenant, mapping: dict[str, Any], report: Report) -> None:
    renamed_ops: dict[str, str] = mapping.get("operations", {})
    projects = await _projects(tenant)
    for old_name, new_name in mapping["connectors"].items():
        old = await tenant.find(f"/orgs/{tenant.org}/connectors/{old_name}")
        if old is None:
            continue
        new = await tenant.find(f"/orgs/{tenant.org}/connectors/{new_name}")
        tightenings = await _tightenings(tenant, projects, old_name, new_name, renamed_ops)
        changes = _policy_differences(old, new, renamed_ops)
        if new is not None and old["type"] == "mcp" and not new["operations"]:
            changes = [("*", {})]  # discovery has not run on the copy yet
        if new is not None and not changes and not tightenings:
            continue

        async def copy(
            old: dict[str, Any] = old,
            new_name: str = new_name,
            exists: bool = new is not None,
            tightenings: list[tuple[str, str, str, str]] = tightenings,
        ) -> None:
            base = f"/orgs/{tenant.org}/connectors"
            if not exists:
                body = {key: old[key] for key in ("type", "kind", "config", "secret_refs")}
                await tenant.call("POST", base, 201, json={"name": new_name, **body})
            created = await tenant.call("GET", f"{base}/{new_name}")
            if old["type"] == "mcp" and not created["operations"]:
                await tenant.call("POST", f"{base}/{new_name}/discover", 200)
                created = await tenant.call("GET", f"{base}/{new_name}")
            for operation, patch in _policy_differences(old, created, renamed_ops):
                await tenant.call("PATCH", f"{base}/{new_name}/operations/{operation}", 200, json=patch)
            for project_id, connector, operation, policy in tightenings:
                await tenant.call(
                    "PUT",
                    f"/projects/{project_id}/operations/{connector}/{operation}",
                    200,
                    json={"policy": policy},
                )

        what = "copy" if new is None else "complete the copy of"
        report.steps.append(
            Step("connector", new_name, f"{what} `{old_name}` (config, secret references, policies)", copy)
        )


async def _tightenings(
    tenant: Tenant, projects: list[dict[str, Any]], old: str, new: str, renamed: dict[str, str]
) -> list[tuple[str, str, str, str]]:
    """(project id, new connector, operation, policy) a project narrowed on the old connector."""
    out: list[tuple[str, str, str, str]] = []
    for project in projects:
        operations = await tenant.call("GET", f"/projects/{project['id']}/operations")
        have = {(o["connector"], o["operation"]): o.get("project_policy") for o in operations}
        for entry in operations:
            if entry["connector"] != old or not entry.get("project_policy"):
                continue
            operation = renamed.get(entry["operation"], entry["operation"])
            if have.get((new, operation)) != entry["project_policy"]:
                out.append((project["id"], new, operation, entry["project_policy"]))
    return out


def _translate_labels(document: dict[str, Any], labels: dict[str, str]) -> bool:
    changed = False
    for state in (document.get("states") or {}).values():
        if isinstance(state, dict) and state.get("display") in labels:
            state["display"] = labels[state["display"]]
            changed = True
    return changed


async def _open_items(tenant: Tenant, project: dict[str, Any], workflow: str) -> list[dict[str, Any]]:
    page = await tenant.call("GET", f"/projects/{project['id']}/work-items")
    return [i for i in page["items"] if i.get("closed_at") is None and i.get("workflow_name") == workflow]


async def _plan_workflows(
    tenant: Tenant, template: pathlib.Path, mapping: dict[str, Any], allow_open: bool, report: Report
) -> None:
    marker = mapping["french_marker_state"]
    for project in await _projects(tenant):
        slug = project["slug"]
        for workflow in await tenant.call("GET", f"/projects/{project['id']}/workflows"):
            name = workflow["name"]
            current = await tenant.call("GET", f"/projects/{project['id']}/workflows/{name}")
            document = current.get("json") or yaml.safe_load(current["yaml"])
            new_yaml: str | None = None
            why = ""
            if name in mapping["workflows"] and marker in (document.get("states") or {}):
                new_yaml = (template / mapping["workflows"][name]).read_text(encoding="utf-8")
                why = "re-publish the template's English workflow"
            elif (labels := mapping["labels"].get(f"{slug}/{name}")) is not None:
                edited = yaml.safe_load(current["yaml"])
                if _translate_labels(edited, labels):
                    new_yaml = yaml.safe_dump(edited, sort_keys=False, allow_unicode=True)
                    why = "translate the state labels (ids untouched)"
            if new_yaml is None:
                continue
            opened = await _open_items(tenant, project, name)
            report.open_items += [
                f"{slug}/{name} v{i.get('workflow_version')}: {i['tracker_key']} in `{i['state']}` "
                f"{tenant.console}/p/{slug}/items/{i['id']}"
                for i in opened
            ]
            if opened and not allow_open:
                report.skipped.append(f"{slug}/{name}: {len(opened)} open work item(s), pass --allow-open")
                continue

            async def publish(
                project_id: str = project["id"], name: str = name, text: str = new_yaml
            ) -> None:
                await tenant.call("PUT", f"/projects/{project_id}/workflows/{name}", 200, json={"yaml": text})

            report.steps.append(
                Step("workflow", f"{slug}/{name}", f"v{workflow['version'] + 1}: {why}", publish)
            )
        await _plan_routing(tenant, project, mapping, report)


async def _plan_routing(
    tenant: Tenant, project: dict[str, Any], mapping: dict[str, Any], report: Report
) -> None:
    routing = await tenant.call("GET", f"/projects/{project['id']}/workflow-routing")
    labels: dict[str, str] = mapping["routing_labels"]
    rules = routing["rules"]
    if not any(set(r.get("when", {}).get("labels_any", [])) & set(labels) for r in rules):
        return
    # The routing is checked against active workflows: the rule changes after the workflows step.

    async def rename(project_id: str = project["id"]) -> None:
        current = await tenant.call("GET", f"/projects/{project_id}/workflow-routing")
        for rule in current["rules"]:
            any_of = rule.get("when", {}).get("labels_any")
            if any_of:
                rule["when"]["labels_any"] = [labels.get(label, label) for label in any_of]
        await tenant.call("PUT", f"/projects/{project_id}/workflow-routing", 200, json=current)

    report.steps.append(Step("routing", project["slug"], f"rename the labels {sorted(labels)}", rename))


async def _plan_projects(tenant: Tenant, mapping: dict[str, Any], report: Report) -> None:
    for project in await _projects(tenant):
        wanted = mapping["projects"].get(project["slug"])
        if wanted is None or wanted == project["name"]:
            continue

        async def rename(project_id: str = project["id"], wanted: str = wanted) -> None:
            await tenant.call("PATCH", f"/projects/{project_id}", 200, json={"name": wanted})

        report.steps.append(Step("project", project["slug"], f"display name -> {wanted!r}", rename))


async def _plan_deprecations(
    tenant: Tenant, mapping: dict[str, Any], new_slugs: list[str], report: Report
) -> None:
    for old, new in mapping["agents"].items():
        agent = await tenant.find(f"/orgs/{tenant.org}/agents/{old}")
        if agent is None or agent.get("lifecycle", "active") != "active" or new not in new_slugs:
            continue

        async def deprecate(old: str = old, new: str = new) -> None:
            body = {"to": "deprecated", "reason": f"replaced by {new} (S39-11)"}
            await tenant.call("POST", f"/orgs/{tenant.org}/agents/{old}/lifecycle", 200, json=body)

        report.steps.append(Step("agent", old, f"deprecate (replaced by `{new}`)", deprecate))


async def plan(
    tenant: Tenant,
    mapping: dict[str, Any],
    template: pathlib.Path = DEFAULT_TEMPLATE,
    *,
    allow_open: bool = False,
) -> Report:
    """Reads the tenant, writes nothing: the steps, in the order they must run."""
    report = Report()
    await _plan_skills(tenant, template, report)
    new_slugs = await _plan_agents(tenant, template, report)
    await _plan_connectors(tenant, mapping, report)
    await _plan_workflows(tenant, template, mapping, allow_open, report)
    await _plan_projects(tenant, mapping, report)
    await _plan_deprecations(tenant, mapping, new_slugs, report)
    return report


async def apply(report: Report) -> list[str]:
    """Runs the steps in order; a failure stops the run (what was done stays, a re-run resumes)."""
    done: list[str] = []
    for step in report.steps:
        await step.run()
        done.append(f"{step.kind} {step.target}")
    return done


def render(report: Report) -> str:
    lines = [f"{'KIND':<10} {'TARGET':<34} DETAIL"]
    lines += [f"{s.kind:<10} {s.target:<34} {s.detail}" for s in report.steps] or ["nothing to do"]
    if report.skipped:
        lines += ["", "SKIPPED:", *[f"  {s}" for s in report.skipped]]
    if report.open_items:
        lines += ["", "OPEN WORK ITEMS still on a version that is replaced (they finish where they are):"]
        lines += [f"  {i}" for i in report.open_items]
    return "\n".join(lines)


# ───────────────────────────────────────── the command ─────────────────────────────────────────


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--api", required=True, help="console/API base URL, e.g. https://choregos.example")
    parser.add_argument("--org", required=True)
    parser.add_argument("--token-file", help="file holding an org admin's API token (or CHOREGOS_TOKEN)")
    parser.add_argument("--mapping", default=str(DEFAULT_MAPPING))
    parser.add_argument("--template-dir", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--apply", action="store_true", help="execute the plan (default: dry run)")
    parser.add_argument(
        "--allow-open", action="store_true", help="also publish workflows with open work items"
    )
    args = parser.parse_args(argv)
    token = (
        pathlib.Path(args.token_file).expanduser().read_text(encoding="utf-8").strip()  # noqa: ASYNC240
        if args.token_file
        else os.environ.get("CHOREGOS_TOKEN", "")
    )
    if not token:
        print("no token: pass --token-file or set CHOREGOS_TOKEN", file=sys.stderr)
        return 2
    mapping = yaml.safe_load(pathlib.Path(args.mapping).read_text(encoding="utf-8"))  # noqa: ASYNC240
    async with httpx.AsyncClient(
        base_url=args.api, headers={"Authorization": f"Bearer {token}"}, timeout=60
    ) as http:
        tenant = Tenant(http, args.org, args.api)
        report = await plan(tenant, mapping, pathlib.Path(args.template_dir), allow_open=args.allow_open)
        print(render(report))
        if not args.apply:
            print("\ndry run: nothing was written; add --apply to execute the plan")
            return 0
        for done in await apply(report):
            print(f"done: {done}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
