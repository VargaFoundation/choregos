# SPDX-License-Identifier: Apache-2.0
"""Every place that reads `public_url` is classified (ADR 0175, S74-03 amendment).

A link the platform builds FOR AN ORGANISATION uses that organisation's verified host when it has one
(`tenant_host.org_base_url`); what the platform stores, registers or emits for the INSTANCE keeps the
canonical `TRANIBOR_PUBLIC_URL`. A new read of `public_url` in the API or the orchestrator fails here until
it is placed in one of the three classes below, with its reason: the choice is made on purpose, in review,
not by default. (Lines that merely hold the settings field's name count: the figure is per file.)
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCES = (ROOT / "apps" / "api" / "src", ROOT / "apps" / "orchestrator" / "src")
#: The seam itself decides the fallback and the scheme: it is the one place allowed to read it freely.
SEAM = "apps/api/src/tranibor_api/tenant_host.py"

#: Per organisation: the link already goes through `org_base_url` / `request_base_url`; what is left is the
#: fallback when the organisation has no verified host, or a parameter's name.
PER_ORGANISATION = {
    "apps/api/src/tranibor_api/mcp/oauth.py": (1, "RFC 9728 `resource`: fallback of `base`, the door's host"),
    "apps/api/src/tranibor_api/mcp/transport.py": (2, "allowed origins + fallback of the 401's `base`"),
    "apps/api/src/tranibor_api/services/conversation_actions.py": (1, "fallback of the `base` argument"),
    "apps/api/src/tranibor_api/services/conversation_launches.py": (1, "fallback of the `base` argument"),
    "apps/orchestrator/src/tranibor_orchestrator/activities/base.py": (1, "`console_base`, the one reader"),
    "apps/orchestrator/src/tranibor_orchestrator/activities/findings.py": (2, "a parameter's name"),
    "apps/orchestrator/src/tranibor_orchestrator/markdown.py": (4, "a parameter's name: `console_base`"),
    "apps/orchestrator/src/tranibor_orchestrator/config.py": (1, "the orchestrator's own setting"),
}

#: The instance's: an identity provider, a provider or the forge registered ONE address, a cookie or an
#: origin is the instance's, or the page is not about one organisation.
INSTANCE = {
    "apps/api/src/tranibor_api/config.py": (3, "the setting itself, its https check"),
    "apps/api/src/tranibor_api/internal_api_guard.py": (3, "the guard's own hosts (public by declaration)"),
    "apps/api/src/tranibor_api/origin_guard.py": (
        2,
        "the instance's origin; a tenant host is added at runtime",
    ),
    "apps/api/src/tranibor_api/routers/auth.py": (3, "OIDC callbacks and redirect targets (S74-02)"),
    "apps/api/src/tranibor_api/routers/cli_downloads.py": (1, "the install script: one address per instance"),
    "apps/api/src/tranibor_api/routers/github_app.py": (
        1,
        "the GitHub App is the instance's (one per forge)",
    ),
    "apps/api/src/tranibor_api/services/github_app_manifest.py": (2, "the App's url and webhook: registered"),
    "apps/api/src/tranibor_api/services/mcp_gateway_identity.py": (
        1,
        "the gateway's `iss`: verbatim, stored",
    ),
    "apps/api/src/tranibor_api/services/agent_catalogue.py": (1, "only a scheme test (https)"),
}

#: Ambiguous, kept canonical ON PURPOSE until the provider side can be registered per host (ADR 0175, S74-03
#: amendment, 'Return URLs'): these end a flow whose callback the provider knows at the instance's address,
#: where a session opened on a tenant host does not exist (host-only cookies, S74-02).
AMBIGUOUS = {
    "apps/api/src/tranibor_api/routers/connected_accounts.py": (1, "return after a consent callback"),
    "apps/api/src/tranibor_api/routers/linked_accounts.py": (1, "return after a forge link callback"),
    "apps/api/src/tranibor_api/routers/remote_credentials.py": (
        1,
        "return after a remote credential callback",
    ),
    "apps/api/src/tranibor_api/services/connected_accounts.py": (
        1,
        "the consent's start page (`connect_url`)",
    ),
    "apps/api/src/tranibor_api/routers/procedures.py": (
        1,
        "a runbook's `<public_url>` placeholder, one value",
    ),
}


def _counts() -> dict[str, int]:
    found: dict[str, int] = {}
    for source in SOURCES:
        for path in source.rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            if relative == SEAM:
                continue
            lines = sum(
                1
                for line in path.read_text(encoding="utf-8").splitlines()
                if re.search(r"\bpublic_url\b", line)
            )
            if lines:
                found[relative] = lines
    return found


def test_every_read_of_public_url_is_classified() -> None:
    expected = {
        path: count
        for table in (PER_ORGANISATION, INSTANCE, AMBIGUOUS)
        for path, (count, _why) in table.items()
    }
    assert _counts() == expected, (
        "a read of `public_url` appeared or disappeared: a link for an organisation goes through "
        "`tenant_host.org_base_url` (ADR 0175, S74-03); an instance-level address stays canonical; "
        "classify it in this file with its reason"
    )


def test_no_file_is_in_two_classes() -> None:
    paths = [*PER_ORGANISATION, *INSTANCE, *AMBIGUOUS]
    assert len(paths) == len(set(paths))
