"""The GitLab SCM adapter against its documented API, request by request (respx mocks)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import httpx
import respx
from choregos_adapters import connector_types
from choregos_adapters.http import RestClient
from choregos_adapters.scm.gitlab import GitLabScm
from choregos_core.domain import PrRef

BASE = "https://gitlab.com/api/v4/projects/varga%2Fbilling-api"


def adapter() -> GitLabScm:
    client = RestClient("https://gitlab.com", headers={"PRIVATE-TOKEN": "tok"}, service="gitlab")
    return GitLabScm(client, "varga/billing-api")


def body_of(route: respx.Route) -> dict[str, object]:
    return json.loads(route.calls[0].request.content)  # type: ignore[no-any-return]


def day(offset: int) -> str:
    return (datetime.now(UTC).date() + timedelta(days=offset)).isoformat()


@respx.mock
async def test_mint_token_asks_for_write_repository_until_tomorrow() -> None:
    respx.get(f"{BASE}/access_tokens").mock(return_value=httpx.Response(200, json=[]))
    created = respx.post(f"{BASE}/access_tokens").mock(
        return_value=httpx.Response(201, json={"token": "glpat-run"})
    )
    token = await adapter().mint_token("varga/billing-api", 3600, ["contents"])
    sent = body_of(created)
    assert token == "glpat-run"
    assert sent["scopes"] == ["write_repository"]
    assert sent["expires_at"] == day(1)
    assert sent["access_level"] == 30
    assert str(sent["name"]).startswith("choregos-run-")


@respx.mock
async def test_mint_token_cleans_expired_run_tokens() -> None:
    respx.get(f"{BASE}/access_tokens").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 1, "name": "choregos-run-old", "expires_at": day(-1)},
                {"id": 2, "name": "choregos-run-live", "expires_at": day(1)},
                {"id": 3, "name": "ops-bot", "expires_at": day(-30)},
            ],
        )
    )
    old = respx.delete(f"{BASE}/access_tokens/1").mock(return_value=httpx.Response(204))
    live = respx.delete(f"{BASE}/access_tokens/2").mock(return_value=httpx.Response(204))
    other = respx.delete(f"{BASE}/access_tokens/3").mock(return_value=httpx.Response(204))
    respx.post(f"{BASE}/access_tokens").mock(return_value=httpx.Response(201, json={"token": "t"}))
    await adapter().mint_token("varga/billing-api", 3600, [])
    assert old.called and not live.called and not other.called


@respx.mock
async def test_a_failing_cleanup_does_not_keep_a_run_from_its_token() -> None:
    respx.get(f"{BASE}/access_tokens").mock(return_value=httpx.Response(403, json={"message": "no"}))
    respx.post(f"{BASE}/access_tokens").mock(return_value=httpx.Response(201, json={"token": "t"}))
    assert await adapter().mint_token("varga/billing-api", 3600, []) == "t"


@respx.mock
async def test_an_existing_branch_is_not_an_error() -> None:
    route = respx.post(f"{BASE}/repository/branches").mock(
        return_value=httpx.Response(400, json={"message": "Branch already exists"})
    )
    await adapter().ensure_branch("varga/billing-api", "feat/x", "main")
    assert body_of(route) == {"branch": "feat/x", "ref": "main"}


@respx.mock
async def test_branch_exists_distinguishes_absence_from_failure() -> None:
    respx.get(f"{BASE}/repository/branches/feat%2Fx").mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{BASE}/repository/branches/gone").mock(return_value=httpx.Response(404))
    assert await adapter().branch_exists("varga/billing-api", "feat/x") is True
    assert await adapter().branch_exists("varga/billing-api", "gone") is False


@respx.mock
async def test_open_pr_then_auto_merge() -> None:
    respx.get(f"{BASE}/merge_requests").mock(return_value=httpx.Response(200, json=[]))
    created = respx.post(f"{BASE}/merge_requests").mock(
        return_value=httpx.Response(201, json={"iid": 7, "web_url": "https://gitlab.com/mr/7"})
    )
    ref = await adapter().open_pr("varga/billing-api", "feat/x", "main", "Add x", "body", True)
    assert ref == PrRef(
        repo="varga/billing-api", number=7, url="https://gitlab.com/mr/7", head="feat/x", base="main"
    )
    assert body_of(created)["title"] == "Draft: Add x"
    merge = respx.put(f"{BASE}/merge_requests/7/merge").mock(return_value=httpx.Response(200, json={}))
    await adapter().enqueue_merge(ref)
    sent = body_of(merge)
    assert sent["auto_merge"] is True and sent["merge_when_pipeline_succeeds"] is True


@respx.mock
async def test_commit_files_sends_create_and_update_actions() -> None:
    respx.get(f"{BASE}/repository/files/new.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE}/repository/files/dir%2Fold.txt").mock(
        return_value=httpx.Response(
            200, json={"encoding": "base64", "content": base64.b64encode(b"before").decode()}
        )
    )
    respx.get(f"{BASE}/repository/files/same.txt").mock(
        return_value=httpx.Response(
            200, json={"encoding": "base64", "content": base64.b64encode(b"same").decode()}
        )
    )
    commit = respx.post(f"{BASE}/repository/commits").mock(
        return_value=httpx.Response(201, json={"id": "abc123"})
    )
    sha = await adapter().commit_files(
        "varga/billing-api",
        "feat/x",
        {"new.txt": "hello", "dir/old.txt": "after", "same.txt": "same"},
        "msg",
    )
    sent = body_of(commit)
    assert sha == "abc123" and sent["branch"] == "feat/x" and sent["commit_message"] == "msg"
    assert sent["actions"] == [
        {"action": "update", "file_path": "dir/old.txt", "content": "after"},
        {"action": "create", "file_path": "new.txt", "content": "hello"},
    ]


@respx.mock
async def test_get_pr_builds_the_state_from_the_merge_request_and_its_pipeline() -> None:
    respx.get(f"{BASE}/merge_requests/7").mock(
        return_value=httpx.Response(
            200,
            json={
                "iid": 7,
                "title": "Draft: Add x",
                "description": "d",
                "state": "opened",
                "draft": True,
                "sha": "abc",
                "source_branch": "feat/x",
                "target_branch": "main",
                "detailed_merge_status": "mergeable",
                "labels": ["a"],
                "web_url": "https://gitlab.com/mr/7",
            },
        )
    )
    respx.get(f"{BASE}/merge_requests/7/pipelines").mock(
        return_value=httpx.Response(200, json=[{"id": 9, "status": "failed"}, {"id": 8, "status": "success"}])
    )
    respx.get(f"{BASE}/merge_requests/7/diffs").mock(
        return_value=httpx.Response(200, json=[{"new_path": "a.py", "old_path": "a.py"}])
    )
    state = await adapter().get_pr(PrRef(repo="varga/billing-api", number=7))
    assert state.draft and not state.merged and state.mergeable is True and state.head_sha == "abc"
    assert [(c.status, c.conclusion) for c in state.checks] == [("completed", "failure")]
    assert state.files == ["a.py"] and state.labels == ["a"]
    assert state.ref.head == "feat/x" and state.ref.base == "main"


@respx.mock
async def test_request_review_resolves_usernames_to_ids() -> None:
    respx.get("https://gitlab.com/api/v4/users", params={"username": "ann"}).mock(
        return_value=httpx.Response(200, json=[{"id": 11}])
    )
    respx.get("https://gitlab.com/api/v4/users", params={"username": "ghost"}).mock(
        return_value=httpx.Response(200, json=[])
    )
    put = respx.put(f"{BASE}/merge_requests/7").mock(return_value=httpx.Response(200, json={}))
    await adapter().request_review(PrRef(repo="varga/billing-api", number=7), ["ann", "ghost", "org/team"])
    assert body_of(put) == {"reviewer_ids": [11]}


@respx.mock
async def test_compare_maps_files_into_a_diff_summary() -> None:
    route = respx.get(f"{BASE}/repository/compare").mock(
        return_value=httpx.Response(
            200,
            json={
                "diffs": [
                    {"new_path": "a.py", "old_path": "a.py", "diff": "@@ -1 +1,2 @@\n-x\n+y\n+z\n"},
                    {"new_path": "b.py", "old_path": "b.py", "new_file": True, "diff": "@@ -0,0 +1 @@\n+b\n"},
                    {
                        "new_path": "c.py",
                        "old_path": "c.py",
                        "deleted_file": True,
                        "diff": "@@ -1 +0,0 @@\n-c\n",
                    },
                    {"new_path": "n.py", "old_path": "o.py", "renamed_file": True, "diff": ""},
                ]
            },
        )
    )
    summary = await adapter().compare("varga/billing-api", "main", "feat/x")
    assert route.calls[0].request.url.params["from"] == "main"
    assert route.calls[0].request.url.params["to"] == "feat/x"
    assert [(f.path, f.status, f.additions, f.deletions) for f in summary.files] == [
        ("a.py", "modified", 2, 1),
        ("b.py", "added", 1, 0),
        ("c.py", "removed", 0, 1),
        ("n.py", "renamed", 0, 0),
    ]
    assert summary.additions == 3 and summary.deletions == 2


def test_parse_webhook_delegates_merge_request_payloads() -> None:
    body = (
        b'{"object_kind": "merge_request", "project": {"path_with_namespace": "varga/billing-api"},'
        b' "object_attributes": {"iid": 7, "action": "open", "source_branch": "feat/x",'
        b' "target_branch": "main"}}'
    )
    events = adapter().parse_webhook({"X-Gitlab-Event-UUID": "u-1"}, body)
    assert [e.type.value for e in events] == ["scm.pr.opened"]
    assert events[0].delivery_id == "u-1"


def test_the_type_is_registered_with_a_secret_token() -> None:
    specs = {(kind, name): spec for kind, name, spec in connector_types()}
    assert specs[("scm", "gitlab")].secret_fields == ("token",)
