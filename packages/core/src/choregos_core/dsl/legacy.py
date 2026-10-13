# SPDX-License-Identifier: Apache-2.0
"""What the core templates declared before every declared output became required (S31-02).

Until then `default-simple`, `full-auto` and `advanced` declared outputs that nothing produces:
`spec` (the platform reads `spec_markdown`), `plan` (`plan_markdown`), `branch` and `commits` (the
runner measures them as artifacts), `duplicate_of` (empty when there is no duplicate),
`allowed_paths` (an optional refinement). The templates are fixed; the copies projects already
store are not rewritten by that fix. Two mechanisms keep them working:

- a stored copy equal to an old core template, its metadata aside, is upgraded to the fixed
  declarations by a data migration (`upgrade_core_copy`);
- a copy a person modified is left alone, a warning names its legacy declarations, and the engine
  does not require those outputs — exactly as before (`legacy_outputs`).
"""

from __future__ import annotations

import copy
import re
from typing import Any

from choregos_contracts import Workflow

#: (transition id) → outputs an old core template declared that nothing produces.
LEGACY_UNPRODUCED_OUTPUTS: dict[str, frozenset[str]] = {
    "t-triage": frozenset({"duplicate_of"}),
    "t-refine": frozenset({"spec", "allowed_paths"}),
    "t-plan": frozenset({"plan"}),
    "t-implement": frozenset({"branch", "commits"}),
}

#: The declarations each core template carried before the fix: {template: {transition: {field: list}}}.
OLD_DECLARATIONS: dict[str, dict[str, dict[str, list[str]]]] = {
    "default-simple": {
        "t-refine": {"outputs": ["spec", "allowed_paths", "size", "risk"]},
        "t-implement": {"inputs": ["spec", "allowed_paths"], "outputs": ["branch", "commits"]},
    },
    "full-auto": {
        "t-triage": {"outputs": ["size", "risk", "duplicate_of"]},
        "t-refine": {"outputs": ["spec", "allowed_paths"]},
        "t-implement": {"inputs": ["spec", "allowed_paths"]},
    },
    "advanced": {
        "t-triage": {"outputs": ["size", "risk", "duplicate_of"]},
        "t-refine": {"outputs": ["spec", "allowed_paths"]},
        "t-plan": {"inputs": ["spec"], "outputs": ["plan"]},
        "t-implement": {"inputs": ["spec", "plan", "allowed_paths"], "outputs": ["branch", "commits"]},
    },
}


def legacy_outputs(transition_id: str | None, outputs: list[str]) -> list[str]:
    """The declared outputs of a transition that an old core template declared and nothing
    produces: the engine does not require them (S31-02), as it never did."""
    legacy = LEGACY_UNPRODUCED_OUTPUTS.get(transition_id or "", frozenset())
    return [name for name in outputs if name in legacy]


def _canonical(document: dict[str, Any]) -> dict[str, Any]:
    """A workflow as the platform stores it, its metadata aside (a project's copy renames it)."""
    dumped = Workflow.model_validate(document).model_dump(mode="json", by_alias=True, exclude_none=True)
    dumped.pop("metadata", None)
    return dumped


def old_template(name: str) -> dict[str, Any]:
    """The JSON of a core template as it was before the fix: today's, with the old declarations."""
    from . import load_template

    document = load_template(name).model_dump(mode="json", by_alias=True, exclude_none=True)
    old = copy.deepcopy(document)
    for transition in old.get("transitions", []):
        for field, values in OLD_DECLARATIONS[name].get(transition.get("id", ""), {}).items():
            transition[field] = list(values)
    return old


def _rewrite_yaml(text: str, name: str, new: dict[str, Any]) -> str | None:
    """The stored YAML with each old declaration replaced in its transition's block, comments kept;
    `None` when a declaration is not written the way the template wrote it."""
    new_by_id = {t.get("id"): t for t in new.get("transitions", [])}
    for transition_id, fields in OLD_DECLARATIONS[name].items():
        start = re.search(rf"^(\s*)- id: {re.escape(transition_id)}\s*$", text, re.M)
        if start is None:
            return None
        end = re.search(r"^\s*- id: ", text[start.end() :], re.M)
        stop = start.end() + (end.start() if end else len(text[start.end() :]))
        block = text[start.end() : stop]
        for field, values in fields.items():
            line = re.compile(rf"^(\s*){field}: \[{re.escape(', '.join(values))}\]\s*\n", re.M)
            if not line.search(block):
                return None
            replacement = new_by_id.get(transition_id, {}).get(field)
            block = line.sub(
                (lambda m, r=replacement, f=field: f"{m.group(1)}{f}: [{', '.join(r)}]\n")
                if replacement
                else "",
                block,
                count=1,
            )
        text = text[: start.end()] + block + text[stop:]
    return text


def upgrade_core_copy(json_doc: dict[str, Any], yaml_text: str) -> tuple[str, dict[str, Any], str] | None:
    """A stored copy of a core template, upgraded to the fixed declarations: `(yaml, json, template)`,
    or `None` when the copy is not, metadata aside, an old core template — a modified copy is never
    rewritten."""
    from . import load_template

    try:
        stored = _canonical(json_doc)
    except Exception:
        return None
    for name in OLD_DECLARATIONS:
        if stored != _canonical(old_template(name)):
            continue
        new = load_template(name).model_dump(mode="json", by_alias=True, exclude_none=True)
        new["metadata"] = json_doc.get("metadata", new.get("metadata"))
        rewritten = _rewrite_yaml(yaml_text, name, new)
        if rewritten is None:
            import yaml

            rewritten = yaml.safe_dump(new, sort_keys=False, allow_unicode=True)
        return rewritten, new, name
    return None


def legacy_declarations(json_doc: dict[str, Any]) -> list[str]:
    """`transition: output` for each legacy declaration of a stored workflow, for the warning."""
    found: list[str] = []
    for transition in json_doc.get("transitions", []) or []:
        for name in legacy_outputs(transition.get("id"), list(transition.get("outputs") or [])):
            found.append(f"{transition.get('id')}: {name}")
    return found
