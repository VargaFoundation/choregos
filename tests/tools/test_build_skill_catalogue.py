"""The skills catalogue generator: replayable, byte-deterministic, strict about licences (ADR 0065)."""

from __future__ import annotations

import io
import json
import pathlib
import tarfile
from typing import Any

import pytest
from choregos_api.services.skill_gate import OPEN_LICENCES, assess
from choregos_api.services.skill_sources import read_tarball
from choregos_api.services.skills import empreinte, entete
from choregos_core import skill_catalogue

from tools.catalogues import _github, _recording
from tools.catalogues import build_skill_catalogue as gen

SEED = gen._load_yaml("seed.yaml")
PUBLISHERS = gen._load_yaml("publishers.yaml")
EXCLUSIONS = gen._load_yaml("exclusions.yaml")
OVERRIDES = gen._load_yaml("overrides.yaml")

APACHE = "Apache License\nVersion 2.0, January 2004\nhttp://www.apache.org/licenses/\n"
MIT = "MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a copy\n"
PROPRIETARY = "© Anthropic, PBC. All rights reserved. Governed by your agreement with Anthropic.\n"


def skill_md(name: str, description: str = "Does the thing") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\nBody of {name}.\n"


def make_tar(files: dict[str, str | bytes], executables: tuple[str, ...] = ()) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for path, content in files.items():
            data = content.encode() if isinstance(content, str) else content
            info = tarfile.TarInfo(f"owner-repo-abc123/{path}")
            info.size = len(data)
            info.mode = 0o755 if path in executables else 0o644
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def facts(stars: int = 5000, *, archived: bool = False) -> _github.RepoFacts:
    return _github.RepoFacts(full_name="owner/repo", stars=stars, archived=archived, licence="MIT")


def run(
    files: dict[str, str | bytes],
    *,
    line: dict[str, Any] | None = None,
    repo: str = "obra/superpowers",
    repo_facts: _github.RepoFacts | None = None,
    executables: tuple[str, ...] = (),
) -> Any:
    """One seed line through `resolve`, on a tarball built in memory."""
    snapshot = read_tarball(make_tar(files, executables), "")
    line = line or {"name": "demo", "categories": ["engineering"]}
    found, why = gen._find(snapshot, line)
    recorded = {
        "sha": "a" * 40,
        "skills": {
            line["name"]: {
                "path": found.path if found else None,
                "problem": why or (found.problem if found else None),
            }
        },
    }
    return gen.resolve(
        repo, line, recorded, snapshot, repo_facts or facts(), PUBLISHERS, EXCLUSIONS, "2026-10-15"
    )


GOOD = {"demo/SKILL.md": skill_md("demo"), "LICENSE": MIT}


# ---- the committed files are what the recording yields


def _generate() -> tuple[dict[str, Any], dict[str, Any], list[Any], list[Any]]:
    meta, snapshots, repo_facts = gen.load_inputs(gen.FIXTURES, SEED)
    return gen.build(
        SEED, meta, snapshots, repo_facts, PUBLISHERS, EXCLUSIONS, OVERRIDES["catalogue_version"]
    )


def test_replaying_the_recordings_reproduces_every_committed_file_byte_for_byte() -> None:
    document, bundles, drops, _ = _generate()
    assert drops == [], drops
    assert gen.render(document).encode() == gen.OUTPUT.read_bytes()
    assert gen.notices(document).encode() == gen.NOTICES.read_bytes()
    assert gen.render_web_mock(document).encode() == gen.WEB_MOCK.read_bytes()
    assert gen.bundled_tree(bundles) == gen.read_tree(gen.BUNDLED)


def test_check_mode_passes_and_fails_on_a_stale_bundled_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    assert gen.main(["--check"]) == 0
    stale = tmp_path / "bundled"
    stale.mkdir()
    monkeypatch.setattr(gen, "BUNDLED", stale)
    assert gen.main(["--check"]) == 1


