# SPDX-License-Identifier: Apache-2.0
"""The look of the console, as data (S39-03, ADR 0064).

The community edition serves ONE theme: its default, which is the console's own `theme.css`. A
plugin may provide another through `greffons.declare_theme_provider`; whatever it provides is
validated here — schema, then the console's contrast budget on the EFFECTIVE colours (the console
defaults, overridden by the theme) — before it reaches the console. An invalid theme is never
served: the route answers the default theme instead, so a theme can never break the console.

`validate_theme` is public so that a plugin can run the same check when its operator WRITES a
theme, and refuse it there rather than discover at read time that it falls back.

The budget is the one of `apps/web/tests/theme-contraste.test.ts` (ADR 0043), rule for rule, with
the same thresholds. Changing one without the other makes the console's tests and the API's
disagree: `tests/test_theme_budget.py` pins this copy, the web test pins `theme.css`.
"""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .greffons import theme_provider

logger = structlog.get_logger(__name__)

Mode = Literal["dark", "light"]
MODES: tuple[Mode, ...] = ("dark", "light")

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
ASSET = re.compile(r"^/api/v1/[A-Za-z0-9/_.-]{1,200}$")
FAMILY = re.compile(r"^[A-Za-z0-9 _-]{1,40}$")
THEME_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ORG_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


@lru_cache(maxsize=1)
def load_defaults() -> dict[str, Any]:
    """`{roles, dark, light}`, generated from `theme.css` (`pnpm theme:defaults` in `apps/web`)."""
    data: dict[str, Any] = json.loads((Path(__file__).with_name("theme_defaults.json")).read_text("utf-8"))
    return data


def themable_roles() -> tuple[str, ...]:
    return tuple(load_defaults()["roles"])


@dataclass(frozen=True)
class Violation:
    """One reason a theme is refused: where, and why."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


# ───────────────────────────── the contract, as models ─────────────────────────────


def _asset(value: str) -> str:
    # `..` would pass the character class (dots and slashes are allowed) yet leave /api/v1.
    if not ASSET.match(value) or ".." in value.split("/"):
        raise ValueError("must be a path under /api/v1 (same origin), without '..'")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ThemeFace(_Strict):
    src: str
    weight: int = Field(ge=100, le=900)
    style: Literal["normal", "italic"]

    @field_validator("src")
    @classmethod
    def _asset_ref(cls, value: str) -> str:
        return _asset(value)


class ThemeFont(_Strict):
    family: str
    weight: int = Field(ge=100, le=900)
    faces: list[ThemeFace] = Field(max_length=8)

    @field_validator("family")
    @classmethod
    def _family(cls, value: str) -> str:
        if not FAMILY.match(value):
            raise ValueError("letters, digits, space, '_' and '-' only (1 to 40)")
        return value


class ThemeRadius(_Strict):
    control: int | None = Field(default=None, ge=0, le=8)
    surface: int | None = Field(default=None, ge=0, le=12)


class ThemeFonts(_Strict):
    title: ThemeFont | None = None
    sans: ThemeFont | None = None


class ThemeFooter(_Strict):
    text: str = Field(max_length=200)


class Theme(_Strict):
    id: str
    product_name: str = Field(min_length=1, max_length=40)
    default_mode: Mode
    tokens: dict[Mode, dict[str, str]]
    radius: ThemeRadius | None = None
    fonts: ThemeFonts | None = None
    logo: str | None = None
    logo_dark: str | None = None
    favicon: str | None = None
    footer: ThemeFooter | None = None

    @field_validator("logo", "logo_dark", "favicon")
    @classmethod
    def _asset_ref(cls, value: str | None) -> str | None:
        return None if value is None else _asset(value)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not THEME_ID.match(value):
            raise ValueError("lowercase letters, digits, '_' and '-' (1 to 64)")
        return value

    @field_validator("tokens")
    @classmethod
    def _tokens(cls, value: dict[Mode, dict[str, str]]) -> dict[Mode, dict[str, str]]:
        if set(value) != set(MODES):
            raise ValueError("both 'dark' and 'light' are required (each may be empty)")
        return value


DEFAULT_THEME: dict[str, Any] = {
    "id": "default",
    "product_name": "Choregos",
    "default_mode": "dark",
    "tokens": {"dark": {}, "light": {}},
}


# ───────────────────────────── the contrast budget ─────────────────────────────

_SURFACES = ("surface", "raised", "sidebar", "surface-muted")
_STATUSES = ("running", "waiting", "succeeded", "failed", "retrying", "neutral", "agent")


def _rgb(value: str) -> tuple[float, float, float]:
    return (int(value[1:3], 16) / 255, int(value[3:5], 16) / 255, int(value[5:7], 16) / 255)


def _linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4


def relative_luminance(value: str) -> float:
    red, green, blue = _rgb(value)
    return 0.2126 * _linear(red) + 0.7152 * _linear(green) + 0.0722 * _linear(blue)


def contrast_ratio(first: str, second: str) -> float:
    light, dark = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def effective_tokens(theme: dict[str, Any], mode: Mode) -> dict[str, str]:
    """The console defaults of a mode, overridden by the theme's (hex lower-cased)."""
    tokens: dict[str, str] = dict(load_defaults()[mode])
    for role, value in (theme.get("tokens", {}).get(mode) or {}).items():
        tokens[role] = value.lower()
    return tokens


