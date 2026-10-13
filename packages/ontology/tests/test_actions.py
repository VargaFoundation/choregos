# SPDX-License-Identifier: Apache-2.0
"""Trial items 4 to 6, on the IT4IT package: propose, approve, open the PR, prove.

4. The agent proposes, through the generated tool `action_open_infra_pr` and with its run token, an
   action whose effect is `gitops.pull_request`: the proposal waits for an approval, with its
   justification. Nothing is open yet.
5. A person approves with a recent authentication (otherwise 401 towards `?reauth=1`); the PLATFORM
   writes the files and opens the PR on `choregos/<proposal>` through the core's SCM adapter. The
   decision is recorded with the age of the authentication (`auth_age_seconds`).
6. The evidence: `verify_finding_fixed` waits for the collector's next report; `succeeded` only if the
   finding's key is absent from it. An absent layer, an `unreachable` line, a partial report or a
   timeout fail it — never "key absent".

Since S20-08, a proposal is a CORE action: decided by `decider`, played by the `ActionWorkflow` in a
Temporal test server — never in the request that approves it. The tests therefore wait for the state
they check.

What these tests do not prove: the console (the decision goes through the API), a real GitHub (the SCM
is the core's fake, whose GitHub adapter has its own tests).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .helpers import (
    IT4IT,
    call_tool,
    create_run,
    line,
    package_files,
    post_report,
    report,
    sign_in,
    stored_objects,
)

RUN = "run-agent-1"
KEY = "os-reboot-required"
WEEK_1 = report(
    line("reboot-required", "node-1", "finding", severity="medium", observed_at="2026-10-03T08:00:00Z"),
    line("reboot-required", "node-2", "finding", severity="high", observed_at="2026-10-03T08:00:01Z"),
    line("ntp-drift", "node-1", "finding", severity="low", observed_at="2026-10-03T08:00:02Z"),
)
FIX = {
    "title": "Reboot the nodes that need it",
    "path": "platform/maintenance/reboot.yaml",
    "content": "nodes: [node-1, node-2]\nstrategy: one-at-a-time\n",
}


@pytest.fixture
def scm() -> Iterator[Any]:
    """One SCM shared by every request of the test, and the engine's clock reset."""
    from choregos_adapters.fakes import FakeScm
    from choregos_ontology.service import actions

    fake = FakeScm()
    actions.replace_scm(fake)
    try:
        yield fake
    finally:
        actions.replace_scm(None)
        actions.CLOCK.offset = timedelta()


@pytest.fixture
async def it4it(client: AsyncClient, project: dict[str, Any], scm: Any, temporal: Any) -> dict[str, str]:
    upload = await client.put(
        f"/api/v1/projects/{project['id']}/ontology", json={"files": package_files(IT4IT)}
    )
    assert upload.status_code == 200, upload.text
    assert (await post_report(client, project, WEEK_1)).status_code == 200
    return await create_run(project, RUN)


async def _propose(client: AsyncClient, headers: dict[str, str], **fix: Any) -> Any:
    arguments = {
        "target": [fix.pop("target", KEY)],
        "justification": "Two nodes report reboot-required since the last kernel update; one PR fixes both.",
        "params": {**FIX, **fix},
    }
    return await call_tool(client, RUN, headers, "action_open_infra_pr", arguments)


async def _decide(client: AsyncClient, project: dict[str, Any], proposal: str, decision: str) -> Any:
    path = f"/api/v1/projects/{project['id']}/proposals/{proposal}/decision"
    return await client.post(path, json={"decision": decision, "reason": "checked the diff"})


async def _proposal(client: AsyncClient, project: dict[str, Any], proposal: str) -> dict[str, Any]:
    response = await client.get(f"/api/v1/projects/{project['id']}/proposals/{proposal}")
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _until(
    client: AsyncClient, project: dict[str, Any], proposal: str, *statuses: str
) -> dict[str, Any]:
    """The `ActionWorkflow` plays the action outside the request: wait for the state to check."""
    import asyncio

    for _ in range(400):
        file = await _proposal(client, project, proposal)
        if file["status"] in statuses:
            return file
        await asyncio.sleep(0.05)
    raise AssertionError(f"{proposal}: {file['status']}, never {statuses} ({file.get('error')})")


