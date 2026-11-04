# SPDX-License-Identifier: Apache-2.0
"""The foundation trial on the dev tenant: a REAL agent, a REAL PR, a re-authenticated human decision.

Three beats, because a human steps in the middle:

    uv run python trials/dev-platform/scenario_dev.py prepare   # project, ontology, findings, work item
    # ... the agent (opencode, a gateway model) investigates the finding and proposes a PR ...
    # ... a human approves the proposal, re-authenticated (see README.md) ...
    uv run python trials/dev-platform/scenario_dev.py verify    # the PR, then the collector's re-run

Variables:
    CHOREGOS_DEV_URL    the address of the tenant's console (e.g. `https://choregos.example`)
    CHOREGOS_DEV_TOKEN  a human's API token (`chg_...`), created in the console; never written here
    CHOREGOS_DEV_ORG    the tenant's organisation
    TRIAL_REPOSITORY    the sandbox repository where the tenant's GitHub App is installed (`org/name`)
    TRIAL_PROJECT       (default essai-it4it)
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
from typing import Any

import httpx

HERE = pathlib.Path(__file__).resolve().parent
IT4IT = HERE.parents[1] / "packages" / "ontology" / "tests" / "fixtures" / "it4it"
#: Required by `client()`: no tenant is assumed - Choregos is an open project.
URL = os.environ.get("CHOREGOS_DEV_URL", "").rstrip("/")
PROJECT = os.environ.get("TRIAL_PROJECT", "essai-it4it")  # lang: fr-ok
KEY = "os-reboot-required"
STATE = HERE / ".state.json"  # identifiers of the last run (outside git)


def require(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        sys.exit(f"missing variable: {name} (see the header of this script)")
    return value


def report(*lines: dict[str, Any]) -> str:
    body = [json.dumps(line) for line in lines]
    body.append(json.dumps({"_end": True, "lines": len(lines)}))
    return "\n".join(body) + "\n"


def line(check: str, scope: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"layer": "os", "check": check, "scope": scope, "status": status, **extra}


def client() -> httpx.Client:
    token = require("CHOREGOS_DEV_TOKEN")
    base = require("CHOREGOS_DEV_URL").rstrip("/")
    return httpx.Client(base_url=f"{base}/api/v1", headers={"Authorization": f"Bearer {token}"}, timeout=60)


def check_response(response: httpx.Response, *expected: int) -> Any:
    if response.status_code not in expected:
        sys.exit(
            f"{response.request.method} {response.request.url.path} -> {response.status_code}: "
            f"{response.text[:600]}"
        )
    return response.json() if response.content else None


def prepare() -> None:
    org, repository = require("CHOREGOS_DEV_ORG"), require("TRIAL_REPOSITORY")
    with client() as http:
        project = check_response(
            http.post(
                f"/orgs/{org}/projects",
                json={
                    "slug": PROJECT,
                    "name": "Trial: IT4IT (foundation)",
                    "config": {
                        "slug": PROJECT,
                        "org": org,
                        "agent": {"default_backend": "opencode", "allowed_backends": ["opencode"]},
                        "models": {"profiles": {"standard": "platform/standard"}},
                    },
                },
            ),
            201,
        )
        pid = project["id"]
        print(f"project {org}/{PROJECT}: {pid}")
        check_response(
            http.put(f"/projects/{pid}/connectors/tracker", json={"type": "internal", "config": {}}), 200
        )
        check_response(
            http.put(
                f"/projects/{pid}/connectors/scm", json={"type": "github", "config": {"repo": repository}}
            ),
            200,
        )
        workflow = (HERE / "workflow-it4it.yaml").read_text(encoding="utf-8")
        check_response(http.put(f"/projects/{pid}/workflow", json={"yaml": workflow}), 200)

        files = {
            p.relative_to(IT4IT).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(IT4IT.rglob("*.yaml"))
        }
        files["actions/open_infra_pr.yaml"] = files["actions/open_infra_pr.yaml"].replace(
            "repo: example/infra", f"repo: {repository}"
        )
        ontology = check_response(http.put(f"/projects/{pid}/ontology", json={"files": files}), 200)
        print(f"ontology {ontology['name']} {ontology['version']}: {ontology['mcp_tools']} tools")

        for ident, os_, ip in (("node-1", "ubuntu", "10.0.0.11"), ("node-2", "debian", "10.0.0.12")):
            proposal = check_response(
                http.post(
                    f"/projects/{pid}/proposals",
                    json={
                        "action_type": "register_host",
                        "params": {"id": ident, "name": ident, "os": os_, "ip": ip},
                        "justification": "Inventory for the trial.",
                    },
                ),
                200,
                201,
            )
            print(f"host {ident}: {proposal['status']}")

        week = report(
            line(
                "reboot-required", "node-1", "finding", severity="medium", evidence="kernel 6.8.0-45 pending"
            ),
            line(
                "reboot-required", "node-2", "finding", severity="high", evidence="kernel 6.8.0-45 pending"
            ),
            line("ntp-drift", "node-1", "finding", severity="low", value=2.5),
            line("disk-usage", "node-1", "ok", value=41.5),
        )
        summary = check_response(
            http.post(
                f"/projects/{pid}/observations",
                content=week.encode(),
                headers={"Content-Type": "application/x-ndjson"},
            ),
            200,
        )
        print(f"report: {summary}")

        item = check_response(
            http.post(
                f"/projects/{pid}/work-items",
                json={
                    "title": "Investigate the most serious health finding and propose its fix",
                    "body": "Findings: `finding_search`. Propose a PR with `action_open_infra_pr`.",
                    "start": True,
                },
            ),
            201,
        )
        STATE.write_text(json.dumps({"project": pid, "item": item["id"]}, indent=2), encoding="utf-8")
        print(f"work item {item['id']} started; follow it: {URL}/p/{PROJECT}")
        wait_for_the_agent(http, pid, item["id"])


def wait_for_the_proposal(
    http: httpx.Client, pid: str, proposal: str, *statuses: str, seconds: int = 180
) -> dict[str, Any]:
    """Since S20-08, a proposal runs in Temporal, outside the request that decides it: we wait for
    the state we want to read (or the last one read at the deadline)."""
    end = time.monotonic() + seconds
    while True:
        record = dict(check_response(http.get(f"/projects/{pid}/proposals/{proposal}"), 200))
        if record["status"] in statuses or time.monotonic() > end:
            return record
        time.sleep(2)


def wait_for_the_agent(http: httpx.Client, pid: str, item_id: str, minutes: int = 25) -> None:
    end = time.monotonic() + minutes * 60
    while time.monotonic() < end:
        item = check_response(http.get(f"/work-items/{item_id}"), 200)
        state = item.get("state")
        if state in {"propose", "needs_human", "done"}:
            break
        time.sleep(20)
    print(f"work item: {item.get('state')}")
    for run in check_response(http.get(f"/work-items/{item_id}/runs"), 200):
        print(f"  run {run['id']}: {run.get('status')} - {run.get('cost_usd')} USD")
    proposals = check_response(
        http.get(f"/projects/{pid}/proposals", params={"status": "pending_approval"}), 200
    )
    for p in proposals:
        print(f"\nproposal {p['proposal']}: {p['action_type']} on {p['target']}")
        print(f"  the agent's justification: {p['justification']}")
        print(f"  to decide in the console, re-authenticated: {URL}/p/{PROJECT}/actions/{p['proposal']}")
    if not proposals:
        print("no proposal pending: read the run's journal in the console")


def verify() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    pid = state["project"]
    with client() as http:
        proposals = check_response(http.get(f"/projects/{pid}/proposals"), 200)
        pull_requests = [p for p in proposals if p["action_type"] == "open_infra_pr"]
        for p in pull_requests:
            print(f"{p['proposal']}: {p['status']}")
            for decision in p["decisions"]:
                print(
                    f"  decision: {decision['decision']} by {decision['by']}, "
                    f"authenticated {decision.get('auth_age_seconds')} s before"
                )
            for effect in p["effects"]:
                print(f"  PR: {effect.get('url')} (branch {effect.get('head')})")
            for proof in p["evidence"]:
                print(f"  proof {proof['name']}: {proof['status']}")
        verification = check_response(
            http.post(
                f"/projects/{pid}/proposals",
                json={
                    "action_type": "verify_finding_fixed",
                    "target": [KEY],
                    "params": {},
                    "justification": "PR merged.",
                },
            ),
            201,
        )
        # The re-run settles only a proof REQUESTED before it: we wait for the action to be waiting.
        verification = wait_for_the_proposal(
            http, pid, verification["proposal"], "awaiting_evidence", "failed"
        )
        print(f"verification {verification['proposal']}: {verification['status']}")
        rerun = report(
            line("reboot-required", "node-1", "ok"),
            line("reboot-required", "node-2", "ok"),
            line("ntp-drift", "node-1", "finding", severity="low"),
        )
        summary = check_response(
            http.post(
                f"/projects/{pid}/observations",
                content=rerun.encode(),
                headers={"Content-Type": "application/x-ndjson"},
            ),
            200,
        )
        print(f"collector re-run: {summary}")
        final = wait_for_the_proposal(http, pid, verification["proposal"], "succeeded", "failed")
        print(f"verification: {final['status']}; fact {final['evidence'][0].get('fact')}")


if __name__ == "__main__":
    {"prepare": prepare, "verify": verify}.get(
        sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__)
    )()
