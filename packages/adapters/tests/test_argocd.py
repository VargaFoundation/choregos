"""Argo CD : lecture par son API, promotion par une PR GitOps — jamais un `kubectl apply`."""

from __future__ import annotations

import base64
import json

import pytest
from choregos_adapters.cd.argocd import ArgoCdAdapter, _replace_image_tag
from choregos_adapters.errors import ConfigurationError
from choregos_adapters.github.client import GitHubClient
from choregos_core.domain import Change, Window

from ._transport import Fil

ARGO = "https://argo.test"
GH = "https://api.github.test"
GITOPS = "varga/gitops"


def argo(fil: Fil, github: Fil | None = None) -> ArgoCdAdapter:
    client = GitHubClient(token="t", base_url=GH, client=github.client()) if github else None
    return ArgoCdAdapter(
        base_url=ARGO, token="argo-token", gitops_repo=GITOPS, github=client, client=fil.client()
    )


async def test_la_sante_et_la_revision_viennent_de_l_application() -> None:
    fil = Fil(
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
    a = argo(fil)
    sante = await a.health("billing-prod")
    assert (sante.status, sante.message, sante.revision) == ("Degraded", "crash", "abc")
    assert await a.current_revision("billing-prod") == "abc"
    assert fil.requetes[0].headers["Authorization"] == "Bearer argo-token"
    inconnue = await argo(Fil()).health("nope")
    assert inconnue.status == "Missing" and await argo(Fil()).current_revision("nope") == "unknown"


async def test_l_etat_du_rollout_lit_le_manifeste_et_voit_un_abandon() -> None:
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
    fil = Fil(
        {("GET", "/api/v1/applications/billing-prod/resource"): (200, {"manifest": json.dumps(manifest)})}
    )
    etat = await argo(fil).rollout_status("billing-prod")
    assert etat.phase == "Aborted" and (etat.current_step, etat.total_steps, etat.canary_weight) == (1, 3, 10)
    assert fil.requetes[0].url.params["kind"] == "Rollout"
    assert (await argo(Fil()).rollout_status("x")).phase == "Unknown"


async def test_les_fenetres_de_synchronisation_sont_declaratives() -> None:
    fil = Fil({("PATCH", "/api/v1/applications/billing-prod"): (200, {})})
    await argo(fil).set_sync_window(
        "billing-prod", [Window(kind="deny", schedule="0 18 * * 5", duration="60h")]
    )
    (fenetre,) = fil.corps(-1)["spec"]["syncWindows"]
    assert (
        fenetre["kind"] == "deny"
        and fenetre["applications"] == ["billing-prod"]
        and fenetre["manualSync"] is True
    )
    await argo(Fil({("POST", "/api/v1/applications/billing-prod/rollback"): (200, {})})).abort_rollout(
        "billing-prod"
    )


async def test_sans_app_github_la_promotion_est_une_erreur_de_configuration() -> None:
    with pytest.raises(ConfigurationError, match="GitOps"):
        await argo(Fil()).promote("prod", [Change(app="billing", tag="1.2.0")], "2026.09.25")


async def test_la_promotion_ecrit_le_tag_et_le_manifeste_puis_ouvre_une_pr() -> None:
    kustomization = "images:\n  - name: billing\n    newTag: 1.1.0\nresources:\n  - ../../base\n"
    gh = Fil(
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
    ref = await argo(Fil(), gh).promote(
        "prod", [Change(app="billing", tag="1.2.0", image="ghcr.io/v/billing")], "2026.09.25"
    )
    assert ref.kind == "pr" and ref.url == "https://gh/pr/1" and ref.merged is False
    chemins = gh.envoyees()
    assert ("POST", f"/repos/{GITOPS}/git/refs") in chemins, "la branche de release est créée depuis main"
    ecriture = next(
        json.loads(r.content) for r in gh.requetes if r.method == "PUT" and "kustomization" in r.url.path
    )
    assert "newTag: 1.2.0" in base64.b64decode(ecriture["content"]).decode() and ecriture["sha"] == "f-sha"
    assert ecriture["branch"] == "choregos/release-2026.09.25"
    manifeste = next(
        json.loads(r.content) for r in gh.requetes if r.method == "PUT" and "manifest" in r.url.path
    )
    assert "sha" not in manifeste, "le manifeste n'existait pas : pas de sha à fournir"
    pr = gh.corps(-1)
    assert (
        pr["title"] == "release(prod): 2026.09.25"
        and pr["base"] == "main"
        and "| billing | `1.2.0` |" in pr["body"]
    )


def test_seul_le_newtag_de_la_section_images_change() -> None:
    contenu = "images:\n  - name: a\n    newTag: 1.0.0\npatches:\n  - path: x.yaml\n    newTag: garde-moi\n"
    sortie = _replace_image_tag(contenu, Change(app="a", tag="2.0.0"))
    assert "newTag: 2.0.0" in sortie and "newTag: garde-moi" in sortie
    assert _replace_image_tag(contenu, Change(app="a")) == contenu


async def test_le_test_de_connexion_lit_la_version() -> None:
    assert (await argo(Fil({("GET", "/api/version"): (200, {"Version": "v2.13"})})).test()) == {
        "ok": True,
        "version": "v2.13",
    }
    assert (await argo(Fil()).test())["ok"] is False
