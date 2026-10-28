# SPDX-License-Identifier: Apache-2.0
"""The plugin contract (ADR 0093, S43-01): the three schemas' guards and the six routes.

Contract only: the API serves none of the routes yet (`CONTRACT_ONLY` in
`apps/api/tests/test_contract_conformance.py`), so what is proven here is what the documents say.
"""

from __future__ import annotations

import copy
import json
import pathlib
from typing import Any

import jsonschema
import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "packages" / "contracts"
SPEC: dict[str, Any] = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text())
SCHEMAS = SPEC["components"]["schemas"]
EXAMPLES = CONTRACTS / "schemas" / "examples"


def _schema(name: str) -> jsonschema.Draft202012Validator:
    doc = json.loads((CONTRACTS / "schemas" / f"{name}.schema.json").read_text())
    return jsonschema.Draft202012Validator(doc)


def _example(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((EXAMPLES / f"{name}.example.json").read_text())
    return data


def _errors(name: str, doc: Any) -> list[str]:
    return [e.message for e in _schema(name).iter_errors(doc)]


@pytest.mark.parametrize("name", ["plugin", "plugin-server", "plugin-index"])
def test_the_examples_are_valid(name: str) -> None:
    assert not _errors(name, _example(name))


def test_a_manifest_of_another_kind_is_refused() -> None:
    doc = _example("plugin") | {"kind": "Template"}
    assert _errors("plugin", doc)
    assert _errors("plugin", _example("plugin") | {"apiVersion": "choregos/v2"})


@pytest.mark.parametrize("version", ["1.0", "v1.0.0", "01.0.0", "1.0.0-", "1.0.0+", "latest"])
def test_a_version_that_is_not_semver_is_refused(version: str) -> None:
    doc = copy.deepcopy(_example("plugin"))
    doc["metadata"]["version"] = version
    assert _errors("plugin", doc)


@pytest.mark.parametrize("version", ["0.1.0", "1.2.3-rc.1", "1.2.3+build.5", "10.20.30-alpha-1"])
def test_semver_versions_are_admitted(version: str) -> None:
    doc = copy.deepcopy(_example("plugin"))
    doc["metadata"]["version"] = version
    assert not _errors("plugin", doc)


@pytest.mark.parametrize("name", ["ServiceNow", "-x", "a_b", "a" * 64, ""])
def test_a_bad_name_is_refused(name: str) -> None:
    doc = copy.deepcopy(_example("plugin"))
    doc["metadata"]["name"] = name
    assert _errors("plugin", doc)


@pytest.mark.parametrize("core", ["any", ">=0.21", "^0.21", ">=0.21.0,<0.23.0", "0.21.0"])
def test_core_ranges(core: str) -> None:
    doc = copy.deepcopy(_example("plugin"))
    doc["metadata"]["core"] = core
    assert bool(_errors("plugin", doc)) is (core == "any")


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "../outside.yaml", "agents/../../x.yaml", "agents/./x.yaml", "a//b", "a\\b", "."],
)
def test_a_path_leaving_the_plugin_is_refused(path: str) -> None:
    doc = copy.deepcopy(_example("plugin"))
    doc["contents"]["agents"] = [path]
    assert _errors("plugin", doc)
    doc["contents"] = {"procedures": [path]}
    assert _errors("plugin", doc)


def test_a_wildcard_is_only_for_procedures() -> None:
    doc = copy.deepcopy(_example("plugin"))
    doc["contents"]["agents"] = ["agents/*.yaml"]
    assert _errors("plugin", doc)


def test_an_empty_contents_and_unknown_keys_are_refused() -> None:
    assert _errors("plugin", _example("plugin") | {"contents": {}})
    assert _errors("plugin", _example("plugin") | {"extensions": []})
    doc = copy.deepcopy(_example("plugin"))
    doc["contents"]["workflows"] = ["workflows/x.yaml"]
    assert _errors("plugin", doc)


@pytest.mark.parametrize(
    "image",
    [
        "ghcr.io/vargafoundation/servicenow-mcp:0.1.0",
        "ghcr.io/vargafoundation/servicenow-mcp",
        "ghcr.io/vargafoundation/servicenow-mcp@sha256:abc",
        "ghcr.io/vargafoundation/servicenow-mcp@sha512:" + "0" * 64,
    ],
)
def test_a_server_image_without_a_digest_is_refused(image: str) -> None:
    assert _errors("plugin-server", _example("plugin-server") | {"image": image})


def test_an_operation_without_access_is_refused() -> None:
    doc = copy.deepcopy(_example("plugin-server"))
    del doc["operations"][1]["access"]
    assert _errors("plugin-server", doc)
    doc["operations"][1]["access"] = "admin"
    assert _errors("plugin-server", doc)


def test_a_read_operation_has_no_compensation() -> None:
    doc = copy.deepcopy(_example("plugin-server"))
    doc["operations"][0]["compensation"] = {"operation": "nothing"}
    assert _errors("plugin-server", doc)


