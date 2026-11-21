# SPDX-License-Identifier: Apache-2.0
"""Open lists: values the core starts with and an extension extends (ADR 0181, round C0).

Five lists of the contracts were closed enumerations although a trade names their values: the inbound
event types (`scm.pr.opened`), the finding types (`flaky-test`), the memory kinds (`hotspot`), the
connector kinds (`scm`) and the stage roles (`fix_ci`); a sixth, the platform's gestures on a system
transition (`does:`), is `workflow.STEP_EFFECTS`. The schemas hold a pattern for each, and this module
holds the list: the core's values, and the ones an extension declares from its `choregos.plugins`
entry point. A value neither in the core's list nor declared is refused where it was refused before,
so opening a list changes nothing for the values that exist: every current value stays valid, under
the same spelling.

A core value is returned as its enumeration's member (`InboundEventType.PR_OPENED`), so code that
compares by identity keeps working; a declared value is returned as a string.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any

from pydantic import ValidationInfo

from .enums import ConnectorKind, FindingType, InboundEventType, MemoryKind, StageRole
from .window import (
    DELIVERY_CONNECTOR_KINDS,
    DELIVERY_EVENT_TYPES,
    DELIVERY_FINDING_TYPES,
    DELIVERY_MEMORY_KINDS,
    SOFTWARE_DELIVERY,
)

#: The validation context of a document read back from the platform's own storage (`{STORED: True}`).
STORED = "stored"


class OpenList:
    """The core's values of one list, and the ones extensions declared, each with its plugin."""

    def __init__(
        self,
        what: str,
        pattern: str,
        core: Iterable[str],
        enum: type[StrEnum] | None = None,
        kept: Mapping[str, str] | None = None,
    ) -> None:
        self.what = what
        self.pattern = pattern
        self.core: frozenset[str] = frozenset(core)
        #: Values of the core's list that are a plugin's (ADR 0181, round C2): its extension declares
        #: them, and the core keeps them in its list for one window, so that a deployment without the
        #: extension still reads them. Each is on the window list (`tranibor_contracts.window`), with
        #: the release that takes it out of the core's list.
        self.kept: Mapping[str, str] = dict(kept or {})
        self._enum = enum
        self._compiled = re.compile(pattern)
        self._declared: dict[str, str] = {}
        for value in self.core:
            if not self._compiled.fullmatch(value):
                raise ValueError(f"the core's {what} `{value}` does not match {pattern}")
        if not set(self.kept) <= self.core:
            raise ValueError(f"a kept {what} is not in the core's list: {sorted(set(self.kept) - self.core)}")

    def declare(self, value: str, *, plugin: str) -> None:
        """Declare a value for `plugin`. A value of the core may be declared too (a plugin that takes
        it over), and declaring again for the same plugin changes nothing; a value another plugin
        declared is refused, naming it: two plugins cannot mean two things by one name."""
        if not self._compiled.fullmatch(value):
            raise ValueError(f"the {self.what} `{value}` of the plugin `{plugin}` must match {self.pattern}")
        owner = self._declared.get(value)
        if owner is not None and owner != plugin:
            raise ValueError(f"the {self.what} `{value}` is already declared by the plugin `{owner}`")
        self._declared[value] = plugin

    def known(self) -> frozenset[str]:
        return self.core | frozenset(self._declared)

    def declared_by(self, plugin: str) -> frozenset[str]:
        """The values `plugin`'s extension declared."""
        return frozenset(value for value, owner in self._declared.items() if owner == plugin)

    def own(self) -> frozenset[str]:
        """The values the core owns: its list without those it keeps for a plugin."""
        return self.core - frozenset(self.kept)

    def plugin_of(self, value: str) -> str | None:
        """The plugin that declared `value`, else the one the core keeps it for; `None` for a value the
        core owns, or nobody's."""
        return self._declared.get(value) or self.kept.get(value)

    def forget(self, plugin: str) -> None:
        """Drop what `plugin` declared: what a test does after installing an extension."""
        self._declared = {value: owner for value, owner in self._declared.items() if owner != plugin}

    def validate(self, value: Any, info: ValidationInfo) -> Any:
        """The validator of a contract's field (`AfterValidator(LIST.validate)`): `check`, with the
        validation context, which says whether the document is one the platform stored."""
        return self.check(value, info)

    def check(self, value: Any, info: ValidationInfo | None = None) -> Any:
        """The validator of a contract's field: the enumeration's member for a value of the core, the
        string for a declared one; anything else is refused, naming the list.

        A document the platform already stored is read with the context `{"stored": True}`: a value
        that matches the pattern is then read even when no installed extension declares it (ADR 0098
        §4, readers accept). A stored workflow that names a plugin's step effect keeps parsing after
        the plugin is gone, and its Temporal history keeps replaying; the effect itself is then refused
        when it is reached, naming it."""
        text = str(value)
        if text in self.core:
            return self._enum(text) if self._enum is not None else text
        if text in self._declared:
            return text
        if info is not None and (info.context or {}).get(STORED) and self._compiled.fullmatch(text):
            return text
        raise ValueError(
            f"unknown {self.what}: {text!r} (the core's: {', '.join(sorted(self.core))}; an extension "
            f"declares more{', declared: ' + ', '.join(sorted(self._declared)) if self._declared else ''})"
        )


#: `<source>.<object>.<event>`: a tracker's, a source control's, a pipeline's, an alert's.
INBOUND_EVENT_TYPE_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
FINDING_TYPE_PATTERN = r"^[a-z][a-z0-9-]{0,31}$"
MEMORY_KIND_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"
CONNECTOR_KIND_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"
#: The pattern of an agent's `role` in the workflow schema.
STAGE_ROLE_PATTERN = r"^[a-z][a-z0-9_]{1,31}$"

INBOUND_EVENT_TYPES = OpenList(
    "inbound event type",
    INBOUND_EVENT_TYPE_PATTERN,
    (v.value for v in InboundEventType),
    InboundEventType,
    kept=dict.fromkeys(DELIVERY_EVENT_TYPES, SOFTWARE_DELIVERY),
)
FINDING_TYPES = OpenList(
    "finding type",
    FINDING_TYPE_PATTERN,
    (v.value for v in FindingType),
    FindingType,
    kept=dict.fromkeys(DELIVERY_FINDING_TYPES, SOFTWARE_DELIVERY),
)
MEMORY_KINDS = OpenList(
    "memory kind",
    MEMORY_KIND_PATTERN,
    (v.value for v in MemoryKind),
    MemoryKind,
    kept=dict.fromkeys(DELIVERY_MEMORY_KINDS, SOFTWARE_DELIVERY),
)
CONNECTOR_KINDS = OpenList(
    "connector kind",
    CONNECTOR_KIND_PATTERN,
    (v.value for v in ConnectorKind),
    ConnectorKind,
    kept=dict.fromkeys(DELIVERY_CONNECTOR_KINDS, SOFTWARE_DELIVERY),
)
STAGE_ROLES = OpenList("stage role", STAGE_ROLE_PATTERN, (v.value for v in StageRole), StageRole)
