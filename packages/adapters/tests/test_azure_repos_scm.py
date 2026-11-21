"""The Azure Repos SCM adapter against a scripted Azure DevOps server, request by request (S13-08).

The server (`FakeAzureDevOpsServer`) is written from Microsoft's REST API 7.1 reference: these tests prove the
adapter speaks that protocol and rules as documented, not that a live organisation accepts it.
"""

from __future__ import annotations

import httpx
import pytest
from tranibor_adapters.azure_devops import azure_client
from tranibor_adapters.errors import ConfigurationError, HeadMovedError, UpstreamError
from tranibor_adapters.fakes.azure_devops import BUILD_POLICY, REVIEWERS_POLICY, FakeAzureDevOpsServer
from tranibor_adapters.scm.azure_repos import AzureReposScm
from tranibor_core.domain import PrRef

BASE = "https://dev.azure.com/northwind"


def build(server: FakeAzureDevOpsServer, **kwargs: object) -> AzureReposScm:
    client = azure_client({"base_url": BASE, "pat": "pat"}, service="azure-repos")
    client._client = httpx.AsyncClient(transport=server.transport())
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    scm = AzureReposScm(client, "Billing", "billing-api", sleep=sleep, **kwargs)  # type: ignore[arg-type]
    scm.slept = sleeps  # type: ignore[attr-defined]
    return scm


@pytest.fixture
def server() -> FakeAzureDevOpsServer:
    return FakeAzureDevOpsServer()


async def test_branches_are_created_once_and_read_back(server: FakeAzureDevOpsServer) -> None:
    scm = build(server)
    assert await scm.branch_exists("r", "choregos/12-fix") is False
    assert await scm.head_of("r", "choregos/12-fix") is None
    await scm.ensure_branch("r", "choregos/12-fix", "main")
    await scm.ensure_branch("r", "choregos/12-fix", "main")  # a replay
    assert await scm.head_of("r", "choregos/12-fix") == server.refs["refs/heads/main"]
    assert [c for c in server.calls if c[0] == "POST" and c[1].endswith("/refs")] != []
    assert len([c for c in server.calls if c[0] == "POST" and c[1].endswith("/refs")]) == 1


async def test_a_missing_base_branch_is_an_error(server: FakeAzureDevOpsServer) -> None:
    with pytest.raises(UpstreamError, match="base branch"):
        await build(server).ensure_branch("r", "x", "nope")


async def test_commit_files_adds_edits_and_skips_identical_files(server: FakeAzureDevOpsServer) -> None:
    scm = build(server)
    await scm.ensure_branch("r", "work", "main")
    sha = await scm.commit_files("r", "work", {"docs/a.md": "A\n", "README.md": "# changed\n"}, "docs: a")
    assert sha == server.refs["refs/heads/work"]
    tree = server.commits[sha]["tree"]
    assert tree["docs/a.md"] == "A\n" and tree["README.md"] == "# changed\n"
    # The same files again: nothing is pushed.
    pushes = [c for c in server.calls if c[1].endswith("/pushes")]
    assert await scm.commit_files("r", "work", {"docs/a.md": "A\n"}, "docs: a") == ""
    assert [c for c in server.calls if c[1].endswith("/pushes")] == pushes


async def test_a_push_to_a_missing_branch_is_refused(server: FakeAzureDevOpsServer) -> None:
    with pytest.raises(UpstreamError, match="does not exist"):
        await build(server).commit_files("r", "nope", {"a": "b"}, "m")


async def test_list_files_and_an_absent_folder(server: FakeAzureDevOpsServer) -> None:
    scm = build(server)
    server.push("main", {"docs/adr/0001-a.md": "x", "docs/adr/0002-b.md": "y"})
    assert await scm.list_files("r", "main", "docs/adr") == ["docs/adr/0001-a.md", "docs/adr/0002-b.md"]
    assert await scm.list_files("r", "main", "nothing") == []


async def test_open_pr_is_idempotent_and_update_comment_work(server: FakeAzureDevOpsServer) -> None:
    scm = build(server)
    await scm.ensure_branch("r", "choregos/12-fix", "main")
    first = await scm.open_pr("r", "choregos/12-fix", "main", "Fix", "body", draft=True)
    again = await scm.open_pr("r", "choregos/12-fix", "main", "Fix", "body", draft=True)
    assert first == again and first.number == 1
    assert first.url == f"{BASE}/Billing/_git/billing-api/pullrequest/1"
    assert server.prs[1]["isDraft"] is True
    await scm.update_pr(first, body="new body", draft=False)
    assert server.prs[1]["description"] == "new body" and server.prs[1]["isDraft"] is False
    url = await scm.comment_pr(first, "hello")
    assert url.endswith("pullrequest/1?discussionId=1")
    assert server.threads[1][0]["comments"][0]["content"] == "hello"


