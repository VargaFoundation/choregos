"""An assistant's instructions: checked at publication with samples, rendered at conversation time with the
installation's config and the caller's preferences, never with a sample (S63-27, issue #1737)."""

from __future__ import annotations

import pytest
from choregos_core.instructions import (
    SAMPLE_VARIABLES,
    instruction_errors,
    render_conversation_instructions,
)
from jinja2.exceptions import SecurityError


def test_a_template_that_reads_preferences_publishes() -> None:
    assert "preferences" in SAMPLE_VARIABLES
    assert instruction_errors("Briefs are {{ preferences.brief_length }}.") == []
    assert instruction_errors("{% if preferences.lang == 'fr' %}FR{% endif %}") == []


def test_the_values_are_rendered_and_a_missing_key_renders_empty() -> None:
    rendered = render_conversation_instructions(
        "{{ config.company }} / {{ preferences.brief_length }} / [{{ preferences.language }}]",
        config={"company": "Varga"},
        preferences={"brief_length": "short"},
    )
    assert rendered == "Varga / short / []"


def test_no_sample_reaches_a_conversation() -> None:
    rendered = render_conversation_instructions(
        "{{ ticket.title }}|{{ config.company }}|{{ preferences.x }}", config={}, preferences={}
    )
    assert rendered == "||", "neither the publication samples nor the braces"


def test_a_key_named_like_a_dict_method_is_the_key() -> None:
    rendered = render_conversation_instructions(
        "{{ config.items }} {{ config['keys'] }}", config={"items": "five", "keys": "two"}, preferences={}
    )
    assert rendered == "five two"


def test_the_conversation_sandbox_still_refuses_python() -> None:
    with pytest.raises(SecurityError):
        render_conversation_instructions(
            "{{ config.__class__.__mro__ }}{{ ''.__class__.__subclasses__() }}", config={}, preferences={}
        )


def test_an_empty_preferences_is_falsy_in_a_condition() -> None:
    source = "{% if preferences %}known{% else %}new{% endif %}"
    assert render_conversation_instructions(source, config={}, preferences={}) == "new"
    assert render_conversation_instructions(source, config={}, preferences={"a": 1}) == "known"
