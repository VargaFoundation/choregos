# SPDX-License-Identifier: Apache-2.0
"""Le scénario RH sur le locataire dev (S20-07) : le gabarit `joiners-leavers`, les faux servis par un
seul pod (`demoFakes.enabled`), un vrai agent, de vraies décisions humaines.

Le dev ne saute pas le temps : la date d'arrivée par défaut est HIER — J-10 et J-7 sont passés, J+1
est aujourd'hui —, et `arrivee --dans 30` montre l'attente, que `deplacer` défait en ramenant la
date (le minuteur se réarme). Ce script prépare, dépose et suit ; il ne DÉCIDE rien : une décision
se prend dans la console, par une personne ré-authentifiée — un jeton d'API propose, il ne décide
pas (ADR 0030). La tâche « badge » aussi : on n'atteste pas d'un script une remise en main propre.

    uv run python essai/rh/scenario_rh.py preparer        # connecteurs, politiques, projet
    uv run python essai/rh/scenario_rh.py arrivee         # la demande, par la porte MCP
    uv run python essai/rh/scenario_rh.py suivre RH-1     # ce qu'attend le ticket, et où le décider
    uv run python essai/rh/scenario_rh.py deplacer RH-2 2026-10-04   # une date ramenée réarme
    uv run python essai/rh/scenario_rh.py depart RH-1     # le départ de la même personne, à J0
    uv run python essai/rh/scenario_rh.py verifier RH-1   # chaque action, son journal, la preuve

Variables :
    CHOREGOS_DEV_URL     l'adresse de la console du locataire (ex. `https://choregos.example`)
    CHOREGOS_DEV_TOKEN   jeton d'API d'un administrateur (`chg_…`), créé dans la console ; jamais écrit ici
    CHOREGOS_DEV_ORG     l'organisation du locataire
    ESSAI_FAUX_URL       (défaut http://choregos-demo-fakes:8090) l'adresse des faux, vue depuis le cluster
    ESSAI_JETON_REF      (facultatif) la référence du jeton des faux, ex. `env:CHOREGOS_DEMO_JETON`
    ESSAI_BACKEND        (défaut opencode) le backend des agents du projet, celui que le dev fait tourner
    ESSAI_MODELE         (défaut platform/standard) le modèle de la passerelle derrière le profil `standard`
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import time
from typing import Any

import httpx

#: Exigée par `client()` : aucun locataire n'est supposé — Choregos est un projet ouvert.
URL = os.environ.get("CHOREGOS_DEV_URL", "").rstrip("/")
FAUX = os.environ.get("ESSAI_FAUX_URL", "http://choregos-demo-fakes:8090").rstrip("/")
PROJET = "rh"
TERMINAUX = {"pret", "clos"}
#: Ce que les agents du gabarit demandent (`model: profile:standard`), et le backend qui les fait
#: tourner : un projet neuf reçoit `claude-code` et aucun profil, ce que le dev ne sert pas — le premier
#: passage, le 06/10, a vu le plan de l'agent échouer deux fois sans un appel au modèle.
BACKEND = os.environ.get("ESSAI_BACKEND", "opencode")
MODELE = os.environ.get("ESSAI_MODELE", "platform/standard")


def exiger(nom: str) -> str:
    valeur = os.environ.get(nom, "")
    if not valeur:
        sys.exit(f"variable absente : {nom} (voir l'en-tête de ce script)")
    return valeur


def client() -> httpx.Client:
    return httpx.Client(
        base_url=f"{exiger('CHOREGOS_DEV_URL').rstrip('/')}/api/v1",
        headers={"Authorization": f"Bearer {exiger('CHOREGOS_DEV_TOKEN')}"},
        timeout=60,
    )


def lire(reponse: httpx.Response, *attendus: int) -> Any:
    if reponse.status_code not in attendus:
        sys.exit(
            f"{reponse.request.method} {reponse.request.url.path} → {reponse.status_code} : "
            f"{reponse.text[:400]}"
        )
    return reponse.json() if reponse.content else None


def _references() -> dict[str, str]:
    reference = os.environ.get("ESSAI_JETON_REF", "")
    return {"api_key": reference} if reference else {}


def preparer() -> None:
    """Le geste de l'administrateur : cinq connecteurs, ce que l'organisation permet, le projet."""
    org = exiger("CHOREGOS_DEV_ORG")
    reference = os.environ.get("ESSAI_JETON_REF", "")
    connecteurs = [
        {"name": "annuaire", "kind": "identity", "type": "entra",
         "config": {"tenant_id": "demo", "client_id": "choregos",
                    "graph_url": f"{FAUX}/graph/v1.0", "login_url": f"{FAUX}/login"},
         **({"secret_refs": {"client_secret": reference}} if reference else {})},
        {"name": "parc", "kind": "mdm", "type": "demo", "config": {"url": f"{FAUX}/mdm/mcp"},
         **({"secret_refs": _references()} if reference else {})},
        {"name": "transporteur", "kind": "shipping", "type": "demo",
         "config": {"url": f"{FAUX}/shipping/mcp"},
         **({"secret_refs": _references()} if reference else {})},
        {"name": "lecteurs", "kind": "access_control", "type": "demo",
         "config": {"url": f"{FAUX}/access_control/mcp"},
         **({"secret_refs": _references()} if reference else {})},
        {"name": "fournisseur", "kind": "mcp", "type": "mcp", "config": {"url": f"{FAUX}/fournisseur/mcp"},
         **({"secret_refs": {"token": reference}} if reference else {})},
    ]  # fmt: skip
    with client() as http:
        existants = {c["name"] for c in lire(http.get(f"/orgs/{org}/connectors"), 200)}
        for corps in connecteurs:
            if corps["name"] in existants:
                print(f"connecteur {corps['name']} : déjà là")
                continue
            lire(http.post(f"/orgs/{org}/connectors", json=corps), 201)
            print(f"connecteur {corps['name']} : déclaré")
        lire(http.post(f"/orgs/{org}/connectors/fournisseur/discover"), 200)
        permises = {
            "annuaire": [
                "create_user",
                "add_to_group",
                "remove_from_group",
                "disable_user",
                "revoke_sessions",
            ],
            "parc": ["enroll_device", "wipe_device"],
            "transporteur": ["create_shipment", "create_return"],
            "lecteurs": ["activate_badge", "deactivate_badge"],
            "fournisseur": ["suivi_commande"],
        }
        for connecteur, noms in permises.items():
            for nom in noms:
                lire(
                    http.patch(
                        f"/orgs/{org}/connectors/{connecteur}/operations/{nom}", json={"policy": "allowed"}
                    ),
                    200,
                )
        lire(
            http.patch(
                f"/orgs/{org}/connectors/fournisseur/operations/commander_poste", json={"policy": "approval"}
            ),
            200,
        )
        print("politiques : écritures permises, la commande du poste sous validation")
        projets = {p["slug"] for p in lire(http.get(f"/orgs/{org}/projects"), 200)["items"]}
        if PROJET not in projets:
            corps = {
                "slug": PROJET,
                "name": "RH",
                "template_ref": "joiners-leavers",
                "config": {"slug": PROJET, "org": org},
            }
            projet = lire(http.post(f"/orgs/{org}/projects", json=corps), 201)
            lire(
                http.put(
                    f"/projects/{projet['id']}/connectors/tracker", json={"type": "internal", "config": {}}
                ),
                200,
            )
        # Le backend et le modèle des agents, posés à chaque passage : un projet né avant les garde.
        projet = dict(lire(http.get(f"/projects/{org}:{PROJET}"), 200))
        config = {
            **(projet.get("config") or {}),
            "agent": {"default_backend": BACKEND, "allowed_backends": [BACKEND]},
            "models": {"profiles": {"standard": MODELE}},
        }
        lire(http.patch(f"/projects/{projet['id']}", json={"config": config}), 200)
        print(f"projet {PROJET} : prêt — agents sur {BACKEND}, profil standard → {MODELE} — {URL}/p/{PROJET}")


def _projet(http: httpx.Client) -> dict[str, Any]:
    org = exiger("CHOREGOS_DEV_ORG")
    return dict(lire(http.get(f"/projects/{org}:{PROJET}"), 200))


def arrivee(dans_jours: int = -1) -> None:
    """La demande, déposée comme le ferait le Claude d'une RH : un jeton `mcp:write` le temps de
    l'appel, révoqué ensuite."""
    org = exiger("CHOREGOS_DEV_ORG")
    date = (dt.date.today() + dt.timedelta(days=dans_jours)).isoformat()
    champs = {"nom": "Léa Martin", "upn": f"lea.{int(time.time())}@demo.test", "date_arrivee": date,
              "poste": "Développeuse", "groupe": "devs", "groupe_sensible": "prod-lecture",
              "adresse": "12 rue de la Paix, Paris"}  # fmt: skip
    with client() as http:
        jeton = lire(http.post("/me/tokens", json={"name": "essai-rh", "scopes": ["mcp:write"]}), 201)
        try:
            appel = httpx.post(
                f"{URL}/mcp/projects/{org}:{PROJET}",
                headers={"Authorization": f"Bearer {jeton['token']}"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "create_work_item",
                                 "arguments": {"title": f"Arrivée de {champs['nom']}",
                                               "workflow": "onboarding",
                                               "fields": champs}}},
                timeout=60,
            )  # fmt: skip
        finally:
            lire(http.delete(f"/me/tokens/{jeton['id']}"), 204, 200)
    resultat = lire(appel, 200)["result"]
    if resultat.get("isError"):
        sys.exit(f"la porte MCP refuse : {resultat}")
    print(
        f"ticket {resultat['structuredContent']['key']} — arrivée le {date} — "
        f"{resultat['structuredContent']['console_url']}"
    )


