# SPDX-License-Identifier: Apache-2.0
"""SAML 2.0 service provider: verifying an assertion, one identity provider per organisation (ADR 0170).

This module is a GATEKEEPER: whatever it lets through opens a session. The rules, in the order the
assertion consumer service applies them:

1. **Size and shape.** The document is bounded, and one that declares a DTD or an entity is refused
   before any parser sees it (XXE and entity expansion). The parser signxml drives resolves no entity
   and opens no network connection.
2. **Signature**, verified by `signxml` against the certificate PINNED for the organisation, never one
   the message carries; RSA / ECDSA with SHA-2 only (SHA-1 refused); exactly ONE reference
   (`expect_references=1`). Everything after is read from the SIGNED subtree `signxml` returns, never from
   the received document: that is the defence against XML signature wrapping, where an attacker keeps
   the signed assertion and slips an unsigned one next to it for a naive reader.
3. **Issuer** is the entity declared for the organisation.
4. **Audience** is this service's entity for THIS organisation; **recipient** is its assertion consumer
   service; a `Destination` on the response, when present, is that service too.
5. **Window**: `NotBefore` / `NotOnOrAfter` of the conditions and of the confirmation, with a bounded
   clock skew (`CLOCK_SKEW`); both bounds are mandatory on the end.
6. **Response to OUR request**: `InResponseTo` is the id held in the signed cookie of the request we
   started. A sign-in the provider starts by itself is refused.
7. **Replay**: an assertion id is good once (`saml_consumed_assertions`).
8. **Domain**: the e-mail it vouches for belongs to the provider's declared domains.
9. **Groups**: translated by the organisation's own table, for THIS organisation only; raw groups are
   never passed on.

The session itself is opened by `routers.auth.terminer_la_connexion(org=...)`, which re-checks the domain
and confines roles to the organisation (ADR 0108).
"""

from __future__ import annotations

import base64
import secrets
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from cryptography import x509
from lxml import etree
from signxml.algorithms import SignatureMethod
from signxml.exceptions import InvalidInput, InvalidSignature
from signxml.verifier import SignatureConfiguration, XMLVerifier

from ..config import get_settings
from ..errors import ApiError, unauthorized

NS = {"saml": "urn:oasis:names:tc:SAML:2.0:assertion", "samlp": "urn:oasis:names:tc:SAML:2.0:protocol"}
SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
BEARER = "urn:oasis:names:tc:SAML:2.0:cm:bearer"
#: Allowed difference between our clock and the provider's, on both ends of a window.
CLOCK_SKEW = timedelta(minutes=2)
#: How long the signed cookie of an authentication request lasts.
REQUEST_LIFETIME_S = 600
#: A SAML response is a few kilobytes; this refuses a bomb before parsing.
MAX_DOCUMENT_BYTES = 256 * 1024
METHODS = frozenset(
    {
        SignatureMethod.RSA_SHA256,
        SignatureMethod.RSA_SHA384,
        SignatureMethod.RSA_SHA512,
        SignatureMethod.ECDSA_SHA256,
        SignatureMethod.ECDSA_SHA384,
        SignatureMethod.ECDSA_SHA512,
    }
)


def sp_entity_id(org: str) -> str:
    return f"{get_settings().api_url}/api/v1/saml/{org}"


def acs_url(org: str) -> str:
    return f"{sp_entity_id(org)}/acs"


def refusal(reason: str) -> ApiError:
    return unauthorized(f"SAML assertion refused: {reason}")


def check_certificate(pem: str) -> None:
    """Raises `ValueError` unless `pem` is one X.509 certificate in PEM."""
    x509.load_pem_x509_certificate(pem.encode("ascii"))


@dataclass(frozen=True)
class Provider:
    """What the verifier needs of an organisation's provider (a row of `org_saml_idps`, or a test's)."""

    idp_entity_id: str
    certificate_pem: str
    domains: tuple[str, ...]
    email_attribute: str | None = None
    groups_attribute: str | None = None
    group_map: dict[str, str] | None = None


