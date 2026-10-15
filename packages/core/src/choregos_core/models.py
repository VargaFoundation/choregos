# SPDX-License-Identifier: Apache-2.0
"""Résolution des modèles (docs/plan/04 §4.2).

Un acteur demande `profile:strong`, `profile:by_size` ou un identifiant LiteLLM direct.
La résolution consulte, dans l'ordre : les profils du projet, ceux de la plateforme,
puis valide le résultat (modèle listé au gateway, tool calling, backend compatible, politique).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from choregos_contracts import ApiFormat, ModelProfile, ModelRef, ProjectConfig, Size

from .domain import GatewayModel
from .errors import ModelResolutionError

PROFILE_PREFIX = "profile:"
BY_SIZE = "profile:by_size"

PLATFORM_PROFILES: dict[str, ModelProfile] = {
    "strong": ModelProfile(litellm_model="platform/strong", params={"temperature": 0}),
    "standard": ModelProfile(litellm_model="platform/standard", params={"temperature": 0}),
    "cheap": ModelProfile(litellm_model="platform/cheap", params={"temperature": 0}),
    "aux": ModelProfile(litellm_model="platform/aux"),
    "embed": ModelProfile(litellm_model="platform/embed"),
    "local": ModelProfile(litellm_model="platform/local"),
}

# `profile:by_size` : S,M → standard ; L → strong ; XL → strong avec max_turns × 1.5
BY_SIZE_MAP: dict[str, tuple[str, float]] = {
    "S": ("standard", 1.0),
    "M": ("standard", 1.0),
    "L": ("strong", 1.0),
    "XL": ("strong", 1.5),
}

# Format d'API attendu par chaque backend ACP.
BACKEND_API_FORMAT: dict[str, ApiFormat] = {
    "claude-code": ApiFormat.ANTHROPIC,
    "codex": ApiFormat.OPENAI,
    "gemini-cli": ApiFormat.GEMINI,
    "goose": ApiFormat.OPENAI,
    "opencode": ApiFormat.OPENAI,
    "copilot-cli": ApiFormat.OPENAI,
    # An agent written in code (ADR 0063): OpenAI-compatible unless its runtime says `anthropic`.
    "code": ApiFormat.OPENAI,
}

# Contraintes dures d'un backend sur la famille de modèles.
BACKEND_MODEL_CONSTRAINTS: dict[str, tuple[str, ...]] = {
    "claude-code": ("claude", "anthropic"),
}

# Les backends ACP livrés avec l'image du runner, dans l'ordre de préférence.
# `choregos_runner.backends` en est la source ; on la redit ici parce que l'orchestrateur
# et l'API doivent pouvoir choisir un backend sans dépendre du paquet runner.
KNOWN_BACKEND_NAMES: tuple[str, ...] = (
    "claude-code",
    "codex",
    "gemini-cli",
    "goose",
    "opencode",
    "copilot-cli",
)


#: The backends a runtime fixes, never chosen by rotation (`profile:by_size`, cross-backend review):
#: an agent written in code runs only as the `code` runtime of its version (ADR 0063).
RUNTIME_BACKENDS: tuple[str, ...] = ("code",)


USAGES: tuple[str, ...] = ("agent", "embed", "distill", "eval")
TIERS: tuple[str, ...] = ("cheap", "standard", "strong", "aux", "embed", "local")


@dataclass(slots=True)
class CatalogueEntry:
    """What the organisation decided about one model (ADR 0060)."""

    enabled: bool = True
    allowed_roles: tuple[str, ...] = ()
    usages: tuple[str, ...] = ("agent",)
    disabled_by: str = ""


@dataclass(slots=True)
class ModelPolicy:
    """The organisation's model catalogue, as the resolver reads it.

    `open` (the default) lets through anything the gateway lists, unless an entry says it is
    disabled; `curated` lets through only what an entry enables. Roles and usages restrict an
    entry that exists; a model without entry is free in `open` and refused in `curated`.
    """

    mode: str = "open"
    entries: dict[str, CatalogueEntry] = field(default_factory=dict)
    tiers: dict[str, str | None] = field(default_factory=dict)

    def check(
        self, name: str, provider_model: str | None = None, *, role: str | None = None, usage: str = "agent"
    ) -> str | None:
        """The text of the refusal, or `None` when the model may be used for this role and usage."""
        # The alias first, then the model behind it: disabling either one refuses.
        for candidate in dict.fromkeys(n for n in (name, provider_model) if n):
            entry = self.entries.get(candidate)
            if entry is None:
                if self.mode == "curated" and candidate == name and not self._covered(name, provider_model):
                    return (
                        f"the model `{candidate}` is not in the organisation's model catalogue "
                        "(curated mode: ask an administrator to enable it in /admin/models)"
                    )
                continue
            if not entry.enabled:
                who = f" by {entry.disabled_by}" if entry.disabled_by else ""
                return f"the model `{candidate}` is disabled{who} in the organisation's model catalogue"
            if usage not in entry.usages:
                return (
                    f"the model `{candidate}` is not enabled for the usage `{usage}` "
                    f"(enabled for: {', '.join(entry.usages) or 'nothing'})"
                )
            if role is not None and entry.allowed_roles and role not in entry.allowed_roles:
                return (
                    f"the model `{candidate}` is not allowed for the role `{role}` "
                    f"(allowed: {', '.join(entry.allowed_roles)})"
                )
        return None

    def _covered(self, name: str, provider_model: str | None) -> bool:
        """An alias with no entry of its own is covered when the model behind it has one."""
        return bool(provider_model) and provider_model in self.entries and provider_model != name

    def tier_model(self, tier: str) -> str | None:
        return self.tiers.get(tier) or None


@dataclass(slots=True)
class ResolvedModel:
    """Modèle résolu pour une étape, prêt à entrer dans un `StageInput`."""

    profile: str
    litellm_model: str
    base_url: str
    api_format: ApiFormat
    params: dict[str, object] = field(default_factory=dict)
    turns_factor: float = 1.0
    validated: bool = True
    warnings: list[str] = field(default_factory=list)
    provider_model: str | None = None
    """Le modèle réel derrière un alias `platform/*`, tel que le gateway le déclare."""

    def to_ref(self) -> ModelRef:
        return ModelRef(
            litellm_model=self.litellm_model,
            base_url=self.base_url,
            api_format=self.api_format,
            params=dict(self.params),
            provider_model=self.provider_model,
        )


class ModelResolver:
    """Résout et valide les modèles pour un projet donné."""

    def __init__(
        self,
        *,
        gateway_url: str,
        platform_profiles: dict[str, ModelProfile] | None = None,
        gateway_models: list[GatewayModel] | None = None,
        validated_backends: dict[str, list[str]] | None = None,
        catalogue: ModelPolicy | None = None,
    ) -> None:
        self.gateway_url = gateway_url
        self.catalogue = catalogue
        self.platform_profiles = {**PLATFORM_PROFILES, **(platform_profiles or {})}
        self.gateway_models = gateway_models or []
        # modèle → backends validés par EvalMatrix
        self.validated_backends = validated_backends or {}

    def resolve(
        self,
        requested: str,
        *,
        project: ProjectConfig | None = None,
        size: Size | str | None = None,
        backend: str = "claude-code",
        allow_unvalidated: bool | None = None,
        role: str | None = None,
        usage: str = "agent",
    ) -> ResolvedModel:
        """Résout `profile:x`, `profile:by_size` ou un identifiant LiteLLM direct."""
        profile_name, turns_factor = self._profile_name(requested, size)
        profile = self._lookup(profile_name, project)
        if profile is None:
            raise ModelResolutionError(
                f"unknown model profile: `{profile_name}` "
                f"(project: {sorted((project.models.profiles if project else {}) or {})}; "
                f"platform: {sorted(self.platform_profiles)})"
            )
        api_format = BACKEND_API_FORMAT.get(backend, ApiFormat.OPENAI)
        resolved = ResolvedModel(
            profile=profile_name,
            litellm_model=profile.litellm_model,
            base_url=self.gateway_url,
            api_format=api_format,
            params=dict(profile.params),
            turns_factor=turns_factor * profile.max_turns_factor,
        )
        permissive = allow_unvalidated
        if permissive is None:
            permissive = project.models.allow_unvalidated if project else False
        self._validate(resolved, backend=backend, allow_unvalidated=permissive)
        self._check_catalogue(resolved, role=role, usage=usage)
        return resolved

    # ───────────────────────── interne ─────────────────────────

    def _profile_name(self, requested: str, size: Size | str | None) -> tuple[str, float]:
        if requested == BY_SIZE:
            name, factor = BY_SIZE_MAP.get(str(size or Size.M), ("standard", 1.0))
            return name, factor
        if requested.startswith(PROFILE_PREFIX):
            return requested.removeprefix(PROFILE_PREFIX), 1.0
        return requested, 1.0  # identifiant LiteLLM direct

    def _lookup(self, name: str, project: ProjectConfig | None) -> ModelProfile | None:
        if project is not None:
            profile = project.models.profiles.get(name)
            if profile is not None:
                return profile
        tier_model = self.catalogue.tier_model(name) if self.catalogue is not None else None
        if tier_model is not None:  # the organisation's tier: between the project and the platform
            return ModelProfile(
                litellm_model=tier_model,
                **self._platform_defaults(name),  # params of the platform tier
            )
        profile = self.platform_profiles.get(name)
        if profile is not None:
            return profile
        if "/" in name:  # identifiant LiteLLM direct, ex. anthropic/claude-sonnet-5
            return ModelProfile(litellm_model=name)
        return None

    def _platform_defaults(self, name: str) -> dict[str, Any]:
        platform = self.platform_profiles.get(name)
        return (
            {"params": dict(platform.params), "max_turns_factor": platform.max_turns_factor}
            if platform
            else {}
        )

    def _check_catalogue(self, resolved: ResolvedModel, *, role: str | None, usage: str) -> None:
        """The organisation's catalogue: never bypassed by `allow_unvalidated`, which is about the
        eval matrix and the gateway, not about what the organisation allows."""
        if self.catalogue is None:
            return
        refusal = self.catalogue.check(
            resolved.litellm_model, resolved.provider_model, role=role, usage=usage
        )
        if refusal is not None:
            raise ModelResolutionError(refusal)

    def _validate(self, resolved: ResolvedModel, *, backend: str, allow_unvalidated: bool) -> None:
        model = resolved.litellm_model
        listed = {m.model_name for m in self.gateway_models} | {m.litellm_model for m in self.gateway_models}
        if self.gateway_models and model not in listed:
            self._reject(
                resolved,
                f"the model `{model}` is not listed by the gateway ({len(self.gateway_models)} models known)",
                allow_unvalidated,
            )
        effective = model
        for gateway_model in self.gateway_models:
            if model in {gateway_model.model_name, gateway_model.litellm_model}:
                effective = gateway_model.litellm_model  # un alias `platform/*` cache le vrai modèle
                resolved.provider_model = effective
                if not gateway_model.supports_tool_calling:
                    self._reject(
                        resolved, f"the model `{model}` does not support tool calling", allow_unvalidated
                    )
                break
        constraints = BACKEND_MODEL_CONSTRAINTS.get(backend)
        if constraints and effective.startswith("platform/"):
            # Un alias que le catalogue du gateway n'a pas résolu cache le vrai modèle : la
            # contrainte ne peut pas être vérifiée ici. La refuser rendrait tout alias
            # inutilisable avec ce backend ; on le signale, et le gateway reste seul juge.
            resolved.warnings.append(
                f"the backend `{backend}` only accepts {' / '.join(constraints)} models; "
                f"`{model}` is an unresolved alias, the constraint could not be checked"
            )
        elif constraints and not any(token in effective.lower() for token in constraints):
            self._reject(
                resolved,
                f"the backend `{backend}` only accepts {' / '.join(constraints)} models "
                f"(got `{model}` → `{effective}`)",
                allow_unvalidated=False,  # contrainte dure : jamais contournable
            )
        validated = self.validated_backends.get(model)
        if validated is not None and backend not in validated:
            self._reject(
                resolved,
                f"combination not validated by the eval matrix: {backend} × {model} "
                f"(validated: {', '.join(validated) or 'none'})",
                allow_unvalidated,
            )

    @staticmethod
    def _reject(resolved: ResolvedModel, message: str, allow_unvalidated: bool) -> None:
        if allow_unvalidated:
            resolved.validated = False
            resolved.warnings.append(message)
            return
        raise ModelResolutionError(message)


def estimate_tokens(text: str) -> int:
    """Estimation grossière (≈ 4 caractères par token) utilisée pour borner les context packs."""
    return max(1, (len(text) + 3) // 4)
