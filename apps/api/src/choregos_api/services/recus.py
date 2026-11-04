# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal; former `services.receipts`, ADR 0098
"""The former name of `choregos_api.services.receipts`: the same module object, so a patch on one
is a patch on the other."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .receipts import (
    DOCUMENTS_DU_PROMPT,
    LUE,
    PRODUCED,
    PRODUITE,
    PROMPT_DOCUMENTS,
    READ,
    consigner,
    digest,
    empreinte,
    lu_par_le_prompt,
    passages_du_ticket,
    read_by_the_prompt,
    record,
    record_digests,
    work_item_handoffs,
)

__all__ = [
    "DOCUMENTS_DU_PROMPT",
    "LUE",
    "PRODUCED",
    "PRODUITE",
    "PROMPT_DOCUMENTS",
    "READ",
    "consigner",
    "digest",
    "empreinte",
    "lu_par_le_prompt",
    "passages_du_ticket",
    "read_by_the_prompt",
    "record",
    "record_digests",
    "work_item_handoffs",
]

replace_module(__name__, sys.modules["choregos_api.services.receipts"])
