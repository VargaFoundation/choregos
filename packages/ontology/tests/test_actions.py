# SPDX-License-Identifier: Apache-2.0
"""Les éléments 4 à 6 de l'essai, sur le paquet IT4IT : proposer, valider, ouvrir la PR, prouver.

4. L'agent propose, par l'outil généré `action_open_infra_pr` et avec son jeton de run, une action
   dont l'effet est `gitops.pull_request` : la proposition attend une validation, avec sa
   justification. Rien n'est encore ouvert.
5. Un humain valide avec une authentification récente (sinon 401 vers `?reauth=1`) ; la PLATEFORME
   écrit les fichiers et ouvre la PR sur `choregos/<proposition>` par l'adaptateur SCM du cœur. La
   décision est consignée avec son `auth_time`.
6. La preuve : `verify_finding_fixed` attend le rapport suivant du collecteur ; `succeeded` seulement
   si la clé du constat en est absente. Une couche absente, une ligne `unreachable`, un rapport
   partiel ou un délai dépassé la font échouer — jamais « clé absente ».

Ce que ces tests ne prouvent pas : la console (la décision passe par l'API), un vrai GitHub (le SCM
est le faux du cœur, dont l'adaptateur GitHub a ses propres tests), la reprise sur panne d'un moteur
qui s'exécute dans la requête et non dans Temporal.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .aides import IT4IT, appeler_outil, connecter, creer_run, ligne, objets, paquet, poster, rapport

RUN = "run-agent-1"
CLE = "os-reboot-required"
SEMAINE_1 = rapport(
    ligne("reboot-required", "node-1", "finding", severity="medium", observed_at="2026-10-03T08:00:00Z"),
    ligne("reboot-required", "node-2", "finding", severity="high", observed_at="2026-10-03T08:00:01Z"),
    ligne("ntp-drift", "node-1", "finding", severity="low", observed_at="2026-10-03T08:00:02Z"),
)
CORRECTIF = {
    "title": "Reboot the nodes that need it",
    "path": "platform/maintenance/reboot.yaml",
    "content": "nodes: [node-1, node-2]\nstrategy: one-at-a-time\n",
}


@pytest.fixture
def scm() -> Iterator[Any]:
    """Un SCM partagé par toutes les requêtes du test, et l'horloge du moteur remise à l'heure."""
    from choregos_adapters.fakes import FakeScm
    from choregos_ontology.service import actions

    faux = FakeScm()
    actions.replace_scm(faux)
    try:
        yield faux
    finally:
        actions.replace_scm(None)
        actions.CLOCK.offset = timedelta()


@pytest.fixture
async def it4it(client: AsyncClient, projet: dict[str, Any], scm: Any) -> dict[str, str]:
    depot = await client.put(f"/api/v1/projects/{projet['id']}/ontology", json={"files": paquet(IT4IT)})
    assert depot.status_code == 200, depot.text
    assert (await poster(client, projet, SEMAINE_1)).status_code == 200
    return await creer_run(projet, RUN)


async def _proposer(client: AsyncClient, entetes: dict[str, str], **correctif: Any) -> Any:
    arguments = {
        "target": [correctif.pop("target", CLE)],
        "justification": "Two nodes report reboot-required since the last kernel update; one PR fixes both.",
        "params": {**CORRECTIF, **correctif},
    }
    return await appeler_outil(client, RUN, entetes, "action_open_infra_pr", arguments)


async def _decider(client: AsyncClient, projet: dict[str, Any], proposition: str, decision: str) -> Any:
    chemin = f"/api/v1/projects/{projet['id']}/proposals/{proposition}/decision"
    return await client.post(chemin, json={"decision": decision, "reason": "checked the diff"})


async def _proposition(client: AsyncClient, projet: dict[str, Any], proposition: str) -> dict[str, Any]:
    reponse = await client.get(f"/api/v1/projects/{projet['id']}/proposals/{proposition}")
    assert reponse.status_code == 200, reponse.text
    return dict(reponse.json())


# ───────────────────────────── élément 4 : proposer ─────────────────────────────


async def test_l_agent_propose_une_pr_qui_attend_une_validation(
    client: AsyncClient, projet: dict[str, Any], it4it: dict[str, str], scm: Any
) -> None:
    reponse = await _proposer(client, it4it)
    assert reponse["status_code"] == 201, reponse
    proposition = reponse["result"]
    assert proposition["status"] == "pending_approval"
    assert proposition["justification"].startswith("Two nodes report")
    assert proposition["proposed_by"] == {"kind": "agent", "id": "agent:platform", "run_id": RUN}
    assert proposition["approval"]["mode"] == "human"
    assert proposition["approval"]["step_up_minutes"] == 10
    assert scm.prs == {} and scm.commits == [], "rien n'est ouvert avant la validation"
    lue = await _proposition(client, projet, proposition["proposal"])
    assert lue["status"] == "pending_approval"


async def test_la_meme_proposition_deux_fois_rend_la_premiere(
    client: AsyncClient, projet: dict[str, Any], it4it: dict[str, str]
) -> None:
    premiere = await _proposer(client, it4it)
    seconde = await _proposer(client, it4it)
    assert seconde["status_code"] == 200
    assert seconde["result"]["proposal"] == premiere["result"]["proposal"]
    assert seconde["result"]["idempotent_replay"] is True
    toutes = await client.get(f"/api/v1/projects/{projet['id']}/proposals")
    assert len(toutes.json()) == 1


async def test_un_chemin_hors_de_paths_allowed_est_refuse_a_la_proposition(
    client: AsyncClient, projet: dict[str, Any], it4it: dict[str, str]
) -> None:
    refus = await _proposer(client, it4it, path="secrets/vault-token.yaml")
    assert refus["status_code"] == 422
    assert "outside paths_allowed" in refus["result"]["message"]
    remontee = await _proposer(client, it4it, path="platform/../secrets/x.yaml")
    assert remontee["status_code"] == 422
    assert (await client.get(f"/api/v1/projects/{projet['id']}/proposals")).json() == []


async def test_une_precondition_fausse_rend_la_proposition_invalide(
    client: AsyncClient, projet: dict[str, Any], it4it: dict[str, str]
) -> None:
    assert (await poster(client, projet, rapport(ligne("ntp-drift", "node-1", "ok")))).status_code == 200
    refus = await _proposer(client, it4it, target="os-ntp-drift", path="platform/ntp.yaml")
    assert refus["status_code"] == 422
    assert refus["result"]["precondition"] == "finding_open"
    assert (await client.get(f"/api/v1/projects/{projet['id']}/proposals")).json() == []


async def test_les_outils_de_statut_et_de_liste(client: AsyncClient, it4it: dict[str, str]) -> None:
    proposition = (await _proposer(client, it4it))["result"]["proposal"]
    statut = await appeler_outil(client, RUN, it4it, "action_status", {"proposal": proposition})
    assert statut["result"]["status"] == "pending_approval"
    liste = await appeler_outil(client, RUN, it4it, "action_list", {"status": "pending_approval"})
    assert [p["proposal"] for p in liste["result"]["proposals"]] == [proposition]


# ───────────────────────────── élément 5 : valider, la plateforme ouvre la PR ─────────────────────────────


async def test_valider_exige_une_authentification_recente_puis_la_plateforme_ouvre_la_pr(
    client: AsyncClient, projet: dict[str, Any], it4it: dict[str, str], scm: Any
) -> None:
    from choregos_api.security import read_session
    from choregos_ontology.service import actions

    proposition = (await _proposer(client, it4it))["result"]["proposal"]

    # Onze minutes plus tard, l'authentification de la session a vieilli au-delà de `stepUp`.
    actions.CLOCK.offset = timedelta(minutes=11)
    refus = await _decider(client, projet, proposition, "approve")
    assert refus.status_code == 401, refus.text
    assert refus.json()["errors"][0]["error"] == "step_up_required"
    assert refus.json()["errors"][0]["reauth"] == "GET /api/v1/auth/login?reauth=1"
    assert scm.prs == {}

    # L'humain se ré-authentifie (`?reauth=1`), puis valide.
    actions.CLOCK.offset = timedelta()
    await connecter(client, "admin@varga.dev", reauth=True)
    auth_time = read_session(client.cookies.get("choregos_session", ""))["auth_time"]
    validee = await _decider(client, projet, proposition, "approve")
    assert validee.status_code == 200, validee.text
    dossier = validee.json()
    assert dossier["status"] == "succeeded"

    decision = dossier["decisions"][-1]
    assert (decision["by"], decision["decision"], decision["auth_time"]) == (
        "admin@varga.dev",
        "approve",
        auth_time,
    )
    assert decision["auth_age_seconds"] < 60

    branche = f"choregos/{proposition}"
    assert scm.files[("example/infra", branche)] == {CORRECTIF["path"]: CORRECTIF["content"]}
    (pr,) = scm.prs.values()
    assert (pr.ref.head, pr.ref.base, pr.title) == (branche, "main", CORRECTIF["title"])
    assert CLE in pr.body
    assert dossier["effects"][0]["pr"] == pr.ref.number
    assert dossier["evidence"] == [
        {
            "name": "pull_request_open",
            "type": "connector.query",
            "expect": "result.state == 'open' && result.head == 'choregos/' + proposal.id",
            "status": "passed",
            "result": {"number": pr.ref.number, "state": "open", "head": branche, "url": pr.ref.url},
        }
    ]


async def test_un_developpeur_ne_valide_pas_une_action_qui_exige_un_owner(
    app: Any, client: AsyncClient, projet: dict[str, Any], it4it: dict[str, str], scm: Any
) -> None:
    proposition = (await _proposer(client, it4it))["result"]["proposal"]
    membre = {"email": "dev@varga.dev", "role": "developer"}
    assert (await client.post("/api/v1/orgs/varga/members", json=membre)).status_code in {200, 201}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as developpeur:
        await connecter(developpeur, "dev@varga.dev")
        refus = await _decider(developpeur, projet, proposition, "approve")
    assert refus.status_code == 403, refus.text
    assert scm.prs == {}


async def test_rejeter_clot_sans_effet_et_une_decision_ne_se_reprend_pas(
    client: AsyncClient, projet: dict[str, Any], it4it: dict[str, str], scm: Any
) -> None:
    proposition = (await _proposer(client, it4it))["result"]["proposal"]
    rejet = await _decider(client, projet, proposition, "reject")
    assert rejet.status_code == 200, rejet.text
    assert rejet.json()["status"] == "rejected"
    assert scm.prs == {} and scm.commits == []
    encore = await _decider(client, projet, proposition, "approve")
    assert encore.status_code == 409, encore.text


async def test_une_pr_qui_n_est_pas_celle_attendue_fait_echouer_la_preuve(
    client: AsyncClient, projet: dict[str, Any], it4it: dict[str, str], scm: Any
) -> None:
    """La preuve constate la PR : si le SCM rend une autre branche, l'action échoue."""
    original = scm.get_pr

    async def autre_branche(ref: Any) -> Any:
        etat = await original(ref)
        etat.ref.head = "someone-else/branch"
        return etat

    scm.get_pr = autre_branche
    proposition = (await _proposer(client, it4it))["result"]["proposal"]
    dossier = (await _decider(client, projet, proposition, "approve")).json()
    assert dossier["status"] == "failed"
    assert dossier["evidence"][0]["status"] == "failed"