# ───────────────────────────── item 4: propose ─────────────────────────────


async def test_the_agent_proposes_a_pr_that_waits_for_an_approval(
    client: AsyncClient, project: dict[str, Any], it4it: dict[str, str], scm: Any
) -> None:
    response = await _propose(client, it4it)
    assert response["status_code"] == 201, response
    proposal = response["result"]
    assert proposal["status"] == "pending_approval"
    assert proposal["justification"].startswith("Two nodes report")
    assert proposal["proposed_by"] == {"kind": "agent", "id": "agent:platform", "run_id": RUN}
    assert proposal["approval"]["mode"] == "human"
    assert proposal["approval"]["step_up_minutes"] == 10
    assert scm.prs == {} and scm.commits == [], "nothing is open before the approval"
    read = await _proposal(client, project, proposal["proposal"])
    assert read["status"] == "pending_approval"


async def test_the_same_proposal_twice_returns_the_first(
    client: AsyncClient, project: dict[str, Any], it4it: dict[str, str]
) -> None:
    first = await _propose(client, it4it)
    second = await _propose(client, it4it)
    assert second["status_code"] == 200
    assert second["result"]["proposal"] == first["result"]["proposal"]
    assert second["result"]["idempotent_replay"] is True
    every = await client.get(f"/api/v1/projects/{project['id']}/proposals")
    assert len(every.json()) == 1


async def test_a_path_outside_paths_allowed_is_refused_at_proposal_time(
    client: AsyncClient, project: dict[str, Any], it4it: dict[str, str]
) -> None:
    refusal = await _propose(client, it4it, path="secrets/vault-token.yaml")
    assert refusal["status_code"] == 422
    assert "outside paths_allowed" in refusal["result"]["message"]
    climbing = await _propose(client, it4it, path="platform/../secrets/x.yaml")
    assert climbing["status_code"] == 422
    assert (await client.get(f"/api/v1/projects/{project['id']}/proposals")).json() == []


async def test_a_false_precondition_makes_the_proposal_invalid(
    client: AsyncClient, project: dict[str, Any], it4it: dict[str, str]
) -> None:
    assert (await post_report(client, project, report(line("ntp-drift", "node-1", "ok")))).status_code == 200
    refusal = await _propose(client, it4it, target="os-ntp-drift", path="platform/ntp.yaml")
    assert refusal["status_code"] == 422
    assert refusal["result"]["precondition"] == "finding_open"
    assert (await client.get(f"/api/v1/projects/{project['id']}/proposals")).json() == []


async def test_the_status_and_list_tools(client: AsyncClient, it4it: dict[str, str]) -> None:
    proposal = (await _propose(client, it4it))["result"]["proposal"]
    status = await call_tool(client, RUN, it4it, "action_status", {"proposal": proposal})
    assert status["result"]["status"] == "pending_approval"
    listed = await call_tool(client, RUN, it4it, "action_list", {"status": "pending_approval"})
    assert [p["proposal"] for p in listed["result"]["proposals"]] == [proposal]


# ───────────────────────────── item 5: approve, the platform opens the PR ─────────────────────────────


