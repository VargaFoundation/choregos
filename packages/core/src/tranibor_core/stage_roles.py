# SPDX-License-Identifier: Apache-2.0
"""A plugin's stage roles: its playbooks, and their names in the contract's open list (ADR 0181, C0).

The workflow schema's `role` was always a free pattern; what a role needs is a playbook, resolved by
its name. A deployment brings its own with `CHOREGOS_PLAYBOOKS_DIR`; an extension brings its plugin's
with `declare_stage_roles`, which adds its directory to the playbooks' search path (after the
deployment's) and declares each role it holds in `tranibor_contracts.STAGE_ROLES`, with the plugin.
"""

from __future__ import annotations

from pathlib import Path

from tranibor_contracts import STAGE_ROLES


def declare_stage_roles(directory: Path, *, plugin: str) -> list[str]:
    """Declare the roles whose playbooks `directory` holds, for `plugin`, and return them."""
    from tranibor_playbooks import declare_roles_dir

    roles = declare_roles_dir(directory, plugin=plugin)
    for role in roles:
        STAGE_ROLES.declare(role, plugin=plugin)
    return roles


def forget_stage_roles(plugin: str) -> None:
    """Drop what `plugin` declared."""
    from tranibor_playbooks import forget_roles_dirs

    forget_roles_dirs(plugin)
    STAGE_ROLES.forget(plugin)
