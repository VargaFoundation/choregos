"""The documentation site is built and published from this repository (ADR 0185).

The publishing identity says where the documentation lives; the Docusaurus project and the workflow must
agree with it, or the console's Help link points at a site that nothing publishes.
"""

from __future__ import annotations

import json
import pathlib
import re
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parents[2]
WEBSITE = ROOT / "website"
WORKFLOW = ROOT / ".github" / "workflows" / "docs-site.yml"
IDENTITY = json.loads((ROOT / "packages/core/src/choregos_core/publishing.json").read_text("utf-8"))


def test_the_site_is_served_where_the_publishing_identity_says() -> None:
    """The Docusaurus config reads the identity: it states no address of its own."""
    config = (WEBSITE / "docusaurus.config.ts").read_text("utf-8")
    assert "publishing.json" in config and "identity.documentation" in config
    for value in IDENTITY.values():
        if value.startswith("https://") and value != IDENTITY["documentation"]:
            assert value not in config, f"the config spells {value}: it reads the identity"
    assert "graalsystems" not in config and "northwind" not in config.lower()


def test_the_documentation_is_not_hosted_by_a_third_party() -> None:
    host = urlparse(IDENTITY["documentation"]).hostname or ""
    owner = IDENTITY["owner"]
    assert host == f"{owner}.github.io", f"{host} is not the GitHub Pages of {owner}"
    assert urlparse(IDENTITY["documentation"]).path == "/" + IDENTITY["source_repository"].rsplit("/", 1)[1]


def test_the_site_reads_the_pages_of_this_repository() -> None:
    assert (ROOT / "docs" / "site" / "index.md").is_file()
    assert (WEBSITE / "package-lock.json").is_file(), "the workflow installs with `npm ci`"
    script = (WEBSITE / "scripts" / "import-docs.mjs").read_text("utf-8")
    assert "publishing.json" in script and "source_repository" in script
    assert "graalsystems" not in script


def test_the_workflow_builds_on_pull_requests_and_publishes_from_main_and_releases() -> None:
    text = WORKFLOW.read_text("utf-8")
    assert "pull_request:" in text and "branches: [main]" in text and "release:" in text
    assert "actions/deploy-pages@" in text and "id-token: write" in text
    # The publishing job never runs for a pull request.
    assert re.search(r"deploy:\n\s+if: github\.event_name != 'pull_request'", text)
