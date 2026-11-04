"""The database layer, the A2A door, egress and knowledge are English (S45-48, ADR 0098).

The persisted audit keys of the A2A door (`ecriture`, `jeton`, the value `refus`) do not change
until S45-39, and the former names of `db.session` still resolve.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType
from typing import Any

import pytest
from httpx import AsyncClient

from .test_a2a_inbound import _ready, _send

ROOT = pathlib.Path(__file__).resolve().parents[3]
SRC = "apps/api/src/choregos_api/"
OWNED = tuple(f"{SRC}{name}/" for name in ("db", "a2a", "egress", "knowledge"))


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english_s45_48", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


def test_subsystems_have_no_french() -> None:
    checker = _checker()
    try:
        found = checker.scan_tree(OWNED)
    except RuntimeError:  # an exported tree without .git: nothing to scan
        pytest.skip("not a git checkout")
    assert [str(f) for f in found] == []


async def test_door_audit_keys_unchanged(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    token = await _ready(client, project)
    await _send(client, token)
    async with session_scope(orgs="*") as session:
        calls = list((await session.execute(select(AuditLog).where(AuditLog.action == "a2a.call"))).scalars())
    assert calls
    for call in calls:
        assert {"ecriture", "jeton", "issue"} <= set(call.payload)  # lang: fr-ok - persisted audit keys


def test_former_session_names_still_import() -> None:
    from choregos_api.db import session
    from choregos_api.db.models import is_core_object, objet_du_coeur  # lang: fr-ok alias:internal

    assert session.TOUT == session.ALL_ORGS == "*"  # lang: fr-ok alias:internal
    assert session.limiter_aux_organisations is session.limit_to_organisations  # lang: fr-ok alias:internal
    assert session.nommer_l_utilisateur is session.name_the_user  # lang: fr-ok alias:internal
    assert session.en_portee_de_plateforme is session.in_platform_scope  # lang: fr-ok alias:internal
    assert objet_du_coeur is is_core_object  # lang: fr-ok alias:internal
