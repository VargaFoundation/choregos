# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal; former `services.projects`, ADR 0098
"""The former name of `choregos_api.services.projects`: the same module object, so a patch on one
is a patch on the other."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .projects import (
    active_project_slugs,
    config_revision,
    declares_a_release_train,
    first_pass_merge_rate,
    project_dto,
    project_stats,
)

__all__ = [
    "active_project_slugs",
    "config_revision",
    "declares_a_release_train",
    "first_pass_merge_rate",
    "project_dto",
    "project_stats",
]

replace_module(__name__, sys.modules["choregos_api.services.projects"])
