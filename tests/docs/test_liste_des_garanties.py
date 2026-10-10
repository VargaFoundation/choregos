"""La page des concepts nomme chaque garantie du cœur (S22-20).

La liste « Gates shipped today » de `docs/concepts.md` avait pris du retard : `outputs_in`,
`markdown_sections` et `action_succeeded` livrées, absentes de la page. Une garantie que la
documentation de référence ne nomme pas n'existe pas pour qui écrit un workflow.
"""

from __future__ import annotations

import re
from pathlib import Path

from choregos_core.gates import _REGISTRY

CONCEPTS = Path(__file__).resolve().parents[2] / "docs" / "concepts.md"


def test_chaque_garantie_du_coeur_est_nommee_dans_les_concepts() -> None:
    # Les garanties du cœur seulement : un greffon ou un test peut en enregistrer d'autres.
    du_coeur = {nom for nom, fn in _REGISTRY.items() if fn.__module__ == "choregos_core.gates"}
    assert "adr_number_free" in du_coeur
    bloc = re.search(r"Gates shipped today:\n\n```\n(.*?)```", CONCEPTS.read_text(encoding="utf-8"), re.S)
    assert bloc is not None, "la liste des garanties a quitté docs/concepts.md"
    nommees = {nom.strip() for nom in re.split(r"[·\n]", bloc.group(1)) if nom.strip()}
    assert sorted(du_coeur - nommees) == [], "garanties absentes de docs/concepts.md"
    assert sorted(nommees - du_coeur) == [], "garanties nommées qui n'existent pas"
