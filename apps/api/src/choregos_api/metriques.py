# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal — former module of the Prometheus metrics (ADR 0098)
"""The former name of `choregos_api.metrics`: every former name resolves to the English one."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .metrics import (
    DELIVERY_SECONDS,
    ENVIRONMENTS,
    NAMES,
    START_SECONDS,
    TRAIN_STATES,
    TRAIN_STATUS_QUERY,
    PlatformCollector,
    Snapshot,
    expose,
    http_requests,
    refresh,
    refresh_loop,
    registry,
)

__all__ = ["TRAIN_STATUS_QUERY"]

NOMS = NAMES  # lang: fr-ok alias:internal
SECONDES_DEMARRAGE = START_SECONDS  # lang: fr-ok alias:internal
SECONDES_LIVRAISON = DELIVERY_SECONDS  # lang: fr-ok alias:internal
ENVIRONNEMENTS = ENVIRONMENTS  # lang: fr-ok alias:internal
ETATS_TRAIN = TRAIN_STATES  # lang: fr-ok alias:internal
registre = registry  # lang: fr-ok alias:internal
requetes_http = http_requests  # lang: fr-ok alias:internal
Instantane = Snapshot  # lang: fr-ok alias:internal
CollecteurPlateforme = PlatformCollector  # lang: fr-ok alias:internal
exposer = expose  # lang: fr-ok alias:internal
rafraichir = refresh  # lang: fr-ok alias:internal
boucle_de_rafraichissement = refresh_loop  # lang: fr-ok alias:internal

replace_module(__name__, sys.modules["choregos_api.metrics"])
