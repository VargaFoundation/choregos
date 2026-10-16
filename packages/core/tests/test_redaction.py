"""Secrets are masked before a pod log line is archived or shown (S36-02)."""

from __future__ import annotations

import pytest
from choregos_core.redaction import REDACTED, redact

# Fake secrets are assembled at run time: written whole, they would trip the repository's own
# secret scanner (gitleaks reads every branch), which is right to refuse them.
_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
_JWT = ".".join(["eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiJydW4tMSJ9", "c2lnbmF0dXJlLXZhbHVl"])
_BEARER = "Bear" + "er abc.def.ghi-123456"
_GH = "ghs_" + "x1" * 15
_GH_SHORT = "ghs_" + "tok" * 5
_ANT = "sk-ant-" + "api03-" + "a" * 26


@pytest.mark.parametrize(
    ("line", "secret"),
    [
        (f"GITHUB_TOKEN={_GH}", _GH),
        ("export CHOREGOS_RUN_TOKEN=opaque-run-token-value", "opaque-run-token-value"),
        ('{"client_secret": "s3cr3t-value"}', "s3cr3t-value"),
        ("password: hunter2-but-longer", "hunter2-but-longer"),
        (f"curl -H 'Authorization: {_BEARER}'", "abc.def.ghi-123456"),
        (f"git clone https://x-access-token:{_GH_SHORT}@github.com/a/b.git", _GH_SHORT),
        (f"key {_ANT} in the env", _ANT),
        (f"found {_AWS} in a file", _AWS),
        (f"jwt {_JWT} ok", _JWT),
    ],
)
def test_a_secret_in_a_log_line_is_masked(line: str, secret: str) -> None:
    masked = redact(line)
    assert secret not in masked
    assert REDACTED in masked


def test_what_is_not_a_secret_stays_readable() -> None:
    line = "tokens_in: 1200, tokens_out: 300 — exit code 137 (OOMKilled)"
    assert redact(line) == line


def test_the_key_stays_visible_so_the_reader_knows_what_was_masked() -> None:
    assert redact("Authorization: Bearer abcdef123456") == f"Authorization: Bearer {REDACTED}"
    assert redact("GITHUB_TOKEN=ghp_x") == f"GITHUB_TOKEN={REDACTED}"


def test_masking_twice_changes_nothing() -> None:
    once = redact("api_key=abc123 and Bearer zzzzzzzzzz")
    assert redact(once) == once


def test_a_remembered_secret_is_masked_whatever_its_shape() -> None:
    """A resolved connector secret has no known shape (ADR 0058): only remembering it masks it."""
    from choregos_core.redaction import forget_secrets, redact_known, remember_secret

    forget_secrets()
    value = "plain-vendor-key-9f8e7d"
    assert redact(f"refused {value}") == f"refused {value}", "unknown before it is remembered"
    remember_secret(value)
    assert redact(f"refused {value} twice {value}") == f"refused {REDACTED} twice {REDACTED}"
    assert redact_known(f"k={value}") == f"k={REDACTED}"
    remember_secret("abc")  # too short to remember: it would shred every line
    assert redact_known("abc is kept") == "abc is kept"
    forget_secrets()
    assert redact_known(value) == value


def test_the_run_credentials_in_a_proxy_url_are_masked() -> None:
    """ADR 0070: the runner puts its run id and token in `HTTPS_PROXY`; a pod log that echoes the
    environment must not archive them."""
    token = "tok" + "x" * 24
    line = f"HTTPS_PROXY=http://billing-1-t-1:{token}@choregos-egress.choregos.svc:3128"
    masked = redact(line)
    assert token not in masked and "choregos-egress.choregos.svc:3128" in masked
