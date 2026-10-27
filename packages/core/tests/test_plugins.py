# SPDX-License-Identifier: Apache-2.0
"""The plugin loader (ADR 0093, S43-02): a valid plugin loads, and each guard refuses its own case."""

from __future__ import annotations

import io
import os
import shutil
import tarfile
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_core.plugins import PluginError, build_index, digest, load_directory, opened, pack

HELLO = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "plugins" / "hello"


@pytest.fixture
def plugin(tmp_path: Path) -> Path:
    copy = tmp_path / "hello"
    shutil.copytree(HELLO, copy)
    return copy


def _edit(path: Path, change: Any) -> None:
    doc = yaml.safe_load(path.read_text())
    change(doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


def _problems(root: Path) -> str:
    with pytest.raises(PluginError) as caught:
        load_directory(root)
    return "\n".join(caught.value.problems)


def test_the_fixture_loads_with_everything_it_ships() -> None:
    loaded = load_directory(HELLO)
    assert (loaded.name, loaded.version, loaded.core) == ("hello", "1.0.0", ">=0.20.0")
    assert list(loaded.agents) == ["greeter"]
    assert list(loaded.servers) == ["hello"]
    assert loaded.procedures == ("connector.mcp.hello",)
    assert loaded.digest.startswith("sha256:") and len(loaded.digest) == 71


def test_a_directory_without_a_manifest_is_refused(tmp_path: Path) -> None:
    assert "plugin.yaml: missing" in _problems(tmp_path)


def test_another_kind_is_refused(plugin: Path) -> None:
    _edit(plugin / "plugin.yaml", lambda d: d.update(kind="Template"))
    assert "'Plugin' was expected" in _problems(plugin)


@pytest.mark.parametrize("version", ["1.0", "v1.0.0", "01.0.0"])
def test_a_version_that_is_not_semver_is_refused(plugin: Path, version: str) -> None:
    _edit(plugin / "plugin.yaml", lambda d: d["metadata"].update(version=version))
    assert "plugin.yaml:metadata/version" in _problems(plugin)


@pytest.mark.parametrize("ref", ["../outside.yaml", "/etc/passwd", "agents/../../x.yaml"])
def test_a_path_leaving_the_plugin_is_refused(plugin: Path, ref: str) -> None:
    (plugin.parent / "outside.yaml").write_text("catalogue: {}\nagent: {}\n")
    _edit(plugin / "plugin.yaml", lambda d: d["contents"].update(agents=[ref]))
    assert "plugin.yaml:contents/agents/0" in _problems(plugin)


def test_a_symbolic_link_is_refused_even_inside_the_plugin(plugin: Path) -> None:
    (plugin / "agents" / "alias.yaml").symlink_to(plugin / "agents" / "greeter.yaml")
    assert "agents/alias.yaml: symbolic link refused" in _problems(plugin)


def test_a_symbolic_link_to_a_directory_is_refused(plugin: Path, tmp_path: Path) -> None:
    (tmp_path / "elsewhere").mkdir()
    (plugin / "skills" / "linked").symlink_to(tmp_path / "elsewhere")
    assert "skills/linked: symbolic link refused" in _problems(plugin)


def test_a_server_image_without_a_digest_is_refused(plugin: Path) -> None:
    _edit(plugin / "servers" / "hello.yaml", lambda d: d.update(image="ghcr.io/x/hello-mcp:1.0.0"))
    assert "servers/hello.yaml:image" in _problems(plugin)


def test_a_write_operation_without_access_is_refused(plugin: Path) -> None:
    _edit(plugin / "servers" / "hello.yaml", lambda d: d["operations"][1].pop("access"))
    assert "'access' is a required property" in _problems(plugin)


def test_a_compensation_names_an_operation_of_the_server(plugin: Path) -> None:
    _edit(
        plugin / "servers" / "hello.yaml",
        lambda d: d["operations"][1].update(compensation={"operation": "undo"}),
    )
    assert "compensated by `undo`" in _problems(plugin)


def test_an_operation_is_declared_once(plugin: Path) -> None:
    _edit(plugin / "servers" / "hello.yaml", lambda d: d["operations"].append(dict(d["operations"][0])))
    assert "operation `say_hello` is declared twice" in _problems(plugin)


def test_an_egress_field_is_a_configuration_field(plugin: Path) -> None:
    _edit(plugin / "servers" / "hello.yaml", lambda d: d["egress"].update(from_fields=["host"]))
    assert "egress field `host`" in _problems(plugin)


def test_an_agent_file_has_the_catalogue_shape(plugin: Path) -> None:
    _edit(plugin / "agents" / "greeter.yaml", lambda d: d.pop("catalogue"))
    assert "`catalogue:` and `agent:`" in _problems(plugin)


def test_an_agent_slug_is_checked(plugin: Path) -> None:
    _edit(plugin / "agents" / "greeter.yaml", lambda d: d["agent"].update(slug="Greeter"))
    assert "agent.slug must match" in _problems(plugin)


def test_a_skill_directory_holds_a_skill(plugin: Path) -> None:
    (plugin / "skills" / "greeting" / "SKILL.md").unlink()
    assert "skills/greeting: a skill directory holds a SKILL.md" in _problems(plugin)


def test_a_template_manifest_is_validated(plugin: Path) -> None:
    _edit(plugin / "templates" / "hello-flow" / "manifest.yaml", lambda d: d.pop("steps"))
    assert "templates/hello-flow/manifest.yaml: 'steps' is a required property" in _problems(plugin)


def test_an_eval_names_an_agent_of_the_plugin(plugin: Path) -> None:
    _edit(plugin / "evals" / "greeter.yaml", lambda d: d.update(agent="architect"))
    assert "evaluates `architect`" in _problems(plugin)


def test_a_procedure_is_parsed_with_the_adr_0067_rules(plugin: Path) -> None:
    path = plugin / "procedures" / "connector.mcp.hello.md"
    path.write_text(path.read_text().replace("audience: org_admin", "audience: anyone"))
    assert "procedures/connector.mcp.hello.md" in _problems(plugin)


def test_a_procedure_pattern_that_matches_nothing_is_refused(plugin: Path) -> None:
    _edit(plugin / "plugin.yaml", lambda d: d["contents"].update(procedures=["docs/*.md"]))
    assert "docs/*.md: matches no file" in _problems(plugin)


def test_every_problem_is_reported_not_only_the_first(plugin: Path) -> None:
    _edit(plugin / "servers" / "hello.yaml", lambda d: d.update(image="ghcr.io/x/hello-mcp:1.0.0"))
    _edit(plugin / "evals" / "greeter.yaml", lambda d: d.update(agent="architect"))
    found = _problems(plugin)
    assert "servers/hello.yaml:image" in found and "evaluates `architect`" in found


# -- digest and pack -------------------------------------------------------------------------------


def test_packing_twice_gives_the_same_bytes_and_digest(plugin: Path, tmp_path: Path) -> None:
    first, second = tmp_path / "a.tar.gz", tmp_path / "b.tar.gz"
    assert pack(plugin, first).digest == pack(plugin, second).digest
    assert first.read_bytes() == second.read_bytes()


def test_the_digest_ignores_times_and_owners_but_not_bytes(plugin: Path) -> None:
    before = digest(plugin)
    os.utime(plugin / "plugin.yaml", (1, 1))
    (plugin / "agents" / "greeter.yaml").chmod(0o600)
    assert digest(plugin) == before
    with (plugin / "skills" / "greeting" / "SKILL.md").open("a") as fh:
        fh.write("\n")
    assert digest(plugin) != before


def test_a_packed_plugin_loads_with_the_same_digest(plugin: Path, tmp_path: Path) -> None:
    archive = tmp_path / "hello.tar.gz"
    packed = pack(plugin, archive)
    with opened(archive) as loaded:
        assert loaded.digest == packed.digest
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
    assert [m.name for m in members] == sorted(m.name for m in members)
    assert {(m.mtime, m.uid, m.gid, m.mode) for m in members} == {(0, 0, 0, 0o644)}


def _archive(path: Path, member: tarfile.TarInfo, data: bytes = b"") -> None:
    with tarfile.open(path, "w:gz") as tar:
        manifest = (HELLO / "plugin.yaml").read_bytes()
        info = tarfile.TarInfo("plugin.yaml")
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
        member.size = len(data)
        tar.addfile(member, io.BytesIO(data))


@pytest.mark.parametrize(
    ("member", "message"),
    [
        (tarfile.TarInfo("../escape.yaml"), "goes outside"),
        (tarfile.TarInfo("/abs.yaml"), "goes outside"),
    ],
)
def test_an_archive_entry_leaving_the_plugin_is_refused(
    tmp_path: Path, member: tarfile.TarInfo, message: str
) -> None:
    archive = tmp_path / "bad.tar.gz"
    _archive(archive, member, b"x")
    with pytest.raises(PluginError, match=message), opened(archive):
        pass


def test_an_archive_with_a_link_is_refused(tmp_path: Path) -> None:
    link = tarfile.TarInfo("agents/greeter.yaml")
    link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
    archive = tmp_path / "link.tar.gz"
    _archive(archive, link)
    with pytest.raises(PluginError, match="symbolic link refused"), opened(archive):
        pass


def test_a_file_that_is_not_an_archive_is_refused(tmp_path: Path) -> None:
    junk = tmp_path / "junk.tar.gz"
    junk.write_bytes(b"not a tar")
    with pytest.raises(PluginError, match="not a readable"), opened(junk):
        pass


# -- index -----------------------------------------------------------------------------------------


def test_an_index_records_each_plugin_with_its_digest(plugin: Path) -> None:
    index = build_index(plugin.parent, oci_repository="ghcr.io/acme/plugins/")
    release = index["plugins"]["hello"]["versions"]["1.0.0"]
    assert release["digest"] == digest(plugin)
    assert release["oci"] == "ghcr.io/acme/plugins/hello:1.0.0"
    git = build_index(
        plugin.parent, git_url="https://github.com/acme/agents", git_commit="a" * 40, git_path="plugins"
    )
    assert git["plugins"]["hello"]["versions"]["1.0.0"]["git"]["path"] == "plugins/hello"


def test_an_index_locates_plugins_one_way_and_by_commit(plugin: Path) -> None:
    with pytest.raises(PluginError, match="one of them"):
        build_index(plugin.parent)
    with pytest.raises(PluginError, match="commit"):
        build_index(plugin.parent, git_url="https://github.com/acme/agents", git_commit="main")


def test_an_index_refuses_two_directories_with_one_name(plugin: Path) -> None:
    shutil.copytree(plugin, plugin.parent / "hello-again")
    with pytest.raises(PluginError, match="in two directories"):
        build_index(plugin.parent, oci_repository="ghcr.io/acme/plugins")
