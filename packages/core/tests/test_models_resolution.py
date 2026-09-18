"""Résolution des profils de modèles (§4.2)."""

from __future__ import annotations

import pytest
from choregos_contracts import ProjectConfig, Size
from choregos_core import GatewayModel, ModelResolutionError, ModelResolver, estimate_tokens

GATEWAY = [
    GatewayModel(model_name="platform/standard", litellm_model="anthropic/claude-sonnet-5"),
    GatewayModel(model_name="platform/strong", litellm_model="anthropic/claude-opus-5"),
    GatewayModel(model_name="platform/cheap", litellm_model="anthropic/claude-haiku-4-5"),
    GatewayModel(model_name="platform/aux", litellm_model="vertex_ai/gemini-flash"),
    GatewayModel(model_name="platform/legacy", litellm_model="openai/no-tools", supports_tool_calling=False),
]


def resolver(**kwargs: object) -> ModelResolver:
    return ModelResolver(gateway_url="http://litellm:4000", gateway_models=GATEWAY, **kwargs)  # type: ignore[arg-type]


def project(**models: object) -> ProjectConfig:
    return ProjectConfig.model_validate(
        {
            "slug": "demo",
            "org": "varga",
            "repo": {"url": "https://github.com/varga/demo.git", "default_branch": "main"},
            "models": models or {},
        }
    )


@pytest.mark.parametrize(
    ("size", "expected_profile", "factor"),
    [("S", "standard", 1.0), ("M", "standard", 1.0), ("L", "strong", 1.0), ("XL", "strong", 1.5)],
)
def test_by_size(size: str, expected_profile: str, factor: float) -> None:
    resolved = resolver().resolve("profile:by_size", size=size)
    assert resolved.profile == expected_profile
    assert resolved.turns_factor == factor


def test_project_profile_wins_over_platform() -> None:
    cfg = project(profiles={"standard": "anthropic/claude-sonnet-5"})
    resolved = resolver().resolve("profile:standard", project=cfg, size=Size.M)
    assert resolved.litellm_model == "anthropic/claude-sonnet-5"


def test_direct_litellm_identifier() -> None:
    resolved = resolver().resolve("anthropic/claude-sonnet-5")
    assert resolved.litellm_model == "anthropic/claude-sonnet-5"


def test_unknown_profile_raises() -> None:
    with pytest.raises(ModelResolutionError, match="profil de modèle inconnu"):
        resolver().resolve("profile:licorne")


def test_model_absent_from_gateway_is_refused() -> None:
    with pytest.raises(ModelResolutionError, match="pas listé au gateway"):
        resolver().resolve("openai/inconnu")


def test_model_without_tool_calling_is_refused() -> None:
    with pytest.raises(ModelResolutionError, match="tool calling"):
        resolver().resolve("platform/legacy")


def test_claude_code_requires_claude_model() -> None:
    with pytest.raises(ModelResolutionError, match="claude-code"):
        resolver().resolve("profile:aux", backend="claude-code")
    assert resolver().resolve("profile:standard", backend="claude-code").api_format == "anthropic"


def test_backend_constraint_is_not_bypassable() -> None:
    with pytest.raises(ModelResolutionError):
        resolver().resolve("profile:aux", backend="claude-code", allow_unvalidated=True)


def test_unvalidated_matrix_combination() -> None:
    r = resolver(validated_backends={"platform/strong": ["openhands"]})
    with pytest.raises(ModelResolutionError, match="matrice d'évals"):
        r.resolve("profile:strong", backend="codex")
    permissive = r.resolve("profile:strong", backend="codex", allow_unvalidated=True)
    assert not permissive.validated
    assert permissive.warnings


def test_project_allow_unvalidated_flag() -> None:
    r = resolver(validated_backends={"platform/strong": ["openhands"]})
    cfg = project(allow_unvalidated=True)
    assert not r.resolve("profile:strong", backend="codex", project=cfg).validated


def test_to_ref_and_api_format_per_backend() -> None:
    ref = resolver().resolve("profile:standard", backend="gemini-cli").to_ref()
    assert ref.api_format == "gemini"
    assert ref.base_url == "http://litellm:4000"


def test_estimate_tokens() -> None:
    assert estimate_tokens("") == 1
    assert estimate_tokens("a" * 400) == 100
