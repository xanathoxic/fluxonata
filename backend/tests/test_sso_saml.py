"""SAML 2.0 : fournisseur d'identité simulé qui signe de vraies assertions (XML-DSig) ; on vérifie ce qui est accepté, et surtout ce qui est refusé."""

import base64
import datetime as dt
import uuid
import zlib
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.core.cache import cache
from lxml import etree
from rest_framework.test import APIClient
from signxml import XMLSigner, methods

from audit.models import AuditEvent
from tenancy import sso_config, sso_saml
from tenancy.models import GroupRule, IdentityProvider, Membership, Role

IDP = "https://idp.example.org/saml"
SSO_URL = "https://idp.example.org/saml/sso"
BASE = "http://testserver/api/v1/auth/sso/adfs"
ACS = f"{BASE}/acs/"
SP = f"{BASE}/metadata/"
SAMLP, SAML = "urn:oasis:names:tc:SAML:2.0:protocol", "urn:oasis:names:tc:SAML:2.0:assertion"


def make_cert(cn="idp"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return (
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode(),
        cert.public_bytes(serialization.Encoding.PEM).decode(),
    )


KEY, CERT = make_cert()
ROGUE_KEY, ROGUE_CERT = make_cert("rogue")


def iso(delta=0):
    return (dt.datetime.now(dt.UTC) + dt.timedelta(seconds=delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def sign(el, key=KEY, cert=CERT):
    return XMLSigner(
        method=methods.enveloped,
        signature_algorithm="rsa-sha256",
        digest_algorithm="sha256",
        c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#",
    ).sign(el, key=key, cert=cert, id_attribute="ID")


def assertion(
    req_id,
    *,
    groups=("orch-designers",),
    issuer=IDP,
    audience=SP,
    recipient=ACS,
    after=300,
    before=-5,
    in_response_to=None,
    name_id="user-42",
    email="marie@example.org",
    aid=None,
):
    a = etree.Element(
        f"{{{SAML}}}Assertion",
        nsmap={"saml": SAML},
        ID=aid or "_a" + uuid.uuid4().hex,
        Version="2.0",
        IssueInstant=iso(),
    )
    etree.SubElement(a, f"{{{SAML}}}Issuer").text = issuer
    subj = etree.SubElement(a, f"{{{SAML}}}Subject")
    nid = etree.SubElement(subj, f"{{{SAML}}}NameID", Format="urn:oasis:names:tc:SAML:2.0:nameid-format:persistent")
    nid.text = name_id
    conf = etree.SubElement(subj, f"{{{SAML}}}SubjectConfirmation", Method="urn:oasis:names:tc:SAML:2.0:cm:bearer")
    etree.SubElement(
        conf,
        f"{{{SAML}}}SubjectConfirmationData",
        Recipient=recipient,
        InResponseTo=in_response_to or req_id,
        NotOnOrAfter=iso(after),
    )
    cond = etree.SubElement(a, f"{{{SAML}}}Conditions", NotBefore=iso(before), NotOnOrAfter=iso(after))
    etree.SubElement(etree.SubElement(cond, f"{{{SAML}}}AudienceRestriction"), f"{{{SAML}}}Audience").text = audience
    stmt = etree.SubElement(a, f"{{{SAML}}}AttributeStatement")
    for name, values in (
        ("uid", ["marie"]),
        ("mail", [email]),
        ("displayName", ["Marie Curie"]),
        ("memberOf", list(groups)),
    ):
        attr = etree.SubElement(stmt, f"{{{SAML}}}Attribute", Name=name)
        for v in values:
            etree.SubElement(attr, f"{{{SAML}}}AttributeValue").text = v
    return a


def response(
    req_id, a, *, destination=ACS, status="urn:oasis:names:tc:SAML:2.0:status:Success", in_response_to=None, issuer=IDP
):
    r = etree.Element(
        f"{{{SAMLP}}}Response",
        nsmap={"samlp": SAMLP, "saml": SAML},
        ID="_r" + uuid.uuid4().hex,
        Version="2.0",
        IssueInstant=iso(),
        Destination=destination,
        InResponseTo=in_response_to or req_id,
    )
    etree.SubElement(r, f"{{{SAML}}}Issuer").text = issuer
    etree.SubElement(etree.SubElement(r, f"{{{SAMLP}}}Status"), f"{{{SAMLP}}}StatusCode", Value=status)
    if a is not None:
        r.append(a)
    return r


@pytest.fixture
def provider(world):
    cache.clear()
    cfg, problems = sso_config.clean("saml", {"idp_entity_id": IDP, "idp_sso_url": SSO_URL, "idp_certs": [CERT]})
    assert not problems
    p = IdentityProvider.objects.create(slug="adfs", name="AD FS", kind="saml", enabled=True, config=cfg)
    GroupRule.objects.create(
        provider=p, group="orch-designers", workspace=world["ws_a"], role=Role.DESIGNER, grant="workspace"
    )
    return p


def start(client):
    r = client.get("/api/v1/auth/sso/adfs/login/")
    assert r.status_code == 302 and r["Location"].startswith(SSO_URL + "?")
    q = {k: v[0] for k, v in parse_qs(urlparse(r["Location"]).query).items()}
    xml = zlib.decompress(base64.b64decode(q["SAMLRequest"]), -15)
    req = etree.fromstring(xml)
    return req, q["RelayState"]


def post(client, root, relay, *, raw=None):
    data = raw if raw is not None else etree.tostring(root)
    return client.post(
        "/api/v1/auth/sso/adfs/acs/", {"SAMLResponse": base64.b64encode(data).decode(), "RelayState": relay}
    )


def error_of(r) -> str:
    assert r.status_code == 302 and r["Location"].startswith("/?sso_error="), r["Location"]
    return unquote(r["Location"].split("sso_error=", 1)[1]).replace("+", " ")


def run(provider, *, build=None, signed="assertion", **kw):
    """Une connexion complète. `build(req_id, assertion)` renvoie la réponse ; `signed` : assertion, response, both ou none."""
    c = APIClient()
    req, relay = start(c)
    req_id = req.get("ID")
    a = assertion(req_id, **{k: v for k, v in kw.items() if k in assertion.__code__.co_varnames})
    if signed in ("assertion", "both"):
        a = sign(a)
    r = build(req_id, a) if build else response(req_id, a)
    if signed in ("response", "both"):
        r = sign(r)
    return c, post(c, r, relay)


# ---- la demande ------------------------------------------------------------------------------------------------------------
def test_the_authn_request_targets_our_acs_and_names_our_entity(provider):
    req, relay = start(APIClient())
    assert (
        req.get("AssertionConsumerServiceURL") == ACS
        and req.get("Destination") == SSO_URL
        and req.get("ID").startswith("_")
    )
    assert req.find(f"{{{SAML}}}Issuer").text == SP and relay


def test_sp_metadata_is_public_and_gives_the_acs(provider):
    r = APIClient().get("/api/v1/auth/sso/adfs/metadata/")
    root = etree.fromstring(r.content)
    assert r["Content-Type"].startswith("application/samlmetadata+xml") and root.get("entityID") == SP
    acs = root.find(".//{urn:oasis:names:tc:SAML:2.0:metadata}AssertionConsumerService")
    assert acs.get("Location") == ACS and acs.get("Binding").endswith("HTTP-POST")
    assert APIClient().get("/api/v1/auth/sso/unknown/metadata/").status_code == 404


# ---- ce qui est accepté ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("signed", ["assertion", "response", "both"])
def test_a_signed_response_logs_the_person_in_and_applies_the_rules(provider, world, signed):
    c, r = run(provider, signed=signed)
    assert r.status_code == 302 and r["Location"] == "/", r["Location"]
    me = c.get("/api/v1/auth/me/").json()
    assert me["username"] == "sso.marie" and [(w["slug"], w["role"]) for w in me["workspaces"]] == [
        ("team-a", "designer")
    ]
    assert Membership.objects.get(user__username="sso.marie").source == "sso:adfs"
    assert AuditEvent.objects.filter(action="auth.login", data__provider="adfs", data__method="saml").exists()


def test_a_second_certificate_is_accepted_for_key_rollover(provider):
    provider.config = {**provider.config, "idp_certs": [ROGUE_CERT, CERT]}
    provider.save()
    assert run(provider)[1]["Location"] == "/"


# ---- ce qui est refusé -----------------------------------------------------------------------------------------------------
def test_a_signature_by_another_key_is_refused(provider):
    c = APIClient()
    req, relay = start(c)
    a = sign(assertion(req.get("ID")), key=ROGUE_KEY, cert=ROGUE_CERT)
    assert "Signature" in error_of(post(c, response(req.get("ID"), a), relay))
    assert not Membership.objects.exists() or not Membership.objects.filter(source="sso:adfs").exists()


def test_a_tampered_assertion_is_refused(provider):
    c = APIClient()
    req, relay = start(c)
    xml = etree.tostring(sign(assertion(req.get("ID"), groups=("viewers",)))).replace(b"viewers", b"orch-designers")
    r = response(req.get("ID"), etree.fromstring(xml))
    assert "Signature" in error_of(post(c, r, relay))


def test_nothing_signed_is_refused(provider):
    assert "signées" in error_of(run(provider, signed="none")[1])


def test_signature_wrapping_cannot_smuggle_an_unsigned_assertion(provider):
    """Une assertion signée valide est gardée comme « pièce jointe » pendant qu'une assertion non signée prend la place visible."""
    c = APIClient()
    req, relay = start(c)
    good = sign(assertion(req.get("ID"), groups=("viewers",)))
    evil = assertion(req.get("ID"), groups=("orch-designers",), name_id="admin-sub")
    r = response(req.get("ID"), evil)
    wrapper = etree.SubElement(etree.SubElement(r, "{urn:oasis:names:tc:SAML:2.0:protocol}Extensions"), "Wrap")
    wrapper.append(good)
    assert error_of(post(c, r, relay))
    assert not Membership.objects.filter(source="sso:adfs").exists()


def test_two_assertions_are_refused(provider):
    def build(req_id, a):
        r = response(req_id, a)
        r.append(sign(assertion(req_id)))
        return r

    assert "seule assertion" in error_of(run(provider, build=build)[1])


@pytest.mark.parametrize(
    "kw,fragment",
    [
        ({"audience": "https://autre.example/sp"}, "public"),
        ({"recipient": "https://autre.example/acs"}, "destinée"),
        ({"issuer": "https://evil.example/idp"}, "Émetteur"),
        ({"after": -3600}, "validité"),
        ({"before": 3600}, "expir"),
        ({"in_response_to": "_une-autre-demande"}, "destinée"),
    ],
)
def test_conditions_of_the_assertion_are_enforced(provider, kw, fragment):
    assert fragment in error_of(run(provider, **kw)[1])


def test_the_response_must_answer_this_session_and_this_acs(provider):
    assert "demande" in error_of(run(provider, build=lambda rid, a: response(rid, a, in_response_to="_autre"))[1])
    assert "Destination" in error_of(
        run(provider, build=lambda rid, a: response(rid, a, destination="https://autre/acs"))[1]
    )
    assert "refusé" in error_of(
        run(provider, build=lambda rid, a: response(rid, a, status="urn:oasis:names:tc:SAML:2.0:status:Requester"))[1]
    )
    assert "Émetteur" in error_of(
        run(provider, build=lambda rid, a: response(rid, a, issuer="https://evil.example"))[1]
    )


def test_a_replayed_assertion_is_refused(provider):
    c = APIClient()
    req, relay = start(c)
    r = response(req.get("ID"), sign(assertion(req.get("ID"), aid="_fixed")))
    assert post(c, r, relay)["Location"] == "/"
    c2 = APIClient()
    req2, relay2 = start(c2)
    # même assertion rejouée dans une autre session : l'identifiant d'assertion a déjà servi
    a2 = sign(assertion(req2.get("ID"), aid="_fixed"))
    assert "déjà utilisée" in error_of(post(c2, response(req2.get("ID"), a2), relay2))


def test_relay_state_session_and_encrypted_assertions(provider):
    c = APIClient()
    req, relay = start(c)
    r = response(req.get("ID"), sign(assertion(req.get("ID"))))
    assert "RelayState" in error_of(post(c, r, "forged"))
    assert "expirée" in error_of(post(APIClient(), r, relay))
    c = APIClient()
    req, relay = start(c)
    enc = response(req.get("ID"), None)
    etree.SubElement(enc, f"{{{SAML}}}EncryptedAssertion")
    assert "chiffrées" in error_of(post(c, enc, relay))


def test_xml_with_a_doctype_or_entities_is_refused_before_being_read(provider):
    c = APIClient()
    req, relay = start(c)
    bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "x"><!ENTITY b "&a;&a;&a;">]><samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol">&b;</samlp:Response>'
    assert "entités" in error_of(post(c, None, relay, raw=bomb))
    c = APIClient()
    req, relay = start(c)
    assert "invalide" in error_of(post(c, None, relay, raw=b"pas du xml"))
    with pytest.raises(sso_saml.SamlError):
        sso_saml.parse_xml(b"x" * 2_000_000)


def test_unmapped_deny_and_disabled_provider(provider, world):
    provider.unmapped = IdentityProvider.Unmapped.DENY
    provider.save()
    assert "aucun groupe autorisé" in error_of(run(provider, groups=("autres",))[1])
    provider.enabled = False
    provider.save()
    assert APIClient().get("/api/v1/auth/sso/adfs/login/").status_code == 404
    assert APIClient().post("/api/v1/auth/sso/adfs/acs/", {}).status_code == 404


# ---- métadonnées du fournisseur --------------------------------------------------------------------------------------------
def idp_metadata(sso=True, cert=True, entity=IDP, idp=True):
    body = "".join(CERT.splitlines()[1:-1])
    md = f'<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" xmlns:ds="http://www.w3.org/2000/09/xmldsig#" entityID="{entity}">'
    if idp:
        md += '<md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
        if cert:
            md += f'<md:KeyDescriptor use="signing"><ds:KeyInfo><ds:X509Data><ds:X509Certificate>{body}</ds:X509Certificate></ds:X509Data></ds:KeyInfo></md:KeyDescriptor>'
        md += "<md:NameIDFormat>urn:oasis:names:tc:SAML:2.0:nameid-format:persistent</md:NameIDFormat>"
        if sso:
            md += f'<md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" Location="https://idp/post"/><md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="{SSO_URL}"/>'
        md += "</md:IDPSSODescriptor>"
    return (md + "</md:EntityDescriptor>").encode()


def test_idp_metadata_is_read_into_settings_and_the_certificate_matches(provider):
    out = sso_saml.read_idp_metadata(idp_metadata())
    assert (
        out["idp_entity_id"] == IDP and out["idp_sso_url"] == SSO_URL and out["name_id_format"].endswith("persistent")
    )
    assert len(out["idp_certs"]) == 1 and sso_config.clean("saml", out)[1] == {}
    # le certificat relu vérifie bien une signature faite avec la clé de test
    provider.config = {**provider.config, "idp_certs": out["idp_certs"]}
    provider.save()
    assert run(provider)[1]["Location"] == "/"


@pytest.mark.parametrize("kw", [{"sso": False}, {"cert": False}, {"idp": False}])
def test_incomplete_idp_metadata_is_refused(kw):
    with pytest.raises(sso_saml.SamlError):
        sso_saml.read_idp_metadata(idp_metadata(**kw))


def test_the_metadata_import_route_and_the_certificate_test(provider, world, monkeypatch):
    from tests.conftest import client_for, make_user

    root = make_user("root")
    root.is_superuser = True
    root.save()
    admin = client_for(root)
    r = admin.post("/api/v1/platform/sso/saml/import-metadata/", {"xml": idp_metadata().decode()}, format="json")
    assert r.status_code == 200 and r.json()["idp_sso_url"] == SSO_URL
    assert admin.post("/api/v1/platform/sso/saml/import-metadata/", {"xml": "<x/>"}, format="json").status_code == 400
    assert admin.post("/api/v1/platform/sso/saml/import-metadata/", {}, format="json").status_code == 400
    from core import netsec

    monkeypatch.setattr(netsec, "safe_request", lambda *a, **k: (200, {}, idp_metadata()))
    assert (
        admin.post(
            "/api/v1/platform/sso/saml/import-metadata/", {"url": "https://idp.example.org/metadata"}, format="json"
        ).status_code
        == 200
    )
    t = admin.post("/api/v1/platform/sso/providers/adfs/test/", format="json").json()
    assert (
        t["ok"] is True
        and t["detail"]["certificates"][0]["expired"] is False
        and t["detail"]["certificates"][0]["days_left"] > 300
    )
