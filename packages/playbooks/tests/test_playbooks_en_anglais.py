"""Ce que l'agent lit est en anglais (ADR 0039, S21-14).

Un playbook écrit en français fait écrire l'agent en français : sa spécification, son plan, sa revue,
le texte de sa PR et ses findings — et c'est cela que les utilisateurs lisent. Cette garde rend chaque
rôle avec des variables représentatives — ceux du paquet et ceux que la démonstration apporte —, puis
lit les invariants, le contrat de sortie, le cadre d'un agent du registre, le `task.md` et le
`context.md` que le runner écrit, et les descriptions des outils MCP ; un balayage AST couvre enfin
tout ce que le runner et le serveur d'outils disent (messages à l'agent, raisons des garde-fous,
résumés d'un résultat), hors docstrings.

Le détecteur est celui de `tests/langue/test_anglais.py`, chargé par son chemin : sous
`--import-mode=importlib`, un module de test n'en importe pas un autre.
"""

from __future__ import annotations

import importlib.util
import pathlib
from collections.abc import Callable, Iterator
from types import ModuleType
from typing import Any

import pytest
from choregos_contracts import (
    AgentRef,
    Budget,
    Callbacks,
    ContextPack,
    MemoryItem,
    ModelRef,
    Permissions,
    PlaybookRef,
    ProjectRef,
    RelatedItem,
    RepoRef,
    StageInput,
    TransitionRef,
    WorkItemRef,
)
from choregos_playbooks import INVARIANTS, KNOWN_ROLES, OUTPUT_CONTRACT, cadrer, render_playbook

RACINE = pathlib.Path(__file__).resolve().parents[3]


