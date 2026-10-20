# SPDX-License-Identifier: Apache-2.0
"""OCI images: references, the registry's digest for a tag, and signature verdicts (ADR 0080)."""

from .registry import (
    DOCKER_HUB,
    ImageRef,
    OciRegistryClient,
    RegistryError,
    parse_ref,
    under_prefix,
)
from .signatures import SignatureVerdict, UnconfiguredVerifier

__all__ = [
    "DOCKER_HUB",
    "ImageRef",
    "OciRegistryClient",
    "RegistryError",
    "SignatureVerdict",
    "UnconfiguredVerifier",
    "parse_ref",
    "under_prefix",
]
