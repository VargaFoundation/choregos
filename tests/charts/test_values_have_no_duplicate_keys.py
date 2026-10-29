# SPDX-License-Identifier: Apache-2.0
"""A values file says each key once (S39-06e).

`charts/choregos/values.yaml` carried `global.secrets` twice: the `envPattern` block, then the KEK
block. A YAML loader keeps the last one, so `global.secrets.envPattern` was silently dropped from the
defaults the chart documents. Nothing broke only because its default was empty. A loader that
refuses a repeated key makes the next such merge red.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
VALUES = sorted(p for p in (ROOT / "charts").rglob("values*.yaml") if "/tests/" not in p.as_posix())


class _Strict(yaml.SafeLoader):
    """A SafeLoader that refuses a mapping key seen twice at the same level."""


def _mapping(loader: _Strict, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)  # type: ignore[no-untyped-call]
        if key in seen:
            raise yaml.constructor.ConstructorError(None, None, f"key {key!r} repeated", key_node.start_mark)
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_Strict.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def test_there_are_values_files() -> None:
    assert ROOT / "charts/choregos/values.yaml" in VALUES


@pytest.mark.parametrize("path", VALUES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_no_key_is_repeated(path: pathlib.Path) -> None:
    for document in yaml.load_all(path.read_text(encoding="utf-8"), Loader=_Strict):
        assert document is None or isinstance(document, dict)


def test_the_secrets_block_keeps_both_settings() -> None:
    secrets = yaml.safe_load((ROOT / "charts/choregos/values.yaml").read_text())["global"]["secrets"]
    assert {"envPattern", "keksSecretRef", "keksKey", "keksInExternalSecret"} <= set(secrets)
