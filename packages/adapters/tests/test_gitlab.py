"""Tracker GitLab : labels scopés, notes, métadonnées en description, webhooks.

Comme pour Jira, les tests parlent au protocole documenté via un transport simulé.
"""

from __future__ import annotations

import json as jsonlib
from typing import Any

import httpx
import pytest
from choregos_adapters.errors import ConfigurationError
from choregos_adapters.http import RestClient
from choregos_adapters.tracker.gitlab import GitLabTracker
from choregos_adapters.tracker.gitlab_events import parse_gitlab_event
from choregos_contracts import InboundEventType, ProjectConfig, RepoConfig, Risk, Size
from choregos_core.domain import NewItem, TrackerStateMapping

PROJECT = "varga/billing-api"
ENCODED = "varga%2Fbilling-api"


def tracker(handler: Any) -> GitLabTracker:
    client = RestClient(
        "https://gitlab.com",
        headers={"PRIVATE-TOKEN": "jeton"},
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        service="gitlab",
    )
    return GitLabTracker(client, PROJECT, webhook_secret="s3cret")


def test_un_projet_vide_est_refuse_a_la_construction() -> None:
    with pytest.raises(ConfigurationError):
        GitLabTracker(RestClient("https://gitlab.com"), "")


async def test_lecture_d_un_ticket_avec_metadonnees() -> None:
    description = (
        'Les avoirs ne sont pas déduits.\n\n<!-- choregos:fields {"Risque": "low", "Taille": "M"} -->\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/notes"):
            return httpx.Response(
                200,
                json=[
                    {"id": 1, "author": {"username": "marie"}, "body": "un avis", "system": False},
                    {"id": 2, "author": {"username": "gitlab"}, "body": "a fermé", "system": True},
                ],
            )
        # GitLab exige le chemin du projet encodé : c'est `raw_path` qui part sur le fil.
        assert request.url.raw_path.decode().startswith(f"/api/v4/projects/{ENCODED}/issues/12")
        assert request.headers["private-token"] == "jeton"
        return httpx.Response(
            200,
            json={
                "iid": 12,
                "title": "Avoirs",
                "description": description,
                "state": "opened",
                "labels": ["agent-ready", "choregos::implement"],
                "web_url": "https://gitlab.com/varga/billing-api/-/issues/12",
                "author": {"username": "augustin"},
                "assignees": [{"username": "marie"}],
            },
        )

    item = await tracker(handler).fetch_item(f"{PROJECT}#12")

    assert item.title == "Avoirs"
    assert item.size is Size.M and item.risk is Risk.LOW
    assert "choregos:fields" not in item.body, "les métadonnées ne polluent pas le corps"
    assert [c.author for c in item.comments] == ["marie"], "les notes système sont écartées"


async def test_les_candidats_sont_les_issues_agent_ready() -> None:
    vu: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        vu.update(dict(request.url.params))
        return httpx.Response(200, json=[{"iid": 7}, {"iid": 9}])

    config = ProjectConfig(slug="billing-api", org="varga", repo=RepoConfig(url="https://x/y.git"))
    keys = await tracker(handler).list_candidates(config)

    assert keys == [f"{PROJECT}#7", f"{PROJECT}#9"]
    assert vu["labels"] == "agent-ready" and vu["state"] == "opened"


async def test_l_etat_passe_par_un_label_scope() -> None:
    envoye: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        envoye.update(jsonlib.loads(request.content))
        return httpx.Response(200, json={})

    await tracker(handler).set_state(
        f"{PROJECT}#12",
        TrackerStateMapping(label="choregos::verifying", remove_labels=["choregos::implement"]),
    )

    assert envoye == {
        "add_labels": "choregos::verifying",
        "remove_labels": "choregos::implement",
    }


async def test_la_note_de_suivi_est_reecrite() -> None:
    from choregos_adapters.tracker.gitlab import STATUS_COMMENT_MARKER

    appels: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        appels.append((request.method, request.url.raw_path.decode()))
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {"id": 1, "body": "un avis"},
                    {"id": 5, "body": f"{STATUS_COMMENT_MARKER}\nancien"},
                ],
            )
        return httpx.Response(200, json={"id": 5})

    await tracker(handler).upsert_status_comment(f"{PROJECT}#12", "### Suivi\n\nnouveau")

    assert ("PUT", f"/api/v4/projects/{ENCODED}/issues/12/notes/5") in appels
    assert not any(method == "POST" for method, _ in appels)


