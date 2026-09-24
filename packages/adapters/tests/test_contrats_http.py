"""Les adaptateurs face à leur API, requête par requête — avec un transport simulé.

Treize adaptateurs sur dix-neuf n'avaient aucun test (état des lieux du 2026-09-24) ; le
plafond de dépense de LiteLLM n'était vérifié que contre `fakes/gateway.py`, qui
réimplémente sa propre logique : le fake validait le fake. Ici, ce qui part sur le fil est
ce qu'on vérifie : chemin, en-têtes, corps — et ce qu'on fait des réponses qui fâchent.
"""

from __future__ import annotations

import json

import pytest
import respx
from choregos_adapters.errors import UpstreamError
from choregos_adapters.gateway.litellm import LiteLlmGateway
from choregos_adapters.notify.slack import SlackNotifier
from choregos_core.domain import Message
from httpx import Response

LITELLM = "http://litellm.test"


@respx.mock
async def test_litellm_mint_key_envoie_le_plafond_et_l_alias_du_run() -> None:
    route = respx.post(f"{LITELLM}/key/generate").mock(
        return_value=Response(200, json={"key": "sk-run", "token": "tok-1"})
    )
    gateway = LiteLlmGateway(LITELLM, "sk-master")
    cle = await gateway.mint_key(
        {"run_id": "r-1", "project": "p"}, budget_usd=2.5, ttl_s=900, models=["platform/standard"]
    )
    corps = json.loads(route.calls[0].request.content)
    assert corps["max_budget"] == 2.5, "le plafond DUR part chez LiteLLM : c'est lui qui coupe, pas nous"
    assert corps["duration"] == "15m" and corps["models"] == ["platform/standard"]
    assert corps["key_alias"] == "run-r-1" and "tags" not in corps, (
        "les tags sont Enterprise : jamais par défaut"
    )
    assert route.calls[0].request.headers["Authorization"] == "Bearer sk-master"
    assert cle.key == "sk-run" and cle.key_id == "tok-1" and cle.budget_usd == 2.5


@respx.mock
async def test_litellm_un_alias_deja_pris_ne_bloque_pas_la_reprise_d_un_run() -> None:
    """`key_alias` est unique à vie chez LiteLLM ; un run rejoué garde son id — on repart sans alias."""
    route = respx.post(f"{LITELLM}/key/generate").mock(
        side_effect=[
            Response(400, json={"error": "key_alias already exists"}),
            Response(200, json={"key": "sk-2"}),
        ]
    )
    cle = await LiteLlmGateway(LITELLM, "sk").mint_key({"run_id": "r-1"}, 1.0, 60, ["m"])
    assert cle.key == "sk-2"
    assert "key_alias" not in json.loads(route.calls[1].request.content)


@respx.mock
async def test_litellm_la_depense_vient_de_la_cle_et_des_journaux() -> None:
    respx.get(f"{LITELLM}/key/info").mock(return_value=Response(200, json={"info": {"spend": 0.42}}))
    respx.get(f"{LITELLM}/spend/logs").mock(
        return_value=Response(
            200,
            json=[
                {"model": "claude-sonnet-5", "prompt_tokens": 100, "completion_tokens": 20, "spend": 0.3},
                {
                    "model": "claude-haiku-4-5",
                    "prompt_tokens": 50,
                    "completion_tokens": 5,
                    "spend": 0.12,
                    "cache_read_input_tokens": 10,
                },
            ],
        )
    )
    depense = await LiteLlmGateway(LITELLM, "sk").spend("tok-1")
    assert (depense.cost_usd, depense.tokens_in, depense.tokens_out, depense.tokens_cached) == (
        0.42,
        150,
        25,
        10,
    )
    assert depense.requests == 2 and depense.models_used == ["claude-haiku-4-5", "claude-sonnet-5"]


@respx.mock
async def test_litellm_revoquer_une_cle_deja_partie_est_un_succes() -> None:
    respx.post(f"{LITELLM}/key/delete").mock(return_value=Response(404, json={"error": "not found"}))
    await LiteLlmGateway(LITELLM, "sk").revoke("tok-vieux")  # ne lève pas
    respx.post(f"{LITELLM}/key/delete").mock(return_value=Response(500, text="boom"))
    with pytest.raises(UpstreamError):
        await LiteLlmGateway(LITELLM, "sk").revoke("tok-1")


@respx.mock
async def test_litellm_le_catalogue_porte_les_prix_par_millier() -> None:
    respx.get(f"{LITELLM}/model/info").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "model_name": "platform/standard",
                        "litellm_params": {"model": "anthropic/claude-sonnet-5"},
                        "model_info": {
                            "input_cost_per_token": 0.000003,
                            "output_cost_per_token": 0.000015,
                            "max_input_tokens": 200000,
                        },
                    }
                ]
            },
        )
    )
    [modele] = await LiteLlmGateway(LITELLM, "sk").list_models()
    assert modele.provider == "anthropic" and modele.litellm_model == "anthropic/claude-sonnet-5"
    assert (modele.input_cost_per_1k, modele.output_cost_per_1k) == (0.003, 0.015)


@respx.mock
async def test_slack_webhook_puis_bot_et_leurs_refus() -> None:
    hook = respx.post("https://hooks.slack.test/x").mock(return_value=Response(200, text="ok"))
    message = Message(title="Train gelé", body="raison", severity="warning")
    await SlackNotifier(webhook_url="https://hooks.slack.test/x").send("#ops", message)
    corps = json.loads(hook.calls[0].request.content)
    assert corps["text"] == "Train gelé" and corps["blocks"][0]["type"] == "header"

    bot = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": False, "error": "channel_not_found"})
    )
    with pytest.raises(UpstreamError, match="channel_not_found"):
        await SlackNotifier(bot_token="xoxb").send("#nulle-part", message)
    assert bot.calls[0].request.headers["Authorization"] == "Bearer xoxb"

    with pytest.raises(UpstreamError, match="ni `bot_token` ni `webhook_url`"):
        await SlackNotifier().send("#ops", message)


