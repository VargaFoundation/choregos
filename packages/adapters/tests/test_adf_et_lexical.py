"""Les deux traducteurs purs : markdown ↔ ADF pour Jira, vecteur lexical pour pgvector."""

from __future__ import annotations

from choregos_adapters.memory.pgvector import lexical_vector, similarity
from choregos_adapters.tracker.adf import markdown_to_adf, text_of_adf


def test_un_tableau_et_une_liste_deviennent_des_noeuds_adf() -> None:
    md = (
        "# Suivi\n\nÉtat **en cours** via `t-implement`.\n\n"
        "| clé | valeur |\n|:--|:--|\n| coût | 0,42 € |\n\n- un\n- deux\n"
    )
    doc = markdown_to_adf(md)
    types = [n["type"] for n in doc["content"]]
    assert types == ["heading", "paragraph", "table", "bulletList"]
    gras = [n for n in doc["content"][1]["content"] if n.get("marks")]
    assert {m["type"] for n in gras for m in n["marks"]} == {"strong", "code"}
    assert doc["content"][2]["content"][0]["content"][0]["type"] == "tableHeader"
    assert len(doc["content"][3]["content"]) == 2


def test_les_liens_gardent_leur_cible_et_le_texte_se_relit() -> None:
    doc = markdown_to_adf("Voir [le run](https://app/run/1).")
    lien = next(n for n in doc["content"][0]["content"] if n.get("marks"))
    assert lien["marks"][0]["attrs"]["href"] == "https://app/run/1" and lien["text"] == "le run"
    assert text_of_adf(doc) == "Voir le run."
    assert text_of_adf("brut") == "brut" and text_of_adf(None) == ""
    assert markdown_to_adf("")["content"] == [{"type": "paragraph", "content": []}]


def test_le_vecteur_lexical_est_normalise_et_la_similarite_bornee() -> None:
    a = lexical_vector("Les avoirs ne sont pas déduits du total")
    assert "avoirs" in a and "ne" not in a, "les mots de moins de trois lettres sont écartés"
    assert abs(sum(v * v for v in a.values()) - 1.0) < 1e-9
    assert similarity(a, a) > 0.999 and similarity(a, lexical_vector("")) == 0.0
    assert 0 < similarity(a, lexical_vector("le total ignore les avoirs")) < 1