async def test_les_champs_vivent_dans_la_description() -> None:
    envoye: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "iid": 12,
                    "description": 'Corps du ticket.\n\n<!-- choregos:fields {"Taille": "M"} -->',
                },
            )
        envoye.update(jsonlib.loads(request.content))
        return httpx.Response(200, json={})

    await tracker(handler).set_fields(f"{PROJECT}#12", {"Coût (€)": "1,25", "Run": "https://x/1"})

    description = envoye["description"]
    assert description.startswith("Corps du ticket.")
    assert description.count("choregos:fields") == 1, "un seul bloc de métadonnées"
    payload = jsonlib.loads(description.split("choregos:fields", 1)[1].rsplit("-->", 1)[0].strip())
    assert payload == {"Coût (€)": "1,25", "Run": "https://x/1", "Taille": "M"}


async def test_creation_de_ticket() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = jsonlib.loads(request.content)
        assert body["labels"] == "finding,agent-ready"
        return httpx.Response(201, json={"iid": 31})

    key = await tracker(handler).create_item(
        NewItem(title="Requête N+1", body="vue en revue", labels=["finding", "agent-ready"])
    )
    assert key == f"{PROJECT}#31"


def test_le_jeton_protege_le_webhook() -> None:
    gitlab = tracker(lambda request: httpx.Response(200, json={}))
    assert gitlab.verify_webhook({"X-Gitlab-Token": "s3cret"}, b"{}") is True
    assert gitlab.verify_webhook({"X-Gitlab-Token": "autre"}, b"{}") is False


# ───────────────────────────── webhooks ─────────────────────────────


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "object_kind": "issue",
        "project": {"path_with_namespace": PROJECT, "web_url": f"https://gitlab.com/{PROJECT}"},
        "user": {"username": "marie"},
        "object_attributes": {"iid": 12, "title": "Avoirs", "action": "open", "state": "opened"},
        "labels": [{"title": "agent-ready"}],
    }
    payload.update(overrides)
    return payload


def test_une_issue_ouverte_devient_un_evenement() -> None:
    events = parse_gitlab_event("Issue Hook", "u-1", _payload())
    assert [e.type for e in events] == [InboundEventType.ITEM_CREATED]
    assert events[0].work_item_key == f"{PROJECT}#12"
    assert events[0].project_slug == "billing-api"
    assert events[0].actor == "marie"


def test_un_label_ajoute_est_reconnu() -> None:
    events = parse_gitlab_event(
        "Issue Hook",
        "u-2",
        _payload(
            object_attributes={"iid": 12, "title": "Avoirs", "action": "update"},
            changes={"labels": {"previous": [], "current": [{"title": "agent-ready"}]}},
        ),
    )
    assert events[0].type is InboundEventType.ITEM_LABELED
    assert events[0].payload["label"] == "agent-ready"


def test_une_merge_request_fusionnee_remonte() -> None:
    events = parse_gitlab_event(
        "Merge Request Hook",
        "u-3",
        _payload(
            object_kind="merge_request",
            object_attributes={
                "iid": 4,
                "action": "merge",
                "state": "merged",
                "source_branch": "choregos/12-avoirs",
                "target_branch": "main",
                "last_commit": {"id": "abc123"},
                "url": "https://gitlab.com/x/-/merge_requests/4",
            },
        ),
    )
    assert events[0].type is InboundEventType.PR_MERGED
    assert events[0].work_item_key == f"{PROJECT}#12", "la branche désigne le ticket"
    assert events[0].payload["sha"] == "abc123"


def test_un_pipeline_en_echec_remonte_comme_ci() -> None:
    events = parse_gitlab_event(
        "Pipeline Hook",
        "u-4",
        _payload(
            object_kind="pipeline",
            object_attributes={
                "id": 77,
                "status": "failed",
                "ref": "choregos/12-avoirs",
                "sha": "abc123",
            },
        ),
    )
    assert events[0].type is InboundEventType.CI_FAILED
    assert events[0].payload["pipeline_id"] == 77


def test_une_commande_en_note_devient_une_decision() -> None:
    events = parse_gitlab_event(
        "Note Hook",
        "u-5",
        _payload(
            object_kind="note",
            issue={"iid": 12},
            object_attributes={"note": "/choregos reject trop risqué", "url": "https://x#note_1"},
        ),
    )
    assert [e.type for e in events] == [
        InboundEventType.ITEM_COMMENTED,
        InboundEventType.HUMAN_DECISION,
    ]
    assert events[1].payload["verb"] == "reject"
    assert events[1].payload["argument"] == "trop risqué"


def test_une_note_hors_ticket_est_ignoree() -> None:
    """Une note sur une MR ou un commit n'est pas une décision de ticket."""
    events = parse_gitlab_event(
        "Note Hook",
        "u-6",
        _payload(object_kind="note", object_attributes={"note": "/choregos approve"}),
    )
    assert events == []


def test_un_evenement_inconnu_ne_produit_rien() -> None:
    assert parse_gitlab_event("Wiki Page Hook", "u-7", {"object_kind": "wiki_page"}) == []
