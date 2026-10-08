# SPDX-License-Identifier: Apache-2.0
"""Le projet de développement sur le locataire dev (S21-25) : le gabarit `github-software-delivery`,
un vrai dépôt bac à sable, de vrais agents, de vraies décisions.

Trois tickets, un par workflow : un bogue (`dev-simple`, jusqu'à la production vérifiée sans
humain), une étude (`study`, un ADR MADR 4 fusionné après la décision des architectes), une
fonctionnalité (`dev-complex`, trois décisions humaines et le capitaine du train). Ce script prépare,
ouvre et suit ; il ne DÉCIDE rien : une décision se prend dans la console, par une personne
ré-authentifiée (ADR 0030).

    uv run python essai/projet-dev/scenario_dev.py preparer     # projet, connecteurs, réglages
    uv run python essai/projet-dev/scenario_dev.py exigences    # ce que le projet doit brancher
    uv run python essai/projet-dev/scenario_dev.py ouvrir bug   # une issue étiquetée, dans le dépôt
    uv run python essai/projet-dev/scenario_dev.py suivre <id>  # l'état, et où décider
    uv run python essai/projet-dev/scenario_dev.py verifier <id>  # les runs, les décisions, la PR

Variables :
    CHOREGOS_DEV_URL       l'adresse de la console du locataire
    CHOREGOS_DEV_TOKEN     jeton d'API d'un administrateur (`chg_…`) ; jamais écrit ici
    CHOREGOS_DEV_ORG       l'organisation du locataire
    ESSAI_DEPOT            (défaut DiametralGroup/choregos-sandbox-dev) le dépôt bac à sable
    ESSAI_INSTALLATION     l'identifiant d'installation de l'App GitHub de Choregos sur ce dépôt
    ESSAI_FAUX_URL         (défaut http://choregos-demo-fakes:8090) les faux, vus depuis le cluster
    ESSAI_JETON_REF        (facultatif) la référence du jeton des faux, ex. `env:CHOREGOS_DEMO_JETON`
    ESSAI_BACKEND          (défaut opencode) le backend que le dev fait tourner
    ESSAI_MODELES          (défaut standard=platform/standard,cheap=platform/cheap) les profils servis
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any

import httpx

URL = os.environ.get("CHOREGOS_DEV_URL", "").rstrip("/")
DEPOT = os.environ.get("ESSAI_DEPOT", "DiametralGroup/choregos-sandbox-dev")
FAUX = os.environ.get("ESSAI_FAUX_URL", "http://choregos-demo-fakes:8090").rstrip("/")
BACKEND = os.environ.get("ESSAI_BACKEND", "opencode")
MODELES = dict(
    paire.split("=", 1)
    for paire in os.environ.get("ESSAI_MODELES", "standard=platform/standard,cheap=platform/cheap").split(",")
)
PROJET = "dev"
GABARIT = "github-software-delivery@1.0.0"
#: Une issue par workflow : ses étiquettes (le routage du gabarit) et ce qu'elle demande.
ISSUES = {
    "bug": (
        ["agent-ready", "bug"],
        "Credits are not deducted from the invoice total",
        "`invoices.total.compute_total` ignores the credits passed to it: an invoice of 100 with a credit "
        "of 20 shows 100. Expected: 80. Add a test.",
    ),
    "adr": (
        ["agent-ready", "adr"],
        "Decide how invoices are sent: a synchronous call or a queue",
        "Sending an invoice calls the mail provider synchronously. Decide whether to keep it or move to a "
        "queue, with the trade-offs, as an ADR.",
    ),
    "feature": (
        ["agent-ready", "feature"],
        "Totals in several currencies",
        "An invoice can hold lines in EUR and USD. Compute one total per currency, and keep the "
        "single-currency behaviour unchanged.",
    ),
}
TERMINAUX = {"verified", "adopted", "abandoned"}


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


def _projet(http: httpx.Client) -> dict[str, Any]:
    return dict(lire(http.get(f"/projects/{exiger('CHOREGOS_DEV_ORG')}:{PROJET}"), 200))


def preparer() -> None:
    """Le geste de l'administrateur : le projet né du gabarit, ses connecteurs, ses réglages."""
    org = exiger("CHOREGOS_DEV_ORG")
    installation = int(exiger("ESSAI_INSTALLATION"))
    with client() as http:
        projets = {p["slug"] for p in lire(http.get(f"/orgs/{org}/projects"), 200)["items"]}
        if PROJET not in projets:
            corps = {
                "slug": PROJET,
                "name": "Development",
                "template_ref": GABARIT,
                "config": {"slug": PROJET, "org": org, "repo": {"url": f"https://github.com/{DEPOT}.git"}},
            }
            lire(http.post(f"/orgs/{org}/projects", json=corps), 201)
        projet = _projet(http)
        pid = projet["id"]
        reference = os.environ.get("ESSAI_JETON_REF", "")
        connecteurs = {
            "tracker": {"type": "github-issues", "config": {"repo": DEPOT, "installation_id": installation}},
            # Pas de file de fusion sur le bac à sable : la plateforme fusionne directement.
            "scm": {
                "type": "github",
                "config": {"repo": DEPOT, "installation_id": installation, "merge_queue": False},
            },
            "cd": {
                "type": "demo",
                "config": {"url": f"{FAUX}/cd/mcp"},
                **({"secret_refs": {"api_key": reference}} if reference else {}),
            },
        }
        for sorte, corps in connecteurs.items():
            lire(http.put(f"/projects/{pid}/connectors/{sorte}", json=corps), 200)
        # Le backend et les profils que le dev sert : un projet neuf reçoit `claude-code` et aucun
        # profil (premier passage RH du 06/10). Le fait `smoke_ok` que vérifie l'agent de production.
        config = {
            **(projet.get("config") or {}),
            "agent": {"default_backend": BACKEND, "allowed_backends": [BACKEND]},
            "models": {"profiles": MODELES},
            "dod": {"facts": {"smoke_ok": "python -m invoices.smoke"}},
        }
        lire(http.patch(f"/projects/{pid}", json={"config": config}), 200)
        print(f"projet {PROJET} : {GABARIT} — {DEPOT} — agents sur {BACKEND} — {URL}/p/{PROJET}")
    exigences()


