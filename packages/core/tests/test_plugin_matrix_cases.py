"""A plugin ships agent cases of the core's eval matrix, and a server declares its fake (#1372).

`contents.matrix_cases` is a declared content of a plugin (ADR 0093): validated, digested and packed with
the rest, offered by the catalogue view as `plugin:<plugin>/<case>`. A server's `fake` says how the
platform runs it on its fake for the live matrix, through variables no administrator reaches.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from choregos_core.agent_catalogue import catalogue, plugin_ref
from choregos_core.plugins import PluginError, fake_scenes, load_directory, opened, pack

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "plugins"
SERVICENOW = FIXTURES / "servicenow"
E3, C1 = "itsm-triage-similar-incidents", "itsm-triage-vague-incident"


@pytest.fixture
def plugin(tmp_path: Path) -> Path:
    root = tmp_path / "servicenow"
    shutil.copytree(SERVICENOW, root)
    return root


def _edit(path: Path, change: dict[str, object]) -> None:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document.update(change)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def _problems(root: Path) -> list[str]:
    with pytest.raises(PluginError) as refused:
        load_directory(root)
    return refused.value.problems


def test_a_plugin_s_matrix_cases_are_declared_content_validated_digested_and_packed(
    plugin: Path, tmp_path: Path
) -> None:
    loaded = load_directory(plugin)
    assert sorted(loaded.matrix_cases) == [E3, C1]
    case = loaded.matrix_cases[E3]
    assert (case["name"], case["agent"]) == (E3, "itsm-triager")
    assert case["connectors"] == {"servicenow": {"kind": "plugin:servicenow/servicenow", "scene": "default"}}
    assert f"evals/matrix/{E3}.yaml" in loaded.files, "the digest covers it"
    archive = tmp_path / "servicenow.tar.gz"
    pack(plugin, archive)
    with opened(archive) as unpacked:
        assert sorted(unpacked.matrix_cases) == [E3, C1], "packed with the plugin"
        assert unpacked.digest == loaded.digest
    before = loaded.digest
    (plugin / f"evals/matrix/{C1}.yaml").write_text(
        (plugin / f"evals/matrix/{C1}.yaml").read_text() + "\n# changed\n"
    )
    assert load_directory(plugin).digest != before, "a case is part of the plugin's identity"


def test_a_matrix_case_must_play_an_agent_of_the_plugin(plugin: Path) -> None:
    _edit(plugin / f"evals/matrix/{E3}.yaml", {"agent": "someone-else"})
    assert any("plays `someone-else`, which the plugin does not ship" in p for p in _problems(plugin))


def test_a_matrix_case_with_its_own_agent_spec_needs_no_agent_of_the_plugin(plugin: Path) -> None:
    _edit(plugin / f"evals/matrix/{E3}.yaml", {"agent": "case-local", "agent_spec": {"instructions": "x"}})
    assert load_directory(plugin).matrix_cases[E3]["agent"] == "case-local"


@pytest.mark.parametrize(
    ("connector", "reason"),
    [
        ({"servicenow": {"kind": "plugin:jira/jira"}}, "another plugin's server than `servicenow`'s"),
        ({"servicenow": {"kind": "plugin:servicenow/cmdb"}}, "a server `servicenow` does not ship"),
        ({"snow": {"kind": "plugin:servicenow/servicenow"}}, "so the case names it `servicenow`"),
        ({"servicenow": {"scene": "default"}}, "has no `kind`"),
    ],
)
def test_a_matrix_case_s_plugin_connector_names_a_server_of_this_plugin_by_its_name(
    plugin: Path, connector: dict[str, object], reason: str
) -> None:
    _edit(plugin / f"evals/matrix/{C1}.yaml", {"connectors": connector})
    assert any(reason in p for p in _problems(plugin)), _problems(plugin)


def test_a_matrix_case_s_cells_are_the_matrix_s_doors_and_it_has_a_task(plugin: Path) -> None:
    _edit(plugin / f"evals/matrix/{C1}.yaml", {"tools_cells": ["mcp", "carrier-pigeon"], "task": ""})
    problems = _problems(plugin)
    assert any("`tools_cells` lists doors among mcp, scripts" in p for p in problems)
    assert any("a matrix case has a `task`" in p for p in problems)


def test_the_manifest_lists_matrix_cases_as_paths(plugin: Path) -> None:
    _edit(plugin / "plugin.yaml", {"contents": {"agents": ["agents/itsm-triager.yaml"], "matrix_cases": "x"}})
    assert any("plugin.yaml:contents/matrix_cases" in p for p in _problems(plugin))


def test_a_server_declares_its_fake_with_variables_no_administrator_reaches(plugin: Path) -> None:
    server = plugin / "servers/servicenow.yaml"
    assert fake_scenes(load_directory(plugin).servers["servicenow"]) == ("default",)
    _edit(server, {"fake": {"env": {"SERVICENOW_BASE_URL": "https://fake", "CHOREGOS_SERVER_TOKEN": "x"}}})
    problems = _problems(plugin)
    assert any(
        "fake variable `SERVICENOW_BASE_URL` is what the configuration field `base_url` reaches" in p
        for p in problems
    ), problems
    assert any("fake variable `CHOREGOS_SERVER_TOKEN` is the platform's" in p for p in problems)
    _edit(server, {"fake": {"scenes": ["default"]}})
    assert any("servers/servicenow.yaml:fake: 'env' is a required property" in p for p in _problems(plugin))


def test_the_catalogue_offers_a_plugin_s_matrix_cases_and_its_servers_by_reference() -> None:
    view = catalogue(FIXTURES)
    found = view.matrix_case(f"plugin:servicenow/{E3}")
    assert found is not None and (found.plugin, found.name, found.ref) == (
        "servicenow",
        E3,
        f"plugin:servicenow/{E3}",
    )
    assert view.matrix_case("plugin:servicenow/no-such-case") is None
    assert view.matrix_case(E3) is None, "a core case is not a plugin's"
    server = view.server("plugin:servicenow/servicenow")
    assert server is not None and server["fake"] == {"env": {"SERVICENOW_FAKE": "1"}, "scenes": ["default"]}
    assert view.entry("itsm-triager") is not None, "the case's agent comes with it"
    assert (plugin_ref("plugin:a/b"), plugin_ref("plugin:a"), plugin_ref("a/b")) == (("a", "b"), None, None)


def test_an_installed_version_replaces_the_image_s_cases_and_servers(plugin: Path) -> None:
    from choregos_core.agent_catalogue import of_plugin

    image = of_plugin(load_directory(SERVICENOW))
    (plugin / f"evals/matrix/{C1}.yaml").unlink()
    manifest = yaml.safe_load((plugin / "plugin.yaml").read_text())
    manifest["contents"]["matrix_cases"] = [f"evals/matrix/{E3}.yaml"]
    (plugin / "plugin.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    installed = of_plugin(load_directory(plugin)).over(image)
    assert [c.name for c in installed.matrix_cases] == [E3], "the organisation's version, not the image's"
    assert installed.server("plugin:servicenow/servicenow") is not None
