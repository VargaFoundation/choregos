# SPDX-License-Identifier: Apache-2.0
"""Model resolution (docs/plan/04 §4.2).

An actor asks for `profile:strong`, `profile:by_size` or a direct LiteLLM identifier.
Resolution consults, in order: the project's profiles, then the platform's,
then validates the result (model listed at the gateway, tool calling, compatible backend, policy).
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

# `profile:by_size`: S,M → standard; L → strong; XL → strong with max_turns × 1.5
BY_SIZE_MAP: dict[str, tuple[str, float]] = {
    "S": ("standard", 1.0),
    "M": ("standard", 1.0),
    "L": ("strong", 1.0),
    "XL": ("strong", 1.5),
}

# API format expected by each ACP backend.
BACKEND_API_FORMAT: dict[str, ApiFormat] = {
    "claude-code": ApiFormat.ANTHROPIC,
    "codex": ApiFormat.OPENAI,
    "gemini-cli": ApiFormat.GEMINI,
    "goose": ApiFormat.OPENAI,
    "opencode": ApiFormat.OPENAI,
    "copilot-cli": ApiFormat.OPENAI,
    # An agent written in code (ADR 0063): OpenAI-compatible unless its runtime says `anthropic`.
    "code": ApiFormat.OPENAI,
    # A prompt agent (ADR 0080): the runner's tool loop speaks chat completions.
    "prompt": ApiFormat.OPENAI,
    # A hosted agent (ADR 0080): its container's SDKs reach the loopback model, OpenAI-compatible.
    "hosted": ApiFormat.OPENAI,
}

# Hard constraints of a backend on the model family.
BACKEND_MODEL_CONSTRAINTS: dict[str, tuple[str, ...]] = {
    "claude-code": ("claude", "anthropic"),
}

# The ACP backends the runner image ships, in order of preference, and what each can do.
# `choregos_runner.backends` is the source; it is restated here because the orchestrator and the
# API must be able to pick a backend without depending on the runner package.
KNOWN_BACKEND_CAPABILITIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("claude-code", ("acp", "mcp", "agents_md", "structured_output")),
    ("codex", ("acp", "mcp", "agents_md")),
    ("gemini-cli", ("acp", "mcp")),
    ("goose", ("acp", "mcp")),
    ("opencode", ("acp", "mcp")),
    ("copilot-cli", ("acp",)),
)

# The names alone, in the same order.
KNOWN_BACKEND_NAMES: tuple[str, ...] = tuple(name for name, _ in KNOWN_BACKEND_CAPABILITIES)

#: The one backend enabled when the platform administrator never stored a setting for it.
DEFAULT_ENABLED_BACKEND = "claude-code"


def backend_enabled(name: str, stored: bool | None) -> bool:
    """Whether a coding-agent backend may start a stage (S42-07c).

    `stored` is the `enabled` flag of the platform's `agent_backends` row, `None` when there is
    none (fresh install): then only `claude-code` is on. The API listing, the readiness check and
    the orchestrator all call this, so the console and the enforcement cannot diverge."""
    return stored if stored is not None else name == DEFAULT_ENABLED_BACKEND


#: The backends a runtime fixes, never chosen by rotation (`profile:by_size`, cross-backend review):
#: an agent written in code runs only as the `code` runtime of its version (ADR 0063), a prompt agent
#: only as the runner's tool loop, a hosted agent only as its own container (ADR 0080).
RUNTIME_BACKENDS: tuple[str, ...] = ("code", "prompt", "hosted")


#: What a catalogue entry may serve (ADR 0060): `route` picks a work item's workflow (ADR 0116),
#: `decide` answers a decision step's typed questions (ADR 0121).
USAGES: tuple[str, ...] = ("agent", "embed", "distill", "eval", "route", "decide")
#: The `mode` LiteLLM gives a decision model in `/model/info` (`typesafe/*`, `laya/*`): it answers
#: typed questions on `/v1/systemone` and never chats or calls a tool (ADR 0121).
DECISION_MODE = "evaluation"
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

    def check_decision(self, name: str) -> str | None:
        """The refusal for a decision step's model (ADR 0121 §3), or `None` when it may serve one.

        Stricter than `check`: even in `open` mode the model needs an entry that enables `decide`. A
        decision model answers typed questions with probabilities and a chat model does not, so a
        decision step is never sent to a model nobody declared able to answer one.
        """
        entry = self.entries.get(name)
        if entry is None:
            return (
                f"the model `{name}` has no entry in the organisation's model catalogue: a decision step "
                "needs one with the `decide` usage (/admin/models)"
            )
        return self.check(name, usage="decide")

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
    """Model resolved for a stage, ready to enter a `StageInput`."""

    profile: str
    litellm_model: str
    base_url: str
    api_format: ApiFormat
    params: dict[str, object] = field(default_factory=dict)
    turns_factor: float = 1.0
    validated: bool = True
    warnings: list[str] = field(default_factory=list)
    provider_model: str | None = None
    """The real model behind a `platform/*` alias, as the gateway declares it."""

    def to_ref(self) -> ModelRef:
        return ModelRef(
            litellm_model=self.litellm_model,
            base_url=self.base_url,
            api_format=self.api_format,
            params=dict(self.params),
            provider_model=self.provider_model,
        )


class ModelResolver:
    """Resolves and validates the models for a given project."""

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
        # model → backends validated by EvalMatrix
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
        """Resolves `profile:x`, `profile:by_size` or a direct LiteLLM identifier."""
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
                effective = gateway_model.litellm_model  # a `platform/*` alias hides the real model
                resolved.provider_model = effective
                if not gateway_model.supports_tool_calling:
                    self._reject(
                        resolved, f"the model `{model}` does not support tool calling", allow_unvalidated
                    )
                break
        constraints = BACKEND_MODEL_CONSTRAINTS.get(backend)
        if constraints and effective.startswith("platform/"):
            # An alias that the gateway's catalogue did not resolve hides the real model: the
            # constraint cannot be checked here. Refusing it would make every alias
            # unusable with this backend; we flag it, and the gateway stays the sole judge.
            resolved.warnings.append(
                f"the backend `{backend}` only accepts {' / '.join(constraints)} models; "
                f"`{model}` is an unresolved alias, the constraint could not be checked"
            )
        elif constraints and not any(token in effective.lower() for token in constraints):
            self._reject(
                resolved,
                f"the backend `{backend}` only accepts {' / '.join(constraints)} models "
                f"(got `{model}` → `{effective}`)",
                allow_unvalidated=False,  # hard constraint: never bypassable
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
    """Rough estimate (≈ 4 characters per token) used to bound the context packs."""
    return max(1, (len(text) + 3) // 4)
