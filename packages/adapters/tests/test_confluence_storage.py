"""Confluence storage format reduced to plain HTML (S39-20): macros keep their text."""

from __future__ import annotations

from choregos_adapters.documents.confluence import confluence_storage_to_html


def test_a_macro_keeps_its_body_and_loses_its_parameters() -> None:
    xhtml = (
        '<ac:structured-macro ac:name="info"><ac:parameter ac:name="title">Heads up</ac:parameter>'
        "<ac:rich-text-body><p>Rotate the key monthly.</p></ac:rich-text-body></ac:structured-macro>"
    )
    out = confluence_storage_to_html(xhtml)
    assert "Rotate the key monthly." in out
    assert "Heads up" not in out and "ac:" not in out


def test_a_link_to_a_page_or_an_attachment_becomes_its_text() -> None:
    out = confluence_storage_to_html(
        '<p>See <ac:link><ri:page ri:content-title="Deploy guide"/></ac:link> and '
        '<ac:link><ri:page ri:content-title="X"/><ac:plain-text-link-body><![CDATA[the guide]]>'
        '</ac:plain-text-link-body></ac:link>, file <ri:attachment ri:filename="spec.pdf"/>.</p>'
    )
    assert "See Deploy guide and the guide" in out and "spec.pdf" in out and "ri:" not in out


def test_a_code_macro_becomes_an_escaped_pre() -> None:
    out = confluence_storage_to_html(
        '<ac:structured-macro ac:name="code"><ac:plain-text-body><![CDATA[a < b && c]]>'
        "</ac:plain-text-body></ac:structured-macro>"
    )
    assert out == "<pre>a &lt; b &amp;&amp; c</pre>"
