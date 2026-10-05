# SPDX-License-Identifier: Apache-2.0
"""L'essai du socle sur le locataire dev : un VRAI agent, une VRAIE PR, une décision humaine ré-authentifiée.

Trois temps, parce qu'un humain intervient au milieu :

    uv run python essai/plateforme-dev/scenario_dev.py preparer   # projet, ontologie, constats, ticket
    # … l'agent (opencode, modèle de la passerelle) instruit le constat et propose une PR …
    # … un humain valide la proposition, ré-authentifié (voir README.md) …
    uv run python essai/plateforme-dev/scenario_dev.py verifier   # la PR, puis la relance du collecteur

Variables :
    CHOREGOS_DEV_URL    (défaut http://choregos.internal.dev.diametral.com)
    CHOREGOS_DEV_TOKEN  jeton d'API d'un humain (`chg_…`), créé dans la console ; jamais écrit ici
    CHOREGOS_DEV_ORG    l'organisation du locataire
    ESSAI_DEPOT         le dépôt de bac à sable où l'App GitHub du locataire est installée (`org/nom`)
    ESSAI_PROJET        (défaut essai-it4it)
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
from typing import Any

import httpx

ICI = pathlib.Path(__file__).resolve().parent
IT4IT = ICI.parents[1] / "packages" / "ontology" / "tests" / "fixtures" / "it4it"
URL = os.environ.get("CHOREGOS_DEV_URL", "http://choregos.internal.dev.diametral.com").rstrip("/")
PROJET = os.environ.get("ESSAI_PROJET", "essai-it4it")
CLE = "os-reboot-required"
ETAT = ICI / ".etat.json"  # identifiants du dernier passage (hors git)


def exiger(nom: str) -> str:
    valeur = os.environ.get(nom, "")
    if not valeur:
        sys.exit(f"variable absente : {nom} (voir l'en-tête de ce script)")
    return valeur


def rapport(*lignes: dict[str, Any]) -> str:
    corps = [json.dumps(ligne) for ligne in lignes]
    corps.append(json.dumps({"_end": True, "lines": len(lignes)}))
    return "\n".join(corps) + "\n"


def ligne(check: str, scope: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"layer": "os", "check": check, "scope": scope, "status": status, **extra}


def client() -> httpx.Client:
    jeton = exiger("CHOREGOS_DEV_TOKEN")
    return httpx.Client(base_url=f"{URL}/api/v1", headers={"Authorization": f"Bearer {jeton}"}, timeout=60)


def verifier_reponse(reponse: httpx.Response, *attendus: int) -> Any:
    if reponse.status_code not in attendus:
        sys.exit(
            f"{reponse.request.method} {reponse.request.url.path} → {reponse.status_code} : "
            f"{reponse.text[:600]}"
        )
    return reponse.json() if reponse.content else None


def preparer() -> None:
    org, depot = exiger("CHOREGOS_DEV_ORG"), exiger("ESSAI_DEPOT")
    with client() as http:
        projet = verifier_reponse(
            http.post(
                f"/orgs/{org}/projects",
                json={
                    "slug": PROJET,
                    "name": "Essai IT4IT (socle)",
                    "config": {
                        "slug": PROJET,
                        "org": org,
                        "agent": {"default_backend": "opencode", "allowed_backends": ["opencode"]},
                        "models": {"profiles": {"standard": "platform/standard"}},
                    },
                },
            ),
            201,
        )
        pid = projet["id"]
        print(f"projet {org}/{PROJET} : {pid}")
        verifier_reponse(
            http.put(f"/projects/{pid}/connectors/tracker", json={"type": "internal", "config": {}}), 200
        )
        verifier_reponse(
            http.put(f"/projects/{pid}/connectors/scm", json={"type": "github", "config": {"repo": depot}}),
            200,
        )
        flux = (ICI / "workflow-it4it.yaml").read_text(encoding="utf-8")
        verifier_reponse(http.put(f"/projects/{pid}/workflow", json={"yaml": flux}), 200)

        fichiers = {
            p.relative_to(IT4IT).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(IT4IT.rglob("*.yaml"))
        }
        fichiers["actions/open_infra_pr.yaml"] = fichiers["actions/open_infra_pr.yaml"].replace(
            "repo: example/infra", f"repo: {depot}"
        )
        ontologie = verifier_reponse(http.put(f"/projects/{pid}/ontology", json={"files": fichiers}), 200)
        print(f"ontologie {ontologie['name']} {ontologie['version']} : {ontologie['mcp_tools']} outils")

        for ident, os_, ip in (("node-1", "ubuntu", "10.0.0.11"), ("node-2", "debian", "10.0.0.12")):
            proposition = verifier_reponse(
                http.post(
                    f"/projects/{pid}/proposals",
                    json={
                        "action_type": "register_host",
                        "params": {"id": ident, "name": ident, "os": os_, "ip": ip},
                        "justification": "Inventaire de l'essai.",
                    },
                ),
                200,
                201,
            )
            print(f"hôte {ident} : {proposition['status']}")

        semaine = rapport(
            ligne(
                "reboot-required", "node-1", "finding", severity="medium", evidence="kernel 6.8.0-45 pending"
            ),
            ligne(
                "reboot-required", "node-2", "finding", severity="high", evidence="kernel 6.8.0-45 pending"
            ),
            ligne("ntp-drift", "node-1", "finding", severity="low", value=2.5),
            ligne("disk-usage", "node-1", "ok", value=41.5),
        )
        resume = verifier_reponse(
            http.post(
                f"/projects/{pid}/observations",
                content=semaine.encode(),
                headers={"Content-Type": "application/x-ndjson"},
            ),
            200,
        )
        print(f"rapport : {resume}")

        ticket = verifier_reponse(
            http.post(
                f"/projects/{pid}/work-items",
                json={
                    "title": "Instruire le constat de santé le plus grave et proposer sa correction",
                    "body": "Constats : `finding_search`. Proposer une PR par `action_open_infra_pr`.",
                    "start": True,
                },
            ),
            201,
        )
        ETAT.write_text(json.dumps({"projet": pid, "ticket": ticket["id"]}, indent=2), encoding="utf-8")
        print(f"ticket {ticket['id']} démarré ; suivi : {URL}/p/{PROJET}")
        attendre_l_agent(http, pid, ticket["id"])


def attendre_la_proposition(
    http: httpx.Client, pid: str, proposition: str, *statuts: str, secondes: int = 180
) -> dict[str, Any]:
    """Depuis S20-08, une proposition s'exécute dans Temporal, hors de la requête qui la décide : on
    attend l'état qu'on veut lire (ou le dernier lu au délai)."""
    fin = time.monotonic() + secondes
    while True:
        dossier = dict(verifier_reponse(http.get(f"/projects/{pid}/proposals/{proposition}"), 200))
        if dossier["status"] in statuts or time.monotonic() > fin:
            return dossier
        time.sleep(2)


def attendre_l_agent(http: httpx.Client, pid: str, ticket: str, minutes: int = 25) -> None:
    fin = time.monotonic() + minutes * 60
    while time.monotonic() < fin:
        item = verifier_reponse(http.get(f"/work-items/{ticket}"), 200)
        etat = item.get("state")
        if etat in {"propose", "needs_human", "done"}:
            break
        time.sleep(20)
    print(f"ticket : {item.get('state')}")
    for run in verifier_reponse(http.get(f"/work-items/{ticket}/runs"), 200):
        print(f"  run {run['id']} : {run.get('status')} — {run.get('cost_usd')} USD")
    propositions = verifier_reponse(
        http.get(f"/projects/{pid}/proposals", params={"status": "pending_approval"}), 200
    )
    for p in propositions:
        print(f"\nproposition {p['proposal']} : {p['action_type']} sur {p['target']}")
        print(f"  justification de l'agent : {p['justification']}")
        print(f"  à décider dans la console, ré-authentifié : {URL}/p/{PROJET}/actions/{p['proposal']}")
    if not propositions:
        print("aucune proposition en attente : lire le journal du run dans la console")


def verifier() -> None:
    etat = json.loads(ETAT.read_text(encoding="utf-8"))
    pid = etat["projet"]
    with client() as http:
        propositions = verifier_reponse(http.get(f"/projects/{pid}/proposals"), 200)
        pr = [p for p in propositions if p["action_type"] == "open_infra_pr"]
        for p in pr:
            print(f"{p['proposal']} : {p['status']}")
            for decision in p["decisions"]:
                print(
                    f"  décision : {decision['decision']} par {decision['by']}, "
                    f"authentifié {decision.get('auth_age_seconds')} s avant"
                )
            for effet in p["effects"]:
                print(f"  PR : {effet.get('url')} (branche {effet.get('head')})")
            for preuve in p["evidence"]:
                print(f"  preuve {preuve['name']} : {preuve['status']}")
        verification = verifier_reponse(
            http.post(
                f"/projects/{pid}/proposals",
                json={
                    "action_type": "verify_finding_fixed",
                    "target": [CLE],
                    "params": {},
                    "justification": "PR fusionnée.",
                },
            ),
            201,
        )
        # La relance ne tranche qu'une preuve DEMANDÉE avant elle : on attend que l'action l'attende.
        verification = attendre_la_proposition(
            http, pid, verification["proposal"], "awaiting_evidence", "failed"
        )
        print(f"vérification {verification['proposal']} : {verification['status']}")
        relance = rapport(
            ligne("reboot-required", "node-1", "ok"),
            ligne("reboot-required", "node-2", "ok"),
            ligne("ntp-drift", "node-1", "finding", severity="low"),
        )
        resume = verifier_reponse(
            http.post(
                f"/projects/{pid}/observations",
                content=relance.encode(),
                headers={"Content-Type": "application/x-ndjson"},
            ),
            200,
        )
        print(f"relance du collecteur : {resume}")
        finale = attendre_la_proposition(http, pid, verification["proposal"], "succeeded", "failed")
        print(f"vérification : {finale['status']} ; fait {finale['evidence'][0].get('fact')}")


if __name__ == "__main__":
    {"preparer": preparer, "verifier": verifier}.get(
        sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__)
    )()