async def test_approving_needs_a_recent_authentication_then_the_platform_opens_the_pr(
    client: AsyncClient, project: dict[str, Any], it4it: dict[str, str], scm: Any
) -> None:
    import time

    from choregos_api import fraicheur

    proposal = (await _propose(client, it4it))["result"]["proposal"]

    # Eleven minutes later, the session's authentication is older than `stepUp`.
    later = time.time() + 11 * 60
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(fraicheur.time, "time", lambda: later)
        refusal = await _decide(client, project, proposal, "approve")
    assert refusal.status_code == 401, refusal.text
    assert refusal.json()["errors"][0]["error"] == "step_up_required"
    assert refusal.json()["errors"][0]["reauth"] == "GET /api/v1/auth/login?reauth=1"
    assert scm.prs == {}

    # The person authenticates again (`?reauth=1`), then approves: the action leaves for Temporal.
    await sign_in(client, "admin@varga.dev", reauth=True)
    approved = await _decide(client, project, proposal, "approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved", "nothing runs in the request"
    file = await _until(client, project, proposal, "succeeded", "failed")
    assert file["status"] == "succeeded", file.get("error")

    decision = file["decisions"][-1]
    assert (decision["by"], decision["decision"]) == ("admin@varga.dev", "approve")
    assert decision["auth_age_seconds"] < 60

    branch = f"choregos/{proposal}"
    assert scm.files[("example/infra", branch)] == {FIX["path"]: FIX["content"]}
    (pr,) = scm.prs.values()
    assert (pr.ref.head, pr.ref.base, pr.title) == (branch, "main", FIX["title"])
    assert KEY in pr.body
    assert file["effects"][0]["pr"] == pr.ref.number
    assert file["evidence"] == [
        {
            "name": "pull_request_open",
            "type": "connector.query",
            "expect": "result.state == 'open' && result.head == 'choregos/' + proposal.id",
            "status": "passed",
            "result": {"number": pr.ref.number, "state": "open", "head": branch, "url": pr.ref.url},
            "index": 0,
            "position": 1,
        }
    ]


async def test_a_developer_does_not_approve_an_action_that_needs_an_owner(
    app: Any, client: AsyncClient, project: dict[str, Any], it4it: dict[str, str], scm: Any
) -> None:
    proposal = (await _propose(client, it4it))["result"]["proposal"]
    member = {"email": "dev@varga.dev", "role": "developer"}
    assert (await client.post("/api/v1/orgs/varga/members", json=member)).status_code in {200, 201}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as developer:
        await sign_in(developer, "dev@varga.dev")
        refusal = await _decide(developer, project, proposal, "approve")
    assert refusal.status_code == 403, refusal.text
    assert scm.prs == {}


async def test_rejecting_closes_without_effect_and_a_decision_is_not_taken_back(
    client: AsyncClient, project: dict[str, Any], it4it: dict[str, str], scm: Any
) -> None:
    proposal = (await _propose(client, it4it))["result"]["proposal"]
    rejection = await _decide(client, project, proposal, "reject")
    assert rejection.status_code == 200, rejection.text
    assert rejection.json()["status"] == "rejected"
    assert scm.prs == {} and scm.commits == []
    again = await _decide(client, project, proposal, "approve")
    assert again.status_code == 409, again.text


async def test_a_pr_that_is_not_the_expected_one_fails_the_evidence(
    client: AsyncClient, project: dict[str, Any], it4it: dict[str, str], scm: Any
) -> None:
    """The evidence checks the PR: if the SCM returns another branch, the action fails."""
    original = scm.get_pr

    async def other_branch(ref: Any) -> Any:
        state = await original(ref)
        state.ref.head = "someone-else/branch"
        return state

    scm.get_pr = other_branch
    proposal = (await _propose(client, it4it))["result"]["proposal"]
    assert (await _decide(client, project, proposal, "approve")).status_code == 200
    file = await _until(client, project, proposal, "succeeded", "failed")
    assert file["status"] == "failed"
    assert file["evidence"][0]["status"] == "failed"


# ───────────────────────────── item 6: the collector's rerun ─────────────────────────────


async def _verify(client: AsyncClient, headers: dict[str, str], project: dict[str, Any]) -> dict[str, Any]:
    arguments = {"target": [KEY], "justification": "The PR is merged and applied by GitOps.", "params": {}}
    response = await call_tool(client, RUN, headers, "action_verify_finding_fixed", arguments)
    assert response["status_code"] == 201, response
    assert response["result"]["status"] == "approved", "the policy approves; Temporal plays"
    return await _until(client, project, response["result"]["proposal"], "awaiting_evidence", "failed")


async def test_without_the_key_in_the_next_report_the_fix_is_proven(
    client: AsyncClient, project: dict[str, Any], it4it: dict[str, str]
) -> None:
    """#440: the action's workflow wrote its stale `result` over the evidence just settled."""
    verification = await _verify(client, it4it, project)
    assert verification["approval"]["mode"] == "auto", "low risk: the policy approves on its own"
    assert verification["status"] == "awaiting_evidence", verification.get("error")
    assert (await stored_objects(project["id"]))[KEY][1]["verification_requested_at"]

    rerun = report(
        line("reboot-required", "node-1", "ok"),
        line("reboot-required", "node-2", "ok"),
        line("ntp-drift", "node-1", "finding", severity="low"),
    )
    response = await post_report(client, project, rerun)
    assert response.json()["proposals_decided"] == [verification["proposal"]]
    file = await _until(client, project, verification["proposal"], "succeeded", "failed")
    assert file["status"] == "succeeded", file.get("error")
    evidence = file["evidence"][0]
    assert evidence["result"]["key_present"] is False
    assert evidence["fact"] == {
        "name": "collector_clean",
        "scope": KEY,
        "value": True,
        "source": "collector.rerun",
    }
    assert (await stored_objects(project["id"]))[KEY][1]["status"] == "resolved", "the sync followed"


@pytest.mark.parametrize(
    ("rerun", "reason"),
    [
        (
            report(line("reboot-required", "node-1", "ok"), line("reboot-required", "node-2", "finding")),
            None,
        ),
        (report({"layer": "k8s", "check": "pods", "scope": "ns/a", "status": "ok"}), "absent"),
        (
            report(line("reboot-required", "node-1", "ok"), line("disk", "node-2", "unreachable")),
            "unreachable",
        ),
    ],
    ids=["key-still-present", "layer-absent", "layer-unreachable"],
)
async def test_the_evidence_fails_if_the_rerun_does_not_show_the_key_absent(
    client: AsyncClient, project: dict[str, Any], it4it: dict[str, str], rerun: str, reason: str | None
) -> None:
    verification = await _verify(client, it4it, project)
    await post_report(client, project, rerun)
    file = await _until(client, project, verification["proposal"], "succeeded", "failed")
    assert file["status"] == "failed"
    evidence = file["evidence"][0]
    if reason is None:
        assert evidence["result"]["key_present"] is True
        assert evidence["fact"]["value"] is False
    else:
        assert reason in evidence["error"]


async def test_a_partial_report_fails_the_evidence_and_writes_no_object(
    client: AsyncClient, project: dict[str, Any], it4it: dict[str, str]
) -> None:
    verification = await _verify(client, it4it, project)
    before = await stored_objects(project["id"])
    truncated = report(line("reboot-required", "node-1", "ok"), end={"_end": True, "lines": 9})
    refusal = await post_report(client, project, truncated)
    assert refusal.status_code == 422, refusal.text
    assert refusal.json()["proposals_decided"] == [verification["proposal"]]
    assert (await _until(client, project, verification["proposal"], "failed", "succeeded"))[
        "status"
    ] == "failed"
    assert await stored_objects(project["id"]) == before


async def test_without_a_report_in_time_the_evidence_fails(
    client: AsyncClient, project: dict[str, Any], it4it: dict[str, str], temporal: Any
) -> None:
    verification = await _verify(client, it4it, project)
    await temporal.sleep(timedelta(minutes=16))  # the action's deadline passes, in Temporal
    file = await _until(client, project, verification["proposal"], "failed", "succeeded")
    assert file["status"] == "failed"
    assert "timed out" in file["error"]


async def test_the_agent_proposes_through_its_mcp_server(app: Any, it4it: dict[str, str]) -> None:
    """The agent's path: `choregos-tools` relays the proposal to the internal API, run token included."""
    import json

    from choregos_tools_mcp.client import InternalClient
    from choregos_tools_mcp.server import McpServer

    internal = InternalClient("http://test/api/v1/internal", RUN, "unused")
    await internal.aclose()
    internal._client = AsyncClient(transport=ASGITransport(app=app), headers=it4it)
    try:
        call = await McpServer(internal).handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "action_open_infra_pr",
                    "arguments": {"target": [KEY], "justification": "Reboot needed.", "params": FIX},
                },
            }
        )
    finally:
        await internal.aclose()
    assert call is not None and not call["result"].get("isError"), call
    assert json.loads(call["result"]["content"][0]["text"])["status"] == "pending_approval"


