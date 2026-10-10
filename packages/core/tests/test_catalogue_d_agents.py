"""Le catalogue d'agents livré avec la plateforme (ADR 0040, S21-16)."""

from __future__ import annotations

import pytest
import yaml
from choregos_core.catalogue_d_agents import entree, entrees, fichiers_de_la_skill, skills_du_catalogue
from choregos_core.dsl import TEMPLATES_DIR
from choregos_core.instructions import rendre_les_instructions

INTERNES = [e for e in entrees() if e.genre == "internal"]


def _entrees_des_workflows() -> set[str]:
    """Toutes les entrées que les workflows livrés déclarent : un agent doit rendre avec chacune."""
    noms: set[str] = set()
    for chemin in TEMPLATES_DIR.glob("*.yaml"):
        for transition in yaml.safe_load(chemin.read_text(encoding="utf-8"))["transitions"]:
            noms.update(transition.get("inputs") or [])
    return noms | {"human_feedback", "test_report", "review_markdown", "security_review_markdown",
                   "research_markdown", "adr_path", "spec_markdown"}  # fmt: skip


@pytest.mark.parametrize("agent", INTERNES, ids=lambda e: e.slug)
def test_chaque_agent_du_catalogue_se_rend_sans_erreur(agent) -> None:  # type: ignore[no-untyped-def]
    """Avec les variables d'exemple, sans entrée, puis avec toutes : `StrictUndefined` refuse un nom
    que le gabarit lirait sans `.get` — l'agent mourrait au premier run."""
    instructions = agent.document["spec"]["instructions"]
    vide = rendre_les_instructions(instructions)
    plein = rendre_les_instructions(instructions, inputs=dict.fromkeys(_entrees_des_workflows(), "x"))
    assert "## Work item" in vide and "DEMO-1" in vide
    assert len(plein) >= len(vide)


def test_le_contexte_du_ticket_precede_les_instructions_d_un_agent_interne() -> None:
    rendu = rendre_les_instructions(
        entree("developer").document["spec"]["instructions"],  # type: ignore[union-attr]
        ticket={"key": "SBX-12", "title": "Fix rounding", "body": "Totals are off by one cent."},
        spec="Given an invoice",
        allowed_paths=["src/orders/**"],
        inputs={"test_report": "2 tests fail", "human_feedback": ""},
    )
    assert rendu.index("**SBX-12** — Fix rounding") < rendu.index("You implement the work item")
    assert "## Approved specification\nGiven an invoice" in rendu
    assert "- `src/orders/**`" in rendu
    assert "## Input: test_report\n2 tests fail" in rendu
    assert "Input: human_feedback" not in rendu, "une entrée vide ne se montre pas"


def test_aucun_agent_du_catalogue_ne_fixe_de_runtime_ni_de_modele() -> None:
    """Le modèle et le runtime viennent du projet et du déploiement : un agent qui exigerait
    `claude-code` ne tournerait pas sur un déploiement qui ne sert qu'opencode (S20-11)."""
    for agent in entrees():
        spec = agent.document["spec"]
        assert "model" not in spec and "backend" not in spec, agent.slug


def test_chaque_role_du_paquet_a_son_agent_au_catalogue() -> None:
    playbooks = pytest.importorskip("choregos_playbooks")
    roles = {e.role for e in INTERNES}
    assert set(playbooks.KNOWN_ROLES) <= roles, set(playbooks.KNOWN_ROLES) - roles


def test_les_skills_nommees_par_un_agent_sont_au_catalogue() -> None:
    connues = set(skills_du_catalogue())
    for agent in entrees():
        assert set(agent.skills) <= connues, (agent.slug, agent.skills)
    assert fichiers_de_la_skill("madr-4")["SKILL.md"].startswith("---\nname: madr-4\n")


def test_un_client_externe_dit_d_ou_il_appelle_et_n_a_ni_instructions_ni_modele() -> None:
    externes = [e for e in entrees() if e.genre == "external"]
    assert {e.client for e in externes} == {
        "claude-code",
        "claude-desktop",
        "cursor",
        "vscode",
        "chatgpt",
        "claude-ai",
    }
    assert {e.slug for e in externes if e.portee == "cloud"} == {"chatgpt", "claude-ai"}
    assert all(not e.document["spec"] for e in externes)


def test_l_empreinte_change_quand_le_texte_change() -> None:
    from dataclasses import replace

    agent = entree("triager")
    assert agent is not None
    autre = replace(agent, document={**agent.document, "description": "x"})
    assert autre.empreinte != agent.empreinte


def test_l_architecte_verifie_son_numero_avant_d_accepter() -> None:
    """#287 : renvoyé par `adr_number_free`, l'architecte repart de l'acceptation ; c'est là qu'il
    relit les numéros de la branche par défaut et renumérote le sien s'il est pris."""
    architecte = entree("architect")
    assert architecte is not None
    instructions = architecte.document["spec"]["instructions"]
    accepter = rendre_les_instructions(instructions, inputs={"adr_path": "docs/adr/0007-queue.md"})
    assert "docs/adr/0007-queue.md" in accepter
    assert "adr_number_free" in accepter and "git ls-tree" in accepter
    assert "highest number on the default branch plus one" in accepter
