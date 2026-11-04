# SPDX-License-Identifier: Apache-2.0
"""The trial's IT4IT scenario, played against the integrated stack that is RUNNING (element 9; elements 2 to 6).

Nothing is simulated on the platform side: PostgreSQL under RLS with a non-superuser role, core
and extension migrations run by `python -m choregos_api.migrate`, API served by uvicorn, tools
called through the internal API with a run token signed by the stack's shared key.

What is: the agent (this script writes its calls), the scenery (hosts, services, run: `scenery.py`), the
collector (this script posts its reports) and the SCM (the core's fake, inside the API process).

    uv run python trials/local-start/scenario.py     # the stack started by run.sh
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Any

import httpx

HERE = pathlib.Path(__file__).resolve().parent
IT4IT = HERE.parents[1] / "packages" / "ontology" / "tests" / "fixtures" / "it4it"
API = f"http://127.0.0.1:{os.environ.get('TRIAL_API_PORT', '18190')}"
RUN = "run-trial-1"
KEY = "os-reboot-required"


def report(*lines: dict[str, Any], end: dict[str, Any] | None = None) -> str:
    body = [json.dumps(line) for line in lines]
    body.append(json.dumps(end if end is not None else {"_end": True, "lines": len(lines)}))
    return "\n".join(body) + "\n"


def line(check: str, scope: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"layer": "os", "check": check, "scope": scope, "status": status, **extra}


WEEK_1 = report(
    line("reboot-required", "node-1", "finding", severity="medium", observed_at="2026-10-03T08:00:00Z"),
    line("reboot-required", "node-2", "finding", severity="high", observed_at="2026-10-03T08:00:01Z"),
    line("ntp-drift", "node-1", "finding", severity="low", observed_at="2026-10-03T08:00:02Z"),
    line("disk-usage", "node-1", "ok", value=41.5, observed_at="2026-10-03T08:00:03Z"),
)


class Scenario:
    def __init__(self, http: httpx.Client) -> None:
        self.http = http
        self.steps: list[dict[str, Any]] = []

    def step(self, name: str, check: Any) -> Any:
        started = time.perf_counter()
        try:
            result = check()
        except Exception as error:
            self.steps.append({"step": name, "ok": False, "error": str(error)[:500]})
            raise
        self.steps.append({"step": name, "ok": True, "ms": round((time.perf_counter() - started) * 1000)})
        return result

    def sign_in(self, email: str, *, reauth: bool = False) -> None:
        start = self.http.get(
            "/api/v1/auth/login", params={"as": email, **({"reauth": "1"} if reauth else {})}
        )
        assert start.status_code == 307, start.text
        back = self.http.get(start.headers["location"])
        assert back.status_code == 307, back.text

    def wait_for(self, project: str, proposal: str, *statuses: str, seconds: int = 120) -> dict[str, Any]:
        """A proposal's state, once it is one of `statuses` (or the last one read at the deadline)."""
        end = time.monotonic() + seconds
        while True:
            record = dict(self.http.get(f"/api/v1/projects/{project}/proposals/{proposal}").json())
            if record.get("status") in statuses or time.monotonic() > end:
                return record
            time.sleep(1)

    def post(self, project: str, text: str) -> httpx.Response:
        return self.http.post(
            f"/api/v1/projects/{project}/observations",
            content=text.encode(),
            headers={"Content-Type": "application/x-ndjson"},
        )

    def tool(self, token: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        response = self.http.post(
            f"/api/v1/internal/runs/{RUN}/tools/{name}",
            headers={"Authorization": f"Bearer {token}"},
            json=arguments,
        )
        assert response.status_code == 200, response.text
        return dict(response.json())


def _compose(*arguments: str, stdin: str | None = None) -> str:
    """`docker compose` on the trial's stack; the arguments are written by this script, not received."""
    done = subprocess.run(  # noqa: S603
        ["docker", "compose", "-f", str(HERE / "compose.yaml"), *arguments],  # noqa: S607
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return done.stdout


def play(s: Scenario) -> dict[str, Any]:
    s.step("administrator sign-in", lambda: s.sign_in("admin@varga.dev"))

    def project() -> str:
        created = s.http.post(
            "/api/v1/orgs/varga/projects",
            json={"slug": "infra", "name": "Infra", "config": {"slug": "infra", "org": "varga"}},
        )
        assert created.status_code == 201, created.text
        ident = str(created.json()["id"])
        scm = s.http.put(f"/api/v1/projects/{ident}/connectors/scm", json={"type": "fake", "config": {}})
        assert scm.status_code == 200, scm.text
        return ident

    pid = s.step("project and SCM connector", project)

    def ontology() -> None:
        files = {
            p.relative_to(IT4IT).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(IT4IT.rglob("*.yaml"))
        }
        stored = s.http.put(f"/api/v1/projects/{pid}/ontology", json={"files": files})
        assert stored.status_code == 200, stored.text
        # 14: the inventory (`register_host`) is written by the action engine since the measure of 13.
        assert stored.json()["mcp_tools"] == 14, stored.json()

    s.step("element 1 - IT4IT package validated, compiled, active", ontology)

    def synchronise() -> None:
        first = s.post(pid, WEEK_1).json()
        assert (first["created"], first["unchanged"]) == (2, 0), first
        replay = s.post(pid, WEEK_1).json()
        assert (replay["created"], replay["updated"], replay["unchanged"]) == (0, 0, 2), replay
        truncated = s.post(
            pid, report(line("reboot-required", "node-1", "ok"), end={"_end": True, "lines": 4})
        )
        assert truncated.status_code == 422, truncated.text
        findings = s.http.get(f"/api/v1/projects/{pid}/objects/finding").json()["objects"]
        assert {c["key"]: c["status"] for c in findings} == {KEY: "open", "os-ntp-drift": "open"}, findings

    s.step("element 2 - idempotent synchronisation, partial report without a write", synchronise)

    def scenery() -> str:
        output = _compose(
            "exec",
            "-T",
            "-e",
            f"PROJECT={pid}",
            "-e",
            "SLUG=infra",
            "-e",
            f"RUN={RUN}",
            "api",
            "python",
            "-",
            stdin=(HERE / "scenery.py").read_text(encoding="utf-8"),
        )
        return str(json.loads(output.strip().splitlines()[-1])["token"])

    token = s.step("scenery: hosts, services, run and its token", scenery)

    def read() -> None:
        tools = s.http.get(
            f"/api/v1/internal/runs/{RUN}/tools", headers={"Authorization": f"Bearer {token}"}
        )
        assert tools.status_code == 200, tools.text
        names = {o["name"] for o in tools.json()["tools"]}
        assert {"finding_search", "host_get", "action_open_infra_pr"} <= names, names
        opened = s.tool(
            token, "finding_search", {"filters": [{"property": "status", "op": "eq", "value": "open"}]}
        )
        assert {o["key"] for o in opened["result"]["objects"]} == {KEY, "os-ntp-drift"}, opened
        host = s.tool(token, "host_get", {"id": "node-1"})
        assert host["result"]["object"] == {"id": "node-1", "name": "node-1", "os": "ubuntu"}, host

    s.step("element 3 - the agent reads through the generated tools, with its run token", read)

    def propose() -> str:
        arguments = {
            "target": [KEY],
            "justification": "Two nodes report reboot-required since the last kernel update.",
            "params": {
                "title": "Reboot the nodes that need it",
                "path": "platform/maintenance/reboot.yaml",
                "content": "nodes: [node-1, node-2]\nstrategy: one-at-a-time\n",
            },
        }
        response = s.tool(token, "action_open_infra_pr", arguments)
        assert response["status_code"] == 201, response
        assert response["result"]["status"] == "pending_approval", response
        return str(response["result"]["proposal"])

    proposal = s.step("element 4 - `pending_approval` proposal, with its justification", propose)

    def approve() -> dict[str, Any]:
        s.sign_in("admin@varga.dev", reauth=True)
        path = f"/api/v1/projects/{pid}/proposals/{proposal}/decision"
        decision = s.http.post(path, json={"decision": "approve", "comment": "diff re-read"})
        assert decision.status_code == 200, decision.text
        # Since S20-08, the approved action runs in Temporal, after the response: we wait for it.
        record = s.wait_for(pid, proposal, "succeeded", "failed")
        assert record["status"] == "succeeded", record
        assert record["decisions"][-1]["auth_age_seconds"] is not None, record
        assert record["effects"][0]["head"] == f"choregos/{proposal}", record
        return record

    record = s.step("element 5 - re-authenticated approval, PR opened by the platform", approve)

    def prove() -> dict[str, Any]:
        arguments = {"target": [KEY], "justification": "The PR is merged and applied.", "params": {}}
        proposed = s.tool(token, "action_verify_finding_fixed", arguments)["result"]
        # The re-run settles only a proof REQUESTED before it: we wait for the action to be waiting.
        verification = s.wait_for(pid, proposed["proposal"], "awaiting_evidence", "failed")
        assert verification["status"] == "awaiting_evidence", verification
        rerun = report(
            line("reboot-required", "node-1", "ok"),
            line("reboot-required", "node-2", "ok"),
            line("ntp-drift", "node-1", "finding", severity="low"),
        )
        response = s.post(pid, rerun).json()
        assert response["proposals_decided"] == [verification["proposal"]], response
        final = s.wait_for(pid, verification["proposal"], "succeeded", "failed")
        assert final["status"] == "succeeded", final
        return dict(final)

    proof = s.step("element 6 - the collector's re-run without the key proves the fix", prove)

    def rls() -> None:
        query = (
            "select relname, relrowsecurity, relforcerowsecurity from pg_class where relname in "
            "('ontology_versions', 'managed_objects', 'actions') order by 1;"
            "select rolsuper from pg_roles where rolname = 'choregos_app';"
        )
        output = _compose(
            "exec", "-T", "choregos-db", "psql", "-U", "choregos", "-d", "choregos", "-tAc", query
        )
        rows = [x for x in output.split() if x]
        assert rows == [
            "actions|t|t",
            "managed_objects|t|t",
            "ontology_versions|t|t",
            "f",
        ], rows

    s.step("RLS forced on the extension's tables; application role is not a superuser", rls)
    return {
        "pr": record["effects"][0],
        "decision": record["decisions"][-1],
        "fact": proof["evidence"][0]["fact"],
    }


def main() -> int:
    with httpx.Client(base_url=API, follow_redirects=False, timeout=30) as http:
        scenario = Scenario(http)
        try:
            summary = play(scenario)
        except Exception:
            print(json.dumps({"steps": scenario.steps}, ensure_ascii=False, indent=2))
            return 1
    print(json.dumps({"steps": scenario.steps, **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