async def test_get_pr_reads_head_votes_labels_files_and_mergeability(server: FakeAzureDevOpsServer) -> None:
    scm = build(server)
    await scm.ensure_branch("r", "work", "main")
    await scm.commit_files("r", "work", {"src/a.py": "a\n"}, "feat: a")
    ref = await scm.open_pr("r", "work", "main", "T", "B", draft=False)
    server.labels[1] = ["agent"]
    server.vote(1, "ada@n.test", 10)
    server.vote(1, "bob@n.test", -5)
    server.vote(1, "carl@n.test", 0)
    server.vote(1, "team", 10, container=True)
    state = await scm.get_pr(ref)
    assert state.head_sha == server.refs["refs/heads/work"]
    assert state.files == ["src/a.py"] and state.labels == ["agent"]
    assert state.mergeable is True and state.merged is False
    assert {(r.reviewer, r.state) for r in state.reviews} == {
        ("ada@n.test", "approved"),
        ("bob@n.test", "changes_requested"),
    }
    assert state.review_conclusion() == "changes_requested"
    assert state.ref.head == "work" and state.ref.base == "main"


async def test_the_head_follows_the_branch(server: FakeAzureDevOpsServer) -> None:
    scm = build(server)
    await scm.ensure_branch("r", "work", "main")
    ref = await scm.open_pr("r", "work", "main", "T", "B", draft=False)
    before = (await scm.get_pr(ref)).head_sha
    after = server.push("work", {"x": "1"})
    assert (await scm.get_pr(ref)).head_sha == after != before


async def test_checks_come_from_statuses_policies_and_builds_and_ci_green_reads_them(
    server: FakeAzureDevOpsServer,
) -> None:
    scm = build(server)
    await scm.ensure_branch("r", "work", "main")
    head = await scm.commit_files("r", "work", {"a": "1"}, "m")
    ref = await scm.open_pr("r", "work", "main", "T", "B", draft=False)
    server.add_status(1, "lint", "succeeded", genre="ci")
    server.add_policy(1, BUILD_POLICY, "Build validation", "approved")
    server.add_policy(1, REVIEWERS_POLICY, "Minimum reviewers", "rejected")  # not CI: left out
    server.add_build(7, head, branch="work", result="succeeded")
    state = await scm.get_pr(ref)
    names = {c.name: (c.status, c.conclusion) for c in state.checks}
    assert names == {
        "ci/lint": ("completed", "success"),
        "policy: Build validation": ("completed", "success"),
        "ci #2026.100": ("completed", "success"),
    }
    assert state.checks_conclusion() == "success"
    # The same pull request with a red build at its head: the gate's aggregate turns to failure.
    server.add_build(8, head, branch="work", result="failed")
    assert (await scm.get_pr(ref)).checks_conclusion() == "failure"


async def test_a_build_of_another_commit_or_a_running_one_is_never_green(
    server: FakeAzureDevOpsServer,
) -> None:
    scm = build(server)
    await scm.ensure_branch("r", "work", "main")
    head = await scm.commit_files("r", "work", {"a": "1"}, "m")
    ref = await scm.open_pr("r", "work", "main", "T", "B", draft=False)
    server.add_build(7, "f" * 40, branch="work", result="succeeded")  # an older push
    assert (await scm.get_pr(ref)).checks == []  # no check: the gate stays pending
    server.add_build(7, head, branch="work", status="inProgress", result=None)
    state = await scm.get_pr(ref)
    assert state.checks_conclusion() == "pending"


async def test_a_pull_request_build_is_matched_by_its_source_commit(server: FakeAzureDevOpsServer) -> None:
    scm = build(server)
    await scm.ensure_branch("r", "work", "main")
    head = await scm.commit_files("r", "work", {"a": "1"}, "m")
    ref = await scm.open_pr("r", "work", "main", "T", "B", draft=False)
    # A build of `refs/pull/1/merge` is on the merge commit; the policy evaluation is what lists it.
    server.add_build(7, head, pull_request=1, result="succeeded")
    server.add_policy(1, BUILD_POLICY, "Build validation", "running")
    state = await scm.get_pr(ref)
    assert state.checks_conclusion() == "pending"


