"""`StageRole` and the playbooks' `KNOWN_ROLES` name the same roles (ADR 0181, C0).

They disagreed on `brief` and `deliver` (ADR 0110), which the playbooks shipped and the enumeration
lacked. `custom` is the one role with no playbook of its own: an agent of role `custom` brings its
instructions (a registry agent, ADR 0033).
"""

from __future__ import annotations

from tranibor_contracts import StageRole
from tranibor_playbooks import KNOWN_ROLES, ROLES_DIR, roles_in


def test_the_enumeration_and_the_playbooks_agree() -> None:
    assert set(KNOWN_ROLES) == {role.value for role in StageRole} - {StageRole.CUSTOM.value}
    assert len(KNOWN_ROLES) == len(set(KNOWN_ROLES))


def test_every_known_role_ships_its_playbook() -> None:
    assert set(roles_in(ROLES_DIR)) == set(KNOWN_ROLES)
