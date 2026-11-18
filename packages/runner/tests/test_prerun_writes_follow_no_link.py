"""S65-04b — what the runner writes into the tree before the agent starts follows no link and no second name.

On a workspace kept across a person's answer, the agent has already worked in the tree: it can have left,
where the runner is about to write `task.md`, `context.md`, an input, a knowledge document, a skill file, the
fields or a remote agent's deliverable, a symbolic link (the write would land on the target) or a hard link
(`O_NOFOLLOW` does not see it: the write would truncate the other name's file). Each writer below meets one
in its place and refuses it: the file behind it is left as it was, the refusal is named.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from tranibor_contracts import KnowledgeRef, SkillRef, StageInput, knowledge_digest, skill_digest
from tranibor_runner import safe_files
from tranibor_runner.exits import Exit
from tranibor_runner.inputs import INPUTS_DIR, mount_inputs
from tranibor_runner.job import FIELDS, write_fields
from tranibor_runner.knowledge import ROOT, lay_out
from tranibor_runner.runner import Runner
from tranibor_runner.skills import FALLBACK_DIR, place_skills
from tranibor_runner.workspace import Workspace

from .conftest import FakeInternalClient, valid_result

OTHER = "THE-FILE-THE-AGENT-CANNOT-WRITE"


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path]:
    """(workspace, a file outside the agent's reach that the runner could write)."""
    workspace = tmp_path / "workspace"
    (workspace / ".choregos").mkdir(parents=True)
    other = tmp_path / "runner-owned.txt"
    other.write_text(OTHER, encoding="utf-8")
    return workspace, other


def _symlink(at: Path, to: Path) -> None:
    at.parent.mkdir(parents=True, exist_ok=True)
    at.symlink_to(to)


def _hardlink(at: Path, to: Path) -> None:
    at.parent.mkdir(parents=True, exist_ok=True)
    os.link(to, at)


KINDS = {"symlink": _symlink, "hardlink": _hardlink}


class _Inputs:
    async def fetch_inputs(self) -> list[dict[str, Any]]:
        from tranibor_contracts import artifact_digest

        return [{"name": "deck", "version": 1, "filename": "deck.md", "digest": artifact_digest(b"x")}]

    async def fetch_input_content(self, name: str) -> bytes:
        return b"x"


@pytest.mark.parametrize("kind", KINDS)
async def test_an_input_over_a_link_is_refused(kind: str, tree: tuple[Path, Path]) -> None:
    workspace, other = tree
    KINDS[kind](workspace / INPUTS_DIR / "deck.md", other)
    with pytest.raises(safe_files.LinkRefusedError):
        await mount_inputs(_Inputs(), workspace)
    assert other.read_text(encoding="utf-8") == OTHER


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name", ["task.md", "context.md", "allowed_paths.txt"])
def test_the_context_files_over_a_link_are_refused(
    kind: str, name: str, tree: tuple[Path, Path], stage_input: StageInput
) -> None:
    workspace, other = tree
    KINDS[kind](workspace / ".choregos" / name, other)
    runner_files = Workspace.__new__(Workspace)
    runner_files.path = workspace
    with pytest.raises(safe_files.LinkRefusedError):
        runner_files.write_context_files(stage_input, None)
    assert other.read_text(encoding="utf-8") == OTHER


DOCUMENTS = [{"path": "a.md", "title": "A", "filename": "a.md", "markdown": "# A\n"}]
KNOWLEDGE = KnowledgeRef(
    slug="base", name="Base", mode="files", documents=["d1"], digest=knowledge_digest(DOCUMENTS)
)


@pytest.mark.parametrize("kind", KINDS)
def test_a_knowledge_document_over_a_link_is_not_written_through_it(
    kind: str, tree: tuple[Path, Path]
) -> None:
    workspace, other = tree
    KINDS[kind](workspace / ROOT / "base" / "a.md", other)
    # The runner removes the name it finds (a link is unlinked, never followed) and writes its own file:
    # the other name's file is untouched either way.
    lay_out([{"slug": "base", "documents": DOCUMENTS}], [KNOWLEDGE], workspace)
    assert other.read_text(encoding="utf-8") == OTHER
    assert (workspace / ROOT / "base" / "a.md").read_text(encoding="utf-8") == "# A\n"
    assert not os.path.islink(workspace / ROOT / "base" / "a.md")


def test_a_knowledge_folder_that_is_a_link_is_refused(tree: tuple[Path, Path], tmp_path: Path) -> None:
    workspace, _ = tree
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _symlink(workspace / ROOT, elsewhere)
    with pytest.raises(safe_files.LinkRefusedError):
        lay_out([{"slug": "base", "documents": DOCUMENTS}], [KNOWLEDGE], workspace)
    assert list(elsewhere.iterdir()) == []


SKILL = {"SKILL.md": "---\nname: s\ndescription: d\n---\n"}
SKILL_REF = SkillRef(slug="s", version=1, digest=skill_digest(SKILL))


@pytest.mark.parametrize("kind", KINDS)
def test_a_skill_file_over_a_link_is_not_written_through_it(kind: str, tree: tuple[Path, Path]) -> None:
    workspace, other = tree
    KINDS[kind](workspace / FALLBACK_DIR / "s" / "SKILL.md", other)
    place_skills([{"slug": "s", "files": SKILL}], [SKILL_REF], workspace, None)
    assert other.read_text(encoding="utf-8") == OTHER
    assert (workspace / FALLBACK_DIR / "s" / "SKILL.md").read_text(encoding="utf-8") == SKILL["SKILL.md"]


def test_a_skills_folder_that_is_a_link_is_refused(tree: tuple[Path, Path], tmp_path: Path) -> None:
    workspace, _ = tree
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _symlink(workspace / ".choregos" / "skills", elsewhere)
    with pytest.raises(safe_files.LinkRefusedError):
        place_skills([{"slug": "s", "files": SKILL}], [SKILL_REF], workspace, None)
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("kind", KINDS)
def test_the_fields_over_a_link_are_refused(kind: str, tree: tuple[Path, Path]) -> None:
    workspace, other = tree
    KINDS[kind](workspace / FIELDS, other)
    with pytest.raises(safe_files.LinkRefusedError):
        write_fields(workspace, {"a": 1})
    assert other.read_text(encoding="utf-8") == OTHER


# --- the hard link, at the primitive -------------------------------------------------------------


def test_a_file_with_another_name_is_not_truncated(tree: tuple[Path, Path]) -> None:
    workspace, other = tree
    _hardlink(workspace / "x.txt", other)
    with pytest.raises(safe_files.LinkRefusedError, match="hard links"):
        safe_files.write_text(workspace, "x.txt", "new")
    with pytest.raises(safe_files.LinkRefusedError, match="hard links"):
        safe_files.write_text(workspace, "x.txt", "new", append=True)
    assert other.read_text(encoding="utf-8") == OTHER


def test_a_file_with_another_name_is_not_read(tree: tuple[Path, Path]) -> None:
    workspace, other = tree
    _hardlink(workspace / "x.txt", other)
    with pytest.raises(safe_files.LinkRefusedError, match="hard links"):
        safe_files.read_text(workspace, "x.txt")
    assert not safe_files.is_regular(workspace, "x.txt")


def test_a_file_the_agent_alone_names_is_still_written_and_read(tree: tuple[Path, Path]) -> None:
    workspace, _ = tree
    safe_files.write_text(workspace, "y/z.txt", "one")
    safe_files.write_text(workspace, "y/z.txt", "two")
    safe_files.write_text(workspace, "y/z.txt", "+", append=True)
    assert safe_files.read_text(workspace, "y/z.txt") == "two+"


def test_remove_unlinks_a_link_and_empties_a_directory_without_following(
    tree: tuple[Path, Path], tmp_path: Path
) -> None:
    workspace, _other = tree
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep").write_text("keep", encoding="utf-8")
    _symlink(workspace / "d", elsewhere)
    safe_files.remove(workspace, "d")
    assert not os.path.lexists(workspace / "d")
    assert (elsewhere / "keep").read_text(encoding="utf-8") == "keep"
    safe_files.remove(workspace, "missing")  # nothing there is not an error
    _symlink(workspace / "e" / "inner", elsewhere)
    safe_files.remove(workspace, "e")
    assert (elsewhere / "keep").exists()


# --- a workspace kept across a person's answer, through the whole runner -------------------------


@pytest.mark.parametrize("kind", KINDS)
async def test_a_resumed_workspace_with_a_link_on_task_md_stops_the_run_named(
    kind: str, stage_input: StageInput, runner_settings: Any, tmp_path: Path
) -> None:
    other = tmp_path / "runner-owned.txt"
    other.write_text(OTHER, encoding="utf-8")
    KINDS[kind](runner_settings.workspace / ".choregos" / "task.md", other)
    client = FakeInternalClient(stage_input)
    outcome = await Runner(runner_settings, client).execute(stage_input, client)  # type: ignore[arg-type]
    assert outcome.exit_code is Exit.WORKSPACE_UNSAFE
    assert other.read_text(encoding="utf-8") == OTHER, "nothing was written through the link"
    failed = [e for e in client.events if e.get("type") == "run.failed"]
    assert failed
    assert failed[0]["payload"]["reason"] == "tree_write_refused"
    assert failed[0]["payload"]["path"].endswith("task.md")


async def test_a_resumed_workspace_without_a_link_runs_as_before(
    stage_input: StageInput, runner_settings: Any
) -> None:
    from .conftest import agent_script

    agent_script({"turns": [{"result": valid_result("ok")}]}, stage_input, runner_settings.workspace)
    client = FakeInternalClient(stage_input)
    (runner_settings.workspace / ".choregos").mkdir(parents=True)
    (runner_settings.workspace / ".choregos" / "task.md").write_text("an earlier turn", encoding="utf-8")
    outcome = await Runner(runner_settings, client).execute(stage_input, client)  # type: ignore[arg-type]
    assert outcome.exit_code is Exit.OK


@pytest.mark.parametrize("kind", KINDS)
def test_a_remote_agents_file_over_a_link_is_refused(kind: str, tree: tuple[Path, Path]) -> None:
    from tranibor_adapters.a2a.client import A2aArtifact, A2aPart
    from tranibor_runner.remote import _land

    workspace, other = tree
    KINDS[kind](workspace / ".choregos" / "outputs" / "report.md", other)
    artifact = A2aArtifact(
        artifact_id="a", name="report.md", parts=[A2aPart(kind="file", name="report.md", content=b"x")]
    )
    with pytest.raises(safe_files.LinkRefusedError):
        _land(workspace, artifact, Path(".choregos/outputs"), {}, {}, {})
    assert other.read_text(encoding="utf-8") == OTHER
