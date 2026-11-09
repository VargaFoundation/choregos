"""The GitHub SCM: branches, PRs, checks, merge queue, and what goes out on the wire."""

from __future__ import annotations

import pytest
from choregos_adapters.errors import UpstreamError
from choregos_adapters.github.client import GitHubClient
from choregos_adapters.scm.github import GitHubScm

from ._transport import Wire

API = "https://api.github.test"
REPO = "varga/billing"


def scm(wire: Wire, **kwargs: object) -> GitHubScm:
    client = GitHubClient(token="t", base_url=API, graphql_url=f"{API}/graphql", client=wire.client())
    return GitHubScm(client, default_repo=REPO, **kwargs)  # type: ignore[arg-type]


async def test_an_existing_branch_is_not_recreated() -> None:
    wire = Wire(
        {("GET", f"/repos/{REPO}/git/ref/heads/choregos/12-x"): (200, {"ref": "refs/heads/choregos/12-x"})}
    )
    await scm(wire).ensure_branch(REPO, "choregos/12-x", "main")
    assert wire.sent("POST") == []


async def test_a_missing_branch_starts_from_the_base_sha() -> None:
    wire = Wire(
        {
            ("GET", f"/repos/{REPO}/git/ref/heads/main"): (200, {"object": {"sha": "abc123"}}),
            ("POST", f"/repos/{REPO}/git/refs"): (201, {}),
        }
    )
    await scm(wire).ensure_branch(REPO, "choregos/12-x", "main")
    assert wire.body(-1) == {"ref": "refs/heads/choregos/12-x", "sha": "abc123"}


async def test_a_failure_on_the_branch_is_not_taken_for_an_absence() -> None:
    wire = Wire({("GET", f"/repos/{REPO}/git/ref/heads/x"): (401, {"message": "Bad credentials"})})
    with pytest.raises(UpstreamError):
        await scm(wire).ensure_branch(REPO, "x", "main")
    assert wire.sent("POST") == [], "no branch is created on a 401"


async def test_branch_exists_reads_the_ref() -> None:
    present = Wire({("GET", f"/repos/{REPO}/git/ref/heads/choregos/12-x"): (200, {"ref": "x"})})
    assert await scm(present).branch_exists(REPO, "choregos/12-x") is True
    absent = Wire({("GET", f"/repos/{REPO}/git/ref/heads/choregos/12-x"): (404, {"message": "Not Found"})})
    assert await scm(absent).branch_exists(REPO, "choregos/12-x") is False


async def test_an_outage_reading_the_branch_is_not_an_absence() -> None:
    """S22-46: only a 404 means "no branch"; a 403 or a 5xx is raised, and retried upstream."""
    for status in (403, 502):
        wire = Wire({("GET", f"/repos/{REPO}/git/ref/heads/x"): (status, {"message": "nope"})})
        with pytest.raises(UpstreamError):
            await scm(wire).branch_exists(REPO, "x")


async def test_opening_a_pr_reuses_the_existing_one() -> None:
    wire = Wire({("GET", f"/repos/{REPO}/pulls"): (200, [{"number": 7, "html_url": "https://gh/pr/7"}])})
    ref = await scm(wire).open_pr(REPO, "choregos/12-x", "main", "titre", "request_body", draft=True)
    assert ref.number == 7 and ref.url == "https://gh/pr/7"
    assert wire.requests[0].url.params["head"] == "varga:choregos/12-x"
    assert wire.sent("POST") == []


async def test_opening_a_draft_pr_when_there_is_none() -> None:
    wire = Wire(
        {("GET", f"/repos/{REPO}/pulls"): (200, []), ("POST", f"/repos/{REPO}/pulls"): (201, {"number": 8})}
    )
    ref = await scm(wire).open_pr(REPO, "h", "main", "titre", "request_body", draft=True)
    assert ref.number == 8 and wire.body(-1)["draft"] is True


async def test_leaving_draft_goes_through_graphql() -> None:
    wire = Wire(
        {
            ("PATCH", f"/repos/{REPO}/pulls/8"): (200, {}),
            ("GET", f"/repos/{REPO}/pulls/8"): (200, {"node_id": "PR_x"}),
            ("POST", "/graphql"): (200, {"data": {}}),
        }
    )
    from choregos_core.domain import PrRef

    await scm(wire).update_pr(PrRef(repo=REPO, number=8), body="nouveau request_body", draft=False)
    assert wire.sent() == [
        ("PATCH", f"/repos/{REPO}/pulls/8"),
        ("GET", f"/repos/{REPO}/pulls/8"),
        ("POST", "/graphql"),
    ]
    assert wire.body(-1)["variables"] == {"id": "PR_x"}
    assert "markPullRequestReadyForReview" in wire.body(-1)["query"]


async def test_a_draft_left_as_it_is_is_not_marked_ready() -> None:
    """S51-11: `open_pull_request` asked for a draft updates the body with `draft=None`: no GraphQL
    `markPullRequestReadyForReview`, so the draft stays one."""
    from choregos_core.domain import PrRef

    wire = Wire({("PATCH", f"/repos/{REPO}/pulls/8"): (200, {})})
    await scm(wire).update_pr(PrRef(repo=REPO, number=8), body="new body", draft=None)
    assert wire.sent() == [("PATCH", f"/repos/{REPO}/pulls/8")]


