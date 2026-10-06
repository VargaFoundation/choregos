# SPDX-License-Identifier: Apache-2.0
"""L'ontologie branchée dans le cœur de Choregos comme un greffon (essai, éléments 2 et 3).

Le greffon est installé comme le ferait `pip` : un `.dist-info` qui déclare ses deux points
d'entrée, trouvé par `importlib.metadata`. C'est `create_app()` qui le charge — pas le test — et
`python -m choregos_api.migrer` qui joue sa branche de migrations.

Ce que ces tests prouvent : le dépôt d'un paquet, la synchronisation idempotente d'un rapport
observations NDJSON v1 (un rapport partiel n'écrit rien), et la lecture des objets par les outils
MCP générés, avec le jeton du run, par le serveur MCP `choregos-tools` qu'utilise l'agent.
Ce qu'ils ne prouvent pas : qu'un modèle choisisse ces outils (aucun LLM dans la boucle), ni la RLS
sur PostgreSQL (`test_greffon_postgres.py`, joué quand une base est fournie).
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .aides import appeler_outil, connecter, creer_projet, creer_run, ligne, objets, paquet, poster, rapport


@pytest.fixture
async def ontologie(client: AsyncClient, projet: dict[str, Any]) -> dict[str, Any]:
    reponse = await client.put(f"/api/v1/projects/{projet['id']}/ontology", json={"files": paquet()})
    assert reponse.status_code == 200, reponse.text
    return dict(reponse.json())


SEMAINE_1 = rapport(
    ligne("reboot-required", "node-1", "finding", severity="medium", observed_at="2026-10-03T08:00:00Z"),
    ligne("reboot-required", "node-2", "finding", severity="high", observed_at="2026-10-03T08:00:01Z"),
    ligne("reboot-required", "node-3", "ok", observed_at="2026-10-03T08:00:02Z"),
    ligne("ntp-drift", "node-1", "finding", severity="low", value=2.5, observed_at="2026-10-03T08:00:03Z"),
    ligne("disk-usage", "node-1", "ok", value=41.5, observed_at="2026-10-03T08:00:04Z"),
)


# ───────────────────────────── le greffon dans le cœur ─────────────────────────────


async def test_le_coeur_charge_le_greffon_et_sert_ses_routes(app: Any) -> None:
    chemins = app.openapi()["paths"]
    assert "/api/v1/projects/{id}/ontology" in chemins
    assert "/api/v1/projects/{id}/observations" in chemins
    assert "/api/v1/projects/{id}/objects/{object_type}" in chemins


async def test_un_paquet_valide_devient_la_version_active(
    client: AsyncClient, projet: dict[str, Any], ontologie: dict[str, Any]
) -> None:
    assert ontologie["name"] == "core-ref"
    assert ontologie["mcp_tools"] == 15
    seconde = await client.put(f"/api/v1/projects/{projet['id']}/ontology", json={"files": paquet()})
    assert seconde.status_code == 200, seconde.text
    active = await client.get(f"/api/v1/projects/{projet['id']}/ontology")
    assert active.status_code == 200, active.text
    assert active.json()["id"] == seconde.json()["id"], "une seule version active : la dernière"
    assert "finding_search" in active.json()["mcp_tools"]


async def test_un_paquet_invalide_est_refuse_avec_ses_erreurs_et_ne_remplace_rien(
    client: AsyncClient, projet: dict[str, Any], ontologie: dict[str, Any]
) -> None:
    fichiers = paquet()
    fichiers["objects/host.yaml"] = fichiers["objects/host.yaml"].replace("type: timestamp", "type: instant")
    refus = await client.put(f"/api/v1/projects/{projet['id']}/ontology", json={"files": fichiers})
    assert refus.status_code == 422, refus.text
    erreurs = refus.json()["errors"]
    assert erreurs and erreurs[0]["file"] == "objects/host.yaml"
    assert (await client.get(f"/api/v1/projects/{projet['id']}/ontology")).json()["id"] == ontologie["id"]


@pytest.mark.parametrize("nom", ["../evasion.yaml", "/etc/passwd.yaml", "objects/../../x.yaml", "notes.txt"])
async def test_un_nom_de_fichier_hors_du_paquet_est_refuse(
    client: AsyncClient, projet: dict[str, Any], nom: str
) -> None:
    fichiers = {**paquet(), nom: "apiVersion: x\n"}
    refus = await client.put(f"/api/v1/projects/{projet['id']}/ontology", json={"files": fichiers})
    assert refus.status_code == 422, refus.text


# ───────────────────────────── élément 2 : la synchronisation ─────────────────────────────


async def test_un_rapport_ouvre_les_constats_et_son_rejeu_ne_change_rien(
    client: AsyncClient, projet: dict[str, Any], ontologie: dict[str, Any]
) -> None:
    premier = await poster(client, projet, SEMAINE_1)
    assert premier.status_code == 200, premier.text
    assert premier.json() == {
        "lines": 5,
        "created": 2,
        "updated": 0,
        "resolved": 0,
        "unchanged": 0,
        "proposals_decided": [],
    }
    etat = await objets(projet["id"])
    assert set(etat) == {"os-reboot-required", "os-ntp-drift"}
    assert etat["os-reboot-required"][1]["scopes"] == ["node-1", "node-2"]

    rejeu = await poster(client, projet, SEMAINE_1)
    assert rejeu.status_code == 200, rejeu.text
    assert rejeu.json() == {
        "lines": 5,
        "created": 0,
        "updated": 0,
        "resolved": 0,
        "unchanged": 2,
        "proposals_decided": [],
    }
    assert await objets(projet["id"]) == etat, "le rejeu n'a rien écrit, pas même une version de ligne"


@pytest.mark.parametrize(
    "texte",
    [
        # Tronqué : la ligne `_end` manque, alors que le rapport résoudrait et ouvrirait des constats.
        "\n".join(
            [
                json.dumps(ligne("reboot-required", "node-1", "ok")),
                json.dumps(ligne("reboot-required", "node-2", "ok")),
                json.dumps(ligne("selinux", "node-9", "finding")),
            ]
        ),
        rapport(ligne("reboot-required", "node-1", "ok"), fin={"_end": True, "lines": 7}),
        rapport(ligne("reboot-required", "node-1", "ok"), ligne("selinux", "node-9", "maybe")),
    ],
    ids=["sans-fin", "compte-faux", "ligne-invalide"],
)
async def test_un_rapport_partiel_n_ecrit_rien(
    client: AsyncClient, projet: dict[str, Any], ontologie: dict[str, Any], texte: str
) -> None:
    assert (await poster(client, projet, SEMAINE_1)).status_code == 200
    avant = await objets(projet["id"])
    refus = await poster(client, projet, texte)
    assert refus.status_code == 422, refus.text
    assert refus.json()["errors"][0]["code"] == "observations_partial_read"
    assert await objets(projet["id"]) == avant


async def test_un_ok_resout_sa_portee_et_unreachable_ne_resout_rien(
    client: AsyncClient, projet: dict[str, Any], ontologie: dict[str, Any]
) -> None:
    await poster(client, projet, SEMAINE_1)
    semaine_2 = rapport(
        ligne("reboot-required", "node-1", "ok", observed_at="2026-10-10T08:00:00Z"),
        ligne("reboot-required", "node-2", "unreachable", observed_at="2026-10-10T08:00:01Z"),
        ligne("ntp-drift", "node-1", "ok", observed_at="2026-10-10T08:00:02Z"),
    )
    reponse = await poster(client, projet, semaine_2)
    assert reponse.json() == {
        "lines": 3,
        "created": 0,
        "updated": 1,
        "resolved": 1,
        "unchanged": 0,
        "proposals_decided": [],
    }
    etat = await objets(projet["id"])
    assert etat["os-reboot-required"][1]["scopes"] == ["node-2"]
    assert etat["os-reboot-required"][1]["status"] == "open"
    assert etat["os-ntp-drift"][1]["status"] == "resolved"
    assert etat["os-ntp-drift"][1]["resolved_at"] == "2026-10-10T08:00:02+00:00"


async def test_sans_ontologie_active_un_rapport_est_refuse(
    client: AsyncClient, projet: dict[str, Any]
) -> None:
    refus = await poster(client, projet, SEMAINE_1)
    assert refus.status_code == 409, refus.text


async def test_un_lecteur_ne_synchronise_pas(
    app: Any, client: AsyncClient, projet: dict[str, Any], ontologie: dict[str, Any]
) -> None:
    ajout = await client.post(
        "/api/v1/orgs/varga/members", json={"email": "lecteur@varga.dev", "role": "viewer"}
    )
    assert ajout.status_code in {200, 201}, ajout.text
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as lecteur:
        await connecter(lecteur, "lecteur@varga.dev")
        assert (await lecteur.get(f"/api/v1/projects/{projet['id']}/objects/finding")).status_code == 200
        refus = await poster(lecteur, projet, SEMAINE_1)
    assert refus.status_code == 403, refus.text
    assert await objets(projet["id"]) == {}


# ───────────────────────────── élément 3 : les outils, par le jeton du run ─────────────────────────────


async def _decor(projet_id: str) -> None:
    """Hôtes et services, posés en base comme le ferait une synchronisation (pas encore écrite)."""
    from choregos_api.db.session import session_scope
    from choregos_ontology.service.store import ManagedObject

    objets = [
        ("host", "node-1", {"id": "node-1", "name": "node-1", "os": "ubuntu", "ip": "10.0.0.11"}),
        ("host", "node-2", {"id": "node-2", "name": "node-2", "os": "debian", "ip": "10.0.0.12"}),
        ("service", "svc-a", {"id": "svc-a", "name": "api", "host_id": "node-1"}),
        ("service", "svc-b", {"id": "svc-b", "name": "worker", "host_id": "node-1"}),
        ("service", "svc-c", {"id": "svc-c", "name": "db", "host_id": "node-2"}),
    ]
    async with session_scope() as session:
        for type_, ident, proprietes in objets:
            session.add(
                ManagedObject(project_id=projet_id, object_type=type_, id=ident, properties=proprietes)
            )


@pytest.fixture
async def run_infra(client: AsyncClient, projet: dict[str, Any], ontologie: dict[str, Any]) -> dict[str, str]:
    assert (await poster(client, projet, SEMAINE_1)).status_code == 200
    await _decor(projet["id"])
    return await creer_run(projet, "run-infra-1")


async def _appel(http: AsyncClient, entetes: dict[str, str], outil: str, arguments: dict[str, Any]) -> Any:
    return await appeler_outil(http, "run-infra-1", entetes, outil, arguments)


async def test_le_run_voit_les_outils_generes_que_l_essai_sait_servir(
    client: AsyncClient, run_infra: dict[str, str]
) -> None:
    reponse = await client.get("/api/v1/internal/runs/run-infra-1/tools", headers=run_infra)
    assert reponse.status_code == 200, reponse.text
    noms = sorted(t["name"] for t in reponse.json()["tools"])
    assert noms == [
        "action_list",
        "action_status",
        "finding_get",
        "finding_search",
        "host_get",
        "host_search",
        "host_services",
        "ontology_describe",
        "service_get",
        "service_host",
        "service_search",
    ], "ni `alert_*` (connector), ni `escalate_finding` (`notify`), ni `open_finding` (pas un agent)"
    assert all(set(t) == {"name", "description", "inputSchema"} for t in reponse.json()["tools"])


async def test_l_agent_lit_les_constats_et_un_hote_par_les_outils(
    client: AsyncClient, run_infra: dict[str, str]
) -> None:
    from choregos_api.db.models import CostLedger, Event
    from choregos_api.db.session import session_scope
    from choregos_contracts import EventType
    from sqlalchemy import select

    ouverts = await _appel(
        client,
        run_infra,
        "finding_search",
        {"filters": [{"property": "severity", "op": "in", "value": ["high", "critical"]}]},
    )
    assert ouverts["status_code"] == 200
    assert [o["key"] for o in ouverts["result"]["objects"]] == ["os-reboot-required"]

    hote = await _appel(client, run_infra, "host_get", {"id": "node-1"})
    assert hote["result"]["object"] == {"id": "node-1", "name": "node-1", "os": "ubuntu"}, (
        "`ip` est confidentielle"
    )

    services = await _appel(client, run_infra, "host_services", {"id": "node-1"})
    assert [s["id"] for s in services["result"]["objects"]] == ["svc-a", "svc-b"]
    porteur = await _appel(client, run_infra, "service_host", {"id": "svc-c"})
    assert [h["id"] for h in porteur["result"]["objects"]] == ["node-2"]

    async with session_scope() as session:
        appels = select(Event).where(Event.type == EventType.TOOL_CALLED.value).order_by(Event.ts)
        journal = [e.payload["tool"] for e in (await session.execute(appels)).scalars()]
        couts = (
            await session.execute(select(CostLedger).where(CostLedger.run_id == "run-infra-1"))
        ).scalars()
        fournisseurs = {c.provider for c in couts}
    assert journal == ["finding_search", "host_get", "host_services", "service_host"]
    assert fournisseurs == {"greffon:choregos-ontology"}


async def test_une_propriete_confidentielle_n_existe_pas_dans_les_filtres(
    client: AsyncClient, run_infra: dict[str, str]
) -> None:
    refus = await _appel(
        client, run_infra, "host_search", {"filters": [{"property": "ip", "op": "prefix", "value": "10."}]}
    )
    assert refus["status_code"] == 400, "sinon la valeur se déduirait d'un filtre"


async def test_un_argument_hors_schema_est_refuse(client: AsyncClient, run_infra: dict[str, str]) -> None:
    refus = await _appel(client, run_infra, "finding_search", {"limit": 1000})
    assert refus["status_code"] == 400
    assert "schema" in refus["result"]["error"]


async def test_sans_jeton_ou_avec_le_jeton_d_un_autre_run_l_outil_est_refuse(
    client: AsyncClient, projet: dict[str, Any], run_infra: dict[str, str]
) -> None:
    sans = await client.post("/api/v1/internal/runs/run-infra-1/tools/host_get", json={"id": "node-1"})
    assert sans.status_code == 401, sans.text
    autre = await creer_run(projet, "run-infra-2")
    croise = await client.post(
        "/api/v1/internal/runs/run-infra-1/tools/host_get", headers=autre, json={"id": "node-1"}
    )
    assert croise.status_code == 403, croise.text


async def test_le_run_d_un_autre_projet_ne_voit_que_sesobjets(
    client: AsyncClient, projet: dict[str, Any], run_infra: dict[str, str]
) -> None:
    autre = await creer_projet(client, "infra-b")
    assert (
        await client.put(f"/api/v1/projects/{autre['id']}/ontology", json={"files": paquet()})
    ).status_code == 200
    seul = rapport(ligne("selinux", "node-b", "finding"))
    assert (await poster(client, autre, seul)).status_code == 200
    entetes = await creer_run(autre, "run-b-1")
    reponse = await client.post(
        "/api/v1/internal/runs/run-b-1/tools/finding_search", headers=entetes, json={"limit": 100}
    )
    assert [o["key"] for o in reponse.json()["result"]["objects"]] == ["os-selinux"]
    absent = await client.post(
        "/api/v1/internal/runs/run-b-1/tools/host_get", headers=entetes, json={"id": "node-1"}
    )
    assert absent.json()["status_code"] == 404


async def test_par_le_serveur_mcp_de_l_agent(app: Any, run_infra: dict[str, str]) -> None:
    """Le chemin de l'agent : `choregos-tools` (le serveur MCP du runner) relaie à l'API interne."""
    from choregos_tools_mcp.client import InternalClient
    from choregos_tools_mcp.server import McpServer

    interne = InternalClient("http://test/api/v1/internal", "run-infra-1", "inutilise")
    await interne.aclose()
    interne._client = AsyncClient(transport=ASGITransport(app=app), headers=run_infra)
    serveur = McpServer(interne)
    try:
        liste = await serveur.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert liste is not None
        noms = {t["name"] for t in liste["result"]["tools"]}
        assert {"finding_search", "host_get", "report_finding"} <= noms, "les siens ET ceux de l'ontologie"
        appel = await serveur.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "host_get", "arguments": {"id": "node-2"}},
            }
        )
    finally:
        await interne.aclose()
    assert appel is not None
    resultat = appel["result"]
    assert not resultat.get("isError"), resultat
    texte = resultat["content"][0]["text"]
    assert '"node-2"' in texte
    assert "10.0.0.12" not in texte


# ───────────────────────────── la branche de migrations ─────────────────────────────


def test_la_branche_du_greffon_cree_ses_tables_et_redescend_seule(
    greffon: None, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alembic.script import ScriptDirectory
    from choregos_api.config import reset_settings_cache
    from choregos_api.migrer import configuration, main
    from sqlalchemy import create_engine, inspect, text

    fichier = tmp_path / "migree.db"
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{fichier}")
    reset_settings_cache()
    (tete_du_coeur,) = ScriptDirectory.from_config(configuration(avec_les_greffons=False)).get_heads()
    moteur = create_engine(f"sqlite:///{fichier}")

    def etat() -> tuple[set[str], set[str]]:
        with moteur.connect() as connexion:
            versions = {r[0] for r in connexion.execute(text("SELECT version_num FROM alembic_version"))}
        return set(inspect(moteur).get_table_names()), versions

    try:
        main([])
        tables, versions = etat()
        assert {"ontology_versions", "managed_objects", "actions", "projects"} <= tables
        assert "action_proposals" not in tables, "les propositions sont des actions du cœur (onto0003)"
        # La tête du greffon dépend d'une révision du cœur, pas de sa tête : dès que le cœur avance
        # (jetons à portée, ADR 0030), les deux têtes coexistent dans `alembic_version`.
        assert "onto0003" in versions
        assert versions <= {"onto0003", tete_du_coeur}
        main(["downgrade", "ontology@base"])
        tables, versions = etat()
        assert not {"ontology_versions", "managed_objects", "action_proposals"} & tables
        assert versions == {tete_du_coeur}
    finally:
        moteur.dispose()
        reset_settings_cache()


def _proposition(ident: str, statut: str) -> dict[str, Any]:
    import json

    return {
        "id": ident,
        "statut": statut,
        "cibles": json.dumps(["os-key"]),
        "params": json.dumps({"title": "t"}),
        "par": json.dumps({"kind": "agent", "id": "agent:platform", "run_id": "r1"}),
        "approbation": json.dumps({"approvers": [{"role": "owner"}], "step_up_minutes": 10}),
    }


INSERER_UNE_PROPOSITION = (
    "INSERT INTO action_proposals (id, project_id, version_id, action_type, target_ids, params, "
    "justification, status, proposed_by, approval, decisions, effects, evidence, created_at, updated_at) "
    "VALUES (:id, 'p1', 'v1', 'open_infra_pr', :cibles, :params, 'two nodes', :statut, :par, :approbation, "
    "'[]', '[]', '[]', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
)


def test_onto0003_copie_les_propositions_dans_les_actions_puis_retire_la_table(
    greffon: None, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une proposition en attente devient une action du cœur, sous le même identifiant, avec ses
    effets du cœur déduits de sa version — elle se décidera et s'exécutera comme les autres ; une
    proposition qui s'exécutait dans la requête est close, et le dit (S20-08)."""
    import json

    from choregos_api.config import reset_settings_cache
    from choregos_api.db.models import Organization, Project
    from choregos_api.migrer import main
    from choregos_ontology.service.store import OntologyVersion
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.orm import Session

    fichier = tmp_path / "onto0003.db"
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{fichier}")
    reset_settings_cache()
    moteur = create_engine(f"sqlite:///{fichier}")
    effets = [{"type": "gitops.pull_request"}]
    ir = {"action_types": [{"name": "open_infra_pr", "effects": effets, "evidence": [{"name": "pr_open"}]}]}
    try:
        main(["upgrade", "f8a0b2c4d6e9"])
        main(["upgrade", "onto0002"])
        with Session(moteur) as session:
            session.add(Organization(id="o1", slug="varga", name="Varga"))
            session.add(Project(id="p1", org_id="o1", slug="infra", name="Infra", status="active", config={}))
            session.flush()
            session.add(
                OntologyVersion(
                    id="v1", project_id="p1", name="it4it", version="1", checksum="sha", compiled_ir=ir
                )
            )
            session.commit()
        with moteur.begin() as connexion:
            for ident, statut in (("a1", "pending_approval"), ("a2", "running")):
                connexion.execute(text(INSERER_UNE_PROPOSITION), _proposition(ident, statut))
        main([])
        assert "action_proposals" not in set(inspect(moteur).get_table_names())
        with moteur.connect() as connexion:
            lignes = {r["id"]: r for r in connexion.execute(text("SELECT * FROM actions")).mappings()}
        attente, interrompue = lignes["a1"], lignes["a2"]
        assert (attente["origin"], attente["kind"]) == ("ontology", "ontology.open_infra_pr")
        assert attente["status"] == "pending_approval"
        assert json.loads(attente["effects"]) == [
            {"effect": "ontology.effet", "with": {"index": 0}},
            {"effect": "ontology.preuve", "with": {"index": 0, "position": 1}},
        ]
        assert json.loads(attente["approval"])["approvers"] == [{"role": "project_owner", "min": 1}]
        meta = json.loads(attente["params"])["ontologie"]
        assert (meta["version_id"], meta["target_ids"], meta["params"]) == ("v1", ["os-key"], {"title": "t"})
        assert attente["run_id"] == "r1" and attente["org_id"] == "o1"
        assert interrompue["status"] == "failed" and "onto0003" in interrompue["error"]
    finally:
        moteur.dispose()
        reset_settings_cache()