async def test_a_status_of_an_older_iteration_is_stale(server: FakeAzureDevOpsServer) -> None:
    scm = build(server)
    await scm.ensure_branch("r", "work", "main")
    ref = await scm.open_pr("r", "work", "main", "T", "B", draft=False)
    server.add_status(1, "lint", "failed", iteration=1)
    server.push("work", {"fix": "1"})  # iteration 2
    server.add_status(1, "unit", "pending", iteration=2)
    state = await scm.get_pr(ref)
    assert [(c.name, c.status) for c in state.checks] == [("ci/unit", "in_progress")]
    # Within an iteration the newest status of a context wins.
    server.add_status(1, "unit", "succeeded", iteration=2)
    assert (await scm.get_pr(ref)).checks_conclusion() == "success"


async def test_partially_succeeded_is_red_unless_the_connector_says_neutral(
    server: FakeAzureDevOpsServer,
) -> None:
    for setting, expected in (("failure", "failure"), ("neutral", "success")):
        local = FakeAzureDevOpsServer()
        scm = build(local, partially_succeeded=setting)
        await scm.ensure_branch("r", "work", "main")
        head = await scm.commit_files("r", "work", {"a": "1"}, "m")
        ref = await scm.open_pr("r", "work", "main", "T", "B", draft=False)
        local.add_build(7, head, branch="work", result="partiallySucceeded")
        assert (await scm.get_pr(ref)).checks_conclusion() == expected


async def test_enqueue_merge_completes_at_the_judged_commit_without_auto_complete(
    server: FakeAzureDevOpsServer,
) -> None:
    scm = build(server, merge_strategy="noFastForward", delete_source_branch=True)
    await scm.ensure_branch("r", "work", "main")
    head = await scm.commit_files("r", "work", {"a": "1"}, "m")
    ref = await scm.open_pr("r", "work", "main", "T", "B", draft=False)
    await scm.enqueue_merge(ref, head)
    assert server.prs[1]["status"] == "completed"
    patch = next(b for b in server.bodies if isinstance(b, dict) and b.get("status") == "completed")
    assert patch["lastMergeSourceCommit"] == {"commitId": head}
    assert patch["completionOptions"] == {
        "mergeStrategy": "noFastForward",
        "deleteSourceBranch": True,
        "bypassPolicy": False,
    }
    assert "autoCompleteSetBy" not in patch
    # A replay at the same commit completes nothing twice.
    completions = [b for b in server.bodies if isinstance(b, dict) and b.get("status") == "completed"]
    await scm.enqueue_merge(ref, head)
    assert [b for b in server.bodies if isinstance(b, dict) and b.get("status") == "completed"] == completions
    assert (await scm.get_pr(ref)).merged is True


async def test_a_head_that_moved_is_not_merged(server: FakeAzureDevOpsServer) -> None:
    scm = build(server)
    await scm.ensure_branch("r", "work", "main")
    judged = await scm.commit_files("r", "work", {"a": "1"}, "m")
    ref = await scm.open_pr("r", "work", "main", "T", "B", draft=False)
    later = server.push("work", {"b": "2"})
    with pytest.raises(HeadMovedError) as moved:
        await scm.enqueue_merge(ref, judged)
    assert moved.value.actual == later
    assert server.prs[1]["status"] == "active"


async def test_a_push_racing_the_completion_is_a_moved_head(server: FakeAzureDevOpsServer) -> None:
    """The head is read, then moves before the completion: Azure answers 409, which is a moved head."""
    scm = build(server)
    await scm.ensure_branch("r", "work", "main")
    judged = await scm.commit_files("r", "work", {"a": "1"}, "m")
    ref = await scm.open_pr("r", "work", "main", "T", "B", draft=False)
    original = scm.client.request

    async def racing(method: str, path: str, **kw: object) -> object:
        if method == "PATCH":
            server.push("work", {"late": "1"})
        return await original(method, path, **kw)

    scm.client.request = racing  # type: ignore[method-assign]
    with pytest.raises(HeadMovedError):
        await scm.enqueue_merge(ref, judged)
    assert server.prs[1]["status"] == "active"


