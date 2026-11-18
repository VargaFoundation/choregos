"""The Temporal connection every process shares: TLS settings and the AES-256-GCM codec (ADR 0183)."""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag
from temporalio.api.common.v1 import Payload
from temporalio.api.failure.v1 import Failure
from temporalio.converter import DataConverter, DefaultFailureConverterWithEncodedAttributes
from temporalio.exceptions import ApplicationError
from temporalio.service import TLSConfig
from tranibor_core.temporal_connection import (
    ENCRYPTED_ENCODING,
    KEY_ID_METADATA,
    AesGcmCodec,
    TemporalConfigError,
    TemporalSecurity,
    UnknownCodecKeyError,
    data_converter,
    parse_codec_keys,
    tls_config,
)

TICKET = "HR-42: salary review for Jane Doe, 81 500 EUR"


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def _ring(*ids: str) -> tuple[tuple[str, bytes], ...]:
    return parse_codec_keys(",".join(f"{key_id}:{_key()}" for key_id in ids))


def _payloads(value: object) -> list[Payload]:
    return list(DataConverter.default.payload_converter.to_payloads([value]))


# --- configuration -------------------------------------------------------------------------------


def test_nothing_configured_is_clear_text_and_the_default_converter() -> None:
    security = TemporalSecurity.from_env({})
    assert security == TemporalSecurity()
    assert tls_config(security) is False
    assert data_converter(security) is DataConverter.default


def test_tls_files_reach_the_tls_config(tmp_path: Path) -> None:
    (tmp_path / "ca.crt").write_bytes(b"CA")
    (tmp_path / "tls.crt").write_bytes(b"CERT")
    (tmp_path / "tls.key").write_bytes(b"KEY")
    security = TemporalSecurity.from_env(
        {
            "CHOREGOS_TEMPORAL_TLS_CA_FILE": str(tmp_path / "ca.crt"),
            "CHOREGOS_TEMPORAL_TLS_CERT_FILE": str(tmp_path / "tls.crt"),
            "CHOREGOS_TEMPORAL_TLS_KEY_FILE": str(tmp_path / "tls.key"),
            "CHOREGOS_TEMPORAL_TLS_SERVER_NAME": "frontend.temporal",
        }
    )
    config = tls_config(security)
    assert config == TLSConfig(
        server_root_ca_cert=b"CA", client_cert=b"CERT", client_private_key=b"KEY", domain="frontend.temporal"
    )


def test_the_switch_alone_turns_tls_on_with_the_system_roots() -> None:
    assert tls_config(TemporalSecurity.from_env({"CHOREGOS_TEMPORAL_TLS": "true"})) == TLSConfig()
    assert tls_config(TemporalSecurity.from_env({"CHOREGOS_TEMPORAL_TLS": "false"})) is False


@pytest.mark.parametrize(
    ("env", "named"),
    [
        ({"CHOREGOS_TEMPORAL_TLS_CERT_FILE": "/c"}, "go together"),
        ({"CHOREGOS_TEMPORAL_TLS_CA_FILE": "/does/not/exist"}, "CHOREGOS_TEMPORAL_TLS_CA_FILE"),
        ({"CHOREGOS_TEMPORAL_CODEC_KEYS": "k1:abc", "CHOREGOS_TEMPORAL_CODEC_KEYS_FILE": "/k"}, "not both"),
        ({"CHOREGOS_TEMPORAL_CODEC_KEYS": "k1"}, "expected `id:base64`"),
        ({"CHOREGOS_TEMPORAL_CODEC_KEYS": "k1:%%%"}, "not valid base64"),
        ({"CHOREGOS_TEMPORAL_CODEC_KEYS": "k1:" + base64.b64encode(b"short").decode()}, "32 bytes"),
    ],
)
def test_a_configuration_that_cannot_work_is_refused(env: dict[str, str], named: str) -> None:
    with pytest.raises(TemporalConfigError, match=named):
        TemporalSecurity.from_env(env)


def test_a_key_id_listed_twice_is_refused() -> None:
    key = _key()
    with pytest.raises(TemporalConfigError, match="listed twice"):
        parse_codec_keys(f"k1:{key},k1:{key}")


def test_the_keys_come_from_a_mounted_file(tmp_path: Path) -> None:
    keys = tmp_path / "keys"
    keys.write_text(f"k2:{_key()},k1:{_key()}\n")
    security = TemporalSecurity.from_env({"CHOREGOS_TEMPORAL_CODEC_KEYS_FILE": str(keys)})
    assert [key_id for key_id, _ in security.codec_keys] == ["k2", "k1"]
    converter = data_converter(security)
    assert isinstance(converter.payload_codec, AesGcmCodec) and converter.payload_codec.key_id == "k2"
    assert converter.failure_converter_class is DefaultFailureConverterWithEncodedAttributes


# --- the codec -----------------------------------------------------------------------------------


async def test_encoded_payloads_carry_no_plaintext_and_name_their_key() -> None:
    codec = AesGcmCodec(_ring("k1"))
    plain = _payloads({"ticket": TICKET})
    (sealed,) = await codec.encode(plain)
    assert TICKET.encode() not in sealed.SerializeToString()
    assert sealed.metadata["encoding"] == ENCRYPTED_ENCODING
    assert sealed.metadata[KEY_ID_METADATA] == b"k1"
    assert await codec.decode([sealed]) == plain


async def test_rotation_the_new_key_encrypts_and_the_old_one_still_decrypts() -> None:
    old = _ring("k1")
    (before,) = await AesGcmCodec(old).encode(_payloads(TICKET))
    rotated = AesGcmCodec((*_ring("k2"), *old))
    (after,) = await rotated.encode(_payloads(TICKET))
    assert after.metadata[KEY_ID_METADATA] == b"k2"
    assert await rotated.decode([before, after]) == _payloads(TICKET) * 2


async def test_a_payload_under_a_key_no_longer_listed_is_refused_by_name() -> None:
    (sealed,) = await AesGcmCodec(_ring("k1")).encode(_payloads(TICKET))
    with pytest.raises(UnknownCodecKeyError, match="`k1`"):
        await AesGcmCodec(_ring("k2")).decode([sealed])


async def test_a_payload_relabelled_under_another_key_id_does_not_decrypt() -> None:
    """The key id is the associated data: moving a payload under another id breaks the tag."""
    keys = _ring("k1")
    same_key = (("k2", keys[0][1]), *keys)
    (sealed,) = await AesGcmCodec(same_key).encode(_payloads(TICKET))
    sealed.metadata[KEY_ID_METADATA] = b"k1"
    with pytest.raises(InvalidTag):
        await AesGcmCodec(same_key).decode([sealed])


async def test_a_clear_payload_decodes_as_it_is() -> None:
    """Histories begun before the codec was switched on stay readable."""
    plain = _payloads({"ticket": TICKET})
    assert await AesGcmCodec(_ring("k1")).decode(plain) == plain


def test_a_codec_without_keys_is_refused() -> None:
    with pytest.raises(TemporalConfigError):
        AesGcmCodec(())


async def test_a_failure_message_goes_through_the_codec_too() -> None:
    async def serialized(converter: DataConverter) -> bytes:
        failure = Failure()
        await converter.encode_failure(ApplicationError(TICKET), failure)
        return failure.SerializeToString()

    assert TICKET.encode() not in await serialized(data_converter(TemporalSecurity(codec_keys=_ring("k1"))))
    assert TICKET.encode() in await serialized(DataConverter.default)  # the control: in clear it is there
