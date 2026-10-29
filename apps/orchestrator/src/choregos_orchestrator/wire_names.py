# SPDX-License-Identifier: Apache-2.0
"""The French names on the wire, read under both spellings (S45-03, ADR 0098 §4).

Some payload keys and error types were born French. They are recorded in Temporal histories (what
an activity returned, what a workflow sent) and persisted in rows (an event's payload, an action's
result): a workflow in flight, a replayed history and an old row keep them forever. Every reader
accepts the English spelling and the former one; the writers switch to English one release later
(S45-39), once a version that reads both is deployed everywhere.

Pure standard library: workflow code imports it, like `workflows.activity_names`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: English -> former French payload key. Recorded in histories and persisted rows (ADR 0098 §4):
#: read forever.
FORMER_PAYLOAD_KEYS: dict[str, str] = {  # lang: fr-ok
    "effects": "effets",  # lang: fr-ok
    "status": "statut",  # lang: fr-ok
    "states": "etats",  # lang: fr-ok
    "await_evidence": "attendre_une_preuve",  # lang: fr-ok
    "until": "jusqu_a",  # lang: fr-ok
    "waiting": "en_attente",  # lang: fr-ok
    "evidence": "preuve",  # lang: fr-ok
    "conclude": "conclure",  # lang: fr-ok
    "key": "cle",  # lang: fr-ok
    "refusal": "refus",  # lang: fr-ok
}

#: English -> former French error type (`ApplicationError.type`). Recorded in histories as the
#: type of an activity's failure, and read by the workflows' retry policies and refusal sets.
FORMER_ERROR_TYPES: dict[str, str] = {  # lang: fr-ok
    "AgentUnavailable": "AgentIndisponible",  # lang: fr-ok
    "InvalidInstructions": "InstructionsInvalides",  # lang: fr-ok
    "SkillMissing": "SkillAbsente",  # lang: fr-ok
    "EffectRefused": "EffetRefuse",  # lang: fr-ok
    "ActionNotFound": "ActionIntrouvable",  # lang: fr-ok
    "ActionNotYetVisible": "ActionPasEncoreVisible",  # lang: fr-ok
}


def read_key(payload: Mapping[str, Any], english: str, default: Any = None) -> Any:
    """`payload[english]` when the key is there, else the value under its former French spelling,
    else `default`. The English key wins when both are present.

    `english` must be a key of `FORMER_PAYLOAD_KEYS`: a typo would otherwise read nothing,
    silently. Any other key is read with a plain `.get`.
    """
    former = FORMER_PAYLOAD_KEYS[english]
    if english in payload:
        return payload[english]
    return payload.get(former, default)


def require_key(payload: Mapping[str, Any], english: str) -> Any:
    """`read_key` for a key the reader cannot do without: with neither spelling present it raises
    the `KeyError` of the direct read it replaces (`payload['<former>']`), so that what a workflow
    or an activity raises on a malformed payload does not change (S45-03)."""
    former = FORMER_PAYLOAD_KEYS[english]
    if english in payload:
        return payload[english]
    return payload[former]


def both_names(*english: str) -> list[str]:
    """Each English error type followed by its former French one, for a set of error types that
    must match what a history recorded before S45-39 as well as what is raised after."""
    return [name for each in english for name in (each, FORMER_ERROR_TYPES[each])]


__all__ = ["FORMER_ERROR_TYPES", "FORMER_PAYLOAD_KEYS", "both_names", "read_key", "require_key"]