# ───────────────────────── Ecphoria ─────────────────────────

ECPHORIA = "http://ecphoria.test"


@respx.mock
async def test_ecphoria_le_tenant_et_le_jeton_partent_dans_les_en_tetes() -> None:
    from choregos_adapters.memory.ecphoria import EcphoriaMemory

    route = respx.post(f"{ECPHORIA}/api/v1/context-pack").mock(
        return_value=Response(
            200,
            json={
                "tokens_estimated": 120,
                "truncated": False,
                "memories": [],
                "incidents": [],
                "related_items": [{"key": "D-3", "title": "Un ticket lié", "state": "done"}],
            },
        )
    )
    memoire = EcphoriaMemory(ECPHORIA, token="t-secret", read_timeout_ms=500)
    pack = await memoire.context_pack("panier", "TVA du panier", ["src/**"], budget_tokens=2000)
    requete = route.calls[0].request
    assert requete.headers["X-Ecphoria-Tenant"] == "panier", "un tenant par projet : l'isolation est là"
    assert requete.headers["Authorization"] == "Bearer t-secret"
    corps = json.loads(requete.content)
    assert (
        corps["query"] == "TVA du panier" and corps["budget_tokens"] == 2000 and corps["paths"] == ["src/**"]
    )
    assert pack.tokens_estimated == 120 and pack.related_items[0].key == "D-3"


@respx.mock
async def test_ecphoria_une_panne_rend_un_pack_vide_puis_ouvre_le_circuit() -> None:
    """Toute panne rend un pack VIDE, jamais une erreur ; après trois, on cesse d'attendre."""
    from choregos_adapters.memory.ecphoria import EcphoriaMemory

    route = respx.post(f"{ECPHORIA}/api/v1/context-pack").mock(
        return_value=Response(503, text="quorum perdu")
    )
    memoire = EcphoriaMemory(ECPHORIA, read_timeout_ms=200)
    for _ in range(3):
        pack = await memoire.context_pack("p", "q", [], budget_tokens=100)
        assert pack.memories == [] and pack.query == "q"
    assert route.call_count == 3
    await memoire.context_pack("p", "q", [], budget_tokens=100)
    assert route.call_count == 3, "circuit ouvert : plus d'appel, plus d'attente"


@respx.mock
async def test_ecphoria_la_recherche_lit_les_hits_avec_leur_score() -> None:
    from choregos_adapters.memory.ecphoria import EcphoriaMemory

    respx.post(f"{ECPHORIA}/api/v1/memories/search").mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {
                        "memory": {
                            "id": "m-1",
                            "content": "Les avoirs sont soustraits du total.",
                            "kind": "decision",
                        },
                        "score": 0.91,
                    }
                ]
            },
        )
    )
    [hit] = await EcphoriaMemory(ECPHORIA).search("panier", "avoirs", k=5)
    assert hit.id == "m-1" and "avoirs" in hit.content.lower()


# ───────────────────────── Argo CD ─────────────────────────

ARGO = "http://argocd.test"


@respx.mock
async def test_argocd_sante_revision_et_application_inconnue() -> None:
    from choregos_adapters.cd.argocd import ArgoCdAdapter

    respx.get(f"{ARGO}/api/v1/applications/billing-api-prod").mock(
        return_value=Response(
            200,
            json={
                "status": {
                    "health": {"status": "Degraded", "message": "1/3 pods"},
                    "sync": {"revision": "abc123"},
                }
            },
        )
    )
    respx.get(f"{ARGO}/api/v1/applications/nulle-part").mock(return_value=Response(404, text="not found"))
    argo = ArgoCdAdapter(base_url=ARGO, token="t")
    sante = await argo.health("billing-api-prod")
    assert (sante.status, sante.message, sante.revision) == ("Degraded", "1/3 pods", "abc123")
    assert await argo.current_revision("billing-api-prod") == "abc123"
    assert (await argo.health("nulle-part")).status == "Missing"
    assert await argo.current_revision("nulle-part") == "unknown"


@respx.mock
async def test_argocd_un_rollout_avorte_se_lit_dans_ses_conditions() -> None:
    from choregos_adapters.cd.argocd import ArgoCdAdapter

    manifest = {
        "spec": {"strategy": {"canary": {"steps": [{"setWeight": 10}, {"pause": {}}, {"setWeight": 50}]}}},
        "status": {
            "phase": "Paused",
            "currentStepIndex": 1,
            "canary": {"weight": 10},
            "conditions": [{"reason": "RolloutAborted"}],
            "message": "analyse SLO en échec",
        },
    }
    respx.get(f"{ARGO}/api/v1/applications/billing-api-prod/resource").mock(
        return_value=Response(200, json={"manifest": json.dumps(manifest)})
    )
    etat = await ArgoCdAdapter(base_url=ARGO).rollout_status("billing-api-prod")
    assert (
        etat.phase == "Aborted"
        and etat.current_step == 1
        and etat.total_steps == 3
        and etat.canary_weight == 10
    )
    assert "SLO" in etat.message

    abort = respx.post(f"{ARGO}/api/v1/applications/billing-api-prod/rollback").mock(
        return_value=Response(200, json={})
    )
    await ArgoCdAdapter(base_url=ARGO).abort_rollout("billing-api-prod")
    assert json.loads(abort.calls[0].request.content) == {"name": "billing-api-prod", "prune": False}
