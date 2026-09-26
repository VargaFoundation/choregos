"""Quelles clés de secret sont exigées au démarrage, et lesquelles ne doivent pas l'être ?

Le 2026-09-26, le locataire dev a démarré tout son plan de contrôle sauf l'API : elle restait
en `CreateContainerConfigError` sur « couldn't find key generic-webhook-secret in Secret
choregos/choregos-api-secrets ». Le chart réclamait en dur une clé que le modèle
`InfisicalSecret` du locataire ne mappait pas — une fonctionnalité non utilisée (le webhook
générique) empêchait donc l'API d'exister.

La règle qui en sort, et que ce test tient :

  * une clé **structurelle** (secret de session, clé privée des jetons de run) reste exigée :
    démarrer sans elle serait pire que ne pas démarrer — cookies signés à vide, ou deux
    processus qui repartent chacun sur une paire éphémère ;
  * une clé **de fonctionnalité** (webhooks, App GitHub, clé publique déductible) est
    `optional: true` : absente, la valeur est vide, et le code refuse déjà à vide
    (`verify_shared_secret` rend False ; le webhook GitHub refuse en staging/prod).

Le test lit le rendu, pas le modèle : c'est le manifeste qui décide si un pod démarre.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

RACINE = pathlib.Path(__file__).resolve().parents[2]
CHART = RACINE / "charts" / "choregos"

#: Sans elles, l'API ne doit PAS démarrer.
STRUCTURELLES = {"session-secret", "run-token-private-key"}
#: Sans elles, une fonctionnalité est éteinte — ce n'est pas un déploiement cassé.
DE_FONCTIONNALITE = {
    "run-token-public-key",
    "github-webhook-secret",
    "generic-webhook-secret",
    "github-app-id",
    "github-app-private-key",
}

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


#: Le secret propre à l'API (`global.apiSecretRef`) — les autres références pointent des
#: secrets tenus par d'autres (base, OIDC, passerelle), hors de ce classement.
SECRET_DE_L_API = "choregos-api-secrets"


def _refs_de_secret(env: str) -> dict[str, bool]:
    """{clé: optionnelle} pour les `secretKeyRef` de l'API qui visent SON secret."""
    rendu = subprocess.run(
        ["helm", "template", "choregos", str(CHART), "-f", str(CHART / "values" / f"{env}.yaml")],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    trouves: dict[str, bool] = {}
    for doc in yaml.safe_load_all(rendu.stdout):
        if not doc or doc.get("kind") != "Deployment" or doc["metadata"]["name"] != "choregos-api":
            continue
        spec: dict[str, Any] = doc["spec"]["template"]["spec"]
        for conteneur in spec.get("containers", []) + spec.get("initContainers", []):
            for variable in conteneur.get("env", []) or []:
                ref = (variable.get("valueFrom") or {}).get("secretKeyRef")
                if ref and ref.get("name") == SECRET_DE_L_API:
                    trouves[ref["key"]] = bool(ref.get("optional"))
    return trouves


@pytest.mark.parametrize("env", ("local", "dev", "staging", "prod"))
def test_les_cles_structurelles_restent_exigees(env: str) -> None:
    refs = _refs_de_secret(env)
    if not refs:
        pytest.skip(f"{env} ne rend pas le déploiement de l'API")
    for cle in STRUCTURELLES:
        assert cle in refs, f"{env} : {cle} n'est plus lue du tout"
        assert refs[cle] is False, (
            f"{env} : {cle} est devenue optionnelle. Une API qui démarre sans elle signe à "
            "vide ou émet des jetons qu'elle ne saura pas vérifier."
        )


@pytest.mark.parametrize("env", ("local", "dev", "staging", "prod"))
def test_les_cles_de_fonctionnalite_sont_optionnelles(env: str) -> None:
    """Un locataire sans App GitHub ni webhook générique doit voir son API démarrer."""
    refs = _refs_de_secret(env)
    if not refs:
        pytest.skip(f"{env} ne rend pas le déploiement de l'API")
    exigees = sorted(cle for cle in DE_FONCTIONNALITE if refs.get(cle) is False)
    assert not exigees, (
        f"{env} : {exigees} sont exigées en dur. Leur absence empêcherait l'API d'exister "
        "alors que le code les traite comme des fonctionnalités éteintes."
    )


def test_aucune_cle_inconnue_ne_s_est_glissee_dans_le_secret_de_l_api() -> None:
    """Une clé ajoutée sans choisir son camp casserait un locataire au prochain déploiement :
    ce test force la décision ici, pas dans un namespace."""
    refs = _refs_de_secret("prod")
    inconnues = set(refs) - STRUCTURELLES - DE_FONCTIONNALITE
    assert not inconnues, (
        f"clés non classées : {sorted(inconnues)}. Structurelle (exigée) ou de fonctionnalité "
        "(optionnelle) ? Trancher ici, et l'écrire dans ce test."
    )