@dataclass(frozen=True)
class Identity:
    email: str
    name: str
    groups: list[str]
    assertion_id: str
    expires_at: datetime


# ───────────────────────────── the authentication request ─────────────────────────────


def authn_request(org: str, sso_url: str, request_id: str, now: datetime) -> str:
    """The HTTP-Redirect URL: a signed-nothing `AuthnRequest` (the assertion is what is signed), DEFLATEd."""
    root = etree.Element(
        f"{{{NS['samlp']}}}AuthnRequest",
        nsmap={"samlp": NS["samlp"], "saml": NS["saml"]},
        ID=request_id,
        Version="2.0",
        IssueInstant=now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        Destination=sso_url,
        AssertionConsumerServiceURL=acs_url(org),
        ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
    )
    etree.SubElement(root, f"{{{NS['saml']}}}Issuer").text = sp_entity_id(org)
    raw = etree.tostring(root)
    deflated = zlib.compress(raw)[2:-4]  # raw DEFLATE, as the binding requires
    joiner = "&" if "?" in sso_url else "?"
    return f"{sso_url}{joiner}{urlencode({'SAMLRequest': base64.b64encode(deflated).decode()})}"


def new_request_id() -> str:
    return "_" + secrets.token_hex(20)


# ───────────────────────────── verification ─────────────────────────────


def decode_response(encoded: str) -> bytes:
    """The base64 form field, as bytes; bounded and free of any DTD or entity declaration."""
    if len(encoded) > MAX_DOCUMENT_BYTES * 2:
        raise refusal("response too large")
    try:
        document = base64.b64decode(encoded, validate=True)
    except ValueError as unreadable:
        raise refusal("SAMLResponse is not base64") from unreadable
    if len(document) > MAX_DOCUMENT_BYTES:
        raise refusal("response too large")
    # No legitimate SAML message carries a DTD; refusing it outright leaves nothing to expand or fetch.
    if b"<!DOCTYPE" in document.upper() or b"<!ENTITY" in document.upper():
        raise refusal("a DTD or entity declaration is not allowed")
    return document


def verify_signature(document: bytes, certificate_pem: str) -> Any:
    """The SIGNED assertion: the only thing read afterwards."""
    try:
        result = XMLVerifier().verify(
            document,
            x509_cert=certificate_pem,
            expect_config=SignatureConfiguration(
                require_x509=False,
                expect_references=1,
                signature_methods=METHODS,
                ignore_ambiguous_key_info=True,
            ),
        )
    except (InvalidSignature, InvalidInput, etree.XMLSyntaxError, ValueError) as invalid:
        raise refusal(f"invalid signature ({type(invalid).__name__})") from invalid
    signed = result.signed_xml if not isinstance(result, list) else None
    if signed is None:
        raise refusal("nothing signed")
    if signed.tag == f"{{{NS['saml']}}}Assertion":
        return signed
    if signed.tag != f"{{{NS['samlp']}}}Response":
        raise refusal("the signed part is neither a response nor an assertion")
    assertions = signed.findall("saml:Assertion", NS)
    if len(assertions) != 1:
        raise refusal("the signed part does not hold exactly one assertion")
    return assertions[0]