def _rules() -> list[tuple[str, str, float, str]]:
    """`(text, background, minimum ratio, what)`: the pairs of `theme-contraste.test.ts`."""
    rules: list[tuple[str, str, float, str]] = []
    for text in ("ink-muted", "ink-placeholder", "accent-strong"):
        rules += [(text, surface, 4.5, "secondary text") for surface in _SURFACES]
    rules += [
        ("inverse-ink", "inverse", 4.5, "text on a solid action"),
        ("inverse-ink", "inverse-hover", 4.5, "text on a solid action"),
        ("accent-ink", "accent", 4.5, "text on a solid action"),
        ("accent-ink", "accent-hover", 4.5, "text on a solid action"),
    ]
    for text in ("ok", "warn", "danger"):
        rules += [(text, surface, 4.5, "state text") for surface in _SURFACES]
    for status in _STATUSES:
        rules += [(f"{status}-ink", fill, 4.5, "status text") for fill in (*_SURFACES, f"{status}-soft")]
    for surface in ("surface", "raised"):
        rules += [("line-strong", surface, 3, "control border"), ("line", surface, 1.3, "hairline")]
    rules += [("focus", surface, 3, "focus ring") for surface in _SURFACES]
    return rules


def budget_violations(tokens: dict[str, str], mode: str = "") -> list[str]:
    """What `theme-contraste.test.ts` asserts, on one mode's complete set of colours."""
    out: list[str] = []
    where = f" ({mode})" if mode else ""
    for surface in _SURFACES:
        got = contrast_ratio(tokens["ink"], tokens[surface])
        if got < 7:
            out.append(f"ink on surface{where}: ink on {surface} is {got:.2f}:1, at least 7:1 is required")
        if got > 17:
            out.append(
                f"ink on surface: too hard{where}: ink on {surface} is {got:.2f}:1, at most 17:1 is allowed"
            )
    for text, fill, minimum, what in _rules():
        got = contrast_ratio(tokens[text], tokens[fill])
        if got < minimum:
            out.append(f"{what}{where}: {text} on {fill} is {got:.2f}:1, at least {minimum:g}:1 is required")
    if relative_luminance(tokens["sidebar"]) >= relative_luminance(tokens["surface"]):
        out.append(f"sidebar{where}: must be darker than the page (surface)")
    return out


# ───────────────────────────── validation ─────────────────────────────


def _location(error_loc: tuple[Any, ...]) -> str:
    return ".".join(str(part) for part in error_loc) or "theme"


def validate_theme(data: Any) -> list[Violation]:
    """Every reason `data` is not a servable theme; empty = servable."""
    if not isinstance(data, dict):
        return [Violation("theme", "must be an object")]
    try:
        theme = Theme.model_validate(data)
    except ValidationError as error:
        return [Violation(_location(e["loc"]), e["msg"]) for e in error.errors()]
    violations: list[Violation] = []
    roles = set(themable_roles())
    for mode in MODES:
        for role, value in theme.tokens[mode].items():
            path = f"tokens.{mode}.{role}"
            if role not in roles:
                violations.append(Violation(path, "unknown colour role"))
            elif not HEX.match(value):
                violations.append(Violation(path, "must be #rrggbb"))
    if violations:
        return violations
    for mode in MODES:
        for message in budget_violations(effective_tokens(data, mode), mode):
            violations.append(Violation(f"tokens.{mode}", message))
    return violations


def normalise(data: dict[str, Any]) -> dict[str, Any]:
    """The theme as the contract shows it: validated, without absent optionals."""
    dumped: dict[str, Any] = Theme.model_validate(data).model_dump(mode="json", exclude_none=True)
    return dumped


async def resolve_theme(session: Any, org: str | None) -> tuple[dict[str, Any], str]:
    """`(theme, source)`; `source` is `default`, `provided` or `fallback`. Never raises."""
    provider = theme_provider()
    if provider is None:
        return DEFAULT_THEME, "default"
    try:
        provided = provider(session, org)
        if inspect.isawaitable(provided):
            provided = await provided
    except Exception:
        logger.exception("theme.provider_failed", org=org)
        return DEFAULT_THEME, "fallback"
    if provided is None:
        return DEFAULT_THEME, "default"
    violations = validate_theme(provided)
    if violations:
        logger.error("theme.invalid", org=org, violations=[str(v) for v in violations])
        return DEFAULT_THEME, "fallback"
    return normalise(provided), "provided"
