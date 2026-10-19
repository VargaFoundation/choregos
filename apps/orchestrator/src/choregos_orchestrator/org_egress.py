# SPDX-License-Identifier: Apache-2.0
"""The hosts an organisation opens for its runners (S39-22d, ADR 0066).

A rule of the organisation's egress allowlist whose `used_by` contains `runners` joins the project
policy's `allow_domains` for every run of the organisation: the runner's own guard, the egress
proxy the provisioning renders and (with remote agents) the admission check all read the union.
Since ADR 0070 (amended) they join a run as *implied* hosts of its effective network
(`activities/run_network.py`): the deny lists of the instance, the organisation's ceiling and the
project still win over them. An administrator's "allow `a2a.example.test` for the remote agent"
therefore works in every project without editing each policy. Read in activities only: nothing
under `workflows/` changes.
"""

from __future__ import annotations

from collections.abc import Iterable

# One implementation, in the API, which also serves them to the egress proxy (ADR 0070, amended).
from choregos_api.services.egress import org_runner_hosts, runner_domain


def merged_domains(project: Iterable[str], organisation: Iterable[str]) -> list[str]:
    """The project's own allowlist, then what the organisation opened, sorted and without doubles."""
    return sorted({*project, *organisation})


__all__ = ["merged_domains", "org_runner_hosts", "runner_domain"]