def _instant(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _values(assertion: Any, name: str) -> list[str]:
    return [
        (v.text or "").strip()
        for attribute in assertion.findall(".//saml:Attribute", NS)
        if attribute.get("Name") == name
        for v in attribute.findall("saml:AttributeValue", NS)
    ]


def check_envelope(document: bytes, org: str) -> None:
    """The unsigned parts of the response, as far as they are worth checking: a `Destination` that is not
    our service, or a status that is not success, ends the exchange. They prove nothing when the response is
    unsigned (the assertion's own `Recipient` is what binds it); they only refuse earlier and clearer."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    try:
        root = etree.fromstring(document, parser)
    except etree.XMLSyntaxError as unreadable:
        raise refusal("not well-formed XML") from unreadable
    if root.tag != f"{{{NS['samlp']}}}Response":
        raise refusal("not a SAML response")
    destination = root.get("Destination")
    if destination is not None and destination != acs_url(org):
        raise refusal("unexpected destination")
    code = root.find("samlp:Status/samlp:StatusCode", NS)
    if code is not None and code.get("Value") != SUCCESS:
        raise refusal("the provider reported a failure")


def read_assertion(
    assertion: Any, provider: Provider, org: str, expected_request: str, now: datetime
) -> Identity:
    """Rules 3 to 6, 8 and 9. Everything is read from the SIGNED assertion."""
    if (assertion.findtext("saml:Issuer", namespaces=NS) or "").strip() != provider.idp_entity_id:
        raise refusal("unexpected issuer")
    conditions = assertion.find("saml:Conditions", NS)
    if conditions is None:
        raise refusal("no conditions")
    start, end = _instant(conditions.get("NotBefore")), _instant(conditions.get("NotOnOrAfter"))
    if end is None or now - CLOCK_SKEW >= end or (start is not None and now + CLOCK_SKEW < start):
        raise refusal("outside its validity window")
    # Restrictions are cumulative (SAML core 2.5.1.4): EVERY one of them must name this service, and at
    # least one must exist.
    restrictions = conditions.findall("saml:AudienceRestriction", NS)
    if not restrictions or any(
        sp_entity_id(org) not in [(a.text or "").strip() for a in restriction.findall("saml:Audience", NS)]
        for restriction in restrictions
    ):
        raise refusal("unexpected audience")
    confirmation = assertion.find(
        f"saml:Subject/saml:SubjectConfirmation[@Method='{BEARER}']/saml:SubjectConfirmationData", NS
    )
    if confirmation is None:
        raise refusal("no bearer confirmation")
    if confirmation.get("Recipient") != acs_url(org):
        raise refusal("unexpected recipient")
    if not expected_request or not secrets.compare_digest(
        (confirmation.get("InResponseTo") or "").encode(), expected_request.encode()
    ):
        raise refusal("does not answer our request")
    limit = _instant(confirmation.get("NotOnOrAfter"))
    if limit is None or now - CLOCK_SKEW >= limit:
        raise refusal("confirmation expired")
    confirmation_start = _instant(confirmation.get("NotBefore"))
    if confirmation_start is not None and now + CLOCK_SKEW < confirmation_start:
        raise refusal("confirmation not yet valid")

    email = _values(assertion, provider.email_attribute)[:1] if provider.email_attribute else []
    address = (
        (email[0] if email else assertion.findtext("saml:Subject/saml:NameID", namespaces=NS) or "")
        .strip()
        .lower()
    )
    domain = address.rsplit("@", 1)[-1] if "@" in address else ""
    if not domain or domain not in provider.domains:
        raise refusal(f"the address does not belong to the domains of the provider of {org}")
    mapping = provider.group_map or {}
    claimed = _values(assertion, provider.groups_attribute) if provider.groups_attribute else []
    groups = sorted({f"choregos:{org}:{mapping[g]}" for g in claimed if g in mapping})
    name = (_values(assertion, "displayName")[:1] or [address.split("@")[0]])[0]
    assertion_id = assertion.get("ID") or ""
    if not assertion_id:
        raise refusal("the assertion has no id")
    return Identity(address, name, groups, assertion_id, end)


def verify_response(
    encoded: str, provider: Provider, org: str, expected_request: str, now: datetime
) -> Identity:
    """Decode, verify the signature against the pinned certificate, and read the signed assertion."""
    document = decode_response(encoded)
    check_envelope(document, org)
    assertion = verify_signature(document, provider.certificate_pem)
    return read_assertion(assertion, provider, org, expected_request, now)