async def test_a_policy_that_is_not_met_is_raised_never_bypassed(server: FakeAzureDevOpsServer) -> None:
    scm = build(server)
    await scm.ensure_branch("r", "work", "main")
    head = await scm.commit_files("r", "work", {"a": "1"}, "m")
    ref = await scm.open_pr("r", "work", "main", "T", "B", draft=False)
    server.add_policy(1, BUILD_POLICY, "Build validation", "running")
    with pytest.raises(UpstreamError, match="policy") as refused:
        await scm.enqueue_merge(ref, head)
    assert refused.value.status_code == 400
    assert server.prs[1]["status"] == "active"


async def test_completion_is_awaited_a_few_polls_and_then_left(server: FakeAzureDevOpsServer) -> None:
    slow = FakeAzureDevOpsServer(completion_polls_needed=3)
    scm = build(slow)
    await scm.ensure_branch("r", "work", "main")
    head = await scm.commit_files("r", "work", {"a": "1"}, "m")
    ref = await scm.open_pr("r", "work", "main", "T", "B", draft=False)
    await scm.enqueue_merge(ref, head)
    assert scm.slept == [1.0, 1.0]  # type: ignore[attr-defined]
    assert slow.prs[1]["status"] == "completed"
    never = FakeAzureDevOpsServer(completion_polls_needed=99)
    scm = build(never, completion_polls=2)
    await scm.ensure_branch("r", "work", "main")
    head = await scm.commit_files("r", "work", {"a": "1"}, "m")
    ref = await scm.open_pr("r", "work", "main", "T", "B", draft=False)
    await scm.enqueue_merge(ref, head)  # queued: a replay finds it done, nothing is raised
    assert never.prs[1]["status"] == "active"


async def test_a_conflicting_merge_is_an_error(server: FakeAzureDevOpsServer) -> None:
    scm = build(server)
    await scm.ensure_branch("r", "work", "main")
    head = await scm.commit_files("r", "work", {"a": "1"}, "m")
    ref = await scm.open_pr("r", "work", "main", "T", "B", draft=False)
    server.prs[1]["mergeStatus"] = "conflicts"
    server.prs[1]["_completing"] = False
    server.completion_polls_needed = 99
    with pytest.raises(UpstreamError, match="conflicts"):
        await scm.enqueue_merge(ref, head)
    assert (await scm.get_pr(ref)).mergeable is False


async def test_request_review_resolves_a_name_through_the_identities_api(
    server: FakeAzureDevOpsServer,
) -> None:
    scm = build(server)
    await scm.ensure_branch("r", "work", "main")
    ref = await scm.open_pr("r", "work", "main", "T", "B", draft=False)
    await scm.request_review(ref, ["ada@northwind.test", "northwind/team", "ghost@northwind.test"])
    assert [r["uniqueName"] for r in server.prs[1]["reviewers"]] == ["33333333-3333-3333-3333-333333333333"]
    assert any(path == "/northwind/_apis/identities" for _m, path in server.calls)


async def test_a_near_miss_is_never_made_a_reviewer(server: FakeAzureDevOpsServer) -> None:
    scm = build(server)
    await scm.ensure_branch("r", "work", "main")
    ref = await scm.open_pr("r", "work", "main", "T", "B", draft=False)
    await scm.request_review(ref, ["ada", "Ada Lovelace"])  # the first matches by substring only
    assert [r["vote"] for r in server.prs[1]["reviewers"]] == [0]  # "Ada Lovelace" is exact, "ada" is not


async def test_the_identities_are_asked_on_the_identity_host_of_the_services() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"value": []})

    client = azure_client({"base_url": BASE, "pat": "pat"}, service="azure-repos")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    scm = AzureReposScm(client, "Billing", "billing-api")
    assert await scm._identity_id("ada") is None
    assert seen[0].startswith("https://vssps.dev.azure.com/northwind/_apis/identities?")


async def test_compare_counts_lines_and_gives_a_patch(server: FakeAzureDevOpsServer) -> None:
    scm = build(server)
    server.push("main", {"keep.txt": "1\n2\n3\n", "gone.txt": "x\ny\n"})
    server.push("work", {"keep.txt": "1\n2\n3\n4\n5\n", "new.md": "# t\n", "gone.txt": None})
    diff = await scm.compare("r", "main", "work")
    by_path = {f.path: f for f in diff.files}
    assert (by_path["keep.txt"].status, by_path["keep.txt"].additions, by_path["keep.txt"].deletions) == (
        "modified",
        2,
        0,
    )
    assert (by_path["new.md"].status, by_path["new.md"].additions) == ("added", 1)
    assert (by_path["gone.txt"].status, by_path["gone.txt"].deletions) == ("removed", 2)
    assert "+4" in (by_path["keep.txt"].patch or "") and (by_path["new.md"].patch or "").startswith("@@")
    # A commit names the head just as well as a branch does.
    head = server.refs["refs/heads/work"]
    assert {f.path for f in (await scm.compare("r", "main", head)).files} == set(by_path)