def _ticket(http: httpx.Client, cle: str) -> dict[str, Any]:
    return dict(lire(http.get(f"/work-items/{cle}"), 200))


def suivre(cle: str, minutes: int = 60) -> None:
    """Ce qu'attend le ticket, et OÙ le décider : une personne, ré-authentifiée, dans la console."""
    vu = None
    fin = time.monotonic() + minutes * 60
    with client() as http:
        projet = _projet(http)
        while time.monotonic() < fin:
            ticket = _ticket(http, cle)
            attente = []
            if ticket.get("pending_request"):
                demande = ticket["pending_request"]
                attente.append(f"{demande['kind']} : {URL}/p/{PROJET}/items/{ticket['id']}")
            for action in lire(
                http.get(f"/projects/{projet['id']}/actions", params={"status": "pending_approval"}), 200
            ):
                if action.get("work_item_id") == ticket["id"]:
                    attente.append(
                        f"action « {action['title']} » à décider : {URL}/p/{PROJET}/actions/{action['id']}"
                    )
            etat = (ticket["state"], tuple(attente))
            if etat != vu:
                print(f"[{time.strftime('%H:%M:%S')}] {ticket['state']}", *[f"\n  → {a}" for a in attente])
                vu = etat
            if ticket["state"] in TERMINAUX:
                return
            time.sleep(10)
    sys.exit(f"{cle} n'est pas arrivé au bout en {minutes} min")


