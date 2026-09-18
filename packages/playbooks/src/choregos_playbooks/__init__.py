"""Playbooks Choregos : un prompt par rôle, rendu en Jinja2, versionné et évalué.

Invariants communs à tous les rôles (docs/plan/02 §2.5) : écrire `.choregos/result.json`,
ne jamais élargir le périmètre sans `request_scope_change`, signaler par `report_finding`,
s'arrêter et `ask_human` plutôt que deviner.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

ROLES_DIR = Path(__file__).parent / "roles"
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


@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader([str(ROLES_DIR), str(ROLES_DIR.parent)]),
        undefined=StrictUndefined,
        autoescape=select_autoescape(enabled_extensions=(), default=False),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def playbook_path(role: str) -> Path:
    path = ROLES_DIR / role / "prompt.md"
    if not path.exists():
        raise FileNotFoundError(f"playbook inconnu : {role} (connus : {', '.join(KNOWN_ROLES)})")
    return path


def playbook_source(role: str) -> str:
    return playbook_path(role).read_text(encoding="utf-8")


def render_playbook(role: str, **variables: Any) -> str:
    """Rend le prompt d'un rôle. Les surcharges projet (`.choregos/playbooks/`) sont fusionnées en amont."""
    playbook_path(role)  # lève si le rôle est inconnu
    template = _env().get_template(f"{role}/prompt.md")
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
    "playbook_path",
    "playbook_source",
    "render_playbook",
]