async def test_an_api_token_does_not_decide_and_a_rejection_needs_a_reason(
    client: AsyncClient, project: dict[str, Any], it4it: dict[str, str], scm: Any
) -> None:
    """A decision needs a human session (403 `decision_requires_session`), and a rejection, a reason."""
    proposal = (await _propose(client, it4it))["result"]["proposal"]
    issued = await client.post("/api/v1/me/tokens", json={"name": "automation", "expires_in_days": 1})
    assert issued.status_code == 201, issued.text
    path = f"/api/v1/projects/{project['id']}/proposals/{proposal}/decision"
    async with AsyncClient(transport=client._transport, base_url="http://test") as bearer:
        token = {"Authorization": f"Bearer {issued.json()['token']}"}
        refusal = await bearer.post(path, headers=token, json={"decision": "approve", "reason": "ok"})
    assert refusal.status_code == 403, refusal.text
    assert "decision_requires_session" in refusal.text
    without_reason = await client.post(path, json={"decision": "reject"})
    assert without_reason.status_code == 422, without_reason.text
    assert scm.prs == {}
    assert (await _proposal(client, project, proposal))["status"] == "pending_approval"


# ───────────────────────────── a proposal made by a person ─────────────────────────────


async def test_a_person_registers_a_host_through_the_action_engine(
    client: AsyncClient, project: dict[str, Any], it4it: dict[str, str]
) -> None:
    """The inventory is written by an action (`register_host`, low risk: automatic), never in the database."""
    body = {
        "action_type": "register_host",
        "params": {"id": "node-7", "name": "node-7", "os": "rhel", "ip": "10.0.0.17"},
        "justification": "New node delivered by the infrastructure team.",
    }
    response = await client.post(f"/api/v1/projects/{project['id']}/proposals", json=body)
    assert response.status_code == 201, response.text
    assert response.json()["proposed_by"]["kind"] == "user"
    file = await _until(client, project, response.json()["proposal"], "succeeded", "failed")
    assert file["status"] == "succeeded", file.get("error")
    assert (await stored_objects(project["id"], "host"))["node-7"][1]["os"] == "rhel"
    replay = await client.post(f"/api/v1/projects/{project['id']}/proposals", json=body)
    assert replay.status_code == 200, "same idempotency key: the first proposal"


