"""Les sorties d'une étape nourrissent la suivante — y compris quand elles portent un nom de métier.

Banc du 2026-09-24 : le sourcing RH produisait `outputs.profils`, la garantie
`outputs_present` l'acceptait, puis la qualification cherchait « les profils proposés à
l'étape précédente » dans un workspace vide. Rien ne rangeait une sortie qui ne s'appelait
pas `spec_markdown`, `plan_markdown`, `review_markdown` ou `release_notes_markdown`.
"""

from __future__ import annotations

from choregos_contracts import StageOutputs


def test_une_sortie_declaree_par_la_transition_est_rangee_sous_son_nom() -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.services import ranger_les_sorties

    item = WorkItem(tracker_key="RH-1", title="T", state="sourcing", documents={"spec_markdown": "ancien"})
    outputs = StageOutputs.model_validate({"profils": "## Trois profils\n- A\n- B", "bruit": "non déclaré"})
    documents = ranger_les_sorties(item, outputs, ["profils"])
    assert documents["profils"].startswith("## Trois profils")
    assert "bruit" not in documents, "une sortie que la transition ne déclare pas ne suit pas le ticket"
    assert documents["spec_markdown"] == "ancien", "les documents existants restent"


def test_les_documents_logiciels_sont_toujours_ranges_meme_sans_declaration() -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.services import ranger_les_sorties

    item = WorkItem(tracker_key="X-1", title="T", state="refining")
    ranger_les_sorties(item, StageOutputs(spec_markdown="## Spec", size="M"), None)
    assert item.documents == {"spec_markdown": "## Spec"}


def test_le_playbook_suivant_recoit_les_entrees_sous_leur_nom() -> None:
    from choregos_playbooks import render_playbook

    prompt = render_playbook(
        "implement",
        ticket={"key": "RH-1", "title": "T", "body": ""},
        inputs={"profils": "## Trois profils"},
    )
    assert "RH-1" in prompt  # le rôle du paquet ignore `inputs` : rendu sans erreur, StrictUndefined compris
