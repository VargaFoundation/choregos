# SPDX-License-Identifier: Apache-2.0
"""Embeddings for the knowledge index (ADR 0051): through the LiteLLM gateway, or a fake.

The model is the deployment's embedding profile (`CHOREGOS_DEFAULT_MODEL_PROFILES={"embed": …}`),
else `CHOREGOS_KNOWLEDGE_EMBEDDING_MODEL` (`platform/embed`). Under `CHOREGOS_FAKES` the embedder is
deterministic and local: hashed word counts, L2-normalised — enough for tests and demos to
exercise the hybrid ranking, never a claim about semantic quality. Without a gateway key, there is
no embedder, and the index serves full-text only (and says so).
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from typing import Protocol

import httpx

from ..config import Settings, get_settings

_WORD = re.compile(r"[a-z0-9]+")
BATCH = 64


class Embedder(Protocol):
    model: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def words(text: str) -> list[str]:
    """Lower-case words without accents: what the fake embedder and the in-process rank count."""
    plain = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode("ascii")
    return _WORD.findall(plain)


class FakeEmbedder:
    """Deterministic: the same text gives the same vector, on every machine."""

    model = "fake/hashed-words"

    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in words(text):
            if len(word) < 3:
                continue
            slot = int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big") % self.dimensions
            vector[slot] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


class GatewayEmbedder:
    """`POST /v1/embeddings` on the LiteLLM gateway, with the platform's key."""

    def __init__(self, base_url: str, key: str, model: str, *, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.key = key
        self.model = model
        self.timeout = timeout

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for start in range(0, len(texts), BATCH):
                response = await client.post(
                    f"{self.base_url}/v1/embeddings",
                    headers={"Authorization": f"Bearer {self.key}"},
                    json={"model": self.model, "input": texts[start : start + BATCH]},
                )
                response.raise_for_status()
                data = sorted(response.json()["data"], key=lambda item: item["index"])
                vectors.extend([list(map(float, item["embedding"])) for item in data])
        return vectors


def embedding_model(settings: Settings) -> str:
    return settings.default_model_profiles.get("embed") or settings.knowledge_embedding_model


def get_embedder(settings: Settings | None = None) -> Embedder | None:
    """The embedder of this deployment, or `None`: then the index is full-text only."""
    settings = settings or get_settings()
    if settings.fakes:
        return FakeEmbedder()
    if not settings.gateway_master_key:
        return None
    return GatewayEmbedder(settings.gateway_url, settings.gateway_master_key, embedding_model(settings))


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0
