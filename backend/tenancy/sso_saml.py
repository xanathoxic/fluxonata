"""SAML 2.0, côté fournisseur de service (SP) : connexion initiée par l'application (AuthnRequest, liaison HTTP-Redirect) et réponse reçue en
POST (liaison HTTP-POST, point `acs`).

Ce qui est vérifié, dans cet ordre, avant de faire confiance à quoi que ce soit :
- le XML est lu sans entités ni DTD ni accès réseau (pas de XXE), taille bornée ;
- la signature (XML-DSig, par `signxml`) est vérifiée avec les certificats ÉPINGLÉS dans les réglages, sur la réponse ou sur l'assertion, et
  seul l'élément réellement signé est ensuite lu (contre la substitution d'élément, « signature wrapping ») ;
- une seule assertion, non chiffrée (les assertions chiffrées ne sont pas prises en charge : refusées avec un message clair) ;
- statut de succès, `InResponseTo` = la demande faite depuis cette session, destinataire (`Recipient`/`Destination`) = notre point `acs`,
  émetteur = le fournisseur réglé, public (`Audience`) = notre identifiant, fenêtre de validité, assertion jamais rejouée.
La personne est liée par (émetteur, NameID), jamais par l'adresse e-mail."""

import base64
import secrets
import time
import zlib
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from cryptography import x509
from django.core.cache import cache
from lxml import etree
from signxml import SignatureConfiguration, XMLVerifier
from signxml.exceptions import InvalidCertificate, InvalidDigest, InvalidInput, InvalidSignature

from core import netsec

from . import sso_config
from .models import IdentityProvider
from .sso_engine import Identity, clean_groups

NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
    "md": "urn:oasis:names:tc:SAML:2.0:metadata",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
}
Q = {k: f"{{{v}}}" for k, v in NS.items()}
STATE_TTL = 600
MAX_XML = 1_000_000
SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
BEARER = "urn:oasis:names:tc:SAML:2.0:cm:bearer"
REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"


class SamlError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def parse_xml(data: bytes) -> etree._Element:
    """Lecture sûre : ni DTD, ni entités, ni réseau. Un XML qui en déclare est refusé avant même d'être lu."""
    if len(data) > MAX_XML:
        raise SamlError("Document XML trop volumineux.")
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in data.lower():
        raise SamlError("Document XML refusé : il déclare un type de document ou des entités.")
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        remove_comments=True,
    )
    try:
        return etree.fromstring(data, parser)
    except etree.XMLSyntaxError:
        raise SamlError("Document XML invalide.") from None


# ---- métadonnées -----------------------------------------------------------------------------------------------------------
def _pem(b64: str) -> str:
    body = "".join(b64.split())
    return (
        "-----BEGIN CERTIFICATE-----\n"
        + "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
        + "\n-----END CERTIFICATE-----"
    )


def read_idp_metadata(data: bytes) -> dict:
    """Les réglages d'un fournisseur lus dans ses métadonnées : identifiant, adresse de connexion (HTTP-Redirect), certificats de signature."""
    root = parse_xml(data)
    if root.tag == Q["md"] + "EntitiesDescriptor":
        found = root.findall(Q["md"] + "EntityDescriptor")
        if len(found) != 1:
            raise SamlError("Ces métadonnées décrivent plusieurs fournisseurs : fournissez celles d'un seul.")
        root = found[0]
    if root.tag != Q["md"] + "EntityDescriptor":
        raise SamlError("Ce ne sont pas des métadonnées SAML (EntityDescriptor attendu).")
    idp = root.find(Q["md"] + "IDPSSODescriptor")
    if idp is None:
        raise SamlError("Ces métadonnées ne décrivent pas un fournisseur d'identité (IDPSSODescriptor absent).")
    sso = [(s.get("Binding"), s.get("Location")) for s in idp.findall(Q["md"] + "SingleSignOnService")]
    url = next((loc for b, loc in sso if b == REDIRECT and loc), None)
    if url is None:
        raise SamlError("Le fournisseur ne propose pas la connexion par redirection HTTP (HTTP-Redirect).")
    certs: list[str] = []
    for kd in idp.findall(Q["md"] + "KeyDescriptor"):
        if kd.get("use") in (None, "signing"):
            certs += [_pem(c.text or "") for c in kd.iter(Q["ds"] + "X509Certificate") if (c.text or "").strip()]
    if not certs:
        raise SamlError("Aucun certificat de signature dans ces métadonnées.")
    fmt = next(
        (
            f.text.strip()
            for f in idp.findall(Q["md"] + "NameIDFormat")
            if f.text and f.text.strip() in sso_config.NAME_ID_FORMATS
        ),
        "",
    )
    return {
        "idp_entity_id": root.get("entityID", ""),
        "idp_sso_url": url,
        "idp_certs": certs[:4],
        "name_id_format": fmt,
    }


