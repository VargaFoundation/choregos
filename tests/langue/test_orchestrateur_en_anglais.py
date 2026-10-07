"""Ce que l'orchestrateur écrit pour quelqu'un est en anglais (ADR 0039, S21-13).

Le commentaire de suivi du ticket, les notifications, les demandes humaines, les raisons d'un état,
les étapes du provisioning, les issues de findings, la sortie de `make demo` : tout cela se lit dans
le tracker, Slack ou la console. Le code, les commentaires, les docstrings et les journaux restent en
français. La liste `RESTE_EN_FRANCAIS` ne peut que raccourcir : une entrée qui ne correspond plus à
rien fait rougir la suite.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib

RACINE = pathlib.Path(__file__).resolve().parents[2]
ORCHESTRATEUR = RACINE / "apps/orchestrator/src/choregos_orchestrator"
JOURNAUX = {"debug", "info", "warning", "error", "exception", "critical", "warn"}

#: (fichier relatif, début du littéral) — chacune avec sa raison.
RESTE_EN_FRANCAIS = {
    # Le banc d'évaluation des agents : un outil de la plateforme, lu par ses développeurs.
    ("activities/evals.py", "fichier créé alors"),
    ("activities/evals.py", "fichier modifié hors"),
    ("activities/evals.py", "dépôt jouet absent"),
    ("activities/evals.py", "aucune modification scriptée"),
    ("activities/evals.py", "présent"),
    ("activities/evals.py", "délai dépassé ("),
    # Une expression régulière, pas un texte.
    ("activities/findings.py", "[a-zà-ÿ0-9_]+"),
    # Le verdict du rapport A/B de la mémoire est produit par l'API (`services/memoire.py`) et comparé
    # ici : il change avec elle, pas seul.
    ("activities/memory.py", "la mémoire ne paie pas"),
    # Les champs du board GitHub Projects d'un projet déjà provisionné : les renommer est une migration.
    ("activities/tracker.py", "Coût (€)"),
}


def _detecteur() -> object:
    spec = importlib.util.spec_from_file_location(
        "anglais", pathlib.Path(__file__).with_name("test_anglais.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ressemble_a_du_francais


def _litteraux_en_francais() -> set[tuple[str, str, int]]:
    francais = _detecteur()
    trouves: set[tuple[str, str, int]] = set()
    for chemin in sorted(ORCHESTRATEUR.rglob("*.py")):
        arbre = ast.parse(chemin.read_text(encoding="utf-8"))
        a_ignorer: set[int] = set()
        for noeud in ast.walk(arbre):
            corps = getattr(noeud, "body", None)
            if (
                isinstance(noeud, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                and corps
            ):
                premier = corps[0]
                if isinstance(premier, ast.Expr) and isinstance(premier.value, ast.Constant):
                    a_ignorer.add(id(premier.value))
            if (
                isinstance(noeud, ast.Call)
                and isinstance(noeud.func, ast.Attribute)
                and noeud.func.attr in JOURNAUX
            ):
                a_ignorer.update(id(sous) for sous in ast.walk(noeud))
        for noeud in ast.walk(arbre):
            if (
                not (isinstance(noeud, ast.Constant) and isinstance(noeud.value, str))
                or id(noeud) in a_ignorer
            ):
                continue
            if len(noeud.value) > 3 and francais(noeud.value):  # type: ignore[operator]
                trouves.add((chemin.relative_to(ORCHESTRATEUR).as_posix(), noeud.value, noeud.lineno))
    return trouves


def _autorise(fichier: str, texte: str) -> bool:
    return any(fichier == f and texte.startswith(debut) for f, debut in RESTE_EN_FRANCAIS)


def test_l_orchestrateur_n_ecrit_pas_de_francais_a_ses_lecteurs() -> None:
    restes = sorted(
        f"{fichier}:{ligne}: {texte[:80]!r}"
        for fichier, texte, ligne in _litteraux_en_francais()
        if not _autorise(fichier, texte)
    )
    assert not restes, "\n".join(restes)


def test_la_liste_des_exceptions_ne_fait_que_raccourcir() -> None:
    trouves = {(fichier, texte) for fichier, texte, _ in _litteraux_en_francais()}
    perimees = [
        entree
        for entree in sorted(RESTE_EN_FRANCAIS)
        if not any(f == entree[0] and t.startswith(entree[1]) for f, t in trouves)
    ]
    assert not perimees, f"retirer de RESTE_EN_FRANCAIS : {perimees}"


def test_le_commentaire_de_suivi_et_une_demande_humaine_se_lisent_en_anglais() -> None:
    from choregos_orchestrator.markdown import StageLine, StatusComment, render_human_request

    ligne = StageLine(
        1, "Refining", "agent refine", "opencode", "standard", 12_400, 1_800, 0, 0.42, 7_380, "spec written"
    )
    rendu = StatusComment(
        workflow="dev-simple", workflow_version=1, size="S", risk="low", state_display="Implemented",
        budget_eur=9.2, spent_eur=0.42, estimate_eur=0.5, lines=[ligne],
    ).render()  # fmt: skip
    assert "### Choregos — progress" in rendu
    assert "**Size** S · **Risk** low · **State** Implemented" in rendu
    assert "| # | Step | Actor |" in rendu and "€0.42" in rendu and "2 h 3 min" in rendu
    assert "estimated €0.50" in rendu
    demande = render_human_request("approval", {}, "https://choregos.example/p/dev/items/1", "dev#1")
    assert "### Choregos — approval requested" in demande
    assert "Approve or refuse in the console: https://choregos.example/p/dev/items/1" in demande
