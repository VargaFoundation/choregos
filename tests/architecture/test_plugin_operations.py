"""The plugin's operations of the REST contract are marked, and the core's frozen set leaves them out
(ADR 0181 point 5, round C2).

ADR 0168 freezes the REST contract on 1.0, and leaves to its REST guard the list of the operations that
are frozen. ADR 0181 classes the release train's operations (the `trains` tag), `getRunDiff` and
`getProjectDora` as the `software-delivery` plugin's: they follow the plugin's versioning, as the plugin
server protocol does. `openapi.yaml` says so on each node, `x-plugin: software-delivery` on the path,
the component or the property, and `frozen_operations()` is the set a REST guard compares from one
release to the next.

The train's code stays in the core until round D (S83-05): only its contract changes owner here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
OPENAPI = ROOT / "packages/contracts/openapi.yaml"
PLUGIN = "software-delivery"
METHODS = {"get", "put", "post", "patch", "delete", "head", "options"}

#: ADR 0181's inventory, REST, class (b) "the plugin's operations": the `trains` tag, `getRunDiff`
#: and `getProjectDora`.
PLUGIN_OPERATIONS = {
    "listReleases",
    "getRelease",
    "getTrainStatus",
    "departTrain",
    "freezeTrain",
    "unfreezeTrain",
    "approveRelease",
    "abortRelease",
    "getRunDiff",
    "getProjectDora",
}
PLUGIN_COMPONENTS = {"Release", "ReleasePage", "TrainStatus", "DoraMetric", "DoraReport", "DiffSummary"}
PLUGIN_PROPERTIES = {("Project", "has_release_train"), ("ProjectStats", "trains_pending")}


def _document() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    return loaded


def operations(document: dict[str, Any]) -> dict[str, str | None]:
    """operationId -> the plugin its path item names, or `None` for the core's."""
    found: dict[str, str | None] = {}
    for item in document["paths"].values():
        for method, operation in item.items():
            if method in METHODS:
                found[operation["operationId"]] = operation.get("x-plugin") or item.get("x-plugin")
    return found


def frozen_operations(document: dict[str, Any]) -> set[str]:
    """The operations a REST guard freezes on 1.0: every one that no plugin owns (ADR 0168 §3)."""
    return {name for name, plugin in operations(document).items() if plugin is None}


def _refs(node: Any, inside_plugin: bool, found: list[tuple[str, bool]]) -> None:
    if isinstance(node, dict):
        inside = inside_plugin or "x-plugin" in node
        if isinstance(node.get("$ref"), str):
            found.append((node["$ref"], inside))
        for value in node.values():
            _refs(value, inside, found)
    elif isinstance(node, list):
        for value in node:
            _refs(value, inside_plugin, found)


def test_the_plugin_s_operations_are_marked_and_out_of_the_frozen_set() -> None:
    document = _document()
    owned = {name for name, plugin in operations(document).items() if plugin is not None}
    assert owned == PLUGIN_OPERATIONS
    assert {plugin for name, plugin in operations(document).items() if plugin} == {PLUGIN}
    frozen = frozen_operations(document)
    assert not frozen & PLUGIN_OPERATIONS
    assert len(frozen) + len(PLUGIN_OPERATIONS) == len(operations(document))
    trains = next(tag for tag in document["tags"] if tag["name"] == "trains")
    assert trains.get("x-plugin") == PLUGIN


def test_the_plugin_s_components_and_fields_are_marked() -> None:
    schemas = _document()["components"]["schemas"]
    assert {name for name, schema in schemas.items() if schema.get("x-plugin")} == PLUGIN_COMPONENTS
    marked = {
        (name, field)
        for name, schema in schemas.items()
        for field, prop in (schema.get("properties") or {}).items()
        if isinstance(prop, dict) and prop.get("x-plugin")
    }
    assert marked == PLUGIN_PROPERTIES


def test_the_core_s_operations_reach_no_component_of_the_plugin() -> None:
    """A frozen operation that returned a plugin's component would freeze it too: every reference to one
    comes from a node of the plugin."""
    found: list[tuple[str, bool]] = []
    _refs(_document(), False, found)
    leaks = sorted(
        ref for ref, inside in found if not inside and ref.rsplit("/", 1)[-1] in PLUGIN_COMPONENTS
    )
    assert leaks == []


def test_the_detector_sees_a_mark_and_its_absence() -> None:
    document = {
        "paths": {
            "/a": {"get": {"operationId": "core"}},
            "/b": {"x-plugin": PLUGIN, "post": {"operationId": "trade"}},
        }
    }
    assert operations(document) == {"core": None, "trade": PLUGIN}
    assert frozen_operations(document) == {"core"}
