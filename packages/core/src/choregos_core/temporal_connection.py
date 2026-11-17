# SPDX-License-Identifier: Apache-2.0
"""The one way a Choregos process connects to Temporal: TLS and the payload codec (ADR 0183).

Every client and every worker of a deployment must agree on the data converter: a payload encrypted
by the API's client is decrypted by the worker that runs the workflow, and the reverse. Before this
module each process called `Client.connect(address, namespace=...)` on its own, in clear, with the
default converter (S80-06). Now they all call `connect_temporal`, which reads one configuration:

- TLS (`CHOREGOS_TEMPORAL_TLS`, `..._TLS_CA_FILE`, `..._TLS_CERT_FILE`, `..._TLS_KEY_FILE`,
  `..._TLS_SERVER_NAME`): the CA that signs the frontend's certificate, and for mutual TLS the
  client's certificate and key. A file named turns TLS on; a certificate without its key is refused.
- The payload codec (`CHOREGOS_TEMPORAL_CODEC_KEYS`, or `..._CODEC_KEYS_FILE` naming a file that
  holds the same text): `id:base64,id:base64`, 32-byte AES keys, the FIRST encrypts, all decrypt.
  The key id travels in each payload's metadata, so a key can rotate: put the new one first, keep
  the old one listed until no open history carries it.

Off by default (dev, kind, compose): an unconfigured process connects in clear with the default
converter, which is exactly what it did before, so the archived histories replay unchanged.

Phase 1 holds one key for every organisation of the deployment. Per-organisation keys from a KMS
are phase 2 (ADR 0183).
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from temporalio.api.common.v1 import Payload
from temporalio.client import Client
from temporalio.converter import DataConverter, DefaultFailureConverterWithEncodedAttributes, PayloadCodec
from temporalio.service import TLSConfig

ENV_TLS = "CHOREGOS_TEMPORAL_TLS"
ENV_TLS_CA_FILE = "CHOREGOS_TEMPORAL_TLS_CA_FILE"
ENV_TLS_CERT_FILE = "CHOREGOS_TEMPORAL_TLS_CERT_FILE"
ENV_TLS_KEY_FILE = "CHOREGOS_TEMPORAL_TLS_KEY_FILE"
ENV_TLS_SERVER_NAME = "CHOREGOS_TEMPORAL_TLS_SERVER_NAME"
ENV_CODEC_KEYS = "CHOREGOS_TEMPORAL_CODEC_KEYS"
ENV_CODEC_KEYS_FILE = "CHOREGOS_TEMPORAL_CODEC_KEYS_FILE"

#: The encoding an encrypted payload carries; the codec leaves every other payload as it found it.
ENCRYPTED_ENCODING = b"binary/encrypted"
#: The metadata key naming the key that encrypted the payload.
KEY_ID_METADATA = "encryption-key-id"
KEY_BYTES = 32
NONCE_BYTES = 12
KEY_ID = re.compile(r"[A-Za-z0-9_-]{1,32}")
TRUE = frozenset({"1", "true", "yes", "on"})


class TemporalConfigError(ValueError):
    """The Temporal connection is configured in a way that cannot work: the process must not start."""


class UnknownCodecKeyError(Exception):
    """A payload was encrypted under a key this process does not hold (rotated out too early)."""


@dataclass(frozen=True)
class TemporalSecurity:
    """What a process needs to talk to Temporal safely: TLS material and the codec's keys."""

    tls: bool = False
    ca_cert: bytes | None = None
    client_cert: bytes | None = None
    client_key: bytes | None = None
    server_name: str | None = None
    #: (key id, key), the first one encrypts.
    codec_keys: tuple[tuple[str, bytes], ...] = ()

    @property
    def encrypts(self) -> bool:
        return bool(self.codec_keys)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> TemporalSecurity:
        values = os.environ if env is None else env
        ca_file = values.get(ENV_TLS_CA_FILE, "").strip()
        cert_file = values.get(ENV_TLS_CERT_FILE, "").strip()
        key_file = values.get(ENV_TLS_KEY_FILE, "").strip()
        if bool(cert_file) != bool(key_file):
            raise TemporalConfigError(
                f"{ENV_TLS_CERT_FILE} and {ENV_TLS_KEY_FILE} go together: mutual TLS needs both"
            )
        tls = values.get(ENV_TLS, "").strip().lower() in TRUE or bool(ca_file or cert_file)
        inline = values.get(ENV_CODEC_KEYS, "").strip()
        keys_file = values.get(ENV_CODEC_KEYS_FILE, "").strip()
        if inline and keys_file:
            raise TemporalConfigError(f"{ENV_CODEC_KEYS} and {ENV_CODEC_KEYS_FILE}: set one, not both")
        keyring = _read(keys_file, ENV_CODEC_KEYS_FILE).decode().strip() if keys_file else inline
        return cls(
            tls=tls,
            ca_cert=_read(ca_file, ENV_TLS_CA_FILE) if ca_file else None,
            client_cert=_read(cert_file, ENV_TLS_CERT_FILE) if cert_file else None,
            client_key=_read(key_file, ENV_TLS_KEY_FILE) if key_file else None,
            server_name=values.get(ENV_TLS_SERVER_NAME, "").strip() or None,
            codec_keys=parse_codec_keys(keyring),
        )


