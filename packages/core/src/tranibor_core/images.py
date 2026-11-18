# SPDX-License-Identifier: Apache-2.0
"""The images an operator names for jobs and agents written in code (ADR 0056, ADR 0063).

A workflow or an agent version names an image, never pulls one: the operator decides, in a
`name -> reference` map, what may run. The API and the orchestrator read the same maps; the rules
live here so that they cannot drift apart.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

#: A reference that pins its content: `registry/name@sha256:<64 hex>`.
DIGEST_PINNED = re.compile(r"@sha256:[0-9a-f]{64}$")


def is_digest_pinned(reference: str) -> bool:
    return DIGEST_PINNED.search(reference) is not None


def check_image_map(env_name: str, images: Mapping[str, str], *, require_digest: bool) -> None:
    """Refuses a map whose reference is empty or, when asked, not pinned by digest.

    A tag moves: with `require_digest`, what ran yesterday is what runs today, or the platform does
    not start. Raised as `ValueError` so that a settings validator can use it as it is.
    """
    for name, reference in images.items():
        if not reference.strip():
            raise ValueError(f"{env_name}: the image `{name}` has an empty reference")
        if require_digest and not is_digest_pinned(reference):
            raise ValueError(
                f"{env_name}: the image `{name}` is `{reference}`, not pinned by digest "
                "(`@sha256:...`); pin it, or set CHOREGOS_REQUIRE_IMAGE_DIGEST=false for a local setup"
            )


def named_image(images: Mapping[str, str], name: str) -> str | None:
    """The reference an image name maps to, or `None` when the operator did not name it."""
    return images.get(name)
