"""The agent catalogue is a view over plugins (S43-09, ADR 0093 §6).

The 22 agents, 6 clients, 25 eval cases and the skill `madr-4` left the core for the plugins of
`VargaFoundation/choregos-agents`; the core reads them from its offline directory (`plugins/offline/`,
pinned by `plugins.lock`), byte for byte what it shipped before.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from choregos_core import agent_catalogue as catalogue
from choregos_core.agent_catalogue import Catalogue, entries, entry, load, offline_directory

ROOT = Path(__file__).resolve().parents[3]
OFFLINE = ROOT / "plugins" / "offline"

#: The plugin of each agent, and the catalogue version its provenance carries (MIGRATION-FROM-CORE.md).
#: Every internal agent moved one version when its plugin declared its five components (S60-04, ADR 0127);
#: a client did not. netops had moved once before, for its tool-scripts default (S57-15); the researcher
#: moved once more in software-delivery 1.1.1, which restored the web its eval E5 reads.
VERSION_THREE = {"architect", "prod-verifier", "spec-writer", "researcher"}
VERSION_FOUR = {"netops"}
PLUGINS = {
    "software-delivery": 19,
    "netops": 1,
    "authoring": 2,
    "mcp-clients": 6,
    "requests": 2,
}
#: The plugin version the offline directory holds, when it is not 1.0.0.
PLUGIN_VERSIONS = {"software-delivery": "1.1.1", "netops": "1.2.0", "authoring": "1.1.0", "requests": "1.1.0"}

#: The fingerprints the core's own YAML had at 3156d12e (choregos-agents' MIGRATION-FROM-CORE.md): the
#: plugins carry the same documents, so an installed agent's checksum does not move. They include the
#: core's `_context.md`: S51-04 added its `fields.tracker` section, so every agent that prepends it
#: moved once (an installed one is offered the update); `claude-ai`, a client, prepends none. Every
#: internal agent's document moved once more, on purpose, when it declared its five components (S60-04).
FINGERPRINTS = {
    "architect": "sha256:eff0e642adb1912c3764c289654ca961f0068185b8ead70e3a0ebcb0452297b5",
    "developer": "sha256:d5d0d1986526f3936938bd5ad589f329b153374d7305d78e20855011436f3425",
    "netops": "sha256:7f6a8262fee61e909fc5cbeb78d2aa90eff5441868db3a2c150902c846af8718",
    "agent-drafter": "sha256:28d4c5df5d91ed095358fe7990760eaa4b30f47ca3ed04c0d384ef39ed129734",
    "claude-ai": "sha256:56cbd162bc57f803e6e162baf922669d6a50a3d205a521a88b69efc2b3113496",
}


def test_the_core_ships_no_agent_yaml_anymore() -> None:
    folder = Path(catalogue.__file__).parent
    assert not list(folder.rglob("*.yaml")), "an agent, client, eval case or skill came back"
    assert (folder / "_context.md").is_file(), "the work item's context stays the core's"


def test_the_offline_directory_of_a_source_tree_is_the_repositorys() -> None:
    assert offline_directory() == OFFLINE


def test_every_agent_comes_from_its_plugin() -> None:
    counted: dict[str, int] = {}
    for found in entries():
        counted[found.plugin] = counted.get(found.plugin, 0) + 1
        assert found.plugin_version == PLUGIN_VERSIONS.get(found.plugin, "1.0.0")
        expected = 4 if found.slug in VERSION_FOUR else 3 if found.slug in VERSION_THREE else 2
        if found.kind != "internal":
            expected = 1
        assert found.provenance == f"plugin:{found.plugin}/{found.slug}@{expected}"
    assert counted == PLUGINS


@pytest.mark.parametrize("slug", sorted(FINGERPRINTS))
def test_the_documents_are_the_cores_byte_for_byte(slug: str) -> None:
    found = entry(slug)
    assert found is not None and found.digest == FINGERPRINTS[slug]


def test_the_context_is_prepended_once() -> None:
    context = (Path(catalogue.__file__).parent / "_context.md").read_text(encoding="utf-8")
    instructions = entry("developer").document["spec"]["instructions"]  # type: ignore[union-attr]
    assert instructions.startswith(context) and instructions.count(context) == 1


def test_a_client_keeps_its_client_and_reach() -> None:
    claude = entry("claude-ai")
    assert claude is not None and (claude.client, claude.reach) == ("claude-ai", "cloud")


def test_madr_4_is_the_plugins_with_its_licence() -> None:
    skill = catalogue.catalogue().skills["madr-4"]
    assert skill.provenance == "plugin:software-delivery/madr-4@1.1.1"
    assert {"SKILL.md", "LICENSE", "PROVENANCE.json"} <= set(skill.files)


def test_without_an_offline_directory_the_catalogue_is_empty(tmp_path: Path) -> None:
    assert load(None) == Catalogue()
    assert load(tmp_path / "missing").entries == ()
    assert load(tmp_path).entries == ()


def test_the_environment_names_the_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shutil.copy(OFFLINE / "netops-1.2.0.tar.gz", tmp_path)
    monkeypatch.setenv("CHOREGOS_PLUGINS_OFFLINE", str(tmp_path))
    assert [e.slug for e in entries()] == ["netops"]


def test_an_installed_plugin_replaces_the_offline_one_whole() -> None:
    base = catalogue.catalogue()
    newer = Catalogue(
        (base.entry("netops"),),  # type: ignore[arg-type]
        {},
        (),
        {"software-delivery": "9.9.9"},
    )
    merged = newer.over(base)
    assert merged.entry("developer") is None, "the installed version of the plugin no longer has it"
    assert "madr-4" not in merged.skills
    assert merged.entry("agent-drafter") is not None, "another plugin is untouched"
    assert merged.plugins["software-delivery"] == "9.9.9"
