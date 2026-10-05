"""La pile de développement (`dev/compose.yaml`) démarre-t-elle sur un poste neuf ?

Le 2026-10-03, `docker pull minio/minio:latest` a été refusé : l'image n'était plus publiée, ni sur
Docker Hub ni sur quay.io. Un poste qui l'avait en cache démarrait la pile ; un poste neuf, non (#152).
Une étiquette flottante (`latest`, ou pas d'étiquette du tout) est une dépendance qu'un éditeur
peut retirer ou changer sans prévenir : chaque image de la pile porte donc une étiquette explicite.
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
    assert not flottantes, f"images sans étiquette explicite : {flottantes}"


def test_le_stockage_s3_n_est_plus_minio() -> None:
    """Le serveur MinIO n'est plus publié : un poste neuf ne peut pas le tirer."""
    serveurs = ("minio/minio", "quay.io/minio/minio")
    assert not [image for image in _images().values() if image.startswith(serveurs)]
