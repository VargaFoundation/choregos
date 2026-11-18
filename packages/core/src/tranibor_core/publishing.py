# SPDX-License-Identifier: Apache-2.0
"""The publishing identity: who publishes Tranibor and where (ADR 0156).

Where the images and charts are pushed, where the source and its releases live, where the user
documentation is served, where a vulnerability is reported, which address the agent commits as,
and where the platform's own plugins are published. One file holds it, `publishing.json` next to this
module; every runtime use reads it from here, never a literal of its own. The files that cannot read
it (charts, policies, workflows, Dockerfiles, documentation) are listed, checked and rewritten by
`tools/publishing_identity.py`.

The cosign identities are not stored: GitHub's keyless signing names the workflow that signed,
`<repository>/.github/workflows/<file>@<ref>`, so they follow from the repositories.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from importlib import resources
from typing import Any

#: The OIDC issuer of GitHub Actions, which signs the images, the binaries and the plugins.
SIGNING_ISSUER = "https://token.actions.githubusercontent.com"
#: The workflow of the source repository that signs a release, and the one that publishes plugins.
RELEASE_WORKFLOW = "release.yml"
PLUGIN_WORKFLOW = "publish.yml"
PLUGIN_BRANCH = "refs/heads/main"
#: The images a release builds, signs and pushes, each as `<registry>/<prefix>-<component>`
#: (the `images` matrix of the release workflow).
IMAGE_COMPONENTS = ("api", "orchestrator", "tools", "web", "runner", "laya", "outpost")
#: The charts a release pushes to `oci://<registry>/charts`, by the suffix of their name: the
#: platform's, named by the prefix alone, and the outpost's, `<prefix>-outpost` (ADR 0147), each
#: packaged from its directory.
CHARTS = {"": "charts/choregos", "-outpost": "charts/choregos-outpost"}


@dataclass(frozen=True)
class Publishing:
    #: The GitHub organisation that publishes the source, the images and the plugins.
    owner: str
    #: The image and chart namespace: `<registry>/tranibor-api`, `oci://<registry>/charts/tranibor`.
    registry: str
    #: The name every image and chart starts with: `<registry>/<image_prefix>-api`, the charts
    #: `<image_prefix>` and `<image_prefix>-outpost` (ADR 0176 R3).
    image_prefix: str
    #: The former prefix, published beside `image_prefix` (one digest, two names, both signed) for the
    #: deprecation window of ADR 0176 R3; it goes with the old names, in that round's step (e).
    image_alias_prefix: str
    #: The core's repository: its releases, its issues, its files.
    source_repository: str
    #: The public user documentation, without a trailing slash (`CHOREGOS_DOCS_URL` by default).
    documentation: str
    #: Where a vulnerability is reported (`SECURITY.md`).
    security_contact: str
    #: The address the agent's commits are authored with, unless the deployment sets one.
    commit_email: str
    #: The repository the platform's own plugins are built and signed in.
    plugin_repository: str
    #: The signed index of those plugins: the chart's default source.
    plugin_index: str

    @property
    def releases(self) -> str:
        """The release pages, where the CLI binaries are attached."""
        return f"{self.source_repository}/releases"

    def release(self, version: str) -> str:
        """The page of one release: `<releases>/tag/v<version>`."""
        return f"{self.releases}/tag/v{version.removeprefix('v')}"

    def source_file(self, path: str) -> str:
        """A file of the source repository's main branch, as GitHub shows it."""
        return f"{self.source_repository}/blob/main/{path.lstrip('/')}"

    def image(self, name: str, tag: str | None = None) -> str:
        """An image of the registry: `<registry>/<name>[:<tag>]`."""
        return f"{self.registry}/{name}" + (f":{tag}" if tag else "")

    @property
    def image_prefixes(self) -> tuple[str, str]:
        """The prefixes a release publishes under: the current one first, then the alias."""
        return self.image_prefix, self.image_alias_prefix

    def image_names(self, component: str) -> tuple[str, ...]:
        """Every name a release pushes one image under, `<registry>/<prefix>-<component>`, per prefix."""
        if component not in IMAGE_COMPONENTS:
            raise ValueError(
                f"{component!r} is not an image the release builds: {', '.join(IMAGE_COMPONENTS)}"
            )
        return tuple(self.image(f"{prefix}-{component}") for prefix in self.image_prefixes)

    def chart_names(self, suffix: str = "") -> tuple[str, ...]:
        """Every name a release pushes one chart under (`""` the platform's, `"-outpost"`), per prefix."""
        if suffix not in CHARTS:
            raise ValueError(f"{suffix!r} names no chart of the release: {sorted(CHARTS)}")
        return tuple(f"{prefix}{suffix}" for prefix in self.image_prefixes)

    def signing_identity(self, ref: str, workflow: str = RELEASE_WORKFLOW) -> str:
        """The keyless identity of what a workflow of the source repository signed at `ref`."""
        return f"{self.source_repository}/.github/workflows/{workflow}@{ref}"

    @property
    def plugin_signing_identity(self) -> str:
        """The keyless identity of the platform's plugins: the publish workflow on main."""
        return f"{self.plugin_repository}/.github/workflows/{PLUGIN_WORKFLOW}@{PLUGIN_BRANCH}"


def parse(text: str) -> Publishing:
    """A publishing identity from the text of a `publishing.json`; refuses a missing or empty value."""
    data: dict[str, Any] = json.loads(text)
    names = [field.name for field in fields(Publishing)]
    unknown = sorted(set(data) - set(names) - {"$comment"})
    if unknown:
        raise ValueError(f"publishing.json: unknown key(s) {unknown}")
    values: dict[str, str] = {}
    for name in names:
        value = data.get(name)
        if not isinstance(value, str) or not value.strip() or value != value.strip() or value.endswith("/"):
            raise ValueError(
                f"publishing.json: `{name}` must be a non-empty string, without surrounding spaces "
                "or a trailing slash"
            )
        values[name] = value
    return Publishing(**values)


def load() -> Publishing:
    """The identity this distribution ships with."""
    return parse(resources.files("tranibor_core").joinpath("publishing.json").read_text(encoding="utf-8"))


PUBLISHING = load()