async def test_a_prs_state_gathers_checks_reviews_and_files() -> None:
    from choregos_core.domain import PrRef

    wire = Wire(
        {
            ("GET", f"/repos/{REPO}/pulls/8"): (
                200,
                {
                    "html_url": "u",
                    "head": {"sha": "s1", "ref": "choregos/12-x"},
                    "base": {"ref": "main"},
                    "title": "T",
                    "body": None,
                    "draft": False,
                    "merged": False,
                    "mergeable": True,
                    "labels": [{"name": "size:M"}],
                },
            ),
            ("GET", f"/repos/{REPO}/commits/s1/check-runs"): (
                200,
                {
                    "check_runs": [
                        {"name": "ci", "status": "completed", "conclusion": "success", "html_url": "c"}
                    ]
                },
            ),
            ("GET", f"/repos/{REPO}/pulls/8/reviews"): (
                200,
                [
                    {"user": {"login": "marie"}, "state": "APPROVED", "body": "ok"},
                    {"user": {"login": "bot"}, "state": "PENDING"},
                ],
            ),
            ("GET", f"/repos/{REPO}/pulls/8/files"): (
                200,
                [{"filename": "src/a.py"}, {"filename": "src/b.py"}],
            ),
        }
    )
    pr_state = await scm(wire).get_pr(PrRef(repo=REPO, number=8))
    assert pr_state.head_sha == "s1" and pr_state.body == "" and pr_state.labels == ["size:M"]
    assert [c.conclusion for c in pr_state.checks] == ["success"]
    assert [(r.reviewer, r.state) for r in pr_state.reviews] == [("marie", "approved")], (
        "PENDING is not a review"
    )
    assert pr_state.files == ["src/a.py", "src/b.py"]


async def test_the_merge_queue_or_the_direct_merge_depending_on_the_configuration() -> None:
    from choregos_core.domain import PrRef

    wire = Wire(
        {
            ("GET", f"/repos/{REPO}/pulls/8"): (200, {"node_id": "PR_x"}),
            ("POST", "/graphql"): (200, {"data": {}}),
        }
    )
    await scm(wire).enqueue_merge(PrRef(repo=REPO, number=8))
    assert wire.body(-1)["variables"] == {"pullRequestId": "PR_x", "method": "SQUASH"}

    direct = Wire(
        {
            ("GET", f"/repos/{REPO}/pulls/8"): (200, {"node_id": "PR_x"}),
            ("PUT", f"/repos/{REPO}/pulls/8/merge"): (200, {}),
        }
    )
    await scm(direct, use_merge_queue=False, merge_method="REBASE").enqueue_merge(PrRef(repo=REPO, number=8))
    assert direct.body(-1) == {"merge_method": "rebase"}


async def test_the_scope_check_run_annotates_at_most_fifty_files() -> None:
    wire = Wire({("POST", f"/repos/{REPO}/check-runs"): (201, {})})
    out_of_scope = [f"src/{i}.py" for i in range(80)]
    await scm(wire).create_check_run(
        REPO, "s1", "choregos/scope", "failure", "80 files out of scope", out_of_scope
    )
    request_body = wire.body(-1)
    assert (
        request_body["head_sha"] == "s1"
        and request_body["conclusion"] == "failure"
        and request_body["status"] == "completed"
    )
    assert len(request_body["output"]["annotations"]) == 50, (
        "GitHub refuses more than 50 annotations per call"
    )
    assert request_body["output"]["annotations"][0]["annotation_level"] == "failure"


async def test_the_comparison_translates_the_file_statuses() -> None:
    wire = Wire(
        {
            ("GET", f"/repos/{REPO}/compare/main...h"): (
                200,
                {
                    "files": [
                        {"filename": "a", "status": "added", "additions": 3, "deletions": 0},
                        {"filename": "b", "status": "copied", "additions": 1, "deletions": 1, "patch": "@@"},
                    ]
                },
            )
        }
    )
    diff = await scm(wire).compare(REPO, "main", "h")
    assert [(f.path, f.status) for f in diff.files] == [("a", "added"), ("b", "modified")]
    assert diff.files[1].patch == "@@"


async def test_listing_a_folder_of_the_default_branch() -> None:
    """`adr_number_free` (#287) reads the ADRs already merged: the files, not the subfolders."""
    wire = Wire(
        {
            ("GET", f"/repos/{REPO}/contents/docs/adr"): (
                200,
                [
                    {"path": "docs/adr/0002-cache.md", "type": "file"},
                    {"path": "docs/adr/0001-queue.md", "type": "file"},
                    {"path": "docs/adr/images", "type": "dir"},
                ],
            )
        }
    )
    assert await scm(wire).list_files(REPO, "main", "docs/adr") == [
        "docs/adr/0001-queue.md",
        "docs/adr/0002-cache.md",
    ]
    assert wire.requests[0].url.params["ref"] == "main"


