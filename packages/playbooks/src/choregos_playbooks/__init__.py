"""Playbooks Choregos : un prompt par rôle, rendu en Jinja2, versionné et évalué.

Invariants communs à tous les rôles (docs/plan/02 §2.5) : écrire `.choregos/result.json`,
ne jamais élargir le périmètre sans `request_scope_change`, signaler par `report_finding`,
s'arrêter et `ask_human` plutôt que deviner.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

ROLES_DIR = Path(__file__).parent / "roles"


# Playbooks apportés par le DÉPLOIEMENT, en plus de ceux du paquet : répertoires séparés par
# `:` dans `CHOREGOS_PLAYBOOKS_DIR`. C'est ce qui rend le moteur utilisable hors logiciel —
# un rôle `sourcing` ou `instruction_dossier` n'a rien à faire dans ce paquet, mais le reste
# de la machine (états, transitions, budgets, garanties, mémoire) ne change pas d'un domaine
# à l'autre. Deux formes acceptées, parce qu'un ConfigMap monte mal une arborescence :
#   <dir>/<role>.md          (fichier plat, une clé de ConfigMap)
#   <dir>/<role>/prompt.md   (même forme que les rôles du paquet)
# Un rôle du déploiement l'emporte sur celui du paquet : c'est ainsi qu'on adapte `implement`
# à une maison sans réécrire la plateforme.
def extra_roles_dirs() -> list[Path]:
    raw = os.environ.get("CHOREGOS_PLAYBOOKS_DIR", "")
    return [Path(p) for p in raw.split(":") if p.strip()]


KNOWN_ROLES = (
    "triage",
    "refine",
    "plan",
    "implement",
    "verify",
    "review",
    "fix_ci",
    "address_review",
    "release_notes",
    "verify_prod",
)

__version__ = "0.1.0"


def _search_dirs() -> list[str]:
    # Les répertoires du déploiement D'ABORD : le premier trouvé gagne.
    return [str(d) for d in extra_roles_dirs()] + [str(ROLES_DIR), str(ROLES_DIR.parent)]


@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(_search_dirs()),
        undefined=StrictUndefined,
        autoescape=select_autoescape(enabled_extensions=(), default=False),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def playbook_path(role: str) -> Path:
    for directory in extra_roles_dirs():
        for candidate in (directory / f"{role}.md", directory / role / "prompt.md"):
            if candidate.exists():
                return candidate
    path = ROLES_DIR / role / "prompt.md"
    if not path.exists():
        extra = ", ".join(str(d) for d in extra_roles_dirs()) or "aucun"
        raise FileNotFoundError(
            f"playbook inconnu : {role} (rôles du paquet : {', '.join(KNOWN_ROLES)} ; "
            f"répertoires du déploiement : {extra})"
        )
    return path


def playbook_source(role: str) -> str:
    return playbook_path(role).read_text(encoding="utf-8")


def render_playbook(role: str, **variables: Any) -> str:
    """Rend le prompt d'un rôle, du déploiement s'il en fournit un, du paquet sinon."""
    path = playbook_path(role)  # lève si le rôle est inconnu
    template = _env().get_template(_template_name(path, role))
    defaults: dict[str, Any] = {
        "ticket": {},
        "spec": "",
        "plan_markdown": "",
        "allowed_paths": [],
        "context": None,
        "project": None,
        "output_contract": OUTPUT_CONTRACT,
        "invariants": INVARIANTS,
    }
    return template.render({**defaults, **variables})


def _template_name(path: Path, role: str) -> str:
    """Le nom que le chargeur Jinja attend, relatif à l'un des répertoires de recherche."""
    for directory in _search_dirs():
        try:
            return str(path.relative_to(directory))
        except ValueError:
            continue
    return f"{role}/prompt.md"


INVARIANTS = """- Écris `.choregos/result.json` conforme au contrat avant de terminer.
- N'élargis jamais le périmètre toi-même : appelle `request_scope_change(paths, justification)`.
- Un problème hors périmètre se signale avec `report_finding(...)`, il ne se corrige pas.
- Préfère `ask_human(question)` à une hypothèse qui engage le produit.
- Commits conventionnels (`fix(orders): …`), un commit par intention.
- Ne touche pas aux fichiers de configuration Choregos (`.choregos/**`).
- Consulte `search_memory(query)` avant toute décision d'architecture."""

OUTPUT_CONTRACT = """Écris `.choregos/result.json` :

```json
{
  "schema": "choregos/StageResult/v1",
  "status": "done | blocked | needs_human | failed",
  "summary": "une phrase qui dit ce qui a été fait",
  "outputs": { },
  "evidence": { "tests_passed": true, "tests_run": 0 },
  "findings": [],
  "scope_changes_requested": [],
  "questions": []
}
```"""

__all__ = [
    "INVARIANTS",
    "KNOWN_ROLES",
    "OUTPUT_CONTRACT",
    "extra_roles_dirs",
    "playbook_path",
    "playbook_source",
    "render_playbook",
]