def _read(path: str, variable: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise TemporalConfigError(f"{variable}={path}: {exc.strerror or exc}") from None


def parse_codec_keys(text: str) -> tuple[tuple[str, bytes], ...]:
    """`id:base64,id:base64` → ((id, key), ...); empty text, no key. Refuses a malformed keyring."""
    keys: list[tuple[str, bytes]] = []
    for entry in (part.strip() for part in text.split(",")):
        if not entry:
            continue
        key_id, _, encoded = entry.partition(":")
        if not KEY_ID.fullmatch(key_id) or not encoded:
            raise TemporalConfigError(
                f"{ENV_CODEC_KEYS}: expected `id:base64`, with an id of [A-Za-z0-9_-]{{1,32}}"
            )
        try:
            key = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise TemporalConfigError(f"{ENV_CODEC_KEYS}: the key `{key_id}` is not valid base64") from None
        if len(key) != KEY_BYTES:
            raise TemporalConfigError(
                f"{ENV_CODEC_KEYS}: the key `{key_id}` must be 32 bytes, not {len(key)}"
            )
        if any(key_id == known for known, _ in keys):
            raise TemporalConfigError(f"{ENV_CODEC_KEYS}: the key id `{key_id}` is listed twice")
        keys.append((key_id, key))
    return tuple(keys)


def tls_config(security: TemporalSecurity) -> TLSConfig | bool:
    """What `Client.connect(tls=...)` takes: `False` in clear, a `TLSConfig` otherwise."""
    if not security.tls:
        return False
    return TLSConfig(
        server_root_ca_cert=security.ca_cert,
        domain=security.server_name,
        client_cert=security.client_cert,
        client_private_key=security.client_key,
    )


def data_converter(security: TemporalSecurity) -> DataConverter:
    """The converter every client, worker and replayer of the deployment shares.

    Without keys, the SDK's default, unchanged. With keys, the default plus the AES-256-GCM codec,
    and failures whose message and stack trace go through the codec too: a failed activity's message
    can quote a ticket as easily as its result can.
    """
    if not security.encrypts:
        return DataConverter.default
    return dataclasses.replace(
        DataConverter.default,
        payload_codec=AesGcmCodec(security.codec_keys),
        failure_converter_class=DefaultFailureConverterWithEncodedAttributes,
    )


async def connect_temporal(
    address: str, namespace: str, *, security: TemporalSecurity | None = None
) -> Client:
    """Connect to Temporal the deployment's way (ADR 0183). `security` defaults to the environment."""
    resolved = TemporalSecurity.from_env() if security is None else security
    return await Client.connect(
        address,
        namespace=namespace,
        tls=tls_config(resolved),
        data_converter=data_converter(resolved),
    )


class AesGcmCodec(PayloadCodec):
    """AES-256-GCM over each whole serialized payload; the key id rides in the metadata.

    The nonce is random per payload (12 bytes, prefixed to the ciphertext); the key id is the
    associated data, so a payload cannot be passed off as encrypted under another key. A payload that
    is not encrypted is decoded as it is: histories begun before the codec was switched on stay
    readable, and so does a workflow started by a process that has no key yet.
    """

    def __init__(self, keys: Sequence[tuple[str, bytes]]) -> None:
        if not keys:
            raise TemporalConfigError("the payload codec needs at least one key")
        self._encrypting_id = keys[0][0]
        self._ciphers = {key_id: AESGCM(key) for key_id, key in keys}

    @property
    def key_id(self) -> str:
        return self._encrypting_id

    async def encode(self, payloads: Sequence[Payload]) -> list[Payload]:
        cipher = self._ciphers[self._encrypting_id]
        key_id = self._encrypting_id.encode()
        encoded: list[Payload] = []
        for payload in payloads:
            nonce = os.urandom(NONCE_BYTES)
            sealed = cipher.encrypt(nonce, payload.SerializeToString(), key_id)
            encoded.append(
                Payload(
                    metadata={"encoding": ENCRYPTED_ENCODING, KEY_ID_METADATA: key_id},
                    data=nonce + sealed,
                )
            )
        return encoded

    async def decode(self, payloads: Sequence[Payload]) -> list[Payload]:
        decoded: list[Payload] = []
        for payload in payloads:
            if payload.metadata.get("encoding", b"") != ENCRYPTED_ENCODING:
                decoded.append(payload)
                continue
            key_id = payload.metadata.get(KEY_ID_METADATA, b"").decode()
            cipher = self._ciphers.get(key_id)
            if cipher is None:
                raise UnknownCodecKeyError(
                    f"a payload is encrypted under the key `{key_id}`, which {ENV_CODEC_KEYS} no longer lists"
                )
            plain = cipher.decrypt(payload.data[:NONCE_BYTES], payload.data[NONCE_BYTES:], key_id.encode())
            original = Payload()
            original.ParseFromString(plain)
            decoded.append(original)
        return decoded


__all__ = [
    "ENCRYPTED_ENCODING",
    "ENV_CODEC_KEYS",
    "ENV_CODEC_KEYS_FILE",
    "ENV_TLS",
    "ENV_TLS_CA_FILE",
    "ENV_TLS_CERT_FILE",
    "ENV_TLS_KEY_FILE",
    "ENV_TLS_SERVER_NAME",
    "KEY_ID_METADATA",
    "AesGcmCodec",
    "TemporalConfigError",
    "TemporalSecurity",
    "UnknownCodecKeyError",
    "connect_temporal",
    "data_converter",
    "parse_codec_keys",
    "tls_config",
]
