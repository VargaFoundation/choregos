# SPDX-License-Identifier: Apache-2.0
"""Les instructions d'un agent du registre, rendues dans un bac à sable (ADR 0033).

Un playbook est un fichier du déploiement, relu en PR ; les instructions d'un agent s'écrivent dans
la console, par un administrateur d'organisation. Elles sont donc rendues par Jinja **isolé**
(`SandboxedEnvironment`) : un gabarit ne remonte pas aux classes Python — `{{ ''.__class__ }}` est
refusé —, n'appelle pas de méthode dangereuse, ne lit que les variables qu'on lui donne.

Les mêmes variables qu'un playbook : `ticket`, `spec`, `plan_markdown`, `inputs`, `allowed_paths`,
`context`, `project`. Le cadre que l'agent ne peut pas modifier — le contrat de sortie, les
invariants — est ajouté APRÈS le rendu, par l'orchestrateur.
"""

from __future__ import annotations

from typing import Any

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

#: De quoi rendre un gabarit pour le vérifier, à la publication d'une version.
VARIABLES_D_EXEMPLE: dict[str, Any] = {
    "ticket": {"key": "DEMO-1", "title": "Arrivée de Camille", "body": "Arrive le 2 novembre."},
    "spec": "",
    "plan_markdown": "",
    "inputs": {},
    "allowed_paths": [],
    "context": None,
    "project": None,
}

_ENVIRONNEMENT = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)


def rendre_les_instructions(source: str, **variables: Any) -> str:
    """Le gabarit rendu dans le bac à sable ; lève `TemplateError` (dont `SecurityError`)."""
    return _ENVIRONNEMENT.from_string(source).render({**VARIABLES_D_EXEMPLE, **variables})


def erreurs_des_instructions(source: str) -> list[str]:
    """Ce qui empêcherait le gabarit de rendre — vérifié quand une version est publiée."""
    try:
        rendre_les_instructions(source)
    except TemplateError as erreur:
        return [f"{type(erreur).__name__} : {erreur}"]
    return []


__all__ = ["VARIABLES_D_EXEMPLE", "erreurs_des_instructions", "rendre_les_instructions"]