# ───────────────────────────── élément 6 : la relance du collecteur ─────────────────────────────


async def _verifier(client: AsyncClient, entetes: dict[str, str]) -> dict[str, Any]:
    arguments = {"target": [CLE], "justification": "The PR is merged and applied by GitOps.", "params": {}}
    reponse = await appeler_outil(client, RUN, entetes, "action_verify_finding_fixed", arguments)
    assert reponse["status_code"] == 201, reponse
    return dict(reponse["result"])


async def test_sans_la_cle_dans_le_rapport_suivant_la_correction_est_prouvee(
    client: AsyncClient, projet: dict[str, Any], it4it: dict[str, str]
) -> None:
    verification = await _verifier(client, it4it)
    assert verification["approval"]["mode"] == "auto", "risque faible : la politique approuve d'office"
    assert verification["status"] == "awaiting_evidence"
    assert (await objets(projet["id"]))[CLE][1]["verification_requested_at"]

    relance = rapport(
        ligne("reboot-required", "node-1", "ok"),
        ligne("reboot-required", "node-2", "ok"),
        ligne("ntp-drift", "node-1", "finding", severity="low"),
    )
    reponse = await poster(client, projet, relance)
    assert reponse.json()["proposals_decided"] == [verification["proposal"]]
    dossier = await _proposition(client, projet, verification["proposal"])
    assert dossier["status"] == "succeeded"
    preuve = dossier["evidence"][0]
    assert preuve["result"]["key_present"] is False
    assert preuve["fact"] == {
        "name": "collector_clean",
        "scope": CLE,
        "value": True,
        "source": "collector.rerun",
    }
    assert (await objets(projet["id"]))[CLE][1]["status"] == "resolved", "la synchronisation a suivi"


