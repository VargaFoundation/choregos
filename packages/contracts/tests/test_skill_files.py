"""The rule that carries a skill's inert binaries as base64 inside `dict[str, str]` (ADR 0050)."""

from __future__ import annotations

import hashlib

import pytest
from choregos_contracts import (
    INERT_BINARY_EXTENSIONS,
    MAX_BINARY_BYTES,
    check_inert_binary,
    empreinte_de_skill,
    encode_binary,
    is_inert_binary_path,
    skill_file_bytes,
)

SAMPLES = {
    ".png": b"\x89PNG\r\n\x1a\nxx",
    ".jpg": b"\xff\xd8\xffxx",
    ".jpeg": b"\xff\xd8\xffxx",
    ".gif": b"GIF89axx",
    ".webp": b"RIFF\x00\x00\x00\x00WEBPxx",
    ".ico": b"\x00\x00\x01\x00xx",
    ".ttf": b"\x00\x01\x00\x00xx",
    ".otf": b"OTTOxx",
    ".woff": b"wOFFxx",
    ".woff2": b"wOF2xx",
}


def test_every_accepted_extension_has_a_sample_and_svg_is_not_one() -> None:
    assert set(SAMPLES) == set(INERT_BINARY_EXTENSIONS)
    assert not is_inert_binary_path("logo.svg")


@pytest.mark.parametrize("extension", SAMPLES)
def test_a_binary_of_its_type_passes_and_one_of_another_type_does_not(extension: str) -> None:
    check_inert_binary(f"a{extension.upper()}", SAMPLES[extension])
    with pytest.raises(ValueError, match="does not look like"):
        check_inert_binary(f"a{extension}", b"%PDF-1.7 not that")


def test_the_cap_is_on_decoded_bytes() -> None:
    check_inert_binary("a.png", SAMPLES[".png"] + b"\0" * (MAX_BINARY_BYTES - len(SAMPLES[".png"])))
    with pytest.raises(ValueError, match="KiB at most"):
        check_inert_binary("a.png", SAMPLES[".png"] + b"\0" * MAX_BINARY_BYTES)


def test_the_extension_alone_decides_text_or_base64() -> None:
    assert skill_file_bytes("notes.md", "base64:abc") == b"base64:abc"
    assert skill_file_bytes("a.png", encode_binary(b"\x00\xff")) == b"\x00\xff"
    for junk in ("not base64!", "AAE", "é"):
        with pytest.raises(ValueError, match="not valid base64"):
            skill_file_bytes("a.png", junk)


def test_the_digest_of_text_is_what_it_always_was_and_binaries_count_by_their_bytes() -> None:
    expected = hashlib.sha256(b"SKILL.md\0hi\0").hexdigest()
    assert empreinte_de_skill({"SKILL.md": "hi"}) == f"sha256:{expected}"
    blob = SAMPLES[".png"]
    expected = hashlib.sha256(b"a.png\0" + blob + b"\0").hexdigest()
    assert empreinte_de_skill({"a.png": encode_binary(blob)}) == f"sha256:{expected}"
