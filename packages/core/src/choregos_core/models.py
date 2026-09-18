"""Résolution des modèles (docs/plan/04 §4.2).

Un acteur demande `profile:strong`, `profile:by_size` ou un identifiant LiteLLM direct.
La résolution consulte, dans l'ordre : les profils du projet, ceux de la plateforme,
puis valide le résultat (modèle listé au gateway, tool calling, backend compatible, politique).
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    "openhands": ApiFormat.OPENAI,
    "claude-code": ApiFormat.ANTHROPIC,
    "codex": ApiFormat.OPENAI,
    "gemini-cli": ApiFormat.GEMINI,
    "goose": ApiFormat.OPENAI,
    "opencode": ApiFormat.OPENAI,
    "copilot-cli": ApiFormat.OPENAI,
}

# Contraintes dures d'un backend sur la famille de modèles.
BACKEND_MODEL_CONSTRAINTS: dict[str, tuple[str, ...]] = {
    "claude-code": ("claude", "anthropic"),
}

# Les backends ACP livrés avec l'image du runner, dans l'ordre de préférence.
# `choregos_runner.backends` en est la source ; on la redit ici parce que l'orchestrateur
# et l'API doivent pouvoir choisir un backend sans dépendre du paquet runner.
KNOWN_BACKEND_NAMES: tuple[str, ...] = (
    "openhands",
    "claude-code",
    "codex",
    "gemini-cli",
    "goose",
    "opencode",
    "copilot-cli",
)


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

    def to_ref(self) -> ModelRef:
        return ModelRef(
            litellm_model=self.litellm_model,
            base_url=self.base_url,
            api_format=self.api_format,
            params=dict(self.params),
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
    ) -> None:
        self.gateway_url = gateway_url
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
        backend: str = "openhands",
        allow_unvalidated: bool | None = None,
    ) -> ResolvedModel:
        """Résout `profile:x`, `profile:by_size` ou un identifiant LiteLLM direct."""
        profile_name, turns_factor = self._profile_name(requested, size)
        profile = self._lookup(profile_name, project)
        if profile is None:
            raise ModelResolutionError(
                f"profil de modèle inconnu : `{profile_name}` "
                f"(projet : {sorted((project.models.profiles if project else {}) or {})} ; "
                f"plateforme : {sorted(self.platform_profiles)})"
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
        profile = self.platform_profiles.get(name)
        if profile is not None:
            return profile
        if "/" in name:  # identifiant LiteLLM direct, ex. anthropic/claude-sonnet-5
            return ModelProfile(litellm_model=name)
        return None

    def _validate(self, resolved: ResolvedModel, *, backend: str, allow_unvalidated: bool) -> None:
        model = resolved.litellm_model
        listed = {m.model_name for m in self.gateway_models} | {m.litellm_model for m in self.gateway_models}
        if self.gateway_models and model not in listed:
            self._reject(
                resolved,
                f"le modèle `{model}` n'est pas listé au gateway ({len(self.gateway_models)} modèles connus)",
                allow_unvalidated,
            )
        effective = model
        for gateway_model in self.gateway_models:
            if model in {gateway_model.model_name, gateway_model.litellm_model}:
                effective = gateway_model.litellm_model  # un alias `platform/*` cache le vrai modèle
                if not gateway_model.supports_tool_calling:
                    self._reject(
                        resolved, f"le modèle `{model}` ne gère pas le tool calling", allow_unvalidated
                    )
                break
        constraints = BACKEND_MODEL_CONSTRAINTS.get(backend)
        if constraints and not any(token in effective.lower() for token in constraints):
            self._reject(
                resolved,
                f"le backend `{backend}` n'accepte que des modèles {' / '.join(constraints)} "
                f"(reçu `{model}` → `{effective}`)",
                allow_unvalidated=False,  # contrainte dure : jamais contournable
            )
        validated = self.validated_backends.get(model)
        if validated is not None and backend not in validated:
            self._reject(
                resolved,
                f"combinaison non validée par la matrice d'évals : {backend} × {model} "
                f"(validés : {', '.join(validated) or 'aucun'})",
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