def fetch_metadata(url: str) -> bytes:
    try:
        status, _, body = netsec.safe_request(
            "GET",
            url,
            headers={"Accept": "application/samlmetadata+xml, application/xml, text/xml"},
            timeout=8,
            max_bytes=MAX_XML,
        )
    except (netsec.UnsafeURL, OSError, ValueError) as exc:
        raise SamlError(f"Métadonnées injoignables ou adresse refusée ({type(exc).__name__}).") from None
    if status != 200:
        raise SamlError("Le fournisseur d'identité a répondu avec une erreur.")
    return body


def sp_metadata(provider: IdentityProvider, request=None) -> bytes:
    """Les métadonnées de CE service, à donner au fournisseur d'identité (ou son identifiant et son point `acs` à saisir à la main)."""
    ep = sso_config.endpoints(provider, request)
    md = etree.Element(Q["md"] + "EntityDescriptor", nsmap={"md": NS["md"]}, entityID=ep["entity_id"])
    sp = etree.SubElement(
        md,
        Q["md"] + "SPSSODescriptor",
        AuthnRequestsSigned="false",
        WantAssertionsSigned="true",
        protocolSupportEnumeration=NS["samlp"],
    )
    fmt = provider.config.get("name_id_format") or "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified"
    etree.SubElement(sp, Q["md"] + "NameIDFormat").text = fmt
    etree.SubElement(
        sp, Q["md"] + "AssertionConsumerService", Binding=POST, Location=ep["acs"], index="0", isDefault="true"
    )
    return etree.tostring(md, xml_declaration=True, encoding="UTF-8")


# ---- demande ---------------------------------------------------------------------------------------------------------------
def begin(request, provider: IdentityProvider) -> str:
    cfg = provider.config
    ep = sso_config.endpoints(provider, request)
    req_id = "_" + secrets.token_hex(20)
    relay = secrets.token_urlsafe(18)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    root = etree.Element(
        Q["samlp"] + "AuthnRequest",
        nsmap={"samlp": NS["samlp"], "saml": NS["saml"]},
        ID=req_id,
        Version="2.0",
        IssueInstant=now,
        Destination=cfg["idp_sso_url"],
        ProtocolBinding=POST,
        AssertionConsumerServiceURL=ep["acs"],
    )
    if cfg.get("force_authn"):
        root.set("ForceAuthn", "true")
    etree.SubElement(root, Q["saml"] + "Issuer").text = ep["entity_id"]
    pol = etree.SubElement(root, Q["samlp"] + "NameIDPolicy", AllowCreate="true")
    if cfg.get("name_id_format"):
        pol.set("Format", cfg["name_id_format"])
    raw = etree.tostring(root)
    deflater = zlib.compressobj(9, zlib.DEFLATED, -15)
    packed = base64.b64encode(deflater.compress(raw) + deflater.flush()).decode()
    request.session["sso"] = {"slug": provider.slug, "saml_id": req_id, "relay": relay, "at": time.time()}
    sep = "&" if "?" in cfg["idp_sso_url"] else "?"
    return f"{cfg['idp_sso_url']}{sep}{urlencode({'SAMLRequest': packed, 'RelayState': relay})}"