def test_a_compensation_names_its_operation_and_arguments_rendered_from_the_result() -> None:
    """ADR 0096: the shape a connector type declares (`OperationSpec.compensation`), so the broker
    attaches it to the governed action as it does for pfSense."""
    doc = copy.deepcopy(_example("plugin-server"))
    resolve = next(o for o in doc["operations"] if o["name"] == "resolve_incident")
    assert resolve["compensation"]["arguments"]["sys_id"] == "{{ result.sys_id }}"
    assert not _errors("plugin-server", doc)
    resolve["compensation"] = "reopen_incident"
    assert _errors("plugin-server", doc), "a bare name says nothing of the arguments: refused"
    resolve["compensation"] = {"operation": "reopen_incident", "arguments": {"sys_id": 3}}
    assert _errors("plugin-server", doc)


def test_platform_arguments_are_the_ones_the_platform_fills() -> None:
    doc = copy.deepcopy(_example("plugin-server"))
    doc["operations"][1]["platform_arguments"] = ["action_key", "work_item_key"]
    assert not _errors("plugin-server", doc)
    doc["operations"][1]["platform_arguments"] = ["tenant_id"]
    assert _errors("plugin-server", doc)


def test_paths_name_where_the_server_answers() -> None:
    doc = _example("plugin-server") | {"paths": {"mcp": "/rpc", "health": "/ready"}}
    assert not _errors("plugin-server", doc)
    assert _errors("plugin-server", doc | {"paths": {"mcp": "rpc"}})
    assert _errors("plugin-server", doc | {"paths": {"metrics": "/metrics"}})


def test_a_server_declares_operations_and_one_kind_of_egress() -> None:
    assert _errors("plugin-server", _example("plugin-server") | {"operations": []})
    both = {"from_fields": ["base_url"], "hosts": ["example.com"]}
    assert _errors("plugin-server", _example("plugin-server") | {"egress": both})
    assert _errors("plugin-server", _example("plugin-server") | {"egress": {}})
    assert _errors("plugin-server", _example("plugin-server") | {"protocol": "stdio"})
    assert _errors("plugin-server", _example("plugin-server") | {"implements": ["scm"]})


def test_an_index_release_is_oci_or_git_pinned_to_a_commit() -> None:
    doc = copy.deepcopy(_example("plugin-index"))
    release = doc["plugins"]["hello"]["versions"]["1.0.0"]
    release["git"]["commit"] = "main"
    assert _errors("plugin-index", doc)
    release["git"]["commit"] = "0" * 40
    release["oci"] = "ghcr.io/x/hello:1.0.0"
    assert _errors("plugin-index", doc)  # both
    del release["oci"], release["git"]
    assert _errors("plugin-index", doc)  # neither
    doc = copy.deepcopy(_example("plugin-index"))
    doc["plugins"]["servicenow"]["versions"]["0.1.0"]["digest"] = "sha256:short"
    assert _errors("plugin-index", doc)
    doc = copy.deepcopy(_example("plugin-index"))
    doc["plugins"]["servicenow"]["versions"] = {"latest": doc["plugins"]["servicenow"]["versions"]["0.1.0"]}
    assert _errors("plugin-index", doc)


def test_the_plugin_operations_and_their_routes() -> None:
    ops = {
        (path, verb): op["operationId"]
        for path, item in SPEC["paths"].items()
        if "plugin" in path
        for verb, op in item.items()
        if verb in {"get", "put", "delete", "post", "patch"}
    }
    assert ops == {
        ("/orgs/{org}/plugins", "get"): "listPlugins",
        ("/orgs/{org}/plugin-sources", "get"): "listPluginSources",
        ("/orgs/{org}/plugins/install", "post"): "installPlugin",
        ("/orgs/{org}/plugins/{name}/upgrade", "post"): "upgradePlugin",
        ("/orgs/{org}/plugins/{name}", "delete"): "uninstallPlugin",
        ("/orgs/{org}/plugin-approvals", "get"): "listPluginApprovals",
        ("/orgs/{org}/plugin-approvals/{approval_id}/approve", "post"): "approvePluginInstall",
        ("/webhooks/plugins/{org}/{plugin}/{server}", "post"): "pluginWebhook",
    }
    assert SPEC["paths"]["/webhooks/plugins/{org}/{plugin}/{server}"]["post"]["security"] == []
    install = SPEC["paths"]["/orgs/{org}/plugins/install"]["post"]["responses"]
    assert {"201", "202", "422"} <= set(install)


@pytest.mark.parametrize(
    "name",
    [
        "PluginContents",
        "InstalledPlugin",
        "PluginList",
        "PluginTrust",
        "PluginSourceEntry",
        "PluginSource",
        "PluginSources",
        "PluginInstall",
        "PluginInstallPending",
        "PluginUpgrade",
        "PluginUpgradeResult",
        "PluginApproval",
        "PluginApprovalList",
        "PluginKept",
        "PluginUninstallResult",
    ],
)
def test_the_api_schemas_are_closed(name: str) -> None:
    assert SCHEMAS[name]["additionalProperties"] is False


def test_the_install_body_names_source_plugin_and_version() -> None:
    assert SCHEMAS["PluginInstall"]["required"] == ["source", "name", "version"]
    assert "key" not in SCHEMAS["PluginTrust"]["properties"]  # a fingerprint, never the key


def test_adr_0093_is_listed() -> None:
    adr = ROOT / "docs" / "adr" / "0093-an-agent-ships-as-a-plugin.md"
    assert "accepted, 2026-10-27" in adr.read_text()
    assert adr.name in (ROOT / "docs" / "adr" / "README.md").read_text()
