"""`choregos-verify-images` demands, for each image, the signer that actually signed it (#1861).

The images published before the publication moved (0.27.10 and older) stay in the frozen namespace
`ghcr.io/vargafoundation` and keep the signatures their release workflows issued then. `cosign verify`
on 2026-11-18 read these subjects:

* `ghcr.io/vargafoundation/choregos-api:0.27.10`, `choregos-runner:0.27.10` and `:0.17.15`:
  `https://github.com/VargaFoundation/choregos/.github/workflows/release.yml@refs/tags/v…`;
* `ghcr.io/vargafoundation/ecphoria:0.2.5` and `:0.2.5-memory`:
  `https://github.com/VargaFoundation/ecphoria/.github/workflows/release.yml@refs/tags/v0.2.5`.

The publisher sweep of ADR 0186 (#1855) rewrote both subjects to the current repositories, which never
signed those images: a cluster applying the reference policy refused every frozen image ("subject
mismatch", Kyverno 1.19.1 on the real images). `make policies-test` cannot judge signatures (it needs
the registry and Sigstore), so this test reads the policy.
"""

from __future__ import annotations

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
POLICY = ROOT / "infra" / "policies" / "images.yaml"

#: The frozen namespaces and the signer that signed them, read with cosign: history, never rewritten.
FROZEN_SIGNERS = {
    "ghcr.io/vargafoundation/choregos-*": "https://github.com/VargaFoundation/choregos/.github/workflows/*",
    "ghcr.io/vargafoundation/ecphoria*": "https://github.com/VargaFoundation/ecphoria/.github/workflows/*",
}

_IMAGE_OWNER = re.compile(r"^ghcr\.io/([^/]+)/")
_SUBJECT_OWNER = re.compile(r"^https://github\.com/([^/]+)/[^/]+/\.github/workflows/")


def _entries() -> list[tuple[list[str], list[str]]]:
    """(image references, keyless subjects) of every `verifyImages` entry of the policy."""
    policy = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    found: list[tuple[list[str], list[str]]] = []
    for rule in policy["spec"]["rules"]:
        for entry in rule.get("verifyImages", []):
            subjects = [
                keyless["subject"]
                for attestor in entry["attestors"]
                for item in attestor["entries"]
                if (keyless := item.get("keyless")) is not None
            ]
            found.append((list(entry["imageReferences"]), subjects))
    return found


def _subjects_of(reference: str) -> list[str]:
    matches = [subjects for references, subjects in _entries() if reference in references]
    assert len(matches) == 1, f"{reference}: {len(matches)} entries in {POLICY.name}, expected one"
    return matches[0]


def test_the_policy_is_read() -> None:
    references = {reference for references, _ in _entries() for reference in references}
    assert set(FROZEN_SIGNERS) <= references, "the frozen entries moved or changed shape"


def test_each_frozen_namespace_requires_the_signer_that_signed_it() -> None:
    for reference, signer in FROZEN_SIGNERS.items():
        assert _subjects_of(reference) == [signer], (
            f"{reference} was signed by {signer}; any other subject refuses every frozen image"
        )


def test_every_image_is_signed_under_its_own_owner() -> None:
    """A GitHub workflow signs the images its owner pushes: `ghcr.io/<owner>/…` ← `github.com/<owner>/…`.

    The frozen images moved nowhere; a subject under another owner is a rewrite that went too far.
    """
    entries = _entries()
    assert entries, "no verifyImages entry read"
    for references, subjects in entries:
        assert subjects, f"{references}: no keyless subject"
        for reference in references:
            image = _IMAGE_OWNER.match(reference)
            assert image is not None, reference
            for subject in subjects:
                signer = _SUBJECT_OWNER.match(subject)
                assert signer is not None, subject
                assert signer.group(1).lower() == image.group(1).lower(), (
                    f"{reference} is pushed by `{image.group(1)}` but must be signed by `{signer.group(1)}`"
                )
