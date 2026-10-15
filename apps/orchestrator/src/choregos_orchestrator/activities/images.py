# SPDX-License-Identifier: Apache-2.0
"""The images the operator named for job steps (ADR 0056) and agents written in code (ADR 0063)."""

from __future__ import annotations

from typing import Any, Literal

from choregos_core.images import named_image as lookup
from temporalio.exceptions import ApplicationError

_SOURCES = {
    "job": ("job_images", "CHOREGOS_JOB_IMAGES"),
    "agent": ("agent_images", "CHOREGOS_AGENT_IMAGES"),
}


def named_image(settings: Any, kind: Literal["job", "agent"], name: str | None) -> str | None:
    """The image reference a name maps to; `None` for no name runs the step in the runner image.

    An unknown name stops the step without a retry: the mapping will not change between attempts,
    and running the step in another image than the one written would be a silent substitution.
    The error type is `<kind>_image_unknown`.
    """
    if name is None:
        return None
    attribute, env_name = _SOURCES[kind]
    images: dict[str, str] = dict(getattr(settings, attribute, {}) or {})
    reference = lookup(images, name)
    if reference is None:
        known = ", ".join(sorted(images)) or "none"
        raise ApplicationError(
            f"unknown {kind} image `{name}` ({env_name} knows: {known})",
            type=f"{kind}_image_unknown",
            non_retryable=True,
        )
    return reference
