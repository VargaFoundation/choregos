# SPDX-License-Identifier: Apache-2.0
"""OCI images: references, the registry's digest for a tag, signature verdicts and builds (ADR 0080)."""

from .builder import (
    BuildctlBuilder,
    BuildError,
    BuildRequest,
    BuildResult,
    LocalDockerBuilder,
)
from .cosign import CosignRunner, CosignVerifier, cosign_runner
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
    "BuildError",
    "BuildRequest",
    "BuildResult",
    "BuildctlBuilder",
    "CosignRunner",
    "CosignVerifier",
    "ImageRef",
    "LocalDockerBuilder",
    "OciRegistryClient",
    "RegistryError",
    "SignatureVerdict",
    "UnconfiguredVerifier",
    "cosign_runner",
    "parse_ref",
    "under_prefix",
]
