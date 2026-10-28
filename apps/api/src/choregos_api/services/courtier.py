# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal (a compatibility shim: the former names stay importable until S45)
"""Former name of services/broker.py (ADR 0034, ADR 0046), kept for importers until S45 switches them.

The implementation lives in `broker.py`. This module defines nothing: it re-exports the broker's
names, and each former French name as an alias of its English one.
"""

from __future__ import annotations

from .broker import (
    SEPARATOR,
    TRUNCATED,
    ArgumentsRefused,
    BrokerTool,
    bounded_payload,
    bounded_result,
    broker_tools,
    call,
    call_id,
    check_arguments,
    output_refusal,
    propose_call,
    readable_text,
    result_limit,
    result_text,
    selection_of_spec,
    session_text,
    tools_of_selection,
    write_through_action,
)

OutilDuCourtier = BrokerTool  # lang: fr-ok alias:internal
SEPARATEUR = SEPARATOR  # lang: fr-ok alias:internal
outils_du_courtier = broker_tools  # lang: fr-ok alias:internal
proposer_l_appel = propose_call  # lang: fr-ok alias:internal
identifiant_de_l_appel = call_id  # lang: fr-ok alias:internal
ecrire_par_une_action = write_through_action  # lang: fr-ok alias:internal
ArgumentsRefuses = ArgumentsRefused  # lang: fr-ok alias:internal
verifier_les_arguments = check_arguments  # lang: fr-ok alias:internal
appeler = call  # lang: fr-ok alias:internal
texte_du_resultat = result_text  # lang: fr-ok alias:internal

__all__ = [
    "SEPARATEUR",
    "SEPARATOR",
    "TRUNCATED",
    "ArgumentsRefused",
    "ArgumentsRefuses",
    "BrokerTool",
    "OutilDuCourtier",
    "appeler",
    "bounded_payload",
    "bounded_result",
    "broker_tools",
    "call",
    "call_id",
    "check_arguments",
    "ecrire_par_une_action",
    "identifiant_de_l_appel",
    "outils_du_courtier",
    "output_refusal",
    "propose_call",
    "proposer_l_appel",
    "readable_text",
    "result_limit",
    "result_text",
    "selection_of_spec",
    "session_text",
    "texte_du_resultat",
    "tools_of_selection",
    "verifier_les_arguments",
    "write_through_action",
]
