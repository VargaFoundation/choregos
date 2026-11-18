# SPDX-License-Identifier: Apache-2.0
"""SAML sign-in served by the core (ADR 0170): configuration, the sign-in flow, and one test per rule of the
verifier (`services/saml.py`): signature pinning, XML signature wrapping, replay, audience, recipient,
destination, window and clock skew, `InResponseTo`, XXE, domain ownership, group translation."""

from __future__ import annotations

import base64
import datetime as dt
import time
import zlib
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from lxml import etree
from sqlalchemy import select
from tranibor_api.db.models import AuditLog, Membership, Organization, SamlConsumedAssertion, User
from tranibor_api.db.session import session_scope
from tranibor_api.errors import ApiError
from tranibor_api.security import sign_session
from tranibor_api.services import saml as engine

from .kits import saml_idp as kit

PROVIDER_DOMAINS = ("globex.example",)
NOW = dt.datetime(2026, 11, 16, 12, 0, tzinfo=dt.UTC)
REQUEST = "_req-1"


@pytest.fixture(scope="module")
def idp() -> kit.Idp:
    return kit.Idp()


def provider(idp: kit.Idp, **changes: Any) -> engine.Provider:
    base: dict[str, Any] = {
        "idp_entity_id": kit.IDP,
        "certificate_pem": idp.certificate_pem,
        "domains": PROVIDER_DOMAINS,
        "groups_attribute": "groups",
        "group_map": {"Admins": "org-admins", "Devs": "developers"},
    }
    return engine.Provider(**(base | changes))


def signed_response(idp: kit.Idp, *, sign: str = "assertion", **fields: Any) -> str:
    """A response whose assertion (or the whole response) is signed by `idp`."""
    fields.setdefault("request_id", REQUEST)
    fields.setdefault("now", NOW)
    org = fields.get("org", "northwind")
    inner = kit.assertion(**fields)
    if sign == "assertion":
        return kit.encode(kit.response(idp.sign(inner), org=org))
    if sign == "response":
        return kit.encode(idp.sign(kit.response(inner, org=org)))
    return kit.encode(kit.response(inner, org=org))


def verify(idp: kit.Idp, encoded: str, now: dt.datetime = NOW, **changes: Any) -> engine.Identity:
    return engine.verify_response(encoded, provider(idp, **changes), "northwind", REQUEST, now)


def refused(idp: kit.Idp, encoded: str, reason: str, now: dt.datetime = NOW, **changes: Any) -> None:
    with pytest.raises(ApiError) as caught:
        verify(idp, encoded, now, **changes)
    assert caught.value.status_code == 401
    assert reason in str(caught.value.detail), caught.value.detail


# ───────────────────────────── the verifier: what it accepts ─────────────────────────────


def test_a_signed_assertion_is_accepted_and_its_groups_are_translated_for_this_organisation(
    idp: kit.Idp,
) -> None:
    identity = verify(idp, signed_response(idp, groups=("Admins", "Unknown", "choregos:other:org-admins")))
    assert identity.email == "ann@globex.example"
    assert identity.groups == ["choregos:northwind:org-admins"]  # raw and unmapped groups give nothing
    assert identity.assertion_id == "_assertion-1"


def test_a_signed_response_is_accepted_too(idp: kit.Idp) -> None:
    assert verify(idp, signed_response(idp, sign="response")).email == "ann@globex.example"


def test_an_ecdsa_signature_is_accepted() -> None:
    curve = kit.Idp(curve=True)
    assert verify(curve, signed_response(curve)).email == "ann@globex.example"


def test_the_e_mail_attribute_wins_over_the_name_id(idp: kit.Idp) -> None:
    root = kit.assertion(request_id=REQUEST, now=NOW, email="ignored@globex.example")
    attribute = etree.SubElement(
        root.find(f"{{{kit.SAML}}}AttributeStatement"), f"{{{kit.SAML}}}Attribute", Name="mail"
    )
    etree.SubElement(attribute, f"{{{kit.SAML}}}AttributeValue").text = "Real@Globex.Example"
    encoded = kit.encode(kit.response(idp.sign(root)))
    assert verify(idp, encoded, email_attribute="mail").email == "real@globex.example"


# ───────────────────────────── signature: pinned, SHA-2, one reference ─────────────────────────────


