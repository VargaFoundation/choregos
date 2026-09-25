"""Le tracker GitHub : issues par REST, board Projects v2 par GraphQL, webhooks signés."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
from choregos_adapters.errors import ConfigurationError
from choregos_adapters.github.client import GitHubClient
from choregos_adapters.tracker.github import STATUS_COMMENT_MARKER, GitHubTracker
from choregos_adapters.tracker.github_events import parse_command, parse_github_event
from choregos_contracts import InboundEventType, ProjectConfig, Risk, Size
from choregos_core.domain import NewItem, TrackerStateMapping

from ._transport import Fil

API = "https://api.github.test"
REPO = "varga/billing"


def tracker(fil: Fil, **kwargs: Any) -> GitHubTracker:
    client = GitHubClient(token="t", base_url=API, graphql_url=f"{API}/graphql", client=fil.client())
    return GitHubTracker(client, REPO, webhook_secret="s3cret", **kwargs)


def test_un_depot_sans_proprietaire_est_refuse_a_la_construction() -> None:
    with pytest.raises(ConfigurationError, match="owner/name"):
        GitHubTracker(GitHubClient(token="t"), "billing")


async def test_la_lecture_d_un_ticket_traduit_labels_taille_et_risque() -> None:
    fil = Fil(
        {
            ("GET", f"/repos/{REPO}/issues/12"): (
                200,
                {
                    "title": "Avoirs",
                    "body": None,
                    "html_url": "u",
                    "state": "open",
                    "labels": [{"name": "agent-ready"}, {"name": "size:L"}, {"name": "risk:HIGH"}],
                    "assignees": [{"login": "marie"}],
                    "user": {"login": "paul"},
                },
            ),
            ("GET", f"/repos/{REPO}/issues/12/comments"): (
                200,
                [{"id": 1, "user": {"login": "bot"}, "body": f"{STATUS_COMMENT_MARKER}\nétat"}],
            ),
        }
    )
    item = await tracker(fil).fetch_item(f"{REPO}#12")
    assert item.title == "Avoirs" and item.body == "" and item.author == "paul"
    assert item.size == Size.L and item.risk == Risk.HIGH
    assert item.comments[0].marker == STATUS_COMMENT_MARKER


async def test_les_candidats_sont_les_issues_agent_ready_sans_les_pr() -> None:
    fil = Fil(
        {
            ("GET", f"/repos/{REPO}/issues"): (
                200,
                [{"number": 1}, {"number": 2, "pull_request": {"url": "x"}}, {"number": 3}],
            )
        }
    )
    cles = await tracker(fil).list_candidates(ProjectConfig(slug="billing", org="varga"))
    assert cles == [f"{REPO}#1", f"{REPO}#3"], "GitHub liste les PR parmi les issues : on les écarte"
    assert fil.requetes[0].url.params["labels"] == "agent-ready"


async def test_un_etat_remplace_le_label_scope_et_garde_les_autres() -> None:
    fil = Fil(
        {
            ("GET", f"/repos/{REPO}/issues/12"): (
                200,
                {"labels": [{"name": "choregos:inbox"}, {"name": "agent-ready"}, {"name": "bug"}]},
            ),
            ("PUT", f"/repos/{REPO}/issues/12/labels"): (200, {}),
        }
    )
    await tracker(fil).set_state(
        f"{REPO}#12", TrackerStateMapping(label="choregos:in_progress", remove_labels=["agent-ready"])
    )
    assert fil.corps(-1) == {"labels": ["bug", "choregos:in_progress"]}


async def test_le_commentaire_de_suivi_est_reecrit_et_non_duplique() -> None:
    fil = Fil(
        {
            ("GET", f"/repos/{REPO}/issues/12/comments"): (
                200,
                [{"id": 5, "body": "bonjour"}, {"id": 6, "body": f"{STATUS_COMMENT_MARKER}\nancien"}],
            ),
            ("PATCH", f"/repos/{REPO}/issues/comments/6"): (200, {}),
        }
    )
    await tracker(fil).upsert_status_comment(f"{REPO}#12", "nouveau")
    assert fil.envoyees("PATCH") == [("PATCH", f"/repos/{REPO}/issues/comments/6")]
    assert fil.corps(-1)["body"] == f"{STATUS_COMMENT_MARKER}\nnouveau"
    assert fil.envoyees("POST") == []


async def test_sans_commentaire_de_suivi_on_en_cree_un() -> None:
    fil = Fil(
        {
            ("GET", f"/repos/{REPO}/issues/12/comments"): (200, []),
            ("POST", f"/repos/{REPO}/issues/12/comments"): (201, {"id": 9}),
        }
    )
    await tracker(fil).upsert_status_comment(f"{REPO}#12", "premier")
    assert fil.corps(-1)["body"].startswith(STATUS_COMMENT_MARKER)


async def test_la_creation_d_un_ticket_rend_une_cle_lisible_et_les_liens_sont_des_commentaires() -> None:
    fil = Fil(
        {
            ("POST", f"/repos/{REPO}/issues"): (201, {"number": 40}),
            ("POST", f"/repos/{REPO}/issues/40/comments"): (201, {"id": 1}),
        }
    )
    t = tracker(fil)
    cle = await t.create_item(NewItem(title="Base de profils", body="…", labels=["finding"]))
    assert cle == f"{REPO}#40"
    await t.link(cle, f"{REPO}#12", "origin")
    assert "<!-- choregos:link:origin -->" in fil.corps(-1)["body"] and "#12" in fil.corps(-1)["body"]


def _projet(options: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "organization": {
            "projectV2": {
                "id": "PVT_1",
                "fields": {
                    "nodes": [
                        {"id": "F_status", "name": "Status", "dataType": "SINGLE_SELECT", "options": options},
                        {"id": "F_cost", "name": "Coût (€)", "dataType": "NUMBER"},
                    ]
                },
            }
        }
    }


async def test_le_board_recoit_la_colonne_et_cree_l_option_manquante() -> None:
    """Les états du DSL pilotent le board : une colonne absente est créée, pas inventée ailleurs."""
    graphql = [
        (200, {"data": _projet([{"id": "O_inbox", "name": "Inbox"}])}),  # champs du board
        (
            200,
            {
                "data": {
                    "node": {
                        "items": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": [{"id": "I_1", "content": {"number": 12}}],
                        }
                    }
                }
            },
        ),
        (
            200,
            {
                "data": {
                    "updateProjectV2Field": {
                        "projectV2Field": {
                            "options": [
                                {"id": "O_inbox", "name": "Inbox"},
                                {"id": "O_new", "name": "En cours"},
                            ]
                        }
                    }
                }
            },
        ),
        (200, {"data": {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "I_1"}}}}),
    ]
    fil = Fil({("POST", "/graphql"): graphql})
    await tracker(fil, project_number=3).set_state(f"{REPO}#12", TrackerStateMapping(status="En cours"))
    corps = [json.loads(r.content) for r in fil.requetes]
    assert corps[0]["variables"] == {"org": "varga", "number": 3}
    assert corps[2]["variables"]["name"] == "En cours", "l'option est créée"
    assert corps[3]["variables"]["value"] == {"singleSelectOptionId": "O_new"}
    assert corps[3]["variables"]["itemId"] == "I_1"


async def test_un_ticket_absent_du_board_y_est_ajoute_et_un_nombre_part_en_nombre() -> None:
    graphql = [
        (200, {"data": _projet([])}),
        (200, {"data": {"node": {"items": {"pageInfo": {"hasNextPage": False}, "nodes": []}}}}),
        (200, {"data": {"addProjectV2ItemById": {"item": {"id": "I_new"}}}}),
        (200, {"data": {}}),
    ]
    fil = Fil(
        {("POST", "/graphql"): graphql, ("GET", f"/repos/{REPO}/issues/12"): (200, {"node_id": "ISSUE_12"})}
    )
    await tracker(fil, project_number=3).set_fields(f"{REPO}#12", {"Coût (€)": "1.5", "Inconnu": "x"})
    corps = [json.loads(r.content) for r in fil.requetes if r.url.path == "/graphql"]
    assert corps[2]["variables"]["contentId"] == "ISSUE_12"
    assert corps[3]["variables"]["value"] == {"number": 1.5}
    assert len(corps) == 4, "le champ `Inconnu` n'existe pas sur ce board : rien n'est inventé"


async def test_un_board_introuvable_est_une_erreur_de_configuration() -> None:
    fil = Fil({("POST", "/graphql"): (200, {"data": {"organization": {"projectV2": None}}})})
    with pytest.raises(ConfigurationError, match="board Projects v2 #9"):
        await tracker(fil, project_number=9).set_fields(f"{REPO}#12", {"Status": "x"})


def test_la_signature_du_webhook_est_verifiee_a_temps_constant() -> None:
    t = GitHubTracker(GitHubClient(token="t"), REPO, webhook_secret="s3cret")
    corps = b'{"action": "opened"}'
    bonne = "sha256=" + hmac.new(b"s3cret", corps, hashlib.sha256).hexdigest()
    assert t.verify_webhook({"X-Hub-Signature-256": bonne}, corps)
    assert not t.verify_webhook({"x-hub-signature-256": "sha256=" + "0" * 64}, corps)
    assert not t.verify_webhook({}, corps)
    assert not GitHubTracker(GitHubClient(token="t"), REPO).verify_webhook(
        {"X-Hub-Signature-256": bonne}, corps
    )


def test_les_commandes_en_commentaire_deviennent_des_decisions() -> None:
    assert parse_command("merci\n/choregos approve") == {"verb": "approve", "argument": ""}
    assert parse_command("/Choregos reject trop large") == {"verb": "reject", "argument": "trop large"}
    assert parse_command("/choregos fly") is None
    payload = {
        "action": "created",
        "repository": {"full_name": REPO},
        "issue": {"number": 12},
        "comment": {"body": "/choregos answer oui, en euros", "user": {"login": "marie"}},
        "sender": {"login": "marie"},
    }
    events = parse_github_event("issue_comment", "d-1", payload)
    assert [e.type for e in events] == [InboundEventType.ITEM_COMMENTED, InboundEventType.HUMAN_DECISION]
    assert events[1].payload["verb"] == "answer" and events[1].payload["by"] == "marie"
    assert events[1].work_item_key == f"{REPO}#12" and events[1].project_slug == "billing"


def test_une_pr_fermee_est_fusionnee_ou_non_et_designe_son_ticket_par_sa_branche() -> None:
    base = {"repository": {"full_name": REPO}, "sender": {"login": "bot"}}
    pr = {
        "number": 8,
        "head": {"ref": "choregos/12-avoirs", "sha": "s"},
        "base": {"ref": "main"},
        "labels": [],
    }
    fusion = parse_github_event(
        "pull_request", "d", {**base, "action": "closed", "pull_request": {**pr, "merged": True}}
    )
    fermee = parse_github_event(
        "pull_request", "d", {**base, "action": "closed", "pull_request": {**pr, "merged": False}}
    )
    assert fusion[0].type == InboundEventType.PR_MERGED and fermee[0].type == InboundEventType.PR_CLOSED
    assert fusion[0].work_item_key == f"{REPO}#12"
    assert parse_github_event("pull_request", "d", {**base, "action": "edited", "pull_request": pr}) == []
    assert parse_github_event("ping", "d", base) == []


def test_une_carte_deplacee_sur_le_board_est_un_deplacement() -> None:
    payload = {
        "repository": {"full_name": REPO},
        "sender": {"login": "marie"},
        "work_item_key": f"{REPO}#12",
        "projects_v2_item": {"project_node_id": "PVT_1", "content_node_id": "I_1"},
        "changes": {
            "field_value": {"field_name": "Status", "from": {"name": "Inbox"}, "to": {"name": "Done"}}
        },
    }
    (event,) = parse_github_event("projects_v2_item", "d", payload)
    assert event.type == InboundEventType.ITEM_MOVED
    assert (event.payload["from_status"], event.payload["to_status"]) == ("Inbox", "Done")


def test_un_check_suite_termine_remonte_avec_ses_pr() -> None:
    payload = {
        "action": "completed",
        "repository": {"full_name": REPO},
        "check_suite": {
            "conclusion": "failure",
            "head_sha": "s",
            "head_branch": "choregos/12-x",
            "pull_requests": [{"number": 8}],
        },
    }
    (event,) = parse_github_event("check_suite", "d", payload)
    assert event.type == InboundEventType.CHECK_COMPLETED and event.payload["pr_numbers"] == [8]
    assert parse_github_event("check_suite", "d", {**payload, "action": "requested"}) == []
