"""The agent's skills, placed where its backend reads them (ADR 0033, S18-05)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from choregos_contracts import SkillRef, StageInput, skill_digest
from choregos_runner.backends import get_backend
from choregos_runner.exits import Exit
from choregos_runner.runner import Runner
from choregos_runner.skills import FALLBACK_DIR, InvalidSkill, place_skills

from .conftest import FakeInternalClient, agent_script, valid_result

FILES = {
    "SKILL.md": "---\nname: onboarding-procedure\ndescription: The arrival procedure.\n---\n\n# Procedure\n",
    "references/groups.md": "- vpn\n",
}
DELIVERED = {
    "slug": "onboarding-procedure",
    "version": 2,
    "digest": skill_digest(FILES),
    "files": FILES,
}
REF = SkillRef(slug="onboarding-procedure", version=2, digest=skill_digest(FILES))
FOLDERS = {
    "claude-code": ".claude/skills",
    "codex": ".agents/skills",
    "gemini-cli": ".gemini/skills",
    "goose": ".goose/skills",
    "opencode": ".opencode/skills",
    "copilot-cli": ".github/skills",
}


@pytest.mark.parametrize(("backend", "folder"), FOLDERS.items())
def test_each_backend_finds_the_skills_where_it_reads_them(backend: str, folder: str, tmp_path: Path) -> None:
    written, index = place_skills([DELIVERED], [REF], tmp_path, get_backend(backend).skills_dir)
    assert (tmp_path / folder / "onboarding-procedure" / "SKILL.md").read_text() == FILES["SKILL.md"]
    assert sorted(written) == sorted(f"{folder}/onboarding-procedure/{name}" for name in FILES)
    assert index == "", "a backend that reads skills does not need an index"


def test_a_backend_without_skills_receives_an_index(tmp_path: Path) -> None:
    written, index = place_skills([DELIVERED], [REF], tmp_path, None)
    assert (tmp_path / FALLBACK_DIR / "onboarding-procedure" / "SKILL.md").exists()
    assert f"`{FALLBACK_DIR}/onboarding-procedure/SKILL.md` — The arrival procedure." in index
    assert len(written) == 2


@pytest.mark.parametrize(
    ("delivered", "pattern"),
    [
        pytest.param(
            [{**DELIVERED, "files": {**FILES, "references/groups.md": "- all\n"}}],
            "digest",
            id="altered",
        ),
        pytest.param([], "was not delivered", id="missing"),
        pytest.param(
            [{**DELIVERED, "files": {"../escape.sh": "x"}, "digest": REF.digest}],
            "digest",
            id="escape-without-digest",
        ),
    ],
)
def test_a_missing_or_altered_skill_stops_the_run(
    delivered: list[dict[str, Any]], pattern: str, tmp_path: Path
) -> None:
    with pytest.raises(InvalidSkill, match=pattern):
        place_skills(delivered, [REF], tmp_path, ".claude/skills")
    assert not (tmp_path / ".claude").exists(), "nothing is written"


@pytest.mark.parametrize("backend", FOLDERS)
def test_the_stage_input_files_reach_each_backend(
    backend: str, stage_input: StageInput, tmp_path: Path
) -> None:
    """Only claude-code merged `launch.files`: the other five ignored it."""
    with_files = stage_input.model_copy(deep=True)
    with_files.agent.launch.files = {"notes/instructions.md": "read before acting\n"}
    plan = get_backend(backend).launch_plan(with_files, tmp_path)
    assert plan.files.get("notes/instructions.md") == "read before acting\n"


async def test_the_runner_places_the_skills_out_of_the_diff(
    stage_input: StageInput, runner_settings: Any
) -> None:
    with_skills = stage_input.model_copy(deep=True)
    with_skills.skills = [REF]
    agent_script(
        {"turns": [{"messages": ["done"], "result": valid_result()}]}, with_skills, runner_settings.workspace
    )
    client = FakeInternalClient(with_skills)
    client.skills = [DELIVERED]
    outcome = await Runner(runner_settings, client).execute(with_skills, client)  # type: ignore[arg-type]
    assert outcome.exit_code is Exit.OK, outcome.detail
    root = str(get_backend(with_skills.agent.backend).skills_dir)
    assert (Path(runner_settings.workspace) / root / "onboarding-procedure" / "SKILL.md").exists()
    # Out of the diff: the runner excludes them from git, like the backend's configuration.
    exclusions = (Path(runner_settings.workspace) / ".git" / "info" / "exclude").read_text()
    assert f"{root}/onboarding-procedure/SKILL.md" in exclusions


async def test_the_runner_stops_on_an_altered_skill(stage_input: StageInput, runner_settings: Any) -> None:
    with_skills = stage_input.model_copy(deep=True)
    with_skills.skills = [REF]
    agent_script(
        {"turns": [{"messages": ["done"], "result": valid_result()}]}, with_skills, runner_settings.workspace
    )
    client = FakeInternalClient(with_skills)
    client.skills = [{**DELIVERED, "files": {**FILES, "SKILL.md": "---\nname: x\n---\n"}}]
    outcome = await Runner(runner_settings, client).execute(with_skills, client)  # type: ignore[arg-type]
    assert outcome.exit_code is Exit.SKILLS_INVALID
    assert client.results == [], "no result: the agent did not run"


PNG = b"\x89PNG\r\n\x1a\n\x00\xff\xfe"


def test_an_inert_binary_is_written_as_bytes_without_an_exec_bit(tmp_path: Path) -> None:
    from choregos_contracts import encode_binary

    files = {**FILES, "assets/logo.png": encode_binary(PNG)}
    ref = SkillRef(slug="onboarding-procedure", version=2, digest=skill_digest(files))
    delivered = {"slug": "onboarding-procedure", "version": 2, "digest": ref.digest, "files": files}
    written, _ = place_skills([delivered], [ref], tmp_path, ".claude/skills")
    target = tmp_path / ".claude/skills/onboarding-procedure/assets/logo.png"
    assert target.read_bytes() == PNG
    assert target.stat().st_mode & 0o777 == 0o644
    assert ".claude/skills/onboarding-procedure/assets/logo.png" in written


def test_a_binary_swapped_after_preparation_stops_the_run(tmp_path: Path) -> None:
    from choregos_contracts import encode_binary

    expected = {**FILES, "a.png": encode_binary(PNG)}
    ref = SkillRef(slug="onboarding-procedure", version=2, digest=skill_digest(expected))
    swapped = {**FILES, "a.png": encode_binary(PNG + b"x")}
    delivered = {"slug": "onboarding-procedure", "version": 2, "digest": ref.digest, "files": swapped}
    with pytest.raises(InvalidSkill, match="digest"):
        place_skills([delivered], [ref], tmp_path, ".claude/skills")


def test_a_corrupt_binary_stops_the_run(tmp_path: Path) -> None:
    delivered = {**DELIVERED, "files": {**FILES, "a.png": "not base64!"}}
    with pytest.raises(InvalidSkill, match="corrupt binary"):
        place_skills([delivered], [REF], tmp_path, ".claude/skills")