def exigences() -> None:
    """Ce que les trois workflows exigent, et si c'est branché. Le code de sortie le dit."""
    with client() as http:
        lignes = lire(http.get(f"/projects/{_projet(http)['id']}/requirements"), 200)
    manque = False
    for ligne in lignes:
        branche = ligne.get("connector") or {}
        etat = f"{branche.get('type')}" if branche else "MANQUE"
        manque = manque or not branche
        print(f"{ligne['capability']:10s} {etat:14s} {(ligne.get('reasons') or [''])[0]}")
    if manque:
        sys.exit(1)


def ouvrir(sorte: str) -> None:
    """Une issue dans le bac à sable, étiquetée comme le routage l'attend : le webhook la fait naître."""
    etiquettes, titre, corps = ISSUES[sorte]
    # `gh`, déjà authentifié sur le poste : des arguments fixes, aucun shell.
    commande = ["gh", "issue", "create", "--repo", DEPOT, "--title", titre, "--body", corps]
    commande += [arg for etiquette in etiquettes for arg in ("--label", etiquette)]
    sortie = subprocess.run(commande, check=True, capture_output=True, text=True)  # noqa: S603
    print(f"{sorte} : {sortie.stdout.strip()} — le ticket naît dans Choregos au webhook")


def suivre(ident: str, minutes: int = 120) -> None:
    """L'état du ticket, et OÙ le décider quand il attend une personne."""
    vu = None
    fin = time.monotonic() + minutes * 60
    with client() as http:
        while time.monotonic() < fin:
            ticket = dict(lire(http.get(f"/work-items/{ident}"), 200))
            demande = ticket.get("pending_request")
            ligne = f"{ticket['workflow_name']} · {ticket['state']} ({ticket.get('state_display')})"
            if demande:
                ligne += f" — à décider : {URL}/p/{PROJET}/items/{ticket['id']}"
            if ticket.get("failure"):
                ligne += f" — MORT : {ticket['failure']}"
            if ligne != vu:
                print(time.strftime("%H:%M:%S"), ligne, flush=True)
                vu = ligne
            if ticket["state"] in TERMINAUX or ticket.get("failure"):
                return
            time.sleep(20)


def verifier(ident: str) -> None:
    """Le dossier du ticket : chaque run (son agent, sa transition, son coût), chaque décision, la PR."""
    with client() as http:
        ticket = dict(lire(http.get(f"/work-items/{ident}"), 200))
        runs = lire(http.get(f"/work-items/{ident}/runs"), 200)
        decisions = lire(http.get(f"/work-items/{ident}/decisions"), 200)
    print(f"{ticket['tracker_key']} — {ticket['workflow_name']} — {ticket['state']}")
    print(f"  PR : {ticket.get('pr_url')}")
    for run in runs:
        agent, statut = run.get("agent_slug"), run.get("status")
        print(f"  run {run.get('transition_id')} : {agent} {statut} {run.get('cost_usd')} $")
    for decision in decisions or []:
        print(f"  décision {decision}")
    print(f"  coût total : {ticket['totals'].get('cost_usd')} $")
    if ticket["state"] not in TERMINAUX:
        sys.exit(1)


if __name__ == "__main__":
    commande, *arguments = sys.argv[1:] or ["aide"]
    commandes = {"preparer": preparer, "exigences": exigences, "ouvrir": ouvrir, "suivre": suivre,
                 "verifier": verifier}  # fmt: skip
    if commande not in commandes:
        sys.exit(__doc__)
    commandes[commande](*arguments)  # type: ignore[operator]
