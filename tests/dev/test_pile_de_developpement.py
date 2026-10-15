"""Does the development stack (`dev/compose.yaml`) start on a fresh machine?

On 2026-10-03, `docker pull minio/minio:latest` was refused: the image was no longer published,
neither on Docker Hub nor on quay.io. A machine that had it cached started the stack; a fresh one
did not (#152). A floating tag (`latest`, or no tag at all) is a dependency a publisher can
withdraw or change without warning: every image of the stack therefore carries an explicit tag.
"""

from __future__ import annotations

import pathlib

import yaml

PILE = pathlib.Path(__file__).resolve().parents[2] / "dev" / "compose.yaml"


def _images() -> dict[str, str]:
    services = yaml.safe_load(PILE.read_text(encoding="utf-8"))["services"]
    return {nom: str(service["image"]) for nom, service in services.items() if "image" in service}


def test_chaque_image_porte_une_etiquette_explicite() -> None:
    flottantes = {
        nom: image
        for nom, image in _images().items()
        if ":" not in image.rsplit("/", 1)[-1] or image.endswith(":latest")
    }
    assert not flottantes, f"images without an explicit tag: {flottantes}"


def test_le_stockage_s3_n_est_plus_minio() -> None:
    """The MinIO server is no longer published: a fresh machine cannot pull it."""
    serveurs = ("minio/minio", "quay.io/minio/minio")
    assert not [image for image in _images().values() if image.startswith(serveurs)]
