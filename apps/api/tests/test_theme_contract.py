"""The shape of a theme: what the contract refuses before any colour is measured."""

from __future__ import annotations

from typing import Any

import choregos_contracts as contracts
import pytest
from choregos_api.theme import DEFAULT_THEME, validate_theme

from .theme_fixtures import sample


def _refused(theme: dict[str, Any]) -> str:
    violations = validate_theme(theme)
    assert violations, "this theme must be refused"
    return " | ".join(str(v) for v in violations)


def _font(family: str) -> dict[str, Any]:
    return {"title": {"family": family, "weight": 300, "faces": []}}


def test_a_font_family_cannot_close_the_css_rule() -> None:
    assert "fonts.title.family" in _refused(sample(fonts=_font('Geist";}body{')))


@pytest.mark.parametrize(
    "src",
    [
        "https://evil.example/x.woff2",
        "//evil.example/x.woff2",
        "/api/v1/../x.woff2",
        "/other/x.woff2",
        "/api/v1/x woff2",
        "/api/v1/a);}body{",
    ],
)
def test_an_asset_must_be_on_the_api_of_the_same_origin(src: str) -> None:
    face = {"src": src, "weight": 300, "style": "normal"}
    assert _refused(sample(fonts={"title": {"family": "F", "weight": 300, "faces": [face]}}))
    assert _refused(sample(logo=src))


def test_the_footer_is_at_most_200_characters() -> None:
    assert "footer.text" in _refused(sample(footer={"text": "x" * 201}))


def test_the_product_name_is_1_to_40_characters() -> None:
    assert _refused(sample(product_name=""))
    assert _refused(sample(product_name="x" * 41))


def test_radii_are_bounded() -> None:
    assert _refused(sample(radius={"control": 9}))
    assert _refused(sample(radius={"surface": 13}))
    assert _refused(sample(radius={"control": -1}))


def test_unknown_fields_and_a_missing_mode_are_refused() -> None:
    assert _refused({**sample(), "script": "alert(1)"})
    assert _refused(sample(tokens={"dark": {}}))
    assert _refused(sample(default_mode="sepia"))
    assert validate_theme("not an object")[0].path == "theme"


def test_the_python_model_and_the_openapi_agree_on_the_role_names() -> None:
    from choregos_api.theme import themable_roles

    schema = contracts.load_openapi()["components"]["schemas"]["ThemeTokens"]
    assert tuple(sorted(schema["properties"])) == tuple(sorted(themable_roles()))


def test_the_default_theme_is_valid() -> None:
    assert validate_theme(DEFAULT_THEME) == []