# ---- réponse ---------------------------------------------------------------------------------------------------------------
def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise SamlError("Date invalide dans la réponse du fournisseur.") from None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _verify(element: etree._Element, certs: list[str]) -> etree._Element:
    """L'élément tel qu'il est signé (vérifié contre l'un des certificats épinglés). Seule la signature enfant DIRECT de l'élément compte (celle d'une
    assertion imbriquée n'est pas prise pour celle de la réponse) ; tout le reste du document est ignoré."""
    last: Exception | None = None
    for pem in certs:
        try:
            results = XMLVerifier().verify(
                etree.fromstring(etree.tostring(element)),
                x509_cert=pem,
                expect_references=1,
                expect_config=SignatureConfiguration(location="./"),
            )
        except (InvalidSignature, InvalidDigest, InvalidCertificate, InvalidInput) as exc:
            last = exc
            continue
        result = results[0] if isinstance(results, list) else results
        if result.signed_xml is None:
            raise SamlError("Signature vérifiée mais aucun élément signé n'a pu être lu.")
        return result.signed_xml
    raise SamlError(f"Signature absente ou invalide ({type(last).__name__ if last else 'aucun certificat'}).")


def _signed_children(element: etree._Element) -> bool:
    return element.find(Q["ds"] + "Signature") is not None


def _check_status(root: etree._Element) -> None:
    code = root.find(f"{Q['samlp']}Status/{Q['samlp']}StatusCode")
    if code is None or code.get("Value") != SUCCESS:
        raise SamlError("Le fournisseur d'identité a refusé la connexion.")


def _text(parent: etree._Element, path: str) -> str:
    node = parent.find(path)
    return (node.text or "").strip() if node is not None else ""


def _check_assertion(assertion: etree._Element, provider: IdentityProvider, request, sess: dict) -> None:
    cfg = provider.config
    skew = timedelta(seconds=int(cfg.get("clock_skew", 60)))
    now = datetime.now(UTC)
    ep = sso_config.endpoints(provider, request)
    if _text(assertion, Q["saml"] + "Issuer") != cfg["idp_entity_id"]:
        raise SamlError("Émetteur inattendu dans l'assertion.")
    confirmations = [
        c for c in assertion.findall(f"{Q['saml']}Subject/{Q['saml']}SubjectConfirmation") if c.get("Method") == BEARER
    ]
    ok = False
    for c in confirmations:
        data = c.find(Q["saml"] + "SubjectConfirmationData")
        if data is None or data.get("Recipient") != ep["acs"] or data.get("InResponseTo") != sess["saml_id"]:
            continue
        until = _parse_time(data.get("NotOnOrAfter"))
        if until is None or until + skew <= now:
            continue
        ok = True
    if not ok:
        raise SamlError("L'assertion n'est pas destinée à cette demande (destinataire, demande d'origine ou validité).")
    cond = assertion.find(Q["saml"] + "Conditions")
    if cond is None:
        raise SamlError("Conditions de validité absentes de l'assertion.")
    before, after = _parse_time(cond.get("NotBefore")), _parse_time(cond.get("NotOnOrAfter"))
    if (before and before - skew > now) or after is None or after + skew <= now:
        raise SamlError("Assertion expirée ou pas encore valide (vérifiez l'horloge des serveurs).")
    audiences = [(a.text or "").strip() for a in cond.findall(f"{Q['saml']}AudienceRestriction/{Q['saml']}Audience")]
    if ep["entity_id"] not in audiences:
        raise SamlError("L'assertion n'est pas destinée à ce service (public / Audience).")


def _attributes(assertion: etree._Element) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for attr in assertion.findall(f"{Q['saml']}AttributeStatement/{Q['saml']}Attribute"):
        values = [(v.text or "").strip() for v in attr.findall(Q["saml"] + "AttributeValue") if (v.text or "").strip()]
        for key in (attr.get("Name"), attr.get("FriendlyName")):
            if key:
                out.setdefault(key, []).extend(values)
    return out


def _read_response(request, provider: IdentityProvider, sess: dict) -> etree._Element:
    """La réponse décodée et ses contrôles d'enveloppe (demande d'origine, destination, émetteur, statut, pas d'assertion chiffrée)."""
    if not secrets.compare_digest(request.POST.get("RelayState", ""), sess["relay"]):
        raise SamlError("RelayState invalide (possible tentative de falsification).")
    try:
        raw = base64.b64decode(request.POST.get("SAMLResponse", ""), validate=False)
    except ValueError:
        raise SamlError("Réponse SAML illisible.") from None
    root = parse_xml(raw)
    if root.tag != Q["samlp"] + "Response":
        raise SamlError("Réponse SAML attendue.")
    cfg = provider.config
    if root.get("InResponseTo") != sess["saml_id"]:
        raise SamlError("La réponse ne répond pas à la demande faite depuis cette session.")
    if root.get("Destination") and root.get("Destination") != sso_config.endpoints(provider, request)["acs"]:
        raise SamlError("Destination inattendue dans la réponse.")
    issuer = _text(root, Q["saml"] + "Issuer")
    if issuer and issuer != cfg["idp_entity_id"]:
        raise SamlError("Émetteur inattendu dans la réponse.")
    _check_status(root)
    if root.find(Q["saml"] + "EncryptedAssertion") is not None:
        raise SamlError(
            "Les assertions chiffrées ne sont pas prises en charge : demandez au fournisseur de les envoyer signées, sans chiffrement."
        )
    return root


