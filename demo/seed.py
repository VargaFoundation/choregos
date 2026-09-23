#!/usr/bin/env python3
"""Met en place la démonstration : une organisation, deux projets, leurs tickets.

Écrit directement en base, comme `dev/scripts/seed.py` : la plateforme reçoit normalement
ses tickets d'un tracker (GitHub, Jira), et la démonstration n'en a pas — c'est la seule
chose qu'elle simule. Tout le reste est le vrai chemin : workflow, agents, garanties.

    python demo/seed.py            # crée ce qui manque, ne touche pas à l'existant
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys

RACINE = pathlib.Path(__file__).resolve().parent

# Les agents apportent leurs identifiants (abonnement) : on nomme donc le modèle RÉEL, pas
# un alias de passerelle. Le backend `claude-code` n'accepte que des modèles Claude.
# Sans passerelle, l'agent parle DIRECTEMENT au fournisseur : le nom doit donc être
# l'identifiant du modèle chez lui (`claude-sonnet-5`), pas la forme préfixée
# `anthropic/claude-sonnet-5` que LiteLLM attend pour router.
MODELES = {
    "standard": "claude-sonnet-5",
    "strong": "claude-sonnet-5",
    "cheap": "claude-haiku-4-5",
    "by_size": "claude-sonnet-5",
}


def chemin_workflow(nom: str) -> pathlib.Path:
    """`demo/workflows/<nom>` en local, `<nom>` à plat quand un ConfigMap les monte."""
    for candidat in (RACINE / "workflows" / nom, RACINE / nom):
        if candidat.exists():
            return candidat
    raise FileNotFoundError(f"workflow introuvable : {nom}")


PROJETS = {
    "panier": {
        "nom": "Panier (démo logicielle)",
        "workflow": "demo-code.yaml",
        "etat_initial": "inbox",
        "config": {
            "slug": "panier",
            "org": "demo",
            "repo": {
                # Le git du cluster : la démonstration n'emprunte aucun identifiant dehors.
                "url": "git://demo-git:9418/app.git",
                "default_branch": "main",
                "language": "python",
            },
            "models": {"profiles": MODELES},
        },
        # Les connecteurs sont des lignes à part (table `connectors`), pas de la config.
        # `fake` pour le tracker et le SCM : pas de GitHub sur un banc. Le reste est réel.
        "connecteurs": {
            "tracker": ("internal", {}),
            "scm": ("fake", {}),
            "ci": ("fake", {}),
            "cd": ("fake", {}),
            "runtime": ("k8s_job", {}),
            "gateway": ("direct", {}),
            "memory": ("ecphoria", {}),
            "notify": ("fake", {}),
        },
        "tickets": [
            (
                "DEMO-1",
                "Le total du panier ignore la TVA",
                "`Panier.total_ht()` existe, mais rien ne calcule le total TTC.\n\n"
                "Ajouter `total_ttc(taux)` avec un taux par défaut de 20 %, et son test.",
                "S",
            ),
            (
                "DEMO-2",
                "Impossible de retirer un article du panier",
                "Ajouter `Panier.retirer(libelle)` qui retire la première occurrence et "
                "lève `KeyError` si l'article est absent. Avec ses tests.",
                "S",
            ),
            (
                "DEMO-3",
                "Appliquer une remise en pourcentage",
                "Ajouter `Panier.appliquer_remise(pourcentage)` qui borne la remise à "
                "[0, 100] et refuse le reste par `ValueError`. Avec ses tests.",
                "M",
            ),
        ],
    },
    "staffing": {
        "nom": "Staffing (démo métier)",
        "workflow": "staffing.yaml",
        "etat_initial": "demande",
        "config": {
            "slug": "staffing",
            "org": "demo",
            # Un dépôt est exigé par le contrat de projet ; ce métier n'en a pas l'usage.
            # C'est une limite relevée dans l'ADR sur la généricité, pas un choix.
            "repo": {"url": "git://demo-git:9418/app.git", "default_branch": "main"},
            "models": {"profiles": MODELES},
        },
        "connecteurs": {
            "tracker": ("internal", {}),
            "scm": ("fake", {}),
            "ci": ("fake", {}),
            "cd": ("fake", {}),
            "runtime": ("k8s_job", {}),
            "gateway": ("direct", {}),
            "memory": ("ecphoria", {}),
            "notify": ("fake", {}),
        },
        "tickets": [
            (
                "RH-1",
                "Chef de projet data pour une mission de 6 mois",
                "Client : industrie. Démarrage sous 4 semaines, 3 jours sur site à Lille.\n"
                "Attendu : pilotage d'un chantier de migration analytique, interlocuteurs "
                "métier, connaissance dbt appréciée. TJM cible 650 €.",
                "M",
            ),
            (
                "RH-2",
                "Deux développeurs Python en renfort",
                "Renfort de 3 mois sur une API FastAPI existante. Autonomie attendue, "
                "revue de code exigeante, télétravail complet possible.",
                "M",
            ),
        ],
    },
}


async def main() -> int:
    from choregos_api.db.models import Connector, Organization, Project, WorkflowDef, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services import ensure_defaults
    from choregos_core.dsl.parser import checksum, parse_workflow
    from sqlalchemy import select

    async with session_scope() as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == "demo"))
        ).scalar_one_or_none()
        if org is None:
            org = Organization(slug="demo", name="Démonstration")
            session.add(org)
            await session.flush()

        for slug, spec in PROJETS.items():
            projet = (await session.execute(select(Project).where(Project.slug == slug))).scalar_one_or_none()
            if projet is None:
                projet = Project(
                    org_id=org.id,
                    slug=slug,
                    name=spec["nom"],
                    status="active",
                    config=spec["config"],
                )
                session.add(projet)
                await session.flush()
                print(f"projet {slug} créé")
            else:
                projet.config = spec["config"]
                print(f"projet {slug} mis à jour")

            for kind, (type_, config) in spec["connecteurs"].items():
                ligne = (
                    await session.execute(
                        select(Connector).where(Connector.project_id == projet.id, Connector.kind == kind)
                    )
                ).scalar_one_or_none()
                if ligne is None:
                    session.add(
                        Connector(project_id=projet.id, kind=kind, type=type_, config=config, status="ok")
                    )
                else:
                    ligne.type, ligne.config = type_, config
            await session.flush()

            source = chemin_workflow(str(spec["workflow"])).read_text(encoding="utf-8")
            workflow, rapport = parse_workflow(source, strict=False)
            if not rapport.valid:
                print(f"workflow invalide pour {slug} : {rapport.errors}", file=sys.stderr)
                return 1
            # Le projet reçoit d'abord ses défauts (politique, workflow du produit), puis
            # on installe SON workflow à la place — c'est ce que fait `PUT /workflow`.
            await ensure_defaults(session, projet)
            for ancien in (
                await session.execute(
                    select(WorkflowDef).where(
                        WorkflowDef.project_id == projet.id, WorkflowDef.is_active.is_(True)
                    )
                )
            ).scalars():
                ancien.is_active = False
            # Rejouable : (projet, nom, version) est unique. On met à jour celui qui existe
            # plutôt que d'en insérer un second — sinon un second passage casse sur la
            # contrainte, et le script cesserait d'être rejouable.
            defini = (
                await session.execute(
                    select(WorkflowDef).where(
                        WorkflowDef.project_id == projet.id,
                        WorkflowDef.name == workflow.metadata.name,
                        WorkflowDef.version == workflow.metadata.version,
                    )
                )
            ).scalar_one_or_none()
            document = workflow.model_dump(mode="json", by_alias=True, exclude_none=True)
            if defini is None:
                defini = WorkflowDef(
                    project_id=projet.id,
                    name=workflow.metadata.name,
                    version=workflow.metadata.version,
                    source="projet",
                )
                session.add(defini)
            defini.yaml = source
            defini.json_doc = document
            defini.checksum = checksum(workflow)
            defini.is_active = True
            await session.flush()

            for cle, titre, corps, taille in spec["tickets"]:
                deja = (
                    await session.execute(select(WorkItem).where(WorkItem.tracker_key == cle))
                ).scalar_one_or_none()
                if deja is not None:
                    print(f"  ticket {cle} déjà là ({deja.state})")
                    continue
                session.add(
                    WorkItem(
                        project_id=projet.id,
                        tracker_key=cle,
                        title=titre,
                        body_snapshot=corps,
                        state=spec["etat_initial"],
                        size=taille,
                        allowed_paths=["src/**", "tests/**"],
                    )
                )
                print(f"  ticket {cle} créé")
    if "--demarrer" in sys.argv:
        await demarrer()
    return 0


async def demarrer() -> None:
    """Démarre l'interpréteur de chaque ticket — ce que fait `mark_agent_ready` dans l'API.

    Un ticket posé en base ne bouge pas tout seul : c'est un workflow Temporal, par ticket,
    qui lit l'état, choisit la transition et lance l'agent. Le démarrer ici évite d'inventer
    une porte d'entrée que la plateforme n'a pas.
    """
    from choregos_api.db.models import Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.temporal import get_temporal, interpreter_id
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (
            await session.execute(select(WorkItem, Project).join(Project, Project.id == WorkItem.project_id))
        ).all()
        for item, projet in rows:
            if item.temporal_wf_id:
                print(f"  {item.tracker_key} déjà démarré")
                continue
            workflow_id = interpreter_id(projet.slug, item.tracker_key)
            charge = {
                "project_id": projet.id,
                "project_slug": projet.slug,
                "work_item_id": item.id,
                "tracker_key": item.tracker_key,
            }
            try:
                await get_temporal().start_interpreter(workflow_id, charge)
                print(f"  {item.tracker_key} démarré ({workflow_id})")
            except Exception as exc:
                # Un workflow de ce nom tourne déjà : un ticket n'a qu'un interpréteur, et
                # c'est la garantie qu'on ne le traite pas deux fois. Le script est rejouable,
                # il ne doit pas s'arrêter là-dessus.
                if "already started" not in str(exc).lower():
                    raise
                print(f"  {item.tracker_key} : un interpréteur tourne déjà")
            item.temporal_wf_id = workflow_id


if __name__ == "__main__":
    os.environ.setdefault("CHOREGOS_ENV", "dev")
    raise SystemExit(asyncio.run(main()))
