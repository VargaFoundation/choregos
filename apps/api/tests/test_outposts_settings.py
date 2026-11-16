"""The deployment's settings for outposts (S65-11): the switch, the door's origin, the door's keys.

`CHOREGOS_OUTPOSTS` is off unless turned on (ADR 0147 §12). The door's origin is what an outpost's
policy file names as `control_plane`. The door signs every call with one Ed25519 key, a secret of the
installation, and announces others beside it for a rotation: their public halves are the `door_keys`
an operator pins (ADR 0148 §13). A value that could not work is refused when the API starts, not
found later by whoever runs an outpost.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from choregos_contracts.outpost import MAX_DOOR_KEYS, PUBLIC_KEY_PATTERN, OutpostPolicy
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .kits import outposts as kit

VARIABLES = (
    "CHOREGOS_OUTPOSTS",
    "CHOREGOS_OUTPOSTS_DOOR_URL",
    "CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY",
    "CHOREGOS_OUTPOSTS_DOOR_ANNOUNCED_KEYS",
)


@pytest.fixture(autouse=True)
def _unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _settings(**values: object) -> object:
    from choregos_api.config import Settings

    return Settings(_env_file=None, **values)  # type: ignore[arg-type, call-arg]


def test_outposts_are_off_and_the_door_has_nothing_until_an_operator_says_so() -> None:
    settings = _settings()
    assert settings.outposts is False  # type: ignore[attr-defined]
    assert settings.outposts_door_origin is None  # type: ignore[attr-defined]
    assert settings.outposts_door_keys == []  # type: ignore[attr-defined]


@pytest.mark.parametrize(("written", "read"), [("on", True), ("off", False), ("true", True), ("0", False)])
def test_the_switch_reads_on_and_off(monkeypatch: pytest.MonkeyPatch, written: str, read: bool) -> None:
    monkeypatch.setenv("CHOREGOS_OUTPOSTS", written)
    assert _settings().outposts is read  # type: ignore[attr-defined]


def test_the_doors_keys_are_the_one_that_signs_then_the_announced_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    door = kit.door_keys()
    monkeypatch.setenv("CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY", door.signing_pem)
    monkeypatch.setenv("CHOREGOS_OUTPOSTS_DOOR_ANNOUNCED_KEYS", f'["{door.announced}"]')
    monkeypatch.setenv("CHOREGOS_OUTPOSTS_DOOR_URL", "https://Outposts.Example.com:443")
    settings = _settings()
    keys = settings.outposts_door_keys  # type: ignore[attr-defined]
    assert keys == [door.signing_public, door.announced], "only the public half of the key that signs"
    assert all(re.fullmatch(PUBLIC_KEY_PATTERN, key) for key in keys)
    assert "PRIVATE" not in "".join(keys)
    origin = settings.outposts_door_origin  # type: ignore[attr-defined]
    assert origin == "https://outposts.example.com", "lower case, the default port left out"
    # What the management API hands an operator is what a policy file accepts as it is.
    policy = OutpostPolicy.model_validate(
        {
            "control_plane": origin,
            "door_keys": keys,
            "endpoints": {},
            "audit": {"file": "/var/log/audit.jsonl"},
        }
    )
    assert policy.door_keys == keys


def test_a_door_with_a_signing_key_alone_pins_one_key() -> None:
    door = kit.door_keys()
    settings = _settings(outposts_door_signing_key=door.signing_pem)
    assert settings.outposts_door_keys == [door.signing_public]  # type: ignore[attr-defined]


def test_announced_keys_alone_are_not_keys_to_pin() -> None:
    """Without the key that signs there is no door to obey: nothing is shown to pin."""
    settings = _settings(outposts_door_announced_keys=[kit.signing_key()])
    assert settings.outposts_door_keys == []  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "origin",
    [
        "https://outposts.example.com/",
        "https://outposts.example.com/outposts/v1",
        "outposts.example.com",
        "https://user@outposts.example.com",
        "https://outposts.example.com?x=1",
        "ftp://outposts.example.com",
    ],
)
def test_a_door_url_that_is_not_an_origin_is_refused(origin: str) -> None:
    with pytest.raises(ValueError, match="CHOREGOS_OUTPOSTS_DOOR_URL is not an origin"):
        _settings(outposts_door_url=origin)


@pytest.mark.parametrize("origin", ["https://outposts.example.com", "http://door.internal.example:8443"])
def test_an_origin_is_what_a_policy_file_takes_as_control_plane(origin: str) -> None:
    assert _settings(outposts_door_url=origin).outposts_door_origin == origin  # type: ignore[attr-defined]


def test_a_signing_key_that_is_not_an_ed25519_private_key_is_refused() -> None:
    other = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    for unusable, why in (
        ("not a key", "not a PEM private key"),
        (kit.signing_key(), "not a PEM private key"),  # a public key, in the contract's spelling
        (other.decode(), "an Ed25519 key is expected"),
    ):
        with pytest.raises(ValueError, match=f"CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY: {why}") as refused:
            _settings(outposts_door_signing_key=unusable)
        assert "MC4CAQAw" not in str(refused.value.errors()[0]["msg"]), "the message quotes no key"


def test_announced_keys_are_public_keys_three_at_most_and_each_once() -> None:
    door = kit.door_keys()
    with pytest.raises(ValueError, match="each entry is a public key"):
        _settings(outposts_door_announced_keys=["tooshort"])
    with pytest.raises(ValueError, match="each entry is a public key"):
        _settings(outposts_door_announced_keys=[door.signing_pem])
    with pytest.raises(ValueError, match=f"at most {MAX_DOOR_KEYS - 1} keys"):
        _settings(outposts_door_announced_keys=[kit.signing_key() for _ in range(MAX_DOOR_KEYS)])
    with pytest.raises(ValueError, match="a key is announced once"):
        _settings(outposts_door_announced_keys=[door.announced, door.announced])
    with pytest.raises(ValueError, match="not announced beside itself"):
        _settings(
            outposts_door_signing_key=door.signing_pem, outposts_door_announced_keys=[door.signing_public]
        )
    full = _settings(
        outposts_door_signing_key=door.signing_pem,
        outposts_door_announced_keys=[kit.signing_key() for _ in range(MAX_DOOR_KEYS - 1)],
    )
    assert len(full.outposts_door_keys) == MAX_DOOR_KEYS  # type: ignore[attr-defined]


def test_the_settings_repeat_the_contracts_shapes() -> None:
    """`config.py` does not import the contract for three constants: they are held to it here."""
    from choregos_api import config

    assert MAX_DOOR_KEYS == config.MAX_DOOR_KEYS
    assert f"^{config.PUBLIC_KEY.pattern}$" == PUBLIC_KEY_PATTERN
    control_plane = OutpostPolicy.model_json_schema()["properties"]["control_plane"]["pattern"]
    assert f"^{config.ORIGIN.pattern}$" == control_plane


def test_the_deployment_guide_names_the_four_settings() -> None:
    guide = (Path(__file__).resolve().parents[3] / "docs" / "deployment.md").read_text(encoding="utf-8")
    assert "## Outposts" in guide
    section = guide.split("## Outposts", 1)[1].split("\n## ", 1)[0]
    missing = [name for name in VARIABLES if f"`{name}`" not in section]
    assert not missing, f"docs/deployment.md, section Outposts, does not name {missing}"