@pytest.mark.parametrize(
    ("relance", "raison"),
    [
        (
            rapport(ligne("reboot-required", "node-1", "ok"), ligne("reboot-required", "node-2", "finding")),
            None,
        ),
        (rapport({"layer": "k8s", "check": "pods", "scope": "ns/a", "status": "ok"}), "absent"),
        (
            rapport(ligne("reboot-required", "node-1", "ok"), ligne("disk", "node-2", "unreachable")),
            "unreachable",
        ),
    ],
    ids=["cle-encore-presente", "couche-absente", "couche-injoignable"],
)
async def test_la_preuve_echoue_si_la_relance_ne_montre_pas_la_cle_absente(
    client: AsyncClient, projet: dict[str, Any], it4it: dict[str, str], relance: str, raison: str | None
) -> None:
    verification = await _verifier(client, it4it)
    await poster(client, projet, relance)
    dossier = await _proposition(client, projet, verification["proposal"])
    assert dossier["status"] == "failed"
    preuve = dossier["evidence"][0]
    if raison is None:
        assert preuve["result"]["key_present"] is True
        assert preuve["fact"]["value"] is False
    else:
        assert raison in preuve["error"]


async def test_un_rapport_partiel_fait_echouer_la_preuve_et_n_ecrit_aucun_objet(
    client: AsyncClient, projet: dict[str, Any], it4it: dict[str, str]
) -> None:
    verification = await _verifier(client, it4it)
    avant = await objets(projet["id"])
    tronque = rapport(ligne("reboot-required", "node-1", "ok"), fin={"_end": True, "lines": 9})
    refus = await poster(client, projet, tronque)
    assert refus.status_code == 422, refus.text
    assert refus.json()["proposals_decided"] == [verification["proposal"]]
    assert (await _proposition(client, projet, verification["proposal"]))["status"] == "failed"
    assert await objets(projet["id"]) == avant


