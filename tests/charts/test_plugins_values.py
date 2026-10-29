"""`global.plugins` renders the ConfigMap `choregos-plugins` the API reads (ADR 0093 §5, S43-05).

By default (S43-09) the built-in agents' four plugins are installed from the `varga` source, pre-approved,
taken from the image's offline directory; emptied, nothing is rendered or mounted. With values, the file
is the one `choregos_core.plugins.sources.parse_config` accepts, mounted read-only and named by
`CHOREGOS_PLUGINS_CONFIG`; a source without a trust policy refuses to render.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "choregos"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is missing")

PLUGINS: dict[str, Any] = {
    "sources": [
        {
            "name": "varga",
            "index": "oci://ghcr.io/vargafoundation/choregos-agents/index:stable",
            "trust": {
                "identity": "https://github.com/VargaFoundation/choregos-agents/.*",
                "issuer": "https://token.actions.githubusercontent.com",
            },
        }
    ],
    "defaultPlugins": [{"source": "varga", "name": "servicenow", "version": "^0.1"}],
    "offline": "/opt/choregos/plugins",
}


def _render(values: dict[str, Any] | None, tmp_path: pathlib.Path) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "choregos", str(CHART)]
    if values is not None:
        path = tmp_path / "values.yaml"
        path.write_text(yaml.safe_dump(values))
        command += ["-f", str(path)]
    return subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)


def _docs(values: dict[str, Any] | None, tmp_path: pathlib.Path) -> list[dict[str, Any]]:
    result = _render(values, tmp_path)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _api(docs: list[dict[str, Any]]) -> dict[str, Any]:
    for doc in docs:
        if doc.get("kind") == "Deployment" and doc["metadata"]["name"] == "choregos-api":
            spec: dict[str, Any] = doc["spec"]["template"]["spec"]
            return spec
    raise AssertionError("no choregos-api Deployment rendered")


def _config_map(docs: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (d for d in docs if d.get("kind") == "ConfigMap" and d["metadata"]["name"] == "choregos-plugins"),
        None,
    )


BUILT_IN = ["software-delivery", "netops", "authoring", "mcp-clients"]
NONE: dict[str, Any] = {"sources": [], "defaultPlugins": [], "offline": ""}


def test_by_default_the_built_in_agents_are_installed_pre_approved(tmp_path: pathlib.Path) -> None:
    import re

    from choregos_core.plugins.sources import parse_config

    docs = _docs(None, tmp_path)
    config_map = _config_map(docs)
    assert config_map is not None
    config = parse_config(yaml.safe_load(config_map["data"]["plugins.yaml"]))
    assert [(d.source, d.name, d.version, d.preapproved) for d in config.default_plugins] == [
        ("varga", name, "^1.0", True) for name in BUILT_IN
    ]
    (varga,) = config.sources
    assert varga.index == "oci://ghcr.io/vargafoundation/choregos-agents/index:latest"
    publish = (
        "https://github.com/VargaFoundation/choregos-agents/.github/workflows/publish.yml@refs/heads/main"
    )
    assert re.fullmatch(varga.trust.identity, publish)
    assert not re.fullmatch(varga.trust.identity, publish.replace("refs/heads/main", "refs/heads/evil"))
    assert re.fullmatch(varga.trust.issuer, "https://token.actions.githubusercontent.com")
    assert config.offline == pathlib.Path("/opt/choregos/plugins")
    dockerfile = (ROOT / "docker" / "api.Dockerfile").read_text(encoding="utf-8")
    assert "COPY plugins/offline /opt/choregos/plugins" in dockerfile, "the image ships what the chart names"


def test_emptied_there_is_no_plugin_configuration(tmp_path: pathlib.Path) -> None:
    docs = _docs({"global": {"plugins": NONE}}, tmp_path)
    spec = _api(docs)
    assert _config_map(docs) is None
    assert "CHOREGOS_PLUGINS_CONFIG" not in {e["name"] for e in spec["containers"][0]["env"]}
    assert "plugins-config" not in {v["name"] for v in spec["volumes"]}


def test_the_values_become_the_file_the_api_reads(tmp_path: pathlib.Path) -> None:
    from choregos_core.plugins.sources import parse_config

    docs = _docs({"global": {"plugins": PLUGINS}}, tmp_path)
    config_map = _config_map(docs)
    assert config_map is not None
    document = yaml.safe_load(config_map["data"]["plugins.yaml"])
    assert document == PLUGINS
    config = parse_config(document)
    assert [s.name for s in config.sources] == ["varga"]
    assert [(d.source, d.name, d.version) for d in config.default_plugins] == [
        ("varga", "servicenow", "^0.1")
    ]
    assert config.offline == pathlib.Path("/opt/choregos/plugins")

    spec = _api(docs)
    env = {e["name"]: e.get("value") for e in spec["containers"][0]["env"]}
    assert env["CHOREGOS_PLUGINS_CONFIG"] == "/etc/choregos/plugins/plugins.yaml"
    assert {"name": "plugins-config", "configMap": {"name": "choregos-plugins"}} in spec["volumes"]
    assert {
        "name": "plugins-config",
        "mountPath": "/etc/choregos/plugins",
        "readOnly": True,
    } in spec["containers"][0]["volumeMounts"]


def test_an_offline_directory_alone_is_enough(tmp_path: pathlib.Path) -> None:
    docs = _docs({"global": {"plugins": {**NONE, "offline": "/opt/choregos/plugins"}}}, tmp_path)
    config_map = _config_map(docs)
    assert config_map is not None
    assert yaml.safe_load(config_map["data"]["plugins.yaml"]) == {
        "sources": [],
        "defaultPlugins": [],
        "offline": "/opt/choregos/plugins",
    }


def test_a_source_without_a_trust_policy_does_not_render(tmp_path: pathlib.Path) -> None:
    unsigned = {"name": "acme", "index": "https://acme.example/index.yaml"}
    result = _render({"global": {"plugins": {**NONE, "sources": [unsigned]}}}, tmp_path)
    assert result.returncode != 0
    assert "acme" in result.stderr and "trust" in result.stderr
