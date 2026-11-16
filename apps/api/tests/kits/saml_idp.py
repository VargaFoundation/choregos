# SPDX-License-Identifier: Apache-2.0
"""A SAML identity provider for tests: a key, a self-signed certificate, and signed responses."""

from __future__ import annotations

import base64
import datetime as dt
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml import XMLSigner, methods

SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
API = "http://localhost:8000"  # `Settings.api_url` default in the suites
IDP = "https://idp.globex.example/metadata"


class Idp:
    """A provider's signing identity."""

    def __init__(self, curve: bool = False) -> None:
        self.key: Any = (
            ec.generate_private_key(ec.SECP256R1()) if curve else rsa.generate_private_key(65537, 2048)
        )
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test idp")])
        now = dt.datetime.now(dt.UTC)
        self.cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(self.key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=365))
            .sign(self.key, hashes.SHA256())
        )
        self.certificate_pem = self.cert.public_bytes(serialization.Encoding.PEM).decode()
        self.key_pem = self.key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        self.curve = curve

    def sign(self, element: Any, algorithm: str | None = None, digest: str = "sha256") -> Any:
        algorithm = algorithm or ("ecdsa-sha256" if self.curve else "rsa-sha256")
        signer = XMLSigner(
            method=methods.enveloped,
            signature_algorithm=algorithm,
            digest_algorithm=digest,
            c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#",
        )
        return signer.sign(
            element, key=self.key_pem, cert=self.certificate_pem, reference_uri=element.get("ID")
        )


def instant(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def assertion(
    *,
    org: str = "varga",
    request_id: str,
    email: str = "ann@globex.example",
    groups: tuple[str, ...] = ("Admins",),
    assertion_id: str = "_assertion-1",
    issuer: str = IDP,
    audience: str | None = None,
    recipient: str | None = None,
    not_before: dt.timedelta | None = dt.timedelta(minutes=-1),
    not_on_or_after: dt.timedelta | None = dt.timedelta(minutes=5),
    confirmation_expiry: dt.timedelta | None = dt.timedelta(minutes=5),
    in_response_to: str | None = None,
    extra_audience_restriction: str | None = None,
    now: dt.datetime | None = None,
) -> Any:
    now = now or dt.datetime.now(dt.UTC)
    sp = f"{API}/api/v1/saml/{org}"
    root = etree.Element(f"{{{SAML}}}Assertion", nsmap={"saml": SAML}, ID=assertion_id, Version="2.0")
    root.set("IssueInstant", instant(now))
    etree.SubElement(root, f"{{{SAML}}}Issuer").text = issuer
    subject = etree.SubElement(root, f"{{{SAML}}}Subject")
    etree.SubElement(subject, f"{{{SAML}}}NameID").text = email
    confirmation = etree.SubElement(
        subject, f"{{{SAML}}}SubjectConfirmation", Method="urn:oasis:names:tc:SAML:2.0:cm:bearer"
    )
    data = etree.SubElement(
        confirmation,
        f"{{{SAML}}}SubjectConfirmationData",
        Recipient=recipient or f"{sp}/acs",
        InResponseTo=in_response_to if in_response_to is not None else request_id,
    )
    if confirmation_expiry is not None:
        data.set("NotOnOrAfter", instant(now + confirmation_expiry))
    conditions = etree.SubElement(root, f"{{{SAML}}}Conditions")
    if not_before is not None:
        conditions.set("NotBefore", instant(now + not_before))
    if not_on_or_after is not None:
        conditions.set("NotOnOrAfter", instant(now + not_on_or_after))
    restriction = etree.SubElement(conditions, f"{{{SAML}}}AudienceRestriction")
    etree.SubElement(restriction, f"{{{SAML}}}Audience").text = audience or sp
    if extra_audience_restriction:
        second = etree.SubElement(conditions, f"{{{SAML}}}AudienceRestriction")
        etree.SubElement(second, f"{{{SAML}}}Audience").text = extra_audience_restriction
    attributes = etree.SubElement(root, f"{{{SAML}}}AttributeStatement")
    group_attribute = etree.SubElement(attributes, f"{{{SAML}}}Attribute", Name="groups")
    for group in groups:
        etree.SubElement(group_attribute, f"{{{SAML}}}AttributeValue").text = group
    return root


def response(
    inner: Any, *, org: str = "varga", destination: str | None = None, status: str | None = None
) -> Any:
    root = etree.Element(
        f"{{{SAMLP}}}Response", nsmap={"samlp": SAMLP, "saml": SAML}, ID="_response-1", Version="2.0"
    )
    root.set("Destination", destination or f"{API}/api/v1/saml/{org}/acs")
    code = etree.SubElement(etree.SubElement(root, f"{{{SAMLP}}}Status"), f"{{{SAMLP}}}StatusCode")
    code.set("Value", status or "urn:oasis:names:tc:SAML:2.0:status:Success")
    if inner is not None:
        root.append(inner)
    return root


def encode(root: Any) -> str:
    return base64.b64encode(etree.tostring(root)).decode()