# ───────────────────────────── inactif par défaut ─────────────────────────────


async def test_sans_activation_le_greffon_est_inerte(monkeypatch: pytest.MonkeyPatch) -> None:
    """Livré dans chaque image, le greffon ne fait rien tant que `CHOREGOS_ESSAI_ONTOLOGIE` n'est pas posé."""
    import os

    from choregos_api.greffons import reinitialiser, routeurs_declares
    from choregos_api.main import create_app
    from choregos_ontology.service.plugin import MIGRATIONS

    monkeypatch.delenv("CHOREGOS_ESSAI_ONTOLOGIE", raising=False)
    reinitialiser()
    try:
        chemins = create_app().openapi()["paths"]
        assert "/api/v1/projects/{id}/ontology" not in chemins
        assert routeurs_declares() == ()
        assert os.fspath(MIGRATIONS).endswith("inactif"), "inactif, la branche de migrations est vide"
        monkeypatch.setenv("CHOREGOS_ESSAI_ONTOLOGIE", "1")
        assert os.fspath(MIGRATIONS).endswith("versions")
    finally:
        reinitialiser()


def test_sans_activation_aucune_table_du_greffon(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache
    from choregos_api.migrer import main
    from sqlalchemy import create_engine, inspect

    monkeypatch.delenv("CHOREGOS_ESSAI_ONTOLOGIE", raising=False)
    fichier = tmp_path / "sans-greffon.db"
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{fichier}")
    reset_settings_cache()
    moteur = create_engine(f"sqlite:///{fichier}")
    try:
        main([])
        tables = set(inspect(moteur).get_table_names())
        assert "projects" in tables
        assert not {"ontology_versions", "managed_objects", "action_proposals"} & tables
    finally:
        moteur.dispose()
        reset_settings_cache()
