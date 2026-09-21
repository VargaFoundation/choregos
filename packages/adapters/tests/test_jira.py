"""Tracker Jira : ce que Choregos envoie sur le fil, et ce qu'il comprend en retour.

Les tests parlent au protocole HTTP documenté par Atlassian via un transport simulé :
ils prouvent la forme des requêtes, pas qu'une instance Jira réelle les accepte.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from choregos_adapters.errors import ConfigurationError, UpstreamError
from choregos_adapters.http import RestClient
from choregos_adapters.tracker.adf import markdown_to_adf, text_of_adf
from choregos_adapters.tracker.jira import JiraTracker
from choregos_adapters.tracker.jira_events import parse_jira_event
from choregos_contracts import InboundEventType, Risk, Size
from choregos_core.domain import NewItem, TrackerStateMapping

BASE = "https://varga.atlassian.net"


def tracker(handler: Any) -> JiraTracker:
    client = RestClient(
        BASE,
        auth=("bot@varga.dev", "jeton"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        service="jira",
    )
    return JiraTracker(client, "BILL", webhook_secret="s3cret")


def issue_payload(**overrides: Any) -> dict[str, Any]:
    fields = {
        "summary": "Les avoirs ne sont pas déduits",
        "description": markdown_to_adf("Quand une commande a un avoir, le total l'ignore."),
        "status": {"name": "In Progress"},
        "labels": ["agent-ready"],
        "assignee": {"displayName": "Marie"},
        "reporter": {"displayName": "Augustin"},
        "Taille": {"value": "M"},
        "Risque": {"value": "low"},
        "comment": {"comments": []},
    }
    fields.update(overrides)
    return {"key": "BILL-42", "fields": fields}


async def test_lecture_d_un_ticket() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/issue/BILL-42"
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(200, json=issue_payload())

    item = await tracker(handler).fetch_item("BILL-42")

    assert item.title == "Les avoirs ne sont pas déduits"
    assert "avoir" in item.body
    assert item.state == "In Progress"
    assert item.size is Size.M and item.risk is Risk.LOW
    assert item.url == f"{BASE}/browse/BILL-42"


async def test_les_candidats_passent_par_une_requete_jql() -> None:
    vu: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        vu["jql"] = request.url.params.get("jql")
        return httpx.Response(200, json={"issues": [{"key": "BILL-7"}, {"key": "BILL-9"}]})

    from choregos_contracts import ProjectConfig, RepoConfig

    config = ProjectConfig(slug="bill", org="varga", repo=RepoConfig(url="https://x/y.git"))
    assert await tracker(handler).list_candidates(config) == ["BILL-7", "BILL-9"]
    assert 'project = "BILL"' in vu["jql"]
    assert 'labels = "agent-ready"' in vu["jql"]
    assert "statusCategory != Done" in vu["jql"]


async def test_un_etat_du_dsl_devient_une_transition() -> None:
    appels: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        appels.append((request.method, request.url.path, request.content))
        if request.url.path.endswith("/transitions") and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "transitions": [
                        {"id": "11", "name": "Démarrer", "to": {"name": "In Progress"}},
                        {"id": "21", "name": "Terminer", "to": {"name": "Done"}},
                    ]
                },
            )
        return httpx.Response(204)

    await tracker(handler).set_state("BILL-42", TrackerStateMapping(status="Done"))

    import json as jsonlib

    transition = next(c for c in appels if c[0] == "POST")
    envoye = jsonlib.loads(transition[2])
    assert envoye == {"transition": {"id": "21"}}, "la transition choisie est celle qui mène à Done"


async def test_un_statut_inatteignable_est_une_erreur_lisible() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"transitions": [{"id": "11", "name": "Démarrer", "to": {"name": "In Progress"}}]}
        )

    with pytest.raises(ConfigurationError) as error:
        await tracker(handler).set_state("BILL-42", TrackerStateMapping(status="Déployé"))
    assert "Déployé" in str(error.value)
    assert "In Progress" in str(error.value), "l'erreur dit ce qui était possible"


def _jira_francais(appels: list[tuple[str, str, Any]]) -> Any:
    """Les transitions telles que les rend un site Jira en français (relevé sur un vrai site)."""

    def handler(request: httpx.Request) -> httpx.Response:
        appels.append((request.method, request.url.path, request.content))
        if request.url.path.endswith("/transitions") and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "transitions": [
                        {
                            "id": "11",
                            "name": "Backlog",
                            "to": {"name": "Backlog", "statusCategory": {"key": "new"}},
                        },
                        {
                            "id": "21",
                            "name": "En cours",
                            "to": {"name": "En cours", "statusCategory": {"key": "indeterminate"}},
                        },
                        {
                            "id": "31",
                            "name": "Terminé(e)",
                            "to": {"name": "Terminé(e)", "statusCategory": {"key": "done"}},
                        },
                    ]
                },
            )
        return httpx.Response(204)

    return handler


async def test_un_jira_en_francais_comprend_les_noms_anglais_des_templates() -> None:
    """« In Progress » doit trouver « En cours » : Jira traduit les noms de statuts.

    Le test live contre un vrai site francophone a échoué exactement là, avec
    « disponibles : Backlog, Selected for Development, En cours, Terminé(e) ».
    """
    import json as jsonlib

    appels: list[tuple[str, str, Any]] = []
    await tracker(_jira_francais(appels)).set_state("BILL-42", TrackerStateMapping(status="In Progress"))
    envoye = jsonlib.loads(next(c for c in appels if c[0] == "POST")[2])
    assert envoye == {"transition": {"id": "21"}}, "« In Progress » mène à « En cours » par sa catégorie"

    appels.clear()
    await tracker(_jira_francais(appels)).set_state("BILL-42", TrackerStateMapping(status="Done"))
    envoye = jsonlib.loads(next(c for c in appels if c[0] == "POST")[2])
    assert envoye == {"transition": {"id": "31"}}


async def test_le_nom_exact_l_emporte_sur_la_categorie() -> None:
    import json as jsonlib

    appels: list[tuple[str, str, Any]] = []
    await tracker(_jira_francais(appels)).set_state("BILL-42", TrackerStateMapping(status="En cours"))
    envoye = jsonlib.loads(next(c for c in appels if c[0] == "POST")[2])
    assert envoye == {"transition": {"id": "21"}}


async def test_une_categorie_explicite_est_comprise() -> None:
    import json as jsonlib

    appels: list[tuple[str, str, Any]] = []
    await tracker(_jira_francais(appels)).set_state("BILL-42", TrackerStateMapping(status="category:done"))
    envoye = jsonlib.loads(next(c for c in appels if c[0] == "POST")[2])
    assert envoye == {"transition": {"id": "31"}}


async def test_deux_statuts_de_meme_categorie_ne_se_departagent_pas_au_hasard() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "transitions": [
                    {"id": "21", "to": {"name": "En cours", "statusCategory": {"key": "indeterminate"}}},
                    {"id": "22", "to": {"name": "En revue", "statusCategory": {"key": "indeterminate"}}},
                ]
            },
        )

    with pytest.raises(ConfigurationError) as error:
        await tracker(handler).set_state("BILL-42", TrackerStateMapping(status="In Progress"))
    message = str(error.value)
    assert "En cours" in message and "En revue" in message, "l'erreur nomme les deux candidats"


async def test_le_commentaire_de_suivi_est_reecrit_et_non_duplique() -> None:
    from choregos_adapters.tracker.jira import STATUS_COMMENT_MARKER

    appels: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        appels.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "comments": [
                        {"id": "1", "body": markdown_to_adf("bonjour")},
                        {"id": "2", "body": markdown_to_adf(f"{STATUS_COMMENT_MARKER}\nancien")},
                    ]
                },
            )
        return httpx.Response(200, json={"id": "2"})

    await tracker(handler).upsert_status_comment("BILL-42", "### Suivi\n\nnouveau")

    assert ("PUT", "/rest/api/3/issue/BILL-42/comment/2") in appels
    assert not any(method == "POST" for method, _ in appels), "aucun second commentaire"


async def test_le_tableau_de_suivi_part_en_adf() -> None:
    envoye: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"comments": []})
        import json as jsonlib

        envoye.update(jsonlib.loads(request.content))
        return httpx.Response(201, json={"id": "9"})

    markdown = "### Choregos\n\n| Étape | Coût |\n| --- | --- |\n| implement | 0,42 € |"
    await tracker(handler).upsert_status_comment("BILL-42", markdown)

    document = envoye["body"]
    assert document["type"] == "doc"
    kinds = [node["type"] for node in document["content"]]
    assert "table" in kinds, "le tableau n'est pas envoyé en markdown brut"
    assert "implement" in text_of_adf(document)


async def test_les_champs_absents_du_projet_sont_ignores() -> None:
    envoye: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as jsonlib

        if request.url.path == "/rest/api/3/field":
            return httpx.Response(200, json=[{"id": "customfield_1", "name": "Coût (€)"}])
        envoye.append(jsonlib.loads(request.content))
        return httpx.Response(204)

    await tracker(handler).set_fields("BILL-42", {"Coût (€)": 1.25, "Run": "https://x/run/1"})

    assert envoye == [{"fields": {"customfield_1": 1.25}}], "seul le champ existant est écrit"


async def test_creation_de_ticket_et_lien() -> None:
    appels: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        appels.append((request.method, request.url.path))
        if request.url.path == "/rest/api/3/issue":
            return httpx.Response(201, json={"key": "BILL-77"})
        return httpx.Response(201, json={})

    jira = tracker(handler)
    key = await jira.create_item(NewItem(title="Fuite de connexion", body="Trouvé en revue"))
    assert key == "BILL-77"

    await jira.link("BILL-77", "BILL-42", "origin")
    assert ("POST", "/rest/api/3/issueLink") in appels


async def test_une_panne_jira_est_nommee() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="jeton révoqué")

    with pytest.raises(UpstreamError) as error:
        await tracker(handler).fetch_item("BILL-42")
    assert "jira" in str(error.value)
    assert error.value.status_code == 403


def test_le_secret_partage_protege_le_webhook() -> None:
    jira = tracker(lambda request: httpx.Response(200, json={}))
    assert jira.verify_webhook({"X-Choregos-Secret": "s3cret"}, b"{}") is True
    assert jira.verify_webhook({"X-Choregos-Secret": "autre"}, b"{}") is False
    assert jira.verify_webhook({}, b"{}") is False


# ───────────────────────────── webhooks ─────────────────────────────


def test_un_changement_de_statut_devient_un_deplacement() -> None:
    events = parse_jira_event(
        "d-1",
        {
            "webhookEvent": "jira:issue_updated",
            "user": {"displayName": "Marie"},
            "issue": {"key": "BILL-42", "fields": {"summary": "titre", "status": {"name": "Done"}}},
            "changelog": {"items": [{"field": "status", "fromString": "In Progress", "toString": "Done"}]},
        },
    )

    assert [e.type for e in events] == [InboundEventType.ITEM_MOVED]
    assert events[0].work_item_key == "BILL-42"
    assert events[0].project_slug == "bill"
    assert events[0].payload["to_status"] == "Done"
    assert events[0].actor == "Marie"


def test_un_label_ajoute_est_reconnu() -> None:
    events = parse_jira_event(
        "d-2",
        {
            "webhookEvent": "jira:issue_updated",
            "issue": {"key": "BILL-42", "fields": {"summary": "titre"}},
            "changelog": {
                "items": [{"field": "labels", "fromString": "urgent", "toString": "urgent agent-ready"}]
            },
        },
    )

    assert events[0].type is InboundEventType.ITEM_LABELED
    assert events[0].payload["label"] == "agent-ready"
    assert events[0].payload["removed"] is False


def test_une_commande_en_commentaire_devient_une_decision() -> None:
    events = parse_jira_event(
        "d-3",
        {
            "webhookEvent": "comment_created",
            "issue": {"key": "BILL-42", "fields": {"summary": "titre"}},
            "comment": {
                "author": {"displayName": "Augustin"},
                "body": markdown_to_adf("/choregos approve"),
            },
        },
    )

    assert [e.type for e in events] == [
        InboundEventType.ITEM_COMMENTED,
        InboundEventType.HUMAN_DECISION,
    ]
    assert events[1].payload == {
        "verb": "approve",
        "argument": "",
        "channel": "tracker",
        "by": "Augustin",
    }


def test_un_evenement_inconnu_ne_produit_rien() -> None:
    assert parse_jira_event("d-4", {"webhookEvent": "sprint_started"}) == []
    assert parse_jira_event("d-5", {}) == []