def _module_de_la_garde() -> ModuleType:
    spec = importlib.util.spec_from_file_location("anglais", RACINE / "tests/langue/test_anglais.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GARDE = _module_de_la_garde()
ressemble_a_du_francais: Callable[[str], bool] = GARDE.ressemble_a_du_francais

#: Un contexte complet et anglais : ce que le playbook ajoute, pas ce que l'utilisateur écrit.
CONTEXTE = ContextPack(
    query="credit notes",
    memories=[MemoryItem(kind="decision", subject="decision:billing:rounding", content="Round on issue.")],
    incidents=[MemoryItem(kind="incident", subject="incident:billing:2026-09", content="Totals drifted.")],
    related_items=[RelatedItem(key="acme/billing#99", title="Totals ignore discounts", state="done")],
)
VARIABLES: dict[str, Any] = {
    "ticket": {
        "key": "acme/billing#123",
        "title": "Credit notes are not deducted from the total",
        "body": "When an order has a credit note, the displayed total ignores it.",
    },
    "spec": "## Specification\nGiven an order with a credit note…",
    "plan_markdown": "1. Add the computation\n2. Cover it with a test",
    "allowed_paths": ["src/orders/**", "tests/orders/**"],
    "context": CONTEXTE,
}


def _lignes_en_francais(texte: str) -> list[str]:
    """Ligne par ligne, sans rien retirer : un exemple JSON ou un extrait de code est lu lui aussi."""
    return [ligne.strip()[:100] for ligne in texte.splitlines() if ressemble_a_du_francais(ligne)]


def _stage_input() -> StageInput:
    return StageInput(
        run_id="run-1",
        attempt=1,
        project=ProjectRef(slug="billing", org="acme"),
        work_item=WorkItemRef(
            key="acme/billing#123",
            title="Credit notes are not deducted from the total",
            body="When an order has a credit note, the displayed total ignores it.",
        ),
        transition=TransitionRef(id="t-implement", role="implement", **{"from": "ready"}, to="implemented"),
        repo=RepoRef(url="https://example.com/acme/billing.git", base_branch="main", work_branch="w/123"),
        agent=AgentRef(backend="claude-code"),
        model=ModelRef(
            litellm_model="platform/standard", base_url="http://gateway:4000", api_format="openai"
        ),
        budget=Budget(usd=5, max_turns=20, max_minutes=30),
        allowed_paths=["src/orders/**", "tests/orders/**"],
        playbook=PlaybookRef(ref="implement@1"),
        permissions=Permissions(write_paths=["src/orders/**", "tests/orders/**"]),
        callbacks=Callbacks(api_url="http://localhost:0/internal", run_token="tok"),
    )


@pytest.mark.parametrize("role", KNOWN_ROLES)
def test_chaque_playbook_rendu_est_en_anglais(role: str) -> None:
    rendu = render_playbook(role, **VARIABLES)
    assert "Round on issue." in rendu and "Totals ignore discounts" in rendu, "la mémoire est rendue"
    fautes = _lignes_en_francais(rendu)
    assert not fautes, f"du français dans le playbook `{role}` :\n  " + "\n  ".join(fautes)


@pytest.mark.parametrize("role", ["sourcing", "qualification"])
def test_les_playbooks_de_la_demonstration_sont_en_anglais(
    role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Apportés par le déploiement (`demo/playbooks`, montés par `CHOREGOS_PLAYBOOKS_DIR`) : rendus
    par le même chemin qu'en production. Le chargeur Jinja garde ses répertoires en cache."""
    from choregos_playbooks import _env

    monkeypatch.setenv("CHOREGOS_PLAYBOOKS_DIR", str(RACINE / "demo/playbooks"))
    _env.cache_clear()
    try:
        rendu = render_playbook(role, **VARIABLES, inputs={"profils": "- Alice, senior Python developer"})
    finally:
        monkeypatch.delenv("CHOREGOS_PLAYBOOKS_DIR")
        _env.cache_clear()
    assert "acme/billing#123" in rendu and "## Invariants" in rendu
    fautes = _lignes_en_francais(rendu)
    assert not fautes, f"du français dans le playbook de démonstration `{role}` :\n  " + "\n  ".join(fautes)


@pytest.mark.parametrize(
    ("nom", "texte"),
    [
        pytest.param("INVARIANTS", INVARIANTS, id="invariants"),
        pytest.param("OUTPUT_CONTRACT", OUTPUT_CONTRACT, id="contrat-de-sortie"),
        pytest.param("cadrer", cadrer("Do the work described above."), id="cadre-d-un-agent-du-registre"),
    ],
)
def test_les_invariants_le_contrat_et_le_cadre_sont_en_anglais(nom: str, texte: str) -> None:
    fautes = _lignes_en_francais(texte)
    assert not fautes, f"du français dans {nom} :\n  " + "\n  ".join(fautes)


LANGUE_DE_SORTIE = "Write in English everything a person will read"


@pytest.mark.parametrize("role", sorted(KNOWN_ROLES))
def test_chaque_role_demande_d_ecrire_en_anglais(role: str) -> None:
    # Un agent écrit dans la langue de ce qu'il lit : un ticket aux champs français (« Léa Martin »,
    # « 12 rue de la Paix ») faisait écrire un plan en français à un agent tout en anglais (08/10).
    assert LANGUE_DE_SORTIE in render_playbook(role, **VARIABLES)


def test_un_agent_du_registre_recoit_la_consigne_de_langue_dans_son_cadre() -> None:
    assert LANGUE_DE_SORTIE in cadrer("Prepare the onboarding plan.")


def test_le_task_md_et_le_context_md_du_runner_sont_en_anglais() -> None:
    from choregos_runner.workspace import _context_markdown, _task_markdown

    entree = _stage_input()
    for nom, texte in (
        ("task.md", _task_markdown(entree)),
        ("context.md", _context_markdown(CONTEXTE)),
        ("context.md (vide)", _context_markdown(None)),
    ):
        assert "Credit notes" in texte or "Round on issue." in texte or "No memory" in texte, nom
        fautes = _lignes_en_francais(texte)
        assert not fautes, f"du français dans {nom} :\n  " + "\n  ".join(fautes)


def _descriptions(valeur: Any, chemin: str) -> Iterator[tuple[str, str]]:
    if isinstance(valeur, dict):
        for cle, sous_valeur in valeur.items():
            if cle == "description" and isinstance(sous_valeur, str):
                yield chemin, sous_valeur
            else:
                yield from _descriptions(sous_valeur, f"{chemin}.{cle}")
    elif isinstance(valeur, list):
        for element in valeur:
            yield from _descriptions(element, chemin)


def test_les_descriptions_des_outils_mcp_sont_en_anglais() -> None:
    from choregos_tools_mcp.tools import TOOL_SCHEMAS

    descriptions = [d for outil in TOOL_SCHEMAS for d in _descriptions(outil, str(outil["name"]))]
    assert len(descriptions) >= len(TOOL_SCHEMAS), "chaque outil se décrit"
    fautes = [f"{chemin}: {texte[:90]!r}" for chemin, texte in descriptions if ressemble_a_du_francais(texte)]
    assert not fautes, "du français dans les outils de l'agent :\n  " + "\n  ".join(fautes)


@pytest.mark.parametrize(
    "racine",
    ["packages/runner/src/choregos_runner", "packages/tools-mcp/src/choregos_tools_mcp"],
    ids=["runner", "outils-mcp"],
)
def test_ce_que_disent_le_runner_et_les_outils_est_en_anglais(racine: str) -> None:
    """Messages à l'agent, raisons des garde-fous (lues aussi dans la fiche d'accès), résumés d'un
    résultat, erreurs qu'une console montre : toutes les chaînes hors docstrings."""
    fautes = GARDE._francais_d_un_paquet(racine)
    assert not fautes, "du français dans ce que le paquet dit :\n  " + "\n  ".join(fautes)
