"""A neutral sample theme for the tests. Never a real brand: the community edition is public."""

from __future__ import annotations

import copy
from typing import Any

SAMPLE_THEME: dict[str, Any] = {
    "id": "sample",
    "product_name": "Sample",
    "default_mode": "light",
    "tokens": {
        "light": {
            "accent": "#0b6e4f",
            "accent-hover": "#095a40",
            "accent-strong": "#0b6e4f",
            "surface": "#f7f9f8",
            "sidebar": "#eef2f0",
            "focus": "#0b6e4f",
        },
        "dark": {
            "accent": "#1f7a5a",
            "accent-hover": "#18654a",
            "accent-strong": "#5fd3a6",
            "focus": "#5fd3a6",
        },
    },
    "radius": {"control": 2, "surface": 4},
    "fonts": {
        "title": {
            "family": "Sample Serif",
            "weight": 300,
            "faces": [{"src": "/api/v1/theme-assets/sample-serif.woff2", "weight": 300, "style": "normal"}],
        }
    },
    "logo": "/api/v1/theme-assets/logo.svg",
    "favicon": "/api/v1/theme-assets/favicon.svg",
    "footer": {"text": "Operated by Sample"},
}


def sample(**overrides: Any) -> dict[str, Any]:
    theme = copy.deepcopy(SAMPLE_THEME)
    theme.update(overrides)
    return theme