def test_the_catalogue_is_what_the_gate_says_of_every_skill_it_lists() -> None:
    """Every entry's digest, risk and licence are the platform's own answer on the recorded commit."""
    meta, snapshots, _ = gen.load_inputs(gen.FIXTURES, SEED)
    sha_of = {r: v["sha"] for r, v in meta["repos"].items()}
    for e in skill_catalogue.load().entries:
        repo = e.repo.removeprefix("https://github.com/")
        assert e.sha == sha_of[repo], e.id
        found = next(s for s in snapshots[repo].skills if s.path == e.path)
        assert found.name == e.id and entete(found.files["SKILL.md"])["name"] == e.id
        assessed = assess(found.files, executables=found.executables, repo_licences=snapshots[repo].licences)
        assert (e.risk, e.licence, e.digest) == (assessed.risk, assessed.licence, empreinte(found.files)), (
            e.id
        )
        assert e.licence in OPEN_LICENCES, e.id


def test_the_catalogue_meets_the_brief() -> None:
    entries = skill_catalogue.load().entries
    bundled = [e for e in entries if e.delivery == "bundled"]
    assert 70 <= len(entries) <= 130 and len(bundled) >= 20
    assert not {e.id for e in entries} & {"docx", "pdf", "pptx", "xlsx", "doc-coauthoring"}


# ---- the rules in code


def test_a_proprietary_licence_drops_the_skill() -> None:
    result = run({"demo/SKILL.md": skill_md("demo"), "demo/LICENSE.txt": PROPRIETARY})
    assert isinstance(result, gen.Dropped) and result.reason.startswith("licence")


def test_a_skill_without_any_licence_is_dropped() -> None:
    result = run({"demo/SKILL.md": skill_md("demo")})
    assert isinstance(result, gen.Dropped) and "none found" in result.reason


def test_a_binary_file_drops_the_skill_as_the_gate_does() -> None:
    result = run({"demo/SKILL.md": skill_md("demo"), "demo/blob.bin": b"\xff\xfe\x00\x01", "LICENSE": MIT})
    assert isinstance(result, gen.Dropped) and result.reason.startswith("gate:") and "UTF-8" in result.reason


def test_a_skill_declaring_permissions_is_dropped_as_the_gate_does() -> None:
    body = "---\nname: demo\ndescription: x\nallowed-tools: Bash\n---\n"
    result = run({"demo/SKILL.md": body, "LICENSE": MIT})
    assert isinstance(result, gen.Dropped) and "allowed-tools" in result.reason


def test_a_clone_repository_with_a_handful_of_stars_is_dropped_whoever_owns_it() -> None:
    clone = facts(stars=7)
    for repo in ("obra/superpowers", "101-skills/superpowers"):
        result = run(GOOD, repo=repo, repo_facts=clone)
        assert isinstance(result, gen.Dropped) and result.reason.startswith("publisher"), repo
    assert not isinstance(run(GOOD, repo="anthropics/skills", repo_facts=clone), gen.Dropped), (
        "a vendor needs no stars"
    )


def test_an_archived_repository_is_dropped() -> None:
    result = run(GOOD, repo_facts=facts(archived=True))
    assert isinstance(result, gen.Dropped) and "archived" in result.reason


def test_an_excluded_name_or_path_is_dropped() -> None:
    for name in ("docx", "setup-pre-commit", "using-git-worktrees", "onboarding"):
        files = {f"{name}/SKILL.md": skill_md(name), "LICENSE": MIT}
        result = run(files, line={"name": name, "categories": ["engineering"]})
        assert isinstance(result, gen.Dropped) and result.reason.startswith("excluded"), name


def test_a_missing_skill_is_dropped_and_an_ambiguous_one_needs_a_path() -> None:
    assert isinstance(run(GOOD, line={"name": "other", "categories": ["engineering"]}), gen.Dropped)
    twice = {"a/demo/SKILL.md": skill_md("demo"), "b/demo/SKILL.md": skill_md("demo"), "LICENSE": MIT}
    with pytest.raises(gen.GeneratorError, match="give a `path`"):
        run(twice)
    entry, _ = run(twice, line={"name": "demo", "path": "b/demo", "categories": ["engineering"]})
    assert entry["path"] == "b/demo"


