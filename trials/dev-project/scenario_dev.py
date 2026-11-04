# SPDX-License-Identifier: Apache-2.0
"""The development project on the dev tenant (S21-25): the `github-software-delivery` template,
a real sandbox repository, real agents, real decisions.

Three work items, one per workflow: a bug (`dev-simple`, up to a verified production with no
human), a study (`study`, a MADR 4 ADR merged after the architects' decision), a feature
(`dev-complex`, three human decisions and the train captain). This script prepares, opens and
follows; it DECIDES nothing: a decision is taken in the console, by a re-authenticated person
(ADR 0030).

    uv run python trials/dev-project/scenario_dev.py prepare       # project, connectors, settings
    uv run python trials/dev-project/scenario_dev.py requirements  # what the project must plug in
    uv run python trials/dev-project/scenario_dev.py open bug      # a labelled issue, in the repository
    uv run python trials/dev-project/scenario_dev.py follow <id>   # the state, and where to decide
    uv run python trials/dev-project/scenario_dev.py verify <id>   # the runs, the decisions, the PR

Variables:
    CHOREGOS_DEV_URL       the address of the tenant's console
    CHOREGOS_DEV_TOKEN     an administrator's API token (`chg_...`); never written here
    CHOREGOS_DEV_ORG       the tenant's organisation
    TRIAL_REPOSITORY       (default DiametralGroup/choregos-sandbox-dev) the sandbox repository
    The Choregos GitHub App must be installed on the repository (Settings, Connections): the
    installation is discovered and claimed by the organisation, nobody types its id (ADR 0073).
    TRIAL_FAKES_URL        (default http://choregos-demo-fakes:8090) the fakes, seen from the cluster
    TRIAL_TOKEN_REF        (optional) the reference of the fakes' token, e.g. `env:CONNECTOR_DEMO_TOKEN`
    TRIAL_BACKEND          (default opencode) the backend the dev tenant runs
    TRIAL_MODELS           (default standard=platform/standard,cheap=platform/cheap) the profiles served
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any

import httpx

URL = os.environ.get("CHOREGOS_DEV_URL", "").rstrip("/")
REPOSITORY = os.environ.get("TRIAL_REPOSITORY", "DiametralGroup/choregos-sandbox-dev")
FAKES = os.environ.get("TRIAL_FAKES_URL", "http://choregos-demo-fakes:8090").rstrip("/")
BACKEND = os.environ.get("TRIAL_BACKEND", "opencode")
MODELS = dict(
    pair.split("=", 1)
    for pair in os.environ.get("TRIAL_MODELS", "standard=platform/standard,cheap=platform/cheap").split(",")
)
PROJECT = "dev"
TEMPLATE = "github-software-delivery@1.0.0"
#: One issue per workflow: its labels (the template's routing) and what it asks for.
ISSUES = {
    "bug": (
        ["agent-ready", "bug"],
        "Credits are not deducted from the invoice total",
        "`invoices.total.compute_total` ignores the credits passed to it: an invoice of 100 with a credit "
        "of 20 shows 100. Expected: 80. Add a test.",
    ),
    "adr": (
        ["agent-ready", "adr"],
        "Decide how invoices are sent: a synchronous call or a queue",
        "Sending an invoice calls the mail provider synchronously. Decide whether to keep it or move to a "
        "queue, with the trade-offs, as an ADR.",
    ),
    "feature": (
        ["agent-ready", "feature"],
        "Totals in several currencies",
        "An invoice can hold lines in EUR and USD. Compute one total per currency, and keep the "
        "single-currency behaviour unchanged.",
    ),
}
TERMINAL = {"verified", "adopted", "abandoned"}
#: The first line of the commit that puts the three defects back in the sandbox (see the README).
SEED_COMMIT = "chore(seed):"


def consumed_by(title: str, merged: list[dict[str, Any]], seeded_at: str | None) -> dict[str, Any] | None:
    """The pull request that already resolved this issue since the sandbox was last seeded, if any.

    Each issue asks for a change the sandbox was seeded without; once a pull request carrying its
    title is merged, the change is on `main` and a new issue would have nothing to do. GitHub
    timestamps are ISO 8601 in UTC, so they compare as strings.
    """
    for pull in merged:
        if pull["title"] == title and (seeded_at is None or pull["mergedAt"] > seeded_at):
            return pull
    return None


def _gh_json(*arguments: str) -> Any:
    # `gh`, already signed in on the workstation: fixed arguments, no shell.
    output = subprocess.run(["gh", *arguments], check=True, capture_output=True, text=True)  # noqa: S603, S607
    return json.loads(output.stdout)


def _seeded_at() -> str | None:
    commits = _gh_json("api", f"repos/{REPOSITORY}/commits?per_page=100")
    for commit in commits:
        if commit["commit"]["message"].startswith(SEED_COMMIT):
            return str(commit["commit"]["committer"]["date"])
    return None


def require(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        sys.exit(f"missing variable: {name} (see the header of this script)")
    return value


def client() -> httpx.Client:
    return httpx.Client(
        base_url=f"{require('CHOREGOS_DEV_URL').rstrip('/')}/api/v1",
        headers={"Authorization": f"Bearer {require('CHOREGOS_DEV_TOKEN')}"},
        timeout=60,
    )


def expect(response: httpx.Response, *expected: int) -> Any:
    if response.status_code not in expected:
        sys.exit(
            f"{response.request.method} {response.request.url.path} -> {response.status_code}: "
            f"{response.text[:400]}"
        )
    return response.json() if response.content else None


def _project(http: httpx.Client) -> dict[str, Any]:
    return dict(expect(http.get(f"/projects/{require('CHOREGOS_DEV_ORG')}:{PROJECT}"), 200))


def prepare() -> None:
    """The administrator's gesture: the project born from the template, its connectors, its settings."""
    org = require("CHOREGOS_DEV_ORG")
    with client() as http:
        projects = {p["slug"] for p in expect(http.get(f"/orgs/{org}/projects"), 200)["items"]}
        if PROJECT not in projects:
            body = {
                "slug": PROJECT,
                "name": "Development",
                "template_ref": TEMPLATE,
                "config": {"slug": PROJECT, "org": org, "repo": {"url": f"https://github.com/{REPOSITORY}.git"}},
            }
            expect(http.post(f"/orgs/{org}/projects", json=body), 201)
        project = _project(http)
        pid = project["id"]
        reference = os.environ.get("TRIAL_TOKEN_REF", "")
        connectors = {
            "tracker": {"type": "github-issues", "config": {"repo": REPOSITORY}},
            # No merge queue on the sandbox: the platform merges directly.
            "scm": {
                "type": "github",
                "config": {"repo": REPOSITORY, "merge_queue": False},
            },
            "cd": {
                "type": "demo",
                "config": {"url": f"{FAKES}/cd/mcp"},
                **({"secret_refs": {"api_key": reference}} if reference else {}),
            },
        }
        for kind, body in connectors.items():
            expect(http.put(f"/projects/{pid}/connectors/{kind}", json=body), 200)
        # The backend and the profiles the dev tenant serves: a new project gets `claude-code` and no
        # profile (first HR run of 06/10). The `smoke_ok` fact that the production agent checks.
        config = {
            **(project.get("config") or {}),
            "agent": {"default_backend": BACKEND, "allowed_backends": [BACKEND]},
            "models": {"profiles": MODELS},
            "dod": {"facts": {"smoke_ok": "python -m invoices.smoke"}},
        }
        expect(http.patch(f"/projects/{pid}", json={"config": config}), 200)
        print(f"project {PROJECT}: {TEMPLATE} - {REPOSITORY} - agents on {BACKEND} - {URL}/p/{PROJECT}")
    requirements()