def _signed_assertion(root: etree._Element, certs: list[str]) -> etree._Element:
    """L'unique assertion de la réponse, telle qu'elle est signée : par sa propre signature, ou par celle de la réponse qui la contient."""
    scope = _verify(root, certs) if _signed_children(root) else None
    assertions = (scope if scope is not None else root).findall(Q["saml"] + "Assertion")
    if len(assertions) != 1:
        raise SamlError("Une seule assertion est attendue dans la réponse.")
    assertion = assertions[0]
    if _signed_children(assertion):
        assertion = _verify(assertion, certs)
        if assertion.tag != Q["saml"] + "Assertion":
            raise SamlError("Élément signé inattendu.")
    elif scope is None:  # ni la réponse ni l'assertion ne portent de signature
        raise SamlError("Ni la réponse ni l'assertion ne sont signées : connexion refusée.")
    return assertion


def _identity(assertion: etree._Element, provider: IdentityProvider) -> Identity:
    cfg = provider.config
    name_id = assertion.find(f"{Q['saml']}Subject/{Q['saml']}NameID")
    subject = (name_id.text or "").strip() if name_id is not None else ""
    if not subject:
        raise SamlError("Identifiant de la personne (NameID) absent de l'assertion.")
    attrs = _attributes(assertion)
    names = {**sso_config.DEFAULT_SAML_ATTRS, **(cfg.get("attributes") or {})}

    def one(key: str) -> str:
        return (attrs.get(names[key]) or [""])[0]

    by_mail = name_id is not None and name_id.get("Format", "").endswith("emailAddress")
    return Identity(
        provider=provider,
        issuer=cfg["idp_entity_id"],
        subject=subject,
        username=one("username"),
        email=one("email") or (subject if by_mail else ""),
        name=one("name"),
        groups=clean_groups(attrs.get(names["groups"])),
    )


def complete(request, provider: IdentityProvider) -> Identity:
    sess = request.session.pop("sso", None)
    if not sess or sess.get("slug") != provider.slug or "saml_id" not in sess or time.time() - sess["at"] > STATE_TTL:
        raise SamlError("Session de connexion expirée, recommencez.")
    root = _read_response(request, provider, sess)
    assertion = _signed_assertion(root, provider.config["idp_certs"])
    _check_assertion(assertion, provider, request, sess)
    aid = assertion.get("ID") or ""
    if not aid or not cache.add(f"sso:saml:seen:{provider.slug}:{aid}", 1, 3600):
        raise SamlError("Assertion déjà utilisée (rejeu refusé).")
    return _identity(assertion, provider)


def test(provider: IdentityProvider) -> dict:
    """Essai sans connexion : lecture des certificats épinglés (validité, empreinte) et des adresses de ce service."""
    from cryptography.hazmat.primitives import hashes

    now = datetime.now(UTC)
    certs = []
    for pem in provider.config["idp_certs"]:
        try:
            cert = x509.load_pem_x509_certificate(pem.encode())
        except ValueError:
            raise SamlError("Un certificat n'est pas lisible (PEM invalide).") from None
        end = cert.not_valid_after_utc
        certs.append(
            {
                "subject": cert.subject.rfc4514_string()[:200],
                "not_after": end.isoformat(),
                "days_left": (end - now).days,
                "expired": end < now or cert.not_valid_before_utc > now,
                "sha256": cert.fingerprint(hashes.SHA256()).hex(":"),
            }
        )
    return {
        "certificates": certs,
        "idp_entity_id": provider.config["idp_entity_id"],
        "idp_sso_url": provider.config["idp_sso_url"],
    }
