# SPDX-License-Identifier: Apache-2.0
"""Le scénario IT4IT de l'essai, joué contre la pile intégrée qui TOURNE (élément 9 ; éléments 2 à 6).

Rien n'est simulé côté plateforme : PostgreSQL sous RLS avec un rôle non superutilisateur, migrations
du cœur et du greffon jouées par `python -m choregos_api.migrer`, API servie par uvicorn, outils
appelés par l'API interne avec un jeton de run signé par la clé partagée de la pile.

Ce qui l'est : l'agent (ce script écrit ses appels), le décor (hôtes, services, run : `decor.py`), le
collecteur (ce script poste ses rapports) et le SCM (le faux du cœur, dans le processus de l'API).

    uv run python essai/demarrage-local/scenario.py     # la pile démarrée par run.sh
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Any

import httpx

ICI = pathlib.Path(__file__).resolve().parent
IT4IT = ICI.parents[1] / "packages" / "ontology" / "tests" / "fixtures" / "it4it"
API = f"http://127.0.0.1:{os.environ.get('ESSAI_PORT_API', '18190')}"
RUN = "run-essai-1"
CLE = "os-reboot-required"


def rapport(*lignes: dict[str, Any], fin: dict[str, Any] | None = None) -> str:
    corps = [json.dumps(ligne) for ligne in lignes]
    corps.append(json.dumps(fin if fin is not None else {"_end": True, "lines": len(lignes)}))
    return "\n".join(corps) + "\n"


def ligne(check: str, scope: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"layer": "os", "check": check, "scope": scope, "status": status, **extra}


SEMAINE_1 = rapport(
    ligne("reboot-required", "node-1", "finding", severity="medium", observed_at="2026-10-03T08:00:00Z"),
    ligne("reboot-required", "node-2", "finding", severity="high", observed_at="2026-10-03T08:00:01Z"),
    ligne("ntp-drift", "node-1", "finding", severity="low", observed_at="2026-10-03T08:00:02Z"),
    ligne("disk-usage", "node-1", "ok", value=41.5, observed_at="2026-10-03T08:00:03Z"),
)


class Scenario:
    def __init__(self, http: httpx.Client) -> None:
        self.http = http
        self.etapes: list[dict[str, Any]] = []

    def etape(self, nom: str, verifier: Any) -> Any:
        debut = time.perf_counter()
        try:
            resultat = verifier()
        except Exception as erreur:
            self.etapes.append({"etape": nom, "ok": False, "erreur": str(erreur)[:500]})
            raise
        self.etapes.append({"etape": nom, "ok": True, "ms": round((time.perf_counter() - debut) * 1000)})
        return resultat

    def connecter(self, email: str, *, reauth: bool = False) -> None:
        debut = self.http.get(
            "/api/v1/auth/login", params={"as": email, **({"reauth": "1"} if reauth else {})}
        )
        assert debut.status_code == 307, debut.text
        retour = self.http.get(debut.headers["location"])
        assert retour.status_code == 307, retour.text

    def poster(self, projet: str, texte: str) -> httpx.Response:
        return self.http.post(
            f"/api/v1/projects/{projet}/observations",
            content=texte.encode(),
            headers={"Content-Type": "application/x-ndjson"},
        )

    def outil(self, jeton: str, nom: str, arguments: dict[str, Any]) -> dict[str, Any]:
        reponse = self.http.post(
            f"/api/v1/internal/runs/{RUN}/tools/{nom}",
            headers={"Authorization": f"Bearer {jeton}"},
            json=arguments,
        )
        assert reponse.status_code == 200, reponse.text
        return dict(reponse.json())


def _compose(*arguments: str, entree: str | None = None) -> str:
    """`docker compose` sur la pile de l'essai ; les arguments sont écrits par ce script, pas reçus."""
    fait = subprocess.run(  # noqa: S603
        ["docker", "compose", "-f", str(ICI / "compose.yaml"), *arguments],  # noqa: S607
        input=entree,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return fait.stdout


def jouer(s: Scenario) -> dict[str, Any]:
    s.etape("connexion de l'administrateur", lambda: s.connecter("admin@varga.dev"))

    def projet() -> str:
        cree = s.http.post(
            "/api/v1/orgs/varga/projects",
            json={"slug": "infra", "name": "Infra", "config": {"slug": "infra", "org": "varga"}},
        )
        assert cree.status_code == 201, cree.text
        ident = str(cree.json()["id"])
        scm = s.http.put(f"/api/v1/projects/{ident}/connectors/scm", json={"type": "fake", "config": {}})
        assert scm.status_code == 200, scm.text
        return ident

    pid = s.etape("projet et connecteur SCM", projet)

    def ontologie() -> None:
        fichiers = {
            p.relative_to(IT4IT).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(IT4IT.rglob("*.yaml"))
        }
        depot = s.http.put(f"/api/v1/projects/{pid}/ontology", json={"files": fichiers})
        assert depot.status_code == 200, depot.text
        assert depot.json()["mcp_tools"] == 13, depot.json()

    s.etape("élément 1 — paquet IT4IT validé, compilé, actif", ontologie)

    def synchroniser() -> None:
        premier = s.poster(pid, SEMAINE_1).json()
        assert (premier["created"], premier["unchanged"]) == (2, 0), premier
        rejeu = s.poster(pid, SEMAINE_1).json()
        assert (rejeu["created"], rejeu["updated"], rejeu["unchanged"]) == (0, 0, 2), rejeu
        tronque = s.poster(
            pid, rapport(ligne("reboot-required", "node-1", "ok"), fin={"_end": True, "lines": 4})
        )
        assert tronque.status_code == 422, tronque.text
        constats = s.http.get(f"/api/v1/projects/{pid}/objects/finding").json()["objects"]
        assert {c["key"]: c["status"] for c in constats} == {CLE: "open", "os-ntp-drift": "open"}, constats

    s.etape("élément 2 — synchronisation idempotente, rapport partiel sans écriture", synchroniser)

    def decor() -> str:
        sortie = _compose(
            "exec",
            "-T",
            "-e",
            f"PROJET={pid}",
            "-e",
            "SLUG=infra",
            "-e",
            f"RUN={RUN}",
            "api",
            "python",
            "-",
            entree=(ICI / "decor.py").read_text(encoding="utf-8"),
        )
        return str(json.loads(sortie.strip().splitlines()[-1])["token"])

    jeton = s.etape("décor : hôtes, services, run et son jeton", decor)

    def lire() -> None:
        outils = s.http.get(
            f"/api/v1/internal/runs/{RUN}/tools", headers={"Authorization": f"Bearer {jeton}"}
        )
        assert outils.status_code == 200, outils.text
        noms = {o["name"] for o in outils.json()["tools"]}
        assert {"finding_search", "host_get", "action_open_infra_pr"} <= noms, noms
        ouverts = s.outil(
            jeton, "finding_search", {"filters": [{"property": "status", "op": "eq", "value": "open"}]}
        )
        assert {o["key"] for o in ouverts["result"]["objects"]} == {CLE, "os-ntp-drift"}, ouverts
        hote = s.outil(jeton, "host_get", {"id": "node-1"})
        assert hote["result"]["object"] == {"id": "node-1", "name": "node-1", "os": "ubuntu"}, hote

    s.etape("élément 3 — l'agent lit par les outils générés, avec son jeton de run", lire)

    def proposer() -> str:
        arguments = {
            "target": [CLE],
            "justification": "Two nodes report reboot-required since the last kernel update.",
            "params": {
                "title": "Reboot the nodes that need it",
                "path": "platform/maintenance/reboot.yaml",
                "content": "nodes: [node-1, node-2]\nstrategy: one-at-a-time\n",
            },
        }
        reponse = s.outil(jeton, "action_open_infra_pr", arguments)
        assert reponse["status_code"] == 201, reponse
        assert reponse["result"]["status"] == "pending_approval", reponse
        return str(reponse["result"]["proposal"])

    proposition = s.etape("élément 4 — proposition `pending_approval`, avec justification", proposer)

    def valider() -> dict[str, Any]:
        s.connecter("admin@varga.dev", reauth=True)
        chemin = f"/api/v1/projects/{pid}/proposals/{proposition}/decision"
        decision = s.http.post(chemin, json={"decision": "approve", "comment": "diff relu"})
        assert decision.status_code == 200, decision.text
        dossier = dict(decision.json())
        assert dossier["status"] == "succeeded", dossier
        assert dossier["decisions"][-1]["auth_time"], dossier
        assert dossier["effects"][0]["head"] == f"choregos/{proposition}", dossier
        return dossier

    dossier = s.etape("élément 5 — validation ré-authentifiée, PR ouverte par la plateforme", valider)

    def prouver() -> dict[str, Any]:
        arguments = {"target": [CLE], "justification": "The PR is merged and applied.", "params": {}}
        verification = s.outil(jeton, "action_verify_finding_fixed", arguments)["result"]
        assert verification["status"] == "awaiting_evidence", verification
        relance = rapport(
            ligne("reboot-required", "node-1", "ok"),
            ligne("reboot-required", "node-2", "ok"),
            ligne("ntp-drift", "node-1", "finding", severity="low"),
        )
        reponse = s.poster(pid, relance).json()
        assert reponse["proposals_decided"] == [verification["proposal"]], reponse
        final = s.http.get(f"/api/v1/projects/{pid}/proposals/{verification['proposal']}").json()
        assert final["status"] == "succeeded", final
        return dict(final)

    preuve = s.etape("élément 6 — la relance du collecteur sans la clé prouve la correction", prouver)

    def rls() -> None:
        requete = (
            "select relname, relrowsecurity, relforcerowsecurity from pg_class where relname in "
            "('ontology_versions', 'managed_objects', 'action_proposals') order by 1;"
            "select rolsuper from pg_roles where rolname = 'choregos_app';"
        )
        sortie = _compose(
            "exec", "-T", "choregos-db", "psql", "-U", "choregos", "-d", "choregos", "-tAc", requete
        )
        lignes = [x for x in sortie.split() if x]
        assert lignes == [
            "action_proposals|t|t",
            "managed_objects|t|t",
            "ontology_versions|t|t",
            "f",
        ], lignes

    s.etape("RLS forcée sur les tables du greffon ; rôle applicatif non superutilisateur", rls)
    return {
        "pr": dossier["effects"][0],
        "decision": dossier["decisions"][-1],
        "fait": preuve["evidence"][0]["fact"],
    }


def main() -> int:
    with httpx.Client(base_url=API, follow_redirects=False, timeout=30) as http:
        scenario = Scenario(http)
        try:
            resume = jouer(scenario)
        except Exception:
            print(json.dumps({"etapes": scenario.etapes}, ensure_ascii=False, indent=2))
            return 1
    print(json.dumps({"etapes": scenario.etapes, **resume}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