async def test_compare_lists_but_does_not_count_a_file_over_the_limit(server: FakeAzureDevOpsServer) -> None:
    scm = build(server, diff_file_bytes_max=10)
    server.push("work", {"big.txt": "line\n" * 100})
    diff = await scm.compare("r", "main", "work")
    assert [(f.path, f.additions, f.patch) for f in diff.files] == [("big.txt", 0, None)]


async def test_a_commit_status_names_the_verdict_and_the_perimeter(server: FakeAzureDevOpsServer) -> None:
    scm = build(server)
    sha = server.refs["refs/heads/main"]
    await scm.create_check_run("r", sha, "choregos/scope", "failure", "out of scope", ["secrets/a"])
    status = server.commit_statuses[0]
    assert status["sha"] == sha and status["state"] == "failed"
    assert status["context"] == {"name": "choregos/scope", "genre": "choregos"}
    assert "secrets/a" in status["description"]
    await scm.create_check_run("r", sha, "choregos/evidence", "success", "ok")
    assert server.commit_statuses[1]["state"] == "succeeded"


async def test_mint_token_hands_out_the_run_token_or_refuses(server: FakeAzureDevOpsServer) -> None:
    assert await build(server, run_token="run-pat").mint_token("r", 60, []) == "run-pat"
    with pytest.raises(ConfigurationError, match="run_token"):
        await build(server).mint_token("r", 60, [])
    # The connector's own credential is never what a run gets.
    assert await build(server, run_token="run-pat").mint_token("r", 60, []) != "pat"


async def test_the_clone_url_carries_no_credential(server: FakeAzureDevOpsServer) -> None:
    assert build(server).clone_url() == f"{BASE}/Billing/_git/billing-api"


async def test_a_service_principal_sends_a_bearer_token_on_every_call() -> None:
    server = FakeAzureDevOpsServer(entra_secret="s3cret")
    client = azure_client(
        {"base_url": BASE, "auth": "entra", "tenant_id": "t", "client_id": "c", "client_secret": "s3cret"},
        service="azure-repos",
    )
    client._client = httpx.AsyncClient(transport=server.transport())
    scm = AzureReposScm(client, "Billing", "billing-api")
    assert (await scm.test())["ok"] is True
    assert await scm.head_of("r", "main") == server.refs["refs/heads/main"]
    assert len([c for c in server.calls if "oauth2/v2.0/token" in c[1]]) == 1  # cached


async def test_a_wrong_secret_or_pat_is_refused() -> None:
    server = FakeAzureDevOpsServer(entra_secret="right")
    client = azure_client(
        {"base_url": BASE, "auth": "entra", "tenant_id": "t", "client_id": "c", "client_secret": "wrong"},
        service="azure-repos",
    )
    client._client = httpx.AsyncClient(transport=server.transport())
    with pytest.raises(UpstreamError, match="Entra"):
        await AzureReposScm(client, "Billing", "billing-api").test()
    bad = azure_client({"base_url": BASE, "pat": "nope"}, service="azure-repos")
    bad._client = httpx.AsyncClient(transport=FakeAzureDevOpsServer().transport())
    with pytest.raises(UpstreamError) as refused:
        await AzureReposScm(bad, "Billing", "billing-api").test()
    assert refused.value.status_code == 203  # a sign-in page, not a JSON error


async def test_the_test_call_reports_the_default_branch(server: FakeAzureDevOpsServer) -> None:
    assert await build(server).test() == {"ok": True, "repository": "billing-api", "default_branch": "main"}


def test_the_adapter_declares_merge_at_commit_and_needs_a_repository() -> None:
    assert "merge_at_commit" in AzureReposScm.capabilities
    client = azure_client({"base_url": BASE, "pat": "p"}, service="azure-repos")
    with pytest.raises(ConfigurationError):
        AzureReposScm(client, "Billing", "")


async def test_the_pr_is_the_one_asked_for(server: FakeAzureDevOpsServer) -> None:
    scm = build(server)
    with pytest.raises(UpstreamError) as missing:
        await scm.get_pr(PrRef(repo="billing-api", number=99))
    assert missing.value.status_code == 404
