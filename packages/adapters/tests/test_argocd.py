"""Argo CD: reading through its API, promotion by a GitOps PR, never a `kubectl apply`."""

from __future__ import annotations

import base64
import json

import pytest
from choregos_adapters.cd.argocd import ArgoCdAdapter, _replace_image_tag
from choregos_adapters.errors import ConfigurationError
from choregos_adapters.github.client import GitHubClient
from choregos_core.domain import Change, Window

from ._transport import Wire

ARGO = "https://argo.test"
GH = "https://api.github.test"
GITOPS = "varga/gitops"


def argo(wire: Wire, github: Wire | None = None) -> ArgoCdAdapter:
    client = GitHubClient(token="t", base_url=GH, client=github.client()) if github else None
    return ArgoCdAdapter(
        base_url=ARGO, token="argo-token", gitops_repo=GITOPS, github=client, client=wire.client()
    )


async def test_health_and_revision_come_from_the_application() -> None:
    wire = Wire(
        {
            ("GET", "/api/v1/applications/billing-prod"): (
                200,
                {
                    "status": {
                        "health": {"status": "Degraded", "message": "crash"},
                        "sync": {"revision": "abc"},
                    }
                },
            )
        }
    )
    a = argo(wire)
    sante = await a.health("billing-prod")
    assert (sante.status, sante.message, sante.revision) == ("Degraded", "crash", "abc")
    assert await a.current_revision("billing-prod") == "abc"
    assert wire.requests[0].headers["Authorization"] == "Bearer argo-token"
    inconnue = await argo(Wire()).health("nope")
    assert inconnue.status == "Missing" and await argo(Wire()).current_revision("nope") == "unknown"


async def test_the_rollout_state_reads_the_manifest_and_sees_an_abort() -> None:
    manifest = {
        "spec": {"strategy": {"canary": {"steps": [{}, {}, {}]}}},
        "status": {
            "phase": "Paused",
            "currentStepIndex": 1,
            "canary": {"weight": 10},
            "conditions": [{"reason": "RolloutAborted"}],
            "message": "analyse KO",
        },
    }
    wire = Wire(
        {("GET", "/api/v1/applications/billing-prod/resource"): (200, {"manifest": json.dumps(manifest)})}
    )
    state = await argo(wire).rollout_status("billing-prod")
    assert state.phase == "Aborted" and (state.current_step, state.total_steps, state.canary_weight) == (
        1,
        3,
        10,
    )
    assert wire.requests[0].url.params["kind"] == "Rollout"
    assert (await argo(Wire()).rollout_status("x")).phase == "Unknown"


async def test_the_sync_windows_are_declarative() -> None:
    wire = Wire({("PATCH", "/api/v1/applications/billing-prod"): (200, {})})
    await argo(wire).set_sync_window(
        "billing-prod", [Window(kind="deny", schedule="0 18 * * 5", duration="60h")]
    )
    (fenetre,) = wire.body(-1)["spec"]["syncWindows"]
    assert (
        fenetre["kind"] == "deny"
        and fenetre["applications"] == ["billing-prod"]
        and fenetre["manualSync"] is True
    )
    await argo(Wire({("POST", "/api/v1/applications/billing-prod/rollback"): (200, {})})).abort_rollout(
        "billing-prod"
    )


async def test_without_a_github_app_promotion_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="GitOps"):
        await argo(Wire()).promote("prod", [Change(app="billing", tag="1.2.0")], "2026.09.25")


async def test_promotion_writes_the_tag_and_the_manifest_then_opens_a_pr() -> None:
    kustomization = "images:\n  - name: billing\n    newTag: 1.1.0\nresources:\n  - ../../base\n"
    gh = Wire(
        {
            ("GET", f"/repos/{GITOPS}"): (200, {"default_branch": "main"}),
            ("GET", f"/repos/{GITOPS}/git/ref/heads/main"): (200, {"object": {"sha": "base-sha"}}),
            ("POST", f"/repos/{GITOPS}/git/refs"): (201, {}),
            ("GET", f"/repos/{GITOPS}/contents/apps/billing/overlays/prod/kustomization.yaml"): (
                200,
                {"content": base64.b64encode(kustomization.encode()).decode(), "sha": "f-sha"},
            ),
            ("PUT", f"/repos/{GITOPS}/contents/apps/billing/overlays/prod/kustomization.yaml"): (200, {}),
            ("PUT", f"/repos/{GITOPS}/contents/releases/prod/manifest.yaml"): (201, {}),
            ("POST", f"/repos/{GITOPS}/pulls"): (201, {"html_url": "https://gh/pr/1"}),
        }
    )
    ref = await argo(Wire(), gh).promote(
        "prod", [Change(app="billing", tag="1.2.0", image="ghcr.io/v/billing")], "2026.09.25"
    )
    assert ref.kind == "pr" and ref.url == "https://gh/pr/1" and ref.merged is False
    paths = gh.sent()
    assert ("POST", f"/repos/{GITOPS}/git/refs") in paths, "the release branch is created from main"
    write = next(
        json.loads(r.content) for r in gh.requests if r.method == "PUT" and "kustomization" in r.url.path
    )
    assert "newTag: 1.2.0" in base64.b64decode(write["content"]).decode() and write["sha"] == "f-sha"
    assert write["branch"] == "choregos/release-2026.09.25"
    manifest = next(
        json.loads(r.content) for r in gh.requests if r.method == "PUT" and "manifest" in r.url.path
    )
    assert "sha" not in manifest, "the manifest did not exist: no sha to provide"
    pr = gh.body(-1)
    assert (
        pr["title"] == "release(prod): 2026.09.25"
        and pr["base"] == "main"
        and "| billing | `1.2.0` |" in pr["body"]
    )


def test_only_the_newtag_of_the_images_section_changes() -> None:
    content = "images:\n  - name: a\n    newTag: 1.0.0\npatches:\n  - path: x.yaml\n    newTag: keep-me\n"
    output = _replace_image_tag(content, Change(app="a", tag="2.0.0"))
    assert "newTag: 2.0.0" in output and "newTag: keep-me" in output
    assert _replace_image_tag(content, Change(app="a")) == content


async def test_the_connection_test_reads_the_version() -> None:
    assert (await argo(Wire({("GET", "/api/version"): (200, {"Version": "v2.13"})})).test()) == {
        "ok": True,
        "version": "v2.13",
    }
    assert (await argo(Wire()).test())["ok"] is False