def requirements() -> None:
    """What the three workflows require, and whether it is plugged in. The exit code says so."""
    with client() as http:
        rows = expect(http.get(f"/projects/{_project(http)['id']}/requirements"), 200)
    missing = False
    for row in rows:
        plugged = row.get("connector") or {}
        state = f"{plugged.get('type')}" if plugged else "MISSING"
        missing = missing or not plugged
        print(f"{row['capability']:10s} {state:14s} {(row.get('reasons') or [''])[0]}")
    if missing:
        sys.exit(1)


def open_issue(kind: str) -> None:
    """An issue in the sandbox, labelled as the routing expects: the webhook makes it a work item."""
    labels, title, body = ISSUES[kind]
    merged = _gh_json("pr", "list", "--repo", REPOSITORY, "--state", "merged", "--limit", "100",
                      "--json", "number,title,mergedAt,url")  # fmt: skip
    pull = consumed_by(title, merged, _seeded_at())
    if pull:
        sys.exit(
            f"{kind}: already resolved on {REPOSITORY} by {pull['url']} - reseed the sandbox first "
            "(trials/dev-project/README.md, 'Reseeding the sandbox')"
        )
    # `gh`, already signed in on the workstation: fixed arguments, no shell.
    command = ["gh", "issue", "create", "--repo", REPOSITORY, "--title", title, "--body", body]
    command += [arg for label in labels for arg in ("--label", label)]
    output = subprocess.run(command, check=True, capture_output=True, text=True)  # noqa: S603
    print(f"{kind}: {output.stdout.strip()} - the work item is born in Choregos at the webhook")


def follow(ident: str, minutes: int = 120) -> None:
    """The work item's state, and WHERE to decide when it waits for a person."""
    seen = None
    end = time.monotonic() + minutes * 60
    with client() as http:
        while time.monotonic() < end:
            item = dict(expect(http.get(f"/work-items/{ident}"), 200))
            request = item.get("pending_request")
            summary = f"{item['workflow_name']} · {item['state']} ({item.get('state_display')})"
            if request:
                summary += f" - to decide: {URL}/p/{PROJECT}/items/{item['id']}"
            if item.get("failure"):
                summary += f" - DEAD: {item['failure']}"
            if summary != seen:
                print(time.strftime("%H:%M:%S"), summary, flush=True)
                seen = summary
            if item["state"] in TERMINAL or item.get("failure"):
                return
            time.sleep(20)


def verify(ident: str) -> None:
    """The work item's file: each run (its agent, its transition, its cost), each decision, the PR."""
    with client() as http:
        item = dict(expect(http.get(f"/work-items/{ident}"), 200))
        runs = expect(http.get(f"/work-items/{ident}/runs"), 200)
        decisions = expect(http.get(f"/work-items/{ident}/decisions"), 200)
    print(f"{item['tracker_key']} - {item['workflow_name']} - {item['state']}")
    print(f"  PR: {item.get('pr_url')}")
    for run in runs:
        agent, status = run.get("agent_slug"), run.get("status")
        print(f"  run {run.get('transition_id')}: {agent} {status} {run.get('cost_usd')} $")
    for decision in decisions or []:
        print(f"  decision {decision}")
    print(f"  total cost: {item['totals'].get('cost_usd')} $")
    if item["state"] not in TERMINAL:
        sys.exit(1)


if __name__ == "__main__":
    command, *arguments = sys.argv[1:] or ["help"]
    commands = {"prepare": prepare, "requirements": requirements, "open": open_issue, "follow": follow,
                "verify": verify}  # fmt: skip
    if command not in commands:
        sys.exit(__doc__)
    commands[command](*arguments)  # type: ignore[operator]