async def test_sans_rapport_dans_le_delai_la_preuve_echoue(
    client: AsyncClient, projet: dict[str, Any], it4it: dict[str, str]
) -> None:
    from choregos_ontology.service import actions

    verification = await _verifier(client, it4it)
    actions.CLOCK.offset = timedelta(minutes=16)
    dossier = await _proposition(client, projet, verification["proposal"])
    assert dossier["status"] == "failed"
    assert "within the delay" in dossier["evidence"][0]["error"]


async def test_l_agent_propose_par_son_serveur_mcp(app: Any, it4it: dict[str, str]) -> None:
    """Le chemin de l'agent : `choregos-tools` relaie la proposition à l'API interne, jeton du run compris."""
    import json

    from choregos_tools_mcp.client import InternalClient
    from choregos_tools_mcp.server import McpServer

    interne = InternalClient("http://test/api/v1/internal", RUN, "inutilise")
    await interne.aclose()
    interne._client = AsyncClient(transport=ASGITransport(app=app), headers=it4it)
    try:
        appel = await McpServer(interne).handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "action_open_infra_pr",
                    "arguments": {"target": [CLE], "justification": "Reboot needed.", "params": CORRECTIF},
                },
            }
        )
    finally:
        await interne.aclose()
    assert appel is not None and not appel["result"].get("isError"), appel
    assert json.loads(appel["result"]["content"][0]["text"])["status"] == "pending_approval"


async def test_un_jeton_d_api_ne_decide_pas_et_un_refus_exige_un_motif(
    client: AsyncClient, projet: dict[str, Any], it4it: dict[str, str], scm: Any
) -> None:
    """Une décision exige une session humaine (403 `decision_requires_session`), et un refus, un motif."""
    proposition = (await _proposer(client, it4it))["result"]["proposal"]
    emis = await client.post("/api/v1/me/tokens", json={"name": "automate", "expires_in_days": 1})
    assert emis.status_code == 201, emis.text
    chemin = f"/api/v1/projects/{projet['id']}/proposals/{proposition}/decision"
    async with AsyncClient(transport=client._transport, base_url="http://test") as porteur:
        jeton = {"Authorization": f"Bearer {emis.json()['token']}"}
        refus = await porteur.post(chemin, headers=jeton, json={"decision": "approve", "reason": "ok"})
    assert refus.status_code == 403, refus.text
    assert refus.json()["errors"][0]["error"] == "decision_requires_session"
    sans_motif = await client.post(chemin, json={"decision": "reject"})
    assert sans_motif.status_code == 422, sans_motif.text
    assert scm.prs == {}
    assert (await _proposition(client, projet, proposition))["status"] == "pending_approval"
