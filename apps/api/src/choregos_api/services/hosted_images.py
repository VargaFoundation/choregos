# SPDX-License-Identifier: Apache-2.0
"""A hosted agent's image at publication (ADR 0080 §8): allowlisted, pinned, and signed if required.

A person names an image by a reference; the operator names the registries such a reference may come
from (`CHOREGOS_HOSTED_REGISTRIES`). Publication resolves the reference to the digest the registry
serves at that moment and writes it in the version, so every run pulls the same bytes even after the
tag moves. With `CHOREGOS_HOSTED_COSIGN=enforce` an image without a trusted signature is refused;
`warn` publishes it and logs `hosted.signature_unverified`; `off` does not ask. The signature is
cosign's, by the operator's key or keyless identity (`CHOREGOS_HOSTED_COSIGN_KEY`, `_IDENTITY`,
`_ISSUER`), on the pinned digest.
"""

from __future__ import annotations

from typing import Any

from choregos_adapters.base import ImageVerifier, RegistryClient
from choregos_adapters.oci import (
    CosignVerifier,
    ImageRef,
    OciRegistryClient,
    RegistryError,
    UnconfiguredVerifier,
    parse_ref,
    under_prefix,
)
from choregos_adapters.registry import fakes_enabled
from choregos_core.egress import EgressRefusedError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..errors import unprocessable
from ..logging import get_logger
from ..schemas import AgentSpec, HostedImageSource, HostedRuntime, VoiceRuntime

log = get_logger(__name__)

_LOC = ["runtime", "source", "ref"]
#: Tests and `CHOREGOS_FAKES` put their registry and verifier here.
_OVERRIDES: dict[str, Any] = {}


def set_image_services(registry: RegistryClient | None, verifier: ImageVerifier | None = None) -> None:
    """For tests and the fakes: the registry client and the signature verifier publication uses."""
    _OVERRIDES.clear()
    if registry is not None:
        _OVERRIDES["registry"] = registry
    if verifier is not None:
        _OVERRIDES["verifier"] = verifier


def registry_client() -> RegistryClient:
    if "registry" in _OVERRIDES:
        found: RegistryClient = _OVERRIDES["registry"]
        return found
    if fakes_enabled():
        from choregos_adapters.fakes import FakeRegistryClient

        return _OVERRIDES.setdefault("registry", FakeRegistryClient())  # type: ignore[no-any-return]
    from choregos_adapters.http.egress import guarded

    return OciRegistryClient(transport=guarded(None))


def image_verifier() -> ImageVerifier:
    if "verifier" in _OVERRIDES:
        found: ImageVerifier = _OVERRIDES["verifier"]
        return found
    if fakes_enabled():
        from choregos_adapters.fakes import FakeImageVerifier

        return _OVERRIDES.setdefault("verifier", FakeImageVerifier())  # type: ignore[no-any-return]
    settings = get_settings()
    if not (settings.hosted_cosign_key or settings.hosted_cosign_identity or settings.hosted_cosign_issuer):
        return UnconfiguredVerifier()
    try:
        return CosignVerifier(
            key=settings.hosted_cosign_key,
            identity=settings.hosted_cosign_identity,
            issuer=settings.hosted_cosign_issuer,
            tlog=settings.hosted_cosign_tlog,
        )
    except ValueError as exc:
        # A configuration that names a signer wrongly verifies nothing: it never falls back to trust.
        return UnconfiguredVerifier(f"the signature verifier is misconfigured: {exc}")


def allowlisted_ref(source: HostedImageSource) -> ImageRef:
    """The parsed reference when it sits under the operator's allowlist; 422 otherwise. Cheap and
    synchronous: every path that validates a version calls it, before anything is fetched."""
    try:
        ref = parse_ref(source.ref)
    except RegistryError as exc:
        raise unprocessable("the hosted image cannot be used", [{"loc": _LOC, "msg": str(exc)}]) from exc
    allowed = get_settings().hosted_registries
    if not under_prefix(ref, allowed):
        known = ", ".join(allowed) or "none: only zip sources can be hosted here"
        raise unprocessable(
            "the hosted image is not from a registry the operator allows",
            [
                {
                    "loc": _LOC,
                    "msg": f"`{ref.name}` is under no allowed prefix (CHOREGOS_HOSTED_REGISTRIES: {known})",
                }
            ],
        )
    return ref


async def pin_hosted_runtime(session: AsyncSession, org_id: str, spec: AgentSpec) -> AgentSpec:
    """The spec with its image's `digest` set from the registry, after the allowlist and the
    signature policy. A digest the caller wrote is never trusted: it is resolved again. A zip source:
    its `build` is written from the stored zip (`hosted_sources`). A voice agent: refused unless the
    organisation's gateway relays the realtime API. Otherwise: the spec as it is."""
    runtime = spec.runtime
    if isinstance(runtime, VoiceRuntime):
        # Not an image, but the other check of a runtime that needs the organisation (S41-10).
        from .agent_realtime import require_realtime_gateway

        await require_realtime_gateway(session, org_id)
        return spec
    if isinstance(runtime, HostedRuntime) and runtime.source.type == "zip":
        from ..object_store import object_store
        from .hosted_sources import pin_zip_runtime

        return await pin_zip_runtime(object_store(), org_id, spec)
    if not isinstance(runtime, HostedRuntime) or not isinstance(runtime.source, HostedImageSource):
        return spec
    ref = allowlisted_ref(runtime.source)
    from .egress import egress_scope_for_org

    try:
        async with egress_scope_for_org(session, org_id, "api", f"hosted-image:{ref.name}"):
            digest = await registry_client().resolve(str(ref))
    except EgressRefusedError as exc:
        raise unprocessable(str(exc)) from exc
    except RegistryError as exc:
        raise unprocessable("the hosted image cannot be resolved", [{"loc": _LOC, "msg": str(exc)}]) from exc
    pinned = ref.pinned(digest)
    mode = get_settings().hosted_cosign
    if mode != "off":
        verdict = await image_verifier().verify(pinned)
        if not verdict.verified:
            if mode == "enforce":
                raise unprocessable(
                    "the hosted image carries no trusted signature",
                    [{"loc": _LOC, "msg": f"`{pinned}`: {verdict.detail}"}],
                )
            log.warning("hosted.signature_unverified", image=pinned, detail=verdict.detail, org=org_id)
    source = runtime.source.model_copy(update={"digest": digest})
    return spec.model_copy(update={"runtime": runtime.model_copy(update={"source": source})})
