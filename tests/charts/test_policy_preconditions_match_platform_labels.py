"""A Kyverno rule gated on a pod label applies only if the platform writes that label (#790).

`runtimeclass-gvisor` waited for `choregos/sandbox: gvisor`, a value no chart or executor ever
set, so the rule never applied to anything and looked like a guarantee. Each precondition that
compares a label to a value must name a `key: value` pair written somewhere in the charts or in
the Python sources.
"""

from __future__ import annotations

import pathlib
import re
from collections.abc import Iterator

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
POLICIES = ROOT / "infra" / "policies"
LABEL = re.compile(r'request\.object\.metadata\.labels\."([^"]+)"')
WRITERS = [ROOT / "charts", ROOT / "apps", ROOT / "packages"]


def _label_preconditions() -> Iterator[tuple[str, str, str, str]]:
    for path in sorted(POLICIES.glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if not isinstance(doc, dict) or "spec" not in doc:
                continue
            for rule in doc["spec"].get("rules", []):
                for condition in rule.get("preconditions", {}).get("all", []):
                    label = LABEL.search(str(condition.get("key", "")))
                    if label and condition.get("operator") == "Equals":
                        yield path.name, rule["name"], label.group(1), str(condition["value"])


def _written(label: str, value: str) -> bool:
    pair = re.compile(rf"""["']?{re.escape(label)}["']?\s*[:=]\s*["']?{re.escape(value)}["']?""")
    for root in WRITERS:
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".yaml", ".yml", ".tpl"} or "tests" in path.parts:
                continue
            if pair.search(path.read_text(errors="ignore")):
                return True
    return False


def test_every_label_precondition_names_a_label_the_platform_writes() -> None:
    dead = [
        f"{file} {rule}: {label}={value}"
        for file, rule, label, value in _label_preconditions()
        if not _written(label, value)
    ]
    assert not dead, f"rules gated on a label nothing sets: {dead}"


def test_the_matcher_finds_a_label_the_charts_write() -> None:
    assert _written("choregos/sandbox", "true")
    assert not _written("choregos/sandbox", "gvisor")