def deplacer(cle: str, date: str) -> None:
    """Une date changée réarme le minuteur de l'action qui l'attend."""
    with client() as http:
        ticket = _ticket(http, cle)
        champ = "date_arrivee" if "date_arrivee" in (ticket.get("fields") or {}) else "date_depart"
        lire(http.patch(f"/work-items/{ticket['id']}", json={"fields": {champ: date}}), 200)
    print(f"{cle} : {champ} = {date}")


def depart(cle_arrivee: str) -> None:
    """Le départ de la personne arrivée : son badge et son poste, lus dans le dossier de l'arrivée."""
    with client() as http:
        projet = _projet(http)
        arrivee_ = _ticket(http, cle_arrivee)
        champs = arrivee_["fields"]
        actions = lire(http.get(f"/projects/{projet['id']}/actions"), 200)
        poste = next(
            a for a in actions if a["work_item_id"] == arrivee_["id"] and a["kind"] == "arrivee.poste"
        )
        serie = poste["journal"][0]["result"]["serial"] if poste.get("journal") else None
        if serie is None:
            serie = lire(http.get(f"/projects/{projet['id']}/actions/{poste['id']}"), 200)["journal"][0][
                "result"
            ]["serial"]
        corps = {"title": f"Départ de {champs['nom']}", "labels": ["depart"],
                 "fields": {"nom": champs["nom"], "upn": champs["upn"],
                            "date_depart": dt.date.today().isoformat(), "badge_uid": champs["badge_uid"],
                            "serial": serie, "adresse": champs["adresse"]}}  # fmt: skip
        ticket = lire(http.post(f"/projects/{projet['id']}/work-items", json=corps), 201)
    print(f"ticket {ticket['tracker_key']} — départ aujourd'hui — {URL}/p/{PROJET}/items/{ticket['id']}")


def verifier(cle: str) -> None:
    """Le dossier de preuves d'une personne : chaque action, ses décisions, son journal, et ce qui a
    été attesté."""
    with client() as http:
        projet = _projet(http)
        ticket = _ticket(http, cle)
        actions = [
            a
            for a in lire(http.get(f"/projects/{projet['id']}/actions"), 200)
            if a["work_item_id"] == ticket["id"]
        ]
        echecs = 0
        for resume in actions:
            action = lire(http.get(f"/projects/{projet['id']}/actions/{resume['id']}"), 200)
            decideurs = ", ".join(d["by"] for d in action["decisions"]) or "—"
            print(f"{action['status']:>16}  {action['kind']}  (décidée par {decideurs})")
            for effet in action["journal"]:
                print(f"{'':>18}{effet['key']}  {effet['effect']}  {effet['status']}")
            echecs += action["status"] != "succeeded"
        chronologie = lire(http.get(f"/work-items/{ticket['id']}/timeline"), 200)
        for entree in chronologie:
            if "attesté" in (entree.get("detail") or ""):
                print(f"preuve : {entree['detail']} — {entree.get('actor')}")
    print(f"{cle} : {ticket['state']}, {len(actions)} actions, {echecs} sans succès")
    sys.exit(1 if echecs or ticket["state"] not in TERMINAUX else 0)


if __name__ == "__main__":
    commande, *arguments = sys.argv[1:] or ["aide"]
    if commande == "preparer":
        preparer()
    elif commande == "arrivee":
        arrivee(int(arguments[1]) if arguments[:1] == ["--dans"] else -1)
    elif commande == "suivre":
        suivre(arguments[0])
    elif commande == "deplacer":
        deplacer(arguments[0], arguments[1])
    elif commande == "depart":
        depart(arguments[0])
    elif commande == "verifier":
        verifier(arguments[0])
    else:
        print(__doc__)
