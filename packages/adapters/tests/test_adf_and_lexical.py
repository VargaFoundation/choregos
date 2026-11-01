"""The two pure translators: markdown ↔ ADF for Jira, lexical vector for the memory fallback."""

from __future__ import annotations

from choregos_adapters.memory.lexical import lexical_vector, similarity
from choregos_adapters.tracker.adf import markdown_to_adf, text_of_adf


def test_a_table_and_a_list_become_adf_nodes() -> None:
    md = (
        "# Tracking\n\nState **in progress** via `t-implement`.\n\n"
        "| key | value |\n|:--|:--|\n| cost | 0.42 € |\n\n- one\n- two\n"
    )
    doc = markdown_to_adf(md)
    types = [n["type"] for n in doc["content"]]
    assert types == ["heading", "paragraph", "table", "bulletList"]
    bold = [n for n in doc["content"][1]["content"] if n.get("marks")]
    assert {m["type"] for n in bold for m in n["marks"]} == {"strong", "code"}
    assert doc["content"][2]["content"][0]["content"][0]["type"] == "tableHeader"
    assert len(doc["content"][3]["content"]) == 2


def test_links_keep_their_target_and_the_text_reads_back() -> None:
    doc = markdown_to_adf("See [the run](https://app/run/1).")
    link = next(n for n in doc["content"][0]["content"] if n.get("marks"))
    assert link["marks"][0]["attrs"]["href"] == "https://app/run/1" and link["text"] == "the run"
    assert text_of_adf(doc) == "See the run."
    assert text_of_adf("raw") == "raw" and text_of_adf(None) == ""
    assert markdown_to_adf("")["content"] == [{"type": "paragraph", "content": []}]


def test_the_lexical_vector_is_normalised_and_the_similarity_bounded() -> None:
    a = lexical_vector("Credit notes are not deducted from the total, café included")
    assert "credit" in a and "café" in a and "are" in a and "no" not in a, "short words are dropped"
    assert abs(sum(v * v for v in a.values()) - 1.0) < 1e-9
    assert similarity(a, a) > 0.999 and similarity(a, lexical_vector("")) == 0.0
    assert 0 < similarity(a, lexical_vector("the total ignores credit notes")) < 1
