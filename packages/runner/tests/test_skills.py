"""Les skills de l'agent, posées là où son backend les lit (ADR 0033, S18-05)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from choregos_contracts import SkillRef, StageInput, empreinte_de_skill
from choregos_runner.backends import get_backend
from choregos_runner.exits import Exit
from choregos_runner.runner import Runner
from choregos_runner.skills import REPLI, SkillInvalide, poser

from .conftest import FakeInternalClient, agent_script, valid_result

FICHIERS = {
    "SKILL.md": "---\nname: procedure-onboarding\ndescription: La procédure d'arrivée.\n---\n\n# Procédure\n",
    "references/groupes.md": "- vpn\n",
}
LIVREE = {
    "slug": "procedure-onboarding",
    "version": 2,
    "digest": empreinte_de_skill(FICHIERS),
    "files": FICHIERS,
}
REF = SkillRef(slug="procedure-onboarding", version=2, digest=empreinte_de_skill(FICHIERS))
DOSSIERS = {
    "claude-code": ".claude/skills",
    "codex": ".agents/skills",
    "gemini-cli": ".gemini/skills",
    "goose": ".goose/skills",
    "opencode": ".opencode/skills",
    "copilot-cli": ".github/skills",
}


@pytest.mark.parametrize(("backend", "dossier"), DOSSIERS.items())
def test_chaque_backend_trouve_les_skills_ou_il_les_lit(backend: str, dossier: str, tmp_path: Path) -> None:
    ecrits, index = poser([LIVREE], [REF], tmp_path, get_backend(backend).skills_dir)
    assert (tmp_path / dossier / "procedure-onboarding" / "SKILL.md").read_text() == FICHIERS["SKILL.md"]
    assert sorted(ecrits) == sorted(f"{dossier}/procedure-onboarding/{c}" for c in FICHIERS)
    assert index == "", "un backend qui lit les skills n'a pas besoin d'index"


def test_un_backend_sans_skills_recoit_un_index(tmp_path: Path) -> None:
    ecrits, index = poser([LIVREE], [REF], tmp_path, None)
    assert (tmp_path / REPLI / "procedure-onboarding" / "SKILL.md").exists()
    assert f"`{REPLI}/procedure-onboarding/SKILL.md` — La procédure d'arrivée." in index
    assert len(ecrits) == 2


@pytest.mark.parametrize(
    ("livrees", "motif"),
    [
        pytest.param(
            [{**LIVREE, "files": {**FICHIERS, "references/groupes.md": "- tout\n"}}],
            "digest",
            id="alteree",
        ),
        pytest.param([], "was not delivered", id="absente"),
        pytest.param(
            [{**LIVREE, "files": {"../evasion.sh": "x"}, "digest": REF.digest}],
            "digest",
            id="evasion-sans-empreinte",
        ),
    ],
)
def test_une_skill_absente_ou_alteree_arrete_le_run(
    livrees: list[dict[str, Any]], motif: str, tmp_path: Path
) -> None:
    with pytest.raises(SkillInvalide, match=motif):
        poser(livrees, [REF], tmp_path, ".claude/skills")
    assert not (tmp_path / ".claude").exists(), "rien n'est écrit"


@pytest.mark.parametrize("backend", DOSSIERS)
def test_les_fichiers_du_stage_input_arrivent_a_chaque_backend(
    backend: str, stage_input: StageInput, tmp_path: Path
) -> None:
    """Seul claude-code fusionnait `launch.files` : les cinq autres l'ignoraient."""
    avec = stage_input.model_copy(deep=True)
    avec.agent.launch.files = {"notes/consignes.md": "lire avant d'agir\n"}
    plan = get_backend(backend).launch_plan(avec, tmp_path)
    assert plan.files.get("notes/consignes.md") == "lire avant d'agir\n"


async def test_le_runner_pose_les_skills_hors_du_diff(stage_input: StageInput, runner_settings: Any) -> None:
    avec = stage_input.model_copy(deep=True)
    avec.skills = [REF]
    agent_script(
        {"turns": [{"messages": ["fait"], "result": valid_result()}]}, avec, runner_settings.workspace
    )
    client = FakeInternalClient(avec)
    client.skills = [LIVREE]
    sortie = await Runner(runner_settings, client).execute(avec, client)  # type: ignore[arg-type]
    assert sortie.exit_code is Exit.OK, sortie.detail
    racine = str(get_backend(avec.agent.backend).skills_dir)
    assert (Path(runner_settings.workspace) / racine / "procedure-onboarding" / "SKILL.md").exists()
    # Hors du diff : le runner les exclut de git, comme la configuration du backend.
    exclusions = (Path(runner_settings.workspace) / ".git" / "info" / "exclude").read_text()
    assert f"{racine}/procedure-onboarding/SKILL.md" in exclusions


async def test_le_runner_s_arrete_sur_une_skill_alteree(
    stage_input: StageInput, runner_settings: Any
) -> None:
    avec = stage_input.model_copy(deep=True)
    avec.skills = [REF]
    agent_script(
        {"turns": [{"messages": ["fait"], "result": valid_result()}]}, avec, runner_settings.workspace
    )
    client = FakeInternalClient(avec)
    client.skills = [{**LIVREE, "files": {**FICHIERS, "SKILL.md": "---\nname: x\n---\n"}}]
    sortie = await Runner(runner_settings, client).execute(avec, client)  # type: ignore[arg-type]
    assert sortie.exit_code is Exit.SKILLS_INVALID
    assert client.results == [], "aucun résultat : l'agent n'a pas tourné"


PNG = b"\x89PNG\r\n\x1a\n\x00\xff\xfe"


def test_an_inert_binary_is_written_as_bytes_without_an_exec_bit(tmp_path: Path) -> None:
    from choregos_contracts import encode_binary

    fichiers = {**FICHIERS, "assets/logo.png": encode_binary(PNG)}
    ref = SkillRef(slug="procedure-onboarding", version=2, digest=empreinte_de_skill(fichiers))
    livree = {"slug": "procedure-onboarding", "version": 2, "digest": ref.digest, "files": fichiers}
    ecrits, _ = poser([livree], [ref], tmp_path, ".claude/skills")
    ecrit = tmp_path / ".claude/skills/procedure-onboarding/assets/logo.png"
    assert ecrit.read_bytes() == PNG
    assert ecrit.stat().st_mode & 0o777 == 0o644
    assert ".claude/skills/procedure-onboarding/assets/logo.png" in ecrits


def test_a_binary_swapped_after_preparation_stops_the_run(tmp_path: Path) -> None:
    from choregos_contracts import encode_binary

    attendus = {**FICHIERS, "a.png": encode_binary(PNG)}
    ref = SkillRef(slug="procedure-onboarding", version=2, digest=empreinte_de_skill(attendus))
    livres = {**FICHIERS, "a.png": encode_binary(PNG + b"x")}
    livree = {"slug": "procedure-onboarding", "version": 2, "digest": ref.digest, "files": livres}
    with pytest.raises(SkillInvalide, match="digest"):
        poser([livree], [ref], tmp_path, ".claude/skills")


def test_a_corrupt_binary_stops_the_run(tmp_path: Path) -> None:
    livree = {**LIVREE, "files": {**FICHIERS, "a.png": "not base64!"}}
    with pytest.raises(SkillInvalide, match="corrupt binary"):
        poser([livree], [REF], tmp_path, ".claude/skills")
