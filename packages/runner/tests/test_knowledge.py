"""Knowledge bases served as files: laid read-only in the workspace, checked, greppable (S33-02, ADR 0051)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from choregos_contracts import KnowledgeRef, StageInput, knowledge_digest
from choregos_runner.exits import Exit
from choregos_runner.knowledge import INDEX, OKF_VERSION, ROOT, KnowledgeInvalidError, lay_out
from choregos_runner.runner import Runner
from choregos_runner.workspace import git_dir_for

from .conftest import FakeInternalClient, agent_script, valid_result

DOCUMENTS = [
    {
        "path": "network-zoning.md",
        "title": "Network zoning principles",
        "filename": "network-zoning.pptx",
        "markdown": "<!-- Slide number: 1 -->\n# Network zoning principles\nEvery zone has a firewall.\n",
    },
    {
        "path": "encryption.md",
        "title": "Encryption standard",
        "filename": "encryption.docx",
        "markdown": "# Encryption standard\n\n## In transit\n\nTLS 1.3 everywhere.\n",
    },
]
DELIVERED = {"slug": "architecture", "name": "Architecture framework", "documents": DOCUMENTS}
REF = KnowledgeRef(
    slug="architecture",
    name="Architecture framework",
    mode="files",
    documents=["d1", "d2"],
    digest=knowledge_digest(DOCUMENTS),
)


def test_a_base_lands_read_only_with_its_index(tmp_path: Path) -> None:
    written, prompt = lay_out([DELIVERED], [REF], tmp_path)
    folder = tmp_path / ROOT / "architecture"
    assert (folder / "encryption.md").read_text() == DOCUMENTS[1]["markdown"]
    assert "[Network zoning principles](network-zoning.md)" in (folder / INDEX).read_text()
    assert sorted(written) == sorted(
        f"{ROOT}/architecture/{p}" for p in ("network-zoning.md", "encryption.md", INDEX)
    )
    assert not os.access(folder / "encryption.md", os.W_OK), "the corpus is read-only"
    assert f"`{ROOT}/architecture/`" in prompt and "cite the document" in prompt


def test_the_index_lists_each_documents_outline_with_the_line_each_section_starts_on(tmp_path: Path) -> None:
    """The table of contents of each document, so the agent reads the section that answers (STAIR's
    point: a corpus's own structure is a retrieval key), not a blind grep."""
    _, prompt = lay_out([DELIVERED], [REF], tmp_path)
    index = (tmp_path / ROOT / "architecture" / INDEX).read_text()
    entry = index.split("[Encryption standard](encryption.md)", 1)[1]
    assert "  - 1 Encryption standard — " in entry and "(line 1)" in entry
    assert "    - 1.1 In transit — " in entry and "(line 3)" in entry
    assert "outlines in `index.md`" in prompt and "listed with their outlines" in prompt


def test_the_index_counts_the_sections_it_does_not_list(tmp_path: Path) -> None:
    long = "# Handbook\n\n" + "".join(f"## Chapter {n}\n\ntext\n\n" for n in range(60))
    documents = [{"path": "handbook.md", "title": "Handbook", "filename": "handbook.docx", "markdown": long}]
    ref = KnowledgeRef(slug="h", mode="files", documents=["d"], digest=knowledge_digest(documents))
    lay_out([{"slug": "h", "name": "H", "documents": documents}], [ref], tmp_path)
    index = (tmp_path / ROOT / "h" / INDEX).read_text()
    assert "1.39 Chapter 38" in index and "Chapter 39" not in index
    assert "… 21 more section(s) not listed" in index


def test_the_index_keeps_its_okf_header_and_a_documents_header_moves_no_line(tmp_path: Path) -> None:
    """A document comes with its OKF header (ADR 0174): the outline skips it; its lines are the file's."""
    markdown = "---\ntype: Document\n# a YAML comment\n---\n# Policy\n\n## Scope\n"
    documents = [{"path": "policy.md", "title": "Policy", "filename": "policy.docx", "markdown": markdown}]
    ref = KnowledgeRef(slug="p", mode="files", documents=["d"], digest=knowledge_digest(documents))
    lay_out([{"slug": "p", "name": "P", "documents": documents}], [ref], tmp_path)
    index = (tmp_path / ROOT / "p" / INDEX).read_text()
    assert index.startswith('---\nokf_version: "0.2"\n---')
    assert "a YAML comment" not in index
    assert "  - 1 Policy — " in index and "(line 5)" in index and "(line 7)" in index
    lines = (tmp_path / ROOT / "p" / "policy.md").read_text().splitlines()
    assert lines[4] == "# Policy" and lines[6] == "## Scope"


def test_an_index_base_lays_nothing_and_points_to_the_search_tool(tmp_path: Path) -> None:
    written, prompt = lay_out([], [KnowledgeRef(slug="big", mode="index")], tmp_path)
    assert written == []
    assert "`knowledge_search`" in prompt and "`big`" in prompt and "`knowledge_read`" in prompt
    assert not (tmp_path / ROOT).exists()


@pytest.mark.parametrize(
    ("delivered", "reason"),
    [
        pytest.param([], "was not delivered", id="missing"),
        pytest.param(
            [{**DELIVERED, "documents": [{**DOCUMENTS[0], "markdown": "# changed\n"}, DOCUMENTS[1]]}],
            "changed since the run was prepared",
            id="changed",
        ),
        pytest.param(
            [{**DELIVERED, "documents": [{**DOCUMENTS[0], "path": "../escape.md"}]}],
            "digest",
            id="escape-without-digest",
        ),
    ],
)
def test_a_base_missing_or_changed_stops_the_run(
    delivered: list[dict[str, Any]], reason: str, tmp_path: Path
) -> None:
    with pytest.raises(KnowledgeInvalidError, match=reason):
        lay_out(delivered, [REF], tmp_path)
    assert not (tmp_path / ROOT).exists(), "nothing is written"


def test_a_path_that_leaves_its_folder_is_refused_even_with_its_digest(tmp_path: Path) -> None:
    escaping = [{**DOCUMENTS[0], "path": "../escape.md"}]
    ref = REF.model_copy(update={"digest": knowledge_digest(escaping)})
    with pytest.raises(KnowledgeInvalidError, match="bad path"):
        lay_out([{**DELIVERED, "documents": escaping}], [ref], tmp_path)


def test_the_folder_is_an_okf_bundle_whose_root_index_declares_its_version(tmp_path: Path) -> None:
    """ADR 0174: OKF reserves `index.md`; the root one may carry `okf_version`, and nothing else in it
    is a header."""
    _, prompt = lay_out([DELIVERED], [REF], tmp_path)
    index = (tmp_path / ROOT / "architecture" / "index.md").read_text()
    assert INDEX == "index.md"
    assert index.startswith(f'---\nokf_version: "{OKF_VERSION}"\n---\n\n# Architecture framework\n')
    assert "`source_verified` is its author's claim" in prompt and "`stale_after`" in prompt


@pytest.mark.parametrize("reserved", ["index.md", "INDEX.md", "log.md"])
def test_a_document_under_a_name_okf_reserves_is_refused(reserved: str, tmp_path: Path) -> None:
    named = [{**DOCUMENTS[0], "path": reserved}]
    ref = REF.model_copy(update={"digest": knowledge_digest(named)})
    with pytest.raises(KnowledgeInvalidError, match="bad path"):
        lay_out([{**DELIVERED, "documents": named}], [ref], tmp_path)
    assert not (tmp_path / ROOT).exists(), "nothing is written"


async def test_a_fake_run_greps_the_corpus(stage_input: StageInput, runner_settings: Any) -> None:
    """The acceptance of S33-02: the agent finds the reference in its workspace, by grep."""
    with_kb = stage_input.model_copy(deep=True)
    with_kb.knowledge = [REF]
    agent_script(
        {
            "turns": [
                {
                    "greps": [{"pattern": "TLS 1.3", "path": ".choregos/knowledge"}],
                    "result": valid_result(summary="applied: {grep}"),
                }
            ]
        },
        with_kb,
        runner_settings.workspace,
    )
    client = FakeInternalClient(with_kb)
    client.knowledge = [DELIVERED]
    runner = Runner(runner_settings, client)
    outcome = await runner.execute(with_kb, client)  # type: ignore[arg-type]
    assert outcome.exit_code is Exit.OK, outcome.detail
    assert client.results, "a result was posted"
    summary = client.results[0].summary
    assert ".choregos/knowledge/architecture/encryption.md:5:TLS 1.3 everywhere." in summary
    prompt = runner._prompt(with_kb, Path(runner_settings.workspace))
    assert "## Knowledge bases" in prompt and ".choregos/knowledge/architecture/" in prompt
    exclusions = (git_dir_for(Path(runner_settings.workspace)) / "info" / "exclude").read_text()
    assert ".choregos/knowledge/architecture/encryption.md" in exclusions, "the corpus stays out of the diff"


async def test_a_changed_base_stops_the_run_before_the_agent(
    stage_input: StageInput, runner_settings: Any
) -> None:
    with_kb = stage_input.model_copy(deep=True)
    with_kb.knowledge = [REF]
    agent_script({"turns": [{"result": valid_result()}]}, with_kb, runner_settings.workspace)
    client = FakeInternalClient(with_kb)
    client.knowledge = [{**DELIVERED, "documents": DOCUMENTS[:1]}]
    outcome = await Runner(runner_settings, client).execute(with_kb, client)  # type: ignore[arg-type]
    assert outcome.exit_code is Exit.KNOWLEDGE_INVALID
    assert client.results == [], "the agent never ran"