async def test_an_absent_folder_is_empty_but_a_failure_comes_up() -> None:
    assert await scm(Wire()).list_files(REPO, "main", "docs/adr") == [], "no ADR yet: 404"
    panne = Wire({("GET", f"/repos/{REPO}/contents/docs/adr"): (401, {"message": "Bad credentials"})})
    with pytest.raises(UpstreamError):
        await scm(panne).list_files(REPO, "main", "docs/adr")


async def test_reviewers_are_split_between_people_and_teams() -> None:
    from choregos_core.domain import PrRef

    wire = Wire({("POST", f"/repos/{REPO}/pulls/8/requested_reviewers"): (201, {})})
    await scm(wire).request_review(PrRef(repo=REPO, number=8), ["marie", "varga/maintainers"])
    assert wire.body(-1) == {"reviewers": ["marie"], "team_reviewers": ["maintainers"]}
    await scm(wire).request_review(PrRef(repo=REPO, number=8), [])
    assert len(wire.requests) == 1, "without a reviewer, no call"


async def test_the_runners_token_is_the_static_token_or_a_narrowed_app_token() -> None:
    wire = Wire()
    assert await scm(wire).mint_token(REPO, 900, []) == "t"
    without_app = GitHubScm(GitHubClient(base_url=API, client=wire.client()))
    with pytest.raises(UpstreamError, match="no App"):
        await without_app.mint_token(REPO, 900, [])


# ───────────────────────── writing files (effect gitops.pull_request) ─────────────────────────


def _b64(text: str) -> str:
    import base64

    return base64.b64encode(text.encode()).decode()


async def test_writing_files_creates_updates_and_skips_the_identical_one() -> None:
    contents = f"/repos/{REPO}/contents/platform"
    wire = Wire(
        {
            ("GET", f"{contents}/a.yaml"): (404, {"message": "Not Found"}),
            ("PUT", f"{contents}/a.yaml"): (201, {"commit": {"sha": "c1"}}),
            ("GET", f"{contents}/b.yaml"): (
                200,
                {"sha": "s-b", "encoding": "base64", "content": _b64("old\n")},
            ),
            ("PUT", f"{contents}/b.yaml"): (200, {"commit": {"sha": "c2"}}),
            ("GET", f"{contents}/c.yaml"): (
                200,
                {"sha": "s-c", "encoding": "base64", "content": _b64("same\n")},
            ),
        }
    )
    sha = await scm(wire).commit_files(
        REPO,
        "choregos/p-1",
        {"platform/a.yaml": "new\n", "platform/b.yaml": "fresh\n", "platform/c.yaml": "same\n"},
        "fix: restart the nodes",
    )
    assert sha == "c2"
    assert wire.sent("PUT") == [("PUT", f"{contents}/a.yaml"), ("PUT", f"{contents}/b.yaml")]
    assert all(r.url.params["ref"] == "choregos/p-1" for r in wire.requests if r.method == "GET")
    index = next(
        i for i, r in enumerate(wire.requests) if (r.method, r.url.path) == ("PUT", f"{contents}/b.yaml")
    )
    assert wire.body(index) == {
        "message": "fix: restart the nodes",
        "content": _b64("fresh\n"),
        "branch": "choregos/p-1",
        "sha": "s-b",
    }


async def test_a_failure_on_read_writes_nothing() -> None:
    wire = Wire({("GET", f"/repos/{REPO}/contents/x.yaml"): (401, {"message": "Bad credentials"})})
    with pytest.raises(UpstreamError):
        await scm(wire).commit_files(REPO, "b", {"x.yaml": "y"}, "m")
    assert wire.sent("PUT") == []


async def test_commit_statuses_count_as_checks() -> None:
    """S59-09: a CI that posts commit statuses (Tekton's `github-app-status`) is read like a check run:
    pending while it runs, failed on `error` or `failure`, green on `success`."""
    from choregos_core.domain import PrRef

    def wire_with(states: list[str]) -> Wire:
        return Wire(
            {
                ("GET", f"/repos/{REPO}/pulls/9"): (
                    200,
                    {"head": {"sha": "s9", "ref": "choregos/FON-276"}, "base": {"ref": "main"}},
                ),
                ("GET", f"/repos/{REPO}/commits/s9/check-runs"): (200, {"check_runs": []}),
                ("GET", f"/repos/{REPO}/commits/s9/status"): (
                    200,
                    {"statuses": [{"context": f"tekton-{i}", "state": s} for i, s in enumerate(states)]},
                ),
                ("GET", f"/repos/{REPO}/pulls/9/reviews"): (200, []),
                ("GET", f"/repos/{REPO}/pulls/9/files"): (200, []),
            }
        )

    ref = PrRef(repo=REPO, number=9)
    assert (await scm(wire_with(["success"])).get_pr(ref)).checks_conclusion() == "success"
    assert (await scm(wire_with(["pending"])).get_pr(ref)).checks_conclusion() == "pending"
    assert (await scm(wire_with(["success", "error"])).get_pr(ref)).checks_conclusion() == "failure"
    assert (await scm(wire_with([])).get_pr(ref)).checks_conclusion() is None, "no CI at all is not green"
