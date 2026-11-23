"""Image de marque de la plate-forme (ADR 0033) : droits, couleurs (contraste AA), images (SVG assaini), service public, audit."""

import struct
import zlib

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from audit.models import AuditEvent
from tenancy import branding as b
from tenancy.models import Branding, BrandingAsset
from tests.conftest import client_for, make_user

ADMIN = "/api/v1/platform/branding/"
PUBLIC = "/api/v1/branding/"
SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40"><defs><linearGradient id="g"><stop offset="0" stop-color="#0e7490"/>'
    b'<stop offset="1" stop-color="#1d4ed8"/></linearGradient></defs><path d="M0 0H100V40H0Z" fill="url(#g)"/></svg>'
)


def png(w=64, h=64) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\x10\x80\xa0" * w for _ in range(h))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def jpeg(w=40, h=20) -> bytes:
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8\xff\xe0\x00\x04\x00\x00" + sof + b"\xff\xd9"


def webp(w=30, h=30) -> bytes:
    body = (
        b"WEBPVP8X"
        + struct.pack("<I", 10)
        + b"\x00\x00\x00\x00"
        + (w - 1).to_bytes(3, "little")
        + (h - 1).to_bytes(3, "little")
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def up(data: bytes, name="x"):
    return {"file": SimpleUploadedFile(name, data)}


@pytest.fixture
def admin(world):
    u = make_user("root")
    u.is_superuser = True
    u.save()
    return client_for(u)


def put(c, kind, data, name="x"):
    return c.put(f"{ADMIN}assets/{kind}/", up(data, name), format="multipart")


# ───────── droits ─────────


def test_the_public_page_needs_no_session_and_starts_with_the_default_identity(db):
    from rest_framework.test import APIClient

    r = APIClient().get(PUBLIC)
    assert r.status_code == 200
    body = r.json()
    assert (
        body["name"] == "Fluxonata"
        and body["accent"] == "#0e7490"
        and body["logo"] is None
        and body["custom_name"] is False
    )
    assert "updated_by" not in body and "stored" not in body  # rien d'interne avant la connexion


def test_only_platform_administrators_may_change_it(world, admin):
    for user in (world["designer"], world["viewer"]):
        c = client_for(user)
        assert c.get(ADMIN).status_code == 403
        assert c.patch(ADMIN, {"product_name": "X"}, format="json").status_code == 403
        assert put(c, "logo", png()).status_code == 403
        assert c.delete(f"{ADMIN}assets/logo/").status_code == 403
        assert c.post(f"{ADMIN}reset/").status_code == 403
    from rest_framework.test import APIClient

    assert APIClient().patch(ADMIN, {"product_name": "X"}, format="json").status_code in (401, 403)
    assert Branding.get().product_name == ""


# ───────── réglages ─────────


def test_changes_are_applied_audited_and_versioned(admin):
    r = admin.patch(
        ADMIN,
        {
            "product_name": "  Atelier   Bleu ",
            "tagline": "Vos flux, sous contrôle",
            "login_message": "Accès réservé au personnel.",
            "footer_text": "Support : 01 23 45 67 89",
            "footer_url": "https://support.example.org/aide",
            "accent": "#0B5ED7",
            "login_style": "gradient",
        },
        format="json",
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Atelier Bleu" and body["accent"] == "#0b5ed7" and body["version"] == 2
    assert body["login_style"] == "gradient" and body["footer"]["url"] == "https://support.example.org/aide"
    pub = admin.get(PUBLIC).json()
    assert (
        pub["name"] == "Atelier Bleu" and pub["tagline"] == "Vos flux, sous contrôle" and pub["custom_accent"] is True
    )
    ev = AuditEvent.objects.get(action="platform.branding_update")
    assert ev.actor_label == "root" and "accent" in ev.data["fields"] and "0b5ed7" not in str(ev.data)


def test_a_request_that_changes_nothing_is_not_a_change(admin):
    admin.patch(ADMIN, {"product_name": "A"}, format="json")
    v = Branding.get().version
    admin.patch(ADMIN, {"product_name": "A"}, format="json")
    assert Branding.get().version == v and AuditEvent.objects.filter(action="platform.branding_update").count() == 1


def test_an_empty_value_goes_back_to_the_default(admin):
    admin.patch(ADMIN, {"product_name": "A", "accent": "#0b5ed7"}, format="json")
    body = admin.patch(ADMIN, {"product_name": "", "accent": ""}, format="json").json()
    assert body["name"] == "Fluxonata" and body["accent"] == "#0e7490" and body["custom_accent"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("accent", "rouge"),
        ("accent", "#12345"),
        ("accent", "#ffff00"),  # jaune : illisible en texte et sous un texte blanc
        ("accent", "#9ca3af"),  # gris clair
        ("footer_url", "http://non-chiffre.example.org"),
        ("footer_url", "javascript:alert(1)"),
        ("product_name", "x" * 61),
        ("login_style", "disco"),
        ("tagline", "a\x00b"),
    ],
)
def test_bad_values_are_refused_with_a_message(admin, field, value):
    r = admin.patch(ADMIN, {field: value}, format="json")
    assert r.status_code == 400 and field in str(r.json())
    assert Branding.get().version == 1


def test_the_contrast_rule_is_the_wcag_aa_threshold_on_every_background(admin):
    assert admin.patch(ADMIN, {"accent": "#0e7490"}, format="json").status_code == 200  # la couleur d'origine passe
    for ok in ("#0b5ed7", "#6d28d9", "#b91c1c", "#166534", "#0f172a"):
        assert admin.patch(ADMIN, {"accent": ok}, format="json").status_code == 200, ok
    r = admin.patch(ADMIN, {"accent": "#38bdf8"}, format="json")
    assert r.status_code == 400 and "trop claire" in str(r.json()) and "4.5:1" in str(r.json())


def test_contrast_functions_match_known_values():
    # mêmes valeurs que frontend/src/branding/palette.test.ts : les deux calculs doivent rester identiques
    assert b.contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert b.contrast("#ffffff", "#ffffff") == pytest.approx(1.0)
    assert b.contrast("#0e7490", "#ffffff") == pytest.approx(5.36, abs=0.01)
    assert b.soft("#0e7490") == "#e2eef2" and b.mix("#000000", "#ffffff", 0.5) == "#808080"
    assert b.contrast_report("#0e7490")["on_soft"] >= 4.5


def test_the_admin_view_reports_the_contrasts(admin):
    body = admin.get(ADMIN).json()
    assert body["contrast"]["minimum"] == 4.5 and body["contrast"]["on_white"] >= 4.5
    assert body["limits"]["image_bytes"] == 256 * 1024 and "SVG" in body["limits"]["formats"]


# ───────── images ─────────


@pytest.mark.parametrize(
    "data,ctype", [(png(), "image/png"), (jpeg(), "image/jpeg"), (webp(), "image/webp"), (SVG, "image/svg+xml")]
)
def test_each_format_is_accepted_and_served_with_its_real_type(admin, data, ctype):
    r = put(admin, "logo", data, name="nom-trompeur.txt")
    assert r.status_code == 200 and r.json()["logo"]["content_type"] == ctype
    url = r.json()["logo"]["url"]
    from rest_framework.test import APIClient

    got = APIClient().get(url)  # sans session
    assert got.status_code == 200 and got["Content-Type"] == ctype
    assert got["X-Content-Type-Options"] == "nosniff" and "sandbox" in got["Content-Security-Policy"]
    assert "immutable" in got["Cache-Control"]
    etag = got["ETag"]
    assert APIClient().get(url, HTTP_IF_NONE_MATCH=etag).status_code == 304


def test_the_image_changes_the_page_and_the_version(admin):
    v = admin.get(ADMIN).json()["version"]
    r = put(admin, "icon", png(32, 32))
    assert r.json()["version"] == v + 1 and r.json()["icon"]["width"] == 32
    first = r.json()["icon"]["url"]
    second = put(admin, "icon", png(48, 48)).json()["icon"]["url"]
    assert first != second  # l'empreinte est dans l'adresse : le cache du navigateur ne ressert jamais l'ancienne image
    assert BrandingAsset.objects.filter(kind="icon").count() == 1
    assert AuditEvent.objects.filter(action="platform.branding_asset_set").count() == 2


@pytest.mark.parametrize(
    "data,needle",
    [
        (b"", "vide"),
        (b"GIF89a....", "Format non pris en charge"),
        (b"MZ\x90\x00 programme", "Format non pris en charge"),
        (b"<html><script>alert(1)</script></html>", "pas un SVG"),
        (png(4000, 10), "trop grande"),
        (b"x" * (b.MAX_IMAGE_BYTES + 1), "dépasse"),
    ],
)
def test_unsupported_or_oversized_files_are_refused(admin, data, needle):
    r = put(admin, "logo", data)
    assert r.status_code == 400 and needle in str(r.json())
    assert not BrandingAsset.objects.exists()


def svg(inner: str, root_extra: str = "") -> bytes:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"{root_extra}>{inner}</svg>'.encode()


@pytest.mark.parametrize(
    "data",
    [
        svg("<script>alert(1)</script>"),
        svg('<rect width="1" height="1" onload="alert(1)"/>'),
        svg('<rect width="1" height="1" onclick="x()"/>'),
        svg("<foreignObject><div>x</div></foreignObject>"),
        svg('<image href="https://exemple.org/x.png"/>'),
        svg('<use href="#a"/>'),
        svg('<a href="javascript:alert(1)"><rect width="1" height="1"/></a>'),
        svg("<style>@import url(https://exemple.org/x.css)</style>"),
        svg('<rect width="1" height="1" fill="url(https://exemple.org/x)"/>'),
        svg('<rect width="1" height="1" style="fill:red"/>'),
        svg('<rect width="1" height="1" xlink:href="x" xmlns:xlink="http://www.w3.org/1999/xlink"/>'),
        b'<!DOCTYPE svg [<!ENTITY a "boum">]><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">&a;</svg>',
        b'<svg viewBox="0 0 10 10"><rect width="1" height="1"/></svg>',  # sans espace de noms : s'afficherait mal et échappe à la liste
        svg('<rect width="1" height="1"/>', ' onload="x()"'),
        b"<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>",  # ni viewBox ni dimensions
        b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1 1'><rect",
    ],
)
def test_a_dangerous_or_unreadable_svg_is_refused(admin, data):
    r = put(admin, "logo", data)
    assert r.status_code == 400, data
    assert not BrandingAsset.objects.exists()


def test_a_clean_svg_is_rewritten_not_copied(admin):
    noisy = b'<?xml version="1.0"?><!-- commentaire --><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><title>Logo</title><circle cx="5" cy="5" r="4" fill="#0e7490"/></svg>'
    assert put(admin, "logo", noisy).status_code == 200
    stored = bytes(BrandingAsset.objects.get(kind="logo").data)
    assert b"commentaire" not in stored and b"<?xml" not in stored and b"<circle" in stored


def test_an_icon_must_be_roughly_square(admin):
    assert put(admin, "icon", png(100, 20)).status_code == 400
    assert put(admin, "icon", png(20, 20)).status_code == 200
    assert put(admin, "logo", png(300, 60)).status_code == 200  # un logo, lui, peut être large


def test_unknown_image_kind_and_missing_file(admin):
    assert put(admin, "banniere", png()).status_code == 404
    assert admin.put(f"{ADMIN}assets/logo/", {}, format="multipart").status_code == 400
    from rest_framework.test import APIClient

    assert APIClient().get("/api/v1/branding/assets/logo/").status_code == 404
    assert APIClient().get("/api/v1/branding/assets/banniere/").status_code == 404


def test_removing_an_image_and_resetting_everything(admin):
    put(admin, "logo", png())
    put(admin, "icon", png(16, 16))
    admin.patch(
        ADMIN,
        {"product_name": "A", "accent": "#0b5ed7", "login_style": "tint", "footer_url": "https://a.example.org"},
        format="json",
    )
    r = admin.delete(f"{ADMIN}assets/logo/")
    assert r.status_code == 200 and r.json()["logo"] is None and r.json()["icon"] is not None
    assert admin.delete(f"{ADMIN}assets/logo/").status_code == 200  # déjà retirée : sans effet, sans erreur
    assert AuditEvent.objects.filter(action="platform.branding_asset_remove").count() == 1
    body = admin.post(f"{ADMIN}reset/").json()
    assert body["name"] == "Fluxonata" and body["accent"] == "#0e7490" and body["icon"] is None
    assert body["login_style"] == "plain" and body["footer"] == {"text": "", "url": ""}
    assert not BrandingAsset.objects.exists() and AuditEvent.objects.filter(action="platform.branding_reset").exists()


def test_the_version_never_goes_back_so_a_cache_is_never_served_twice(admin):
    admin.patch(ADMIN, {"product_name": "A"}, format="json")
    v1 = Branding.get().version
    admin.post(f"{ADMIN}reset/")
    assert Branding.get().version > v1


# ───────── barres : fond du menu de gauche et de la barre du haut ─────────


def test_the_two_bars_take_any_color_and_start_with_the_original_white(admin):
    pub = admin.get(PUBLIC).json()
    assert pub["topbar_bg"] == "" and pub["sidebar_bg"] == ""
    body = admin.patch(ADMIN, {"topbar_bg": "#0F172A", "sidebar_bg": "#F1F5F9"}, format="json").json()
    assert body["topbar_bg"] == "#0f172a" and body["sidebar_bg"] == "#f1f5f9"
    assert body["stored"]["topbar_bg"] == "#0f172a"
    assert admin.get(PUBLIC).json()["sidebar_bg"] == "#f1f5f9"
    # des fonds clairs, moyens ou sombres sont tous admis : le texte se déduit du fond côté interface
    for color in ("#ffffff", "#7a7a7a", "#ffff00", "#000000", "#0e7490"):
        assert admin.patch(ADMIN, {"topbar_bg": color}, format="json").status_code == 200, color
    assert AuditEvent.objects.filter(action="platform.branding_update", data__fields__contains=["topbar_bg"]).exists()


@pytest.mark.parametrize("field", ["topbar_bg", "sidebar_bg"])
@pytest.mark.parametrize("value", ["noir", "#12345", "#gggggg", "rgb(0,0,0)", "#0f172a; background:url(x)", "0f172a"])
def test_a_bar_color_must_be_a_plain_hex_value(admin, field, value):
    r = admin.patch(ADMIN, {field: value}, format="json")
    assert r.status_code == 400 and field in str(r.json())
    assert Branding.get().version == 1


def test_a_bar_color_can_be_cleared_and_reset_clears_both(admin):
    admin.patch(ADMIN, {"topbar_bg": "#0f172a", "sidebar_bg": "#1e293b"}, format="json")
    assert admin.patch(ADMIN, {"topbar_bg": ""}, format="json").json()["topbar_bg"] == ""
    assert admin.patch(ADMIN, {}, format="json").json()["sidebar_bg"] == "#1e293b"
    body = admin.post(f"{ADMIN}reset/").json()
    assert body["topbar_bg"] == "" and body["sidebar_bg"] == ""


def test_a_logo_for_dark_backgrounds_is_a_third_image(admin):
    r = put(admin, "logo_dark", SVG)
    assert (
        r.status_code == 200 and r.json()["logo_dark"]["content_type"] == "image/svg+xml" and r.json()["logo"] is None
    )
    from rest_framework.test import APIClient

    assert APIClient().get(r.json()["logo_dark"]["url"]).status_code == 200
    assert (
        put(
            admin, "logo_dark", b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1 1'><script/></svg>"
        ).status_code
        == 400
    )
    assert admin.delete(f"{ADMIN}assets/logo_dark/").json()["logo_dark"] is None