def test_bundled_is_a_request_the_generator_grants_only_to_a_permissive_script_free_skill() -> None:
    ask = {"name": "demo", "categories": ["engineering"], "delivery": "bundled"}
    entry, extra = run(GOOD, line=ask)
    assert entry["delivery"] == "bundled" and extra == {"scope": "repository", "name": "LICENSE", "text": MIT}
    scripted = {"demo/SKILL.md": skill_md("demo"), "demo/run.sh": "#!/bin/sh\necho hi\n", "LICENSE": MIT}
    entry, extra = run(scripted, line=ask)
    assert entry["delivery"] == "git" and entry["risk"] == "high" and "downgraded" in (extra or {})
    linked = {"demo/SKILL.md": skill_md("demo") + "See https://example.com/x\n", "LICENSE": MIT}
    entry, _ = run(linked, line=ask)
    assert entry["delivery"] == "git" and entry["risk"] == "high", "an outbound URL is high risk"
    copyleft = {"demo/SKILL.md": skill_md("demo"), "LICENSE": "Mozilla Public License, Version 2.0\n"}
    entry, extra = run(copyleft, line=ask)
    assert (entry["delivery"], entry["licence_class"]) == ("git", "copyleft") and "copyleft" in extra[
        "downgraded"
    ]  # type: ignore[index]
    assert run(GOOD, line={"name": "demo", "categories": ["engineering"]})[0]["delivery"] == "git", (
        "default: git"
    )


def test_an_executable_bit_makes_a_skill_high_risk() -> None:
    files = {
        "demo/SKILL.md": skill_md("demo"),
        "demo/helper": "no extension, but executable\n",
        "LICENSE": MIT,
    }
    entry, _ = run(files, executables=("demo/helper",))
    assert entry["risk"] == "high"


def test_the_entry_carries_the_platforms_own_digest_and_a_description_cut_at_300() -> None:
    entry, _ = run({"demo/SKILL.md": skill_md("demo", "word " * 100), "LICENSE": MIT})
    snapshot = read_tarball(make_tar({"demo/SKILL.md": skill_md("demo", "word " * 100), "LICENSE": MIT}), "")
    assert entry["digest"] == empreinte(snapshot.skills[0].files)
    assert len(entry["description"]) <= 300 and entry["description"].endswith("…")


def test_a_category_outside_the_closed_list_is_refused() -> None:
    with pytest.raises(gen.GeneratorError, match="categories"):
        run(GOOD, line={"name": "demo", "categories": ["nonsense"]})


def test_one_entry_per_skill_name() -> None:
    meta, snapshots, repo_facts = gen.load_inputs(gen.FIXTURES, SEED)
    twice = [
        {
            "repo": "obra/superpowers",
            "ref": "main",
            "skills": [{"name": "writing-plans", "categories": ["engineering"]}],
        },
        {
            "repo": "addyosmani/agent-skills",
            "ref": "main",
            "skills": [{"name": "writing-plans", "categories": ["engineering"]}],
        },
    ]
    with pytest.raises(gen.GeneratorError, match="one entry per skill name"):
        gen.build(twice, meta, snapshots, repo_facts, PUBLISHERS, EXCLUSIONS, "2026.10.0")


# ---- the recording layer


def test_a_reduced_tarball_keeps_the_seed_folders_and_the_licence_and_is_byte_stable() -> None:
    full = make_tar(
        {
            "keep/SKILL.md": skill_md("keep"),
            "keep/ref/a.md": "a",
            "drop/SKILL.md": skill_md("drop"),
            "LICENSE": MIT,
            "README.md": "readme",
        },
        executables=("keep/ref/a.md",),
    )
    reduced = gen.reduce_tarball(full, ["keep"])
    assert reduced == gen.reduce_tarball(full, ["keep"])
    with tarfile.open(fileobj=io.BytesIO(reduced)) as tar:
        names = sorted(m.name for m in tar)
        assert names == [
            "owner-repo-abc123/LICENSE",
            "owner-repo-abc123/keep/SKILL.md",
            "owner-repo-abc123/keep/ref/a.md",
        ]
        assert next(m for m in tar if m.name.endswith("a.md")).mode & 0o111, (
            "the executable bit survives: the gate reads it"
        )
    assert {s.path for s in read_tarball(reduced, "").skills} == {"keep"}


def test_replay_refuses_the_network(tmp_path: pathlib.Path) -> None:
    with (
        _recording.make_client(tmp_path, record=False) as client,
        pytest.raises(_recording.RecordingMissError),
    ):
        client.get("https://api.github.com/repos/obra/superpowers/commits/main")


def test_the_web_demo_data_is_the_catalogue_as_the_api_shows_it() -> None:
    mock = json.loads(gen.WEB_MOCK.read_text("utf-8"))
    assert [e["id"] for e in mock["entries"]] == [e.id for e in skill_catalogue.load().entries]
    assert {e["state"] for e in mock["entries"]} == {"available"}