async def test_a_human_proposal_respects_the_parameters_and_the_roles(
    app: Any, client: AsyncClient, project: dict[str, Any], it4it: dict[str, str]
) -> None:
    path = f"/api/v1/projects/{project['id']}/proposals"
    wrong = {"action_type": "register_host", "params": {"id": "x"}, "justification": "test"}
    assert (await client.post(path, json=wrong)).status_code == 422
    member = {"email": "reader@varga.dev", "role": "viewer"}
    assert (await client.post("/api/v1/orgs/varga/members", json=member)).status_code in {200, 201}
    body = {
        "action_type": "register_host",
        "params": {"id": "node-8", "name": "node-8", "os": "debian", "ip": "10.0.0.18"},
        "justification": "test",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as reader:
        await sign_in(reader, "reader@varga.dev")
        refusal = await reader.post(path, json=body)
    assert refusal.status_code == 403, refusal.text
    assert "node-8" not in await stored_objects(project["id"], "host")


async def test_the_agent_does_not_see_an_action_it_may_not_propose(
    client: AsyncClient, it4it: dict[str, str]
) -> None:
    tools = await client.get(f"/api/v1/internal/runs/{RUN}/tools", headers=it4it)
    names = {o["name"] for o in tools.json()["tools"]}
    assert "action_open_infra_pr" in names
    assert "action_register_host" not in names, "reserved to people and to the system"
    refusal = await client.post(
        f"/api/v1/internal/runs/{RUN}/tools/action_register_host",
        headers=it4it,
        json={"justification": "x", "params": {"id": "n", "name": "n", "os": "rhel", "ip": "10.0.0.1"}},
    )
    assert refusal.status_code == 404, "a tool outside the rights answers like a tool that does not exist"