def test_an_assertion_signed_by_another_key_is_refused_even_with_its_own_certificate_embedded(
    idp: kit.Idp,
) -> None:
    attacker = kit.Idp()
    refused(idp, signed_response(attacker), "invalid signature")  # `attacker.sign` embeds ITS certificate


def test_an_unsigned_assertion_is_refused(idp: kit.Idp) -> None:
    refused(idp, signed_response(idp, sign="none"), "invalid signature")


def test_a_tampered_assertion_is_refused(idp: kit.Idp) -> None:
    root = idp.sign(kit.assertion(request_id=REQUEST, now=NOW, email="ann@globex.example"))
    root.find(f"{{{kit.SAML}}}Subject/{{{kit.SAML}}}NameID").text = "boss@globex.example"
    refused(idp, kit.encode(kit.response(root)), "invalid signature")


def test_a_sha1_signature_is_refused(idp: kit.Idp) -> None:
    inner = kit.assertion(request_id=REQUEST, now=NOW)
    try:
        signed = idp.sign(inner, algorithm="rsa-sha1", digest="sha1")
    except Exception:
        pytest.skip("this signxml refuses to produce a SHA-1 signature")
    refused(idp, kit.encode(kit.response(signed)), "invalid signature")


def test_the_verifier_pins_the_certificate_and_expects_exactly_one_reference(
    idp: kit.Idp, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    real = engine.XMLVerifier.verify

    def spy(self: Any, data: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return real(self, data, **kwargs)

    monkeypatch.setattr(engine.XMLVerifier, "verify", spy)
    verify(idp, signed_response(idp))
    (call,) = calls
    assert call["x509_cert"] == idp.certificate_pem
    configuration = call["expect_config"]
    assert configuration.expect_references == 1
    assert not any("SHA1" in method.name for method in configuration.signature_methods)


# ───────────────────────────── XML signature wrapping ─────────────────────────────


def test_wrapping_an_unsigned_assertion_next_to_the_signed_one_changes_nothing(idp: kit.Idp) -> None:
    """The attacker holds a response signed for ann; they put an unsigned assertion for the boss FIRST.
    The verifier reads the signed subtree only: the identity is ann's."""
    legitimate = idp.sign(kit.assertion(request_id=REQUEST, now=NOW, email="ann@globex.example"))
    evil = kit.assertion(
        request_id=REQUEST, now=NOW, email="boss@globex.example", assertion_id="_evil", groups=("Admins",)
    )
    wrapped = kit.response(None)
    wrapped.append(evil)
    wrapped.append(legitimate)
    identity = verify(idp, kit.encode(wrapped))
    assert identity.email == "ann@globex.example"
    assert identity.assertion_id == "_assertion-1"


def test_an_unsigned_assertion_hidden_inside_the_signed_one_is_not_read(idp: kit.Idp) -> None:
    """An assertion nested in an Extensions-like slot of the signed one, or an evil attribute copy outside
    it, never reaches the reader: `read_assertion` is handed the signed element only."""
    signed = idp.sign(kit.assertion(request_id=REQUEST, now=NOW, email="ann@globex.example"))
    wrapper = kit.response(None)
    wrapper.append(signed)
    outside = kit.assertion(request_id=REQUEST, now=NOW, email="boss@globex.example", assertion_id="_o")
    wrapper.append(outside)
    assert verify(idp, kit.encode(wrapper)).email == "ann@globex.example"


def test_a_signed_response_with_two_assertions_is_refused(idp: kit.Idp) -> None:
    both = kit.response(kit.assertion(request_id=REQUEST, now=NOW))
    both.append(kit.assertion(request_id=REQUEST, now=NOW, email="boss@globex.example", assertion_id="_b"))
    refused(idp, kit.encode(idp.sign(both)), "exactly one assertion")


def test_a_signature_that_does_not_cover_what_it_says_is_refused(idp: kit.Idp) -> None:
    """A signature copied from another assertion onto a forged one fails: the digest no longer matches."""
    legitimate = idp.sign(kit.assertion(request_id=REQUEST, now=NOW, email="ann@globex.example"))
    forged = kit.assertion(request_id=REQUEST, now=NOW, email="boss@globex.example")
    forged.append(legitimate.find("{http://www.w3.org/2000/09/xmldsig#}Signature"))
    refused(idp, kit.encode(kit.response(forged)), "invalid signature")


# ───────────────────────────── XXE and bombs ─────────────────────────────

XXE = (
    b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
    b'<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol">&x;</samlp:Response>'
)
LAUGHS = (
    b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
    b'<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol">&b;&b;&b;</samlp:Response>'
)


@pytest.mark.parametrize("document", [XXE, LAUGHS, b"<!doctype x><a/>"])
def test_a_dtd_or_entity_declaration_is_refused_before_any_parser_sees_it(
    idp: kit.Idp, document: bytes
) -> None:
    refused(idp, base64.b64encode(document).decode(), "DTD")


def test_a_response_too_large_is_refused(idp: kit.Idp) -> None:
    refused(idp, base64.b64encode(b"<a>" + b"x" * engine.MAX_DOCUMENT_BYTES + b"</a>").decode(), "too large")


def test_what_is_not_base64_or_not_xml_is_refused(idp: kit.Idp) -> None:
    refused(idp, "%%%not base64%%%", "not base64")
    refused(idp, base64.b64encode(b"<unclosed").decode(), "not well-formed")


def test_the_signature_check_itself_does_not_resolve_entities(idp: kit.Idp) -> None:
    """Defence in depth: even if a DTD got past the guard, the parser signxml drives must not expand it.
    Called on the verifier directly, bypassing `decode_response`."""
    with pytest.raises(ApiError) as caught:
        engine.verify_signature(XXE, idp.certificate_pem)
    assert caught.value.status_code == 401


# ───────────────────────────── audience, recipient, destination, issuer, status ─────────────────────────────


def test_another_audience_is_refused(idp: kit.Idp) -> None:
    refused(idp, signed_response(idp, audience=f"{kit.API}/api/v1/saml/another-org"), "audience")


def test_every_audience_restriction_must_name_this_service(idp: kit.Idp) -> None:
    refused(idp, signed_response(idp, extra_audience_restriction="https://elsewhere.example"), "audience")


def test_an_assertion_issued_for_another_organisation_of_the_same_instance_is_refused(idp: kit.Idp) -> None:
    refused(
        idp, signed_response(idp, org="other"), "destination"
    )  # its destination and audience are the other's


def test_another_recipient_is_refused(idp: kit.Idp) -> None:
    refused(idp, signed_response(idp, recipient="https://evil.example/acs"), "recipient")


def test_another_destination_is_refused(idp: kit.Idp) -> None:
    inner = idp.sign(kit.assertion(request_id=REQUEST, now=NOW))
    refused(idp, kit.encode(kit.response(inner, destination="https://evil.example/acs")), "destination")


def test_another_issuer_is_refused(idp: kit.Idp) -> None:
    refused(idp, signed_response(idp, issuer="https://other-idp.example"), "issuer")


def test_a_failure_status_is_refused(idp: kit.Idp) -> None:
    inner = idp.sign(kit.assertion(request_id=REQUEST, now=NOW))
    failure = kit.response(inner, status="urn:oasis:names:tc:SAML:2.0:status:Requester")
    refused(idp, kit.encode(failure), "failure")


# ───────────────────────────── the window and the clock ─────────────────────────────


def test_an_expired_assertion_is_refused(idp: kit.Idp) -> None:
    refused(idp, signed_response(idp), "validity window", now=NOW + dt.timedelta(minutes=5, seconds=121))


def test_the_clock_skew_is_bounded_on_the_end(idp: kit.Idp) -> None:
    encoded = signed_response(idp)
    assert verify(idp, encoded, NOW + dt.timedelta(minutes=5, seconds=119)).email  # inside the skew
    refused(idp, encoded, "validity window", now=NOW + dt.timedelta(minutes=5, seconds=120))  # at the bound


def test_an_assertion_not_yet_valid_is_refused_beyond_the_skew(idp: kit.Idp) -> None:
    encoded = signed_response(
        idp,
        not_before=dt.timedelta(minutes=10),
        not_on_or_after=dt.timedelta(minutes=20),
        confirmation_expiry=dt.timedelta(minutes=20),
    )
    assert verify(idp, encoded, NOW + dt.timedelta(minutes=8, seconds=1)).email
    refused(idp, encoded, "validity window", now=NOW)


def test_an_assertion_without_an_end_is_refused(idp: kit.Idp) -> None:
    refused(idp, signed_response(idp, not_on_or_after=None), "validity window")


def test_a_confirmation_without_an_end_or_expired_is_refused(idp: kit.Idp) -> None:
    refused(idp, signed_response(idp, confirmation_expiry=None), "confirmation expired")
    refused(idp, signed_response(idp, confirmation_expiry=dt.timedelta(minutes=-3)), "confirmation expired")


# ───────────────────────────── InResponseTo ─────────────────────────────


def test_an_assertion_that_answers_another_request_is_refused(idp: kit.Idp) -> None:
    refused(idp, signed_response(idp, in_response_to="_somebody-elses"), "does not answer our request")


def test_an_assertion_that_answers_nothing_is_refused(idp: kit.Idp) -> None:
    refused(idp, signed_response(idp, in_response_to=""), "does not answer our request")


def test_no_pending_request_means_no_assertion_is_accepted(idp: kit.Idp) -> None:
    with pytest.raises(ApiError):
        engine.verify_response(signed_response(idp), provider(idp), "northwind", "", NOW)


# ───────────────────────────── domains ─────────────────────────────


def test_an_address_outside_the_provider_s_domains_is_refused(idp: kit.Idp) -> None:
    refused(idp, signed_response(idp, email="ceo@northwind.example"), "domains")


def test_a_look_alike_domain_is_refused(idp: kit.Idp) -> None:
    refused(idp, signed_response(idp, email="ceo@evil-globex.example"), "domains")
    refused(idp, signed_response(idp, email="ceo@globex.example.evil.test"), "domains")


def test_an_address_without_a_domain_is_refused(idp: kit.Idp) -> None:
    refused(idp, signed_response(idp, email="nobody"), "domains")


# ───────────────────────────── the flow over HTTP ─────────────────────────────

ADMIN = "admin@northwind.dev"
CONFIG_KEYS = {"idp_entity_id", "sso_url", "certificate_pem", "domains", "group_map", "groups_attribute"}


def configuration(idp: kit.Idp, **changes: Any) -> dict[str, Any]:
    body = {
        "idp_entity_id": kit.IDP,
        "sso_url": "https://idp.globex.example/sso",
        "certificate_pem": idp.certificate_pem,
        "groups_attribute": "groups",
        "group_map": {"Admins": "org-admins", "Devs": "developers"},
        "domains": ["Globex.Example"],
    }
    return body | changes


@pytest.fixture
async def configured(client: AsyncClient, admin: str, idp: kit.Idp) -> kit.Idp:
    put = await client.put("/api/v1/orgs/northwind/saml", json=configuration(idp))
    assert put.status_code == 200, put.text
    return idp


async def browser(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def request_id_of(location: str) -> str:
    query = parse_qs(urlparse(location).query)
    xml = zlib.decompress(base64.b64decode(query["SAMLRequest"][0]), -15)
    return str(etree.fromstring(xml).get("ID"))


async def start(member: AsyncClient) -> str:
    """The sign-in starts; the request id the provider would answer."""
    started = await member.get("/api/v1/saml/northwind/login", params={"redirect_to": "/projects"})
    assert started.status_code == 307
    assert started.headers["location"].startswith("https://idp.globex.example/sso?SAMLRequest=")
    return request_id_of(started.headers["location"])


async def post(member: AsyncClient, encoded: str) -> Any:
    return await member.post("/api/v1/saml/northwind/acs", data={"SAMLResponse": encoded})


def response_for(idp: kit.Idp, request_id: str, **fields: Any) -> str:
    return signed_response(idp, request_id=request_id, now=dt.datetime.now(dt.UTC), **fields)


async def test_the_administrator_configures_reads_and_removes_the_provider(
    client: AsyncClient, admin: str, idp: kit.Idp
) -> None:
    empty = (await client.get("/api/v1/orgs/northwind/saml")).json()
    assert empty["idp_entity_id"] is None and empty["domains"] == []
    assert empty["acs_url"] == f"{kit.API}/api/v1/saml/northwind/acs"
    put = await client.put("/api/v1/orgs/northwind/saml", json=configuration(idp))
    assert put.status_code == 200 and put.json()["domains"] == ["globex.example"]
    assert (await client.get("/api/v1/orgs/northwind/saml")).json()["certificate_pem"] == idp.certificate_pem
    metadata = await client.get("/api/v1/saml/northwind/metadata")
    assert metadata.status_code == 200 and f'entityID="{kit.API}/api/v1/saml/northwind"' in metadata.text
    assert (await client.delete("/api/v1/orgs/northwind/saml")).status_code == 204
    assert (await client.get("/api/v1/saml/northwind/metadata")).status_code == 404
    async with session_scope(orgs="*") as session:
        actions = set((await session.execute(select(AuditLog.action))).scalars().all())
    assert {"org.saml", "org.saml.remove"} <= actions


async def test_the_configuration_is_refused_when_it_is_wrong(
    client: AsyncClient, admin: str, idp: kit.Idp
) -> None:
    put = client.put
    assert (
        await put("/api/v1/orgs/northwind/saml", json=configuration(idp, certificate_pem="x" * 120))
    ).status_code == 422
    assert (
        await put("/api/v1/orgs/northwind/saml", json=configuration(idp, sso_url="http://plain.example"))
    ).status_code == 422
    assert (
        await put("/api/v1/orgs/northwind/saml", json=configuration(idp, group_map={"A": "root"}))
    ).status_code == 422
    assert (
        await put("/api/v1/orgs/northwind/saml", json=configuration(idp, domains=["not a domain"]))
    ).status_code == 422
    assert (await put("/api/v1/orgs/northwind/saml", json=configuration(idp, domains=[]))).status_code == 422
    assert (await put("/api/v1/orgs/nowhere/saml", json=configuration(idp))).status_code == 404


async def test_only_a_platform_administrator_configures_a_provider(
    app: Any, org: str, admin: str, idp: kit.Idp
) -> None:
    from tranibor_api.db.models import Membership, Organization, User

    async with session_scope(orgs="*") as session:
        other = Organization(slug="acme", name="Acme")
        session.add(other)
        await session.flush()
        user = User(email="owner@acme.example", display_name="Owner", oidc_sub="o-1")
        session.add(user)
        await session.flush()
        session.add(Membership(org_id=other.id, user_id=user.id, role="org_admin"))
    member = await browser(app)
    try:
        started = await member.get("/api/v1/auth/login", params={"as": "owner@acme.example"})
        await member.get(started.headers["location"])
        # An administrator of ONE of two organisations is not a platform administrator.
        assert (await member.put("/api/v1/orgs/acme/saml", json=configuration(idp))).status_code == 403
        assert (await member.get("/api/v1/orgs/acme/saml")).status_code == 403
        assert (await member.delete("/api/v1/orgs/acme/saml")).status_code == 403
    finally:
        await member.aclose()


async def second_organisation_administered_by(email: str) -> None:
    """On a two-organisation instance the platform administrator administers BOTH organisations."""
    async with session_scope(orgs="*") as session:
        other = Organization(slug="acme", name="Acme")
        session.add(other)
        await session.flush()
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        session.add(Membership(org_id=other.id, user_id=user.id, role="org_admin"))


async def test_a_domain_belongs_to_one_organisation(client: AsyncClient, configured: kit.Idp) -> None:
    await second_organisation_administered_by("admin@northwind.dev")
    clash = await client.put(
        "/api/v1/orgs/acme/saml", json=configuration(configured, domains=["globex.example"])
    )
    assert clash.status_code == 409
    assert (
        await client.put("/api/v1/orgs/acme/saml", json=configuration(configured, domains=["acme.example"]))
    ).status_code == 200
    # ... and the organisation that holds it keeps it when it saves again.
    assert (
        await client.put("/api/v1/orgs/northwind/saml", json=configuration(configured))
    ).status_code == 200


async def test_a_domain_or_an_organisation_held_by_an_extension_s_provider_is_refused(
    client: AsyncClient, admin: str, idp: kit.Idp
) -> None:
    from tranibor_api.edition import reset
    from tranibor_api.tenant_sign_in import OidcProvider, TenantSignIn, declare_sign_in_resolver

    async def resolver(session: Any, hint: Any) -> Any:
        oidc = OidcProvider(issuer="https://login.example", client_id="c", client_secret="s")
        if hint.org == "northwind" or hint.email_domain == "taken.example":
            return TenantSignIn(org="northwind" if hint.org else "another", oidc=oidc)
        return None

    declare_sign_in_resolver(resolver)
    try:
        assert (await client.put("/api/v1/orgs/northwind/saml", json=configuration(idp))).status_code == 409
    finally:
        reset()


async def test_the_core_announces_saml() -> None:
    from tranibor_api.extensions import IDENTITY_FEATURES

    assert {"approval_freshness", "session_revocation", "saml"} <= IDENTITY_FEATURES


async def test_the_sign_in_page_leads_to_the_organisation_s_provider(
    client: AsyncClient, configured: kit.Idp
) -> None:
    by_link = await client.get("/api/v1/auth/login", params={"org": "northwind"})
    assert by_link.status_code == 307 and by_link.headers["location"].startswith(
        "/api/v1/saml/northwind/login?"
    )
    by_address = await client.get("/api/v1/auth/login", params={"login_hint": "ann@globex.example"})
    assert by_address.headers["location"].startswith("/api/v1/saml/northwind/login?")
    async with session_scope(orgs="*") as session:
        from tranibor_api.tenant_sign_in import SignInHint, resolve

        assert await resolve(session, SignInHint(org=None, email_domain="unrelated.example")) is None


async def test_a_member_signs_in_through_the_provider(app: Any, configured: kit.Idp) -> None:
    member = await browser(app)
    try:
        request_id = await start(member)
        done = await post(member, response_for(configured, request_id, groups=("Devs",)))
        assert done.status_code == 307, done.text
        assert done.headers["location"] == "/projects"
        assert member.cookies.get("choregos_session")
        me = await member.get("/api/v1/me")
        assert me.status_code == 200 and me.json()["email"] == "ann@globex.example"
    finally:
        await member.aclose()
    async with session_scope(orgs="*") as session:
        roles = (
            (
                await session.execute(
                    select(Membership.role)
                    .join(User, User.id == Membership.user_id)
                    .where(User.email == "ann@globex.example")
                )
            )
            .scalars()
            .all()
        )
    assert list(roles) == ["developer"]


async def test_a_sign_in_that_maps_no_group_grants_no_membership(app: Any, configured: kit.Idp) -> None:
    """The provider vouches for the person, not for a role: without a mapped group nothing is granted."""
    member = await browser(app)
    try:
        request_id = await start(member)
        await post(
            member, response_for(configured, request_id, groups=("choregos:northwind:org-admins", "Nope"))
        )
    finally:
        await member.aclose()
    async with session_scope(orgs="*") as session:
        rows = (
            await session.execute(
                select(Membership.id)
                .join(User, User.id == Membership.user_id)
                .where(User.email == "ann@globex.example")
            )
        ).all()
    assert rows == []


async def test_a_sign_in_keeps_the_subject_of_an_existing_account_and_touches_no_other_organisation(
    app: Any, configured: kit.Idp
) -> None:
    async with session_scope(orgs="*") as session:
        other = Organization(slug="acme", name="Acme")
        known = User(email="ann@globex.example", display_name="Ann", oidc_sub="keycloak-ann")
        session.add_all([other, known])
        await session.flush()
        session.add(Membership(org_id=other.id, user_id=known.id, role="viewer"))
    member = await browser(app)
    try:
        request_id = await start(member)
        assert (
            await post(member, response_for(configured, request_id, groups=("Admins",)))
        ).status_code == 307
    finally:
        await member.aclose()
    async with session_scope(orgs="*") as session:
        user = (await session.execute(select(User).where(User.email == "ann@globex.example"))).scalar_one()
        rows = (
            await session.execute(
                select(Organization.slug, Membership.role)
                .join(Membership, Membership.org_id == Organization.id)
                .where(Membership.user_id == user.id)
            )
        ).all()
    assert user.oidc_sub == "keycloak-ann"  # never replaced by the SAML subject
    assert dict(rows) == {
        "acme": "viewer",
        "northwind": "org_admin",
    }  # acme untouched; northwind from the mapped group


async def test_the_provider_cannot_start_a_sign_in_by_itself(app: Any, configured: kit.Idp) -> None:
    member = await browser(app)
    try:
        refused_ = await post(member, response_for(configured, REQUEST))
        assert refused_.status_code == 401 and "provider-initiated" in refused_.text
        assert not member.cookies.get("choregos_session")
    finally:
        await member.aclose()


async def test_an_assertion_for_another_request_is_refused(app: Any, configured: kit.Idp) -> None:
    member = await browser(app)
    try:
        await start(member)
        assert (await post(member, response_for(configured, "_not-ours"))).status_code == 401
        assert not member.cookies.get("choregos_session")
    finally:
        await member.aclose()


async def test_an_assertion_is_good_once(app: Any, configured: kit.Idp) -> None:
    member = await browser(app)
    try:
        request_id = await start(member)
        encoded = response_for(configured, request_id)
        assert (await post(member, encoded)).status_code == 307
        # The same request cookie again (a captured response replayed with a captured cookie).
        member.cookies.set(
            "choregos_saml",
            sign_session(
                {"id": request_id, "org": "northwind", "target": "/", "exp": int(time.time()) + 300},
                purpose="saml-request",
            ),
        )
        replay = await post(member, encoded)
        assert replay.status_code == 401 and "already consumed" in replay.text
    finally:
        await member.aclose()
    async with session_scope(orgs="*") as session:
        assert (await session.get(SamlConsumedAssertion, "_assertion-1")) is not None


async def test_the_request_cookie_of_another_organisation_or_purpose_is_refused(
    app: Any, configured: kit.Idp
) -> None:
    member = await browser(app)
    try:
        encoded = response_for(configured, REQUEST)
        soon = int(time.time()) + 300
        member.cookies.set(
            "choregos_saml",
            sign_session({"id": REQUEST, "org": "other", "exp": soon}, purpose="saml-request"),
        )
        assert (await post(member, encoded)).status_code == 401
        member.cookies.set(
            "choregos_saml", sign_session({"id": REQUEST, "org": "northwind", "exp": soon})
        )  # a session-purpose tag
        assert (await post(member, encoded)).status_code == 401
        member.cookies.set(
            "choregos_saml",
            sign_session({"id": REQUEST, "org": "northwind", "exp": 1}, purpose="saml-request"),
        )
        assert (await post(member, encoded)).status_code == 401  # expired
    finally:
        await member.aclose()


async def test_a_signature_by_another_key_opens_no_session(app: Any, configured: kit.Idp) -> None:
    member = await browser(app)
    try:
        request_id = await start(member)
        forged = response_for(kit.Idp(), request_id, groups=("Admins",))
        assert (await post(member, forged)).status_code == 401
        assert not member.cookies.get("choregos_session")
    finally:
        await member.aclose()
    async with session_scope(orgs="*") as session:
        assert (await session.execute(select(User).where(User.email == "ann@globex.example"))).first() is None


async def test_an_organisation_s_provider_cannot_sign_in_another_organisation_s_address(
    app: Any, configured: kit.Idp
) -> None:
    member = await browser(app)
    try:
        request_id = await start(member)
        assert (
            await post(member, response_for(configured, request_id, email="ceo@northwind.dev"))
        ).status_code == 401
    finally:
        await member.aclose()


async def test_the_login_target_is_a_safe_redirect(app: Any, configured: kit.Idp) -> None:
    member = await browser(app)
    try:
        started = await member.get(
            "/api/v1/saml/northwind/login", params={"redirect_to": "https://evil.example/x"}
        )
        assert started.status_code == 307
        request_id = request_id_of(started.headers["location"])
        done = await post(member, response_for(configured, request_id))
        assert not done.headers["location"].startswith("https://evil.example")
    finally:
        await member.aclose()


async def test_the_authn_request_names_our_service_and_escapes_the_provider_url() -> None:
    url = engine.authn_request("northwind", 'https://idp.example/sso?a=1&b="2"', "_id", NOW)
    assert url.startswith('https://idp.example/sso?a=1&b="2"&SAMLRequest=')
    xml = zlib.decompress(base64.b64decode(parse_qs(urlparse(url).query)["SAMLRequest"][0]), -15)
    root = etree.fromstring(xml)
    assert (
        root.get("ID") == "_id"
        and root.get("AssertionConsumerServiceURL") == f"{kit.API}/api/v1/saml/northwind/acs"
    )
    assert root.get("Destination") == 'https://idp.example/sso?a=1&b="2"'


@pytest.fixture(autouse=True)
def _no_extension_left_behind() -> Iterator[None]:
    yield
    from tranibor_api.edition import reset

    reset()
