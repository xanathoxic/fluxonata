"""Nœud « Modèle de document » (ADR 0036) : moteur Markdown sûr, images de l'espace, aperçu, rendu dans l'exécution, fichier et e-mail."""

import base64
import io
import struct
import zipfile
import zlib

import pytest

from runs import model_docs, smtp_node
from runs.models import RunFile
from tests.conftest import client_for
from tests.test_file_node import pdf_text
from tests.wf_helpers import STR, drain, edge, launch, node, publish, schema
from workflows import model_md
from workflows.models import DocumentImage
from workflows.validation import validate

URL = "/api/v1/workspaces/team-a"
UUID = "123e4567-e89b-12d3-a456-426614174000"


def png(w=40, h=20) -> bytes:
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def gif() -> bytes:
    return b"GIF89a" + struct.pack("<HH", 3, 2) + b"\x00\x00\x00;"


def upload(user, content, name="logo.png"):
    f = io.BytesIO(content)
    f.name = name
    return client_for(user).post(f"{URL}/model-images/", {"file": f}, format="multipart")


# ------------------------------------------------------------------ moteur (sans base)


def html(src):
    return model_md.to_html(src, lambda i: f"/img/{i}")


def test_the_engine_renders_the_supported_syntax():
    out = html(
        "# Titre\n\nBonjour **Alice** *x* ~~y~~ `c`\n\n- un\n  - sous\n- deux\n\n1. a\n2. b\n\n> cite\n\n---\n\n```\nx < 1\n```"
    )
    for part in (
        "<h1>Titre</h1>",
        "<strong>Alice</strong>",
        "<em>x</em>",
        "<del>y</del>",
        "<code>c</code>",
        "<ul><li>un<ul><li>sous</li></ul></li><li>deux</li></ul>",
        "<ol><li>a</li><li>b</li></ol>",
        "<blockquote><p>cite</p></blockquote>",
        "<hr>",
        "<pre><code>x &lt; 1</code></pre>",
    ):
        assert part in out, part


def test_tables_have_headers_and_alignment():
    out = html("| Nom | Note |\n|:--|--:|\n| Élodie | 5 |")
    assert (
        '<th style="text-align:left">Nom</th>' in out
        and '<td style="text-align:right">5</td>' in out
        and "<thead>" in out
    )


def test_raw_html_scripts_and_dangerous_links_are_never_active():
    out = html(
        "<script>alert(1)</script> <img src=x onerror=alert(1)> [x](javascript:alert(1)) [d](data:text/html,x) [ok](https://a.fr) [m](mailto:a@b.fr)"
    )
    assert "<script" not in out and "<img src=x" not in out and "javascript:" not in out and "data:text" not in out
    assert "&lt;script&gt;" in out and 'href="https://a.fr"' in out and 'href="mailto:a@b.fr"' in out


def test_a_remote_image_is_never_loaded_only_platform_images_are():
    out = html(f"![tracker](https://evil.example/p.png) ![ok](image:{UUID}) ![bad](image:pas-un-uuid)")
    assert (
        "evil.example" not in out
        and f'src="/img/{UUID}"' in out
        and "tracker" in out
        and "<img" in out
        and out.count("<img") == 1
    )


def test_inserted_values_are_text_they_cannot_open_a_title_a_list_or_a_link():
    ctx = {"nom": "# Piraté\n- liste [x](https://evil) **gras**", "n": 3}
    md = model_md.resolve_markdown("Bonjour {{ nom }} ({{ n }})", ctx)
    out = html(md)
    assert "<h1>" not in out and "<li>" not in out and "<a " not in out and "<strong>" not in out
    assert "Bonjour" in out and "Piraté" in out and "(3)" in out


def test_a_value_starting_like_a_list_stays_a_paragraph():
    assert "<li>" not in html(model_md.resolve_markdown("{{ v }}", {"v": "- puce"}))
    assert "<ol" not in html(model_md.resolve_markdown("{{ v }}", {"v": "1. un"}))


def test_plain_text_version_and_image_ids():
    src = f"# Titre\n\nBonjour **Alice**\n\n- un\n- deux\n\n![logo](image:{UUID}) et ![logo](image:{UUID})"
    text = model_md.to_text(src)
    assert text.startswith("TITRE") and "• un" in text and "Bonjour Alice" in text and "logo" in text
    assert model_md.image_ids(src) == [UUID]


def test_the_preview_shows_expressions_as_markers_and_hides_unknown_images():
    out = model_md.preview_html("Bonjour {{ inputs.nom }} ![a](image:" + UUID + ")", lambda i: None)
    assert '<span class="md-var">inputs.nom</span>' in out and "<img" not in out


def test_nesting_depth_is_bounded():
    deep = "".join("> " * 1 for _ in range(1)) + "\n".join(["- " + "  " * i + "x" for i in range(50)])
    assert model_md.to_html(deep, None)  # ne boucle pas, ne dépasse pas la pile


# ------------------------------------------------------------------ images et API


def test_images_are_uploaded_listed_served_and_deduplicated(world):
    r = upload(world["designer"], png())
    assert (
        r.status_code == 201 and r.json()["content_type"] == "image/png" and r.json()["reference"].startswith("image:")
    )
    again = upload(world["designer"], png())
    assert again.status_code == 200 and again.json()["id"] == r.json()["id"] and DocumentImage.objects.count() == 1
    assert upload(world["designer"], gif(), "a.gif").json()["content_type"] == "image/gif"
    listing = client_for(world["viewer"]).get(f"{URL}/model-images/").json()
    assert len(listing) == 2 and all(i["url"].startswith(f"{URL}/model-images/") for i in listing)
    served = client_for(world["viewer"]).get(r.json()["url"])
    assert (
        served.status_code == 200
        and served["Content-Type"] == "image/png"
        and served["X-Content-Type-Options"] == "nosniff"
    )
    assert "sandbox" in served["Content-Security-Policy"] and served.content == png()


@pytest.mark.parametrize(
    ("content", "needle"),
    [
        (b"", "vide"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "Format"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "Format"),
        (b"GIF89a", "Format"),
        (b"\x89PNG\r\n\x1a\n" + b"0" * 2_000_000, "Ko"),
        (png(5000, 2), "trop grande"),
    ],
)
def test_bad_images_are_refused(world, content, needle):
    r = upload(world["designer"], content)
    assert r.status_code == 400 and needle in str(r.json())
    assert DocumentImage.objects.count() == 0


def test_rights_and_isolation_of_the_images(world):
    assert upload(world["viewer"], png()).status_code == 403  # lire oui, ajouter non
    pk = upload(world["designer"], png()).json()["id"]
    assert client_for(world["outsider"]).get(f"{URL}/model-images/{pk}/").status_code in (403, 404)
    assert client_for(world["outsider"]).delete(f"{URL}/model-images/{pk}/").status_code in (403, 404)
    assert client_for(world["viewer"]).delete(f"{URL}/model-images/{pk}/").status_code == 403
    assert client_for(world["designer"]).delete(f"{URL}/model-images/{pk}/").status_code == 204
    assert client_for(world["designer"]).get(f"{URL}/model-images/{pk}/").status_code == 404


def test_an_image_used_by_a_workflow_cannot_be_deleted(ws, world):
    pk = upload(world["designer"], png()).json()["id"]
    publish(ws, "avec-image", [node("m", "model", markdown=f"![logo](image:{pk})")], [], user=world["designer"])
    r = client_for(world["designer"]).delete(f"{URL}/model-images/{pk}/")
    assert r.status_code == 409 and "avec-image" in str(r.json())
    assert DocumentImage.objects.filter(pk=pk).exists()


def test_the_preview_renders_server_side_and_reports_missing_images(world):
    pk = upload(world["designer"], png()).json()["id"]
    r = client_for(world["designer"]).post(
        f"{URL}/model-preview/",
        {"markdown": f"# Salut {{{{ inputs.nom }}}}\n\n![a](image:{pk}) ![b](image:{UUID})"},
        format="json",
    )
    body = r.json()
    assert r.status_code == 200 and "<h1>Salut" in body["html"] and 'class="md-var"' in body["html"]
    assert f'src="{URL}/model-images/{pk}/"' in body["html"] and body["missing_images"] == [UUID]
    assert (
        client_for(world["viewer"]).post(f"{URL}/model-preview/", {"markdown": "x"}, format="json").status_code == 403
    )
    assert (
        client_for(world["designer"])
        .post(f"{URL}/model-preview/", {"markdown": "x" * 200_001}, format="json")
        .status_code
        == 400
    )


# ------------------------------------------------------------------ dans l'exécution


def model_flow(ws, user, nodes, edges=(), inputs=None, values=None):
    wf = publish(ws, f"modele-{nodes[-1]['id']}", nodes, list(edges), inputs=inputs, user=user)
    return drain(launch(ws, wf, user, values or {}))


def test_the_step_renders_the_template_once_with_safe_values(ws, world):
    run = model_flow(
        ws,
        world["designer"],
        [
            node(
                "m",
                "model",
                title="Bilan {{ inputs.mois }}",
                markdown="# Bonjour {{ inputs.nom }}\n\nTotal : **{{ inputs.n }}**",
            )
        ],
        inputs=schema(["nom", "mois", "n"], nom=STR, mois=STR, n={"type": "integer"}),
        values={"nom": "Alice *x*", "mois": "mars", "n": 7},
    )
    out = run.steps.get(node_id="m").output
    assert run.status == "succeeded" and out["title"] == "Bilan mars"
    assert out["markdown"] == "# Bonjour Alice \\*x\\*\n\nTotal : **7**" and "<h1>Bonjour Alice *x*</h1>" in out["html"]
    assert out["text"].startswith("BONJOUR ALICE *X*") or "Alice *x*" in out["text"]


def test_a_missing_image_fails_the_step_with_a_clear_code(ws, world):
    pk = upload(world["designer"], png()).json()["id"]
    wf = publish(ws, "img-perdue", [node("m", "model", markdown=f"![x](image:{pk})")], [], user=world["designer"])
    DocumentImage.objects.filter(
        pk=pk
    ).delete()  # supprimée en base malgré la protection de l'API (restauration, purge…)
    run = drain(launch(ws, wf, world["designer"]))
    assert run.steps.get(node_id="m").error["code"] == "MODEL_IMAGE"


def files_of(run):
    return {f.name: bytes(f.data) for f in RunFile.objects.filter(run=run)}


def with_image(world):
    return upload(world["designer"], png()).json()["id"]


def test_the_template_becomes_html_word_pdf_and_slides(ws, world):
    pk = with_image(world)
    md = f"# Rapport\n\nBonjour **{{{{ inputs.nom }}}}**\n\n![logo](image:{pk})\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n---\n\nFin\n- merci"
    run = model_flow(
        ws,
        world["designer"],
        [
            node("m", "model", title="Mon rapport", markdown=md),
            node("h", "file", format="html", filename="r", model="{{ steps.m.output }}"),
            node("w", "file", format="docx", filename="r", model="{{ steps.m.output }}"),
            node("p", "file", format="pdf", filename="r", model="{{ steps.m.output }}"),
            node("s", "file", format="pptx", filename="r", model="{{ steps.m.output }}"),
        ],
        [edge("m", "h"), edge("h", "w"), edge("w", "p"), edge("p", "s")],
        inputs=schema(["nom"], nom=STR),
        values={"nom": "Alice"},
    )
    assert run.status == "succeeded", [(s.node_id, s.error) for s in run.steps.all()]
    files = files_of(run)
    page = files["r.html"].decode()
    assert (
        "<title>Mon rapport</title>" in page
        and "<strong>Alice</strong>" in page
        and "data:image/png;base64," in page
        and "<table>" in page
    )
    assert "<script" not in page and "https://" not in page
    word = zipfile.ZipFile(io.BytesIO(files["r.docx"]))
    assert (
        "word/media/image1.png" in word.namelist()
        and b"Alice" in word.read("word/document.xml")
        and b"<w:tbl>" in word.read("word/document.xml")
    )
    assert files["r.pdf"].startswith(b"%PDF-1.4")
    text = pdf_text(files["r.pdf"])
    assert "Mon rapport" in text and "Alice" in text and b"/Subtype /Image" in zlib_all(files["r.pdf"]) + files["r.pdf"]
    assert zipfile.ZipFile(io.BytesIO(files["r.pptx"])).namelist().count("ppt/slides/slide1.xml") == 1


def test_a_file_from_a_model_needs_a_model_step_output(ws, world):
    run = model_flow(
        ws,
        world["designer"],
        [node("f", "file", format="docx", filename="x", model="{{ inputs.nom }}")],
        inputs=schema(["nom"], nom=STR),
        values={"nom": "Alice"},
    )
    assert run.steps.get(node_id="f").status == "succeeded"  # un texte brut est aussi accepté comme Markdown
    run = model_flow(
        ws,
        world["designer"],
        [node("g", "file", format="docx", filename="x", model="{{ inputs.n }}")],
        inputs=schema(["n"], n={"type": "integer"}),
        values={"n": 3},
    )
    assert run.steps.get(node_id="g").error["code"] == "MODEL_REF"


def test_an_email_gets_text_html_and_inline_images(ws, world):
    pk = with_image(world)
    source = f"# Bonjour\n\n![logo](image:{pk})\n\nÀ bientôt"
    parts = model_docs.mail_parts(source, model_docs.load_images(ws.pk, source))
    cfg = {"from_address": "noreply@exemple.fr", "from_name": "Plate-forme"}
    spec = {
        "to": ["a@exemple.fr"],
        "cc": [],
        "subject": "Hello",
        "body": parts["text"],
        "html": parts["html"],
        "images": parts["images"],
    }
    msg = smtp_node.build_message(cfg, spec)
    assert msg.get_content_type() == "multipart/alternative"
    assert "BONJOUR" in msg.get_body(("plain",)).get_content()
    related = msg.get_body(("html",))
    html_text = (
        related.get_content() if related.get_content_type() == "text/html" else related.get_payload()[0].get_content()
    )
    assert "cid:" in html_text and "<img" in html_text and "http" not in html_text.replace("http-equiv", "")
    cids = [p["Content-ID"] for p in msg.walk() if p.get_content_type() == "image/png"]
    assert len(cids) == 1 and cids[0].strip("<>") == parts["images"][0]["cid"]
    assert base64.b64decode(parts["images"][0]["data"]) == png()


def test_the_mail_images_are_bounded(ws, world):
    source = "texte"
    many = {str(i): type("I", (), {"content_type": "image/png", "data": b"x"})() for i in range(11)}
    with pytest.raises(model_docs.ModelError):
        model_docs.mail_parts(source, many)


# ------------------------------------------------------------------ validation, essai à blanc


def check(ws, **fields):
    g = {"inputs": schema(), "nodes": [node("m", "model", **fields)], "edges": [], "outputs": {}}
    return [i["message"] for i in validate(g, ws, "x") if i["level"] == "error"]


def test_validation_of_the_model_step(ws, world):
    assert any("Écrivez" in m for m in check(ws, markdown="  "))
    assert any("200 000" in m or "dépasse" in m for m in check(ws, markdown="x" * 200_001))
    assert any("n'existe" in m for m in check(ws, markdown=f"![x](image:{UUID})"))
    assert any(
        "inconnue" in m or "ne produit pas" in m or "inputs" in m
        for m in check(ws, markdown="Bonjour {{ inputs.absent }}")
    )
    pk = with_image(world)
    assert check(ws, markdown=f"# Titre\n\n![logo](image:{pk})", title="Rapport") == []


def test_the_outputs_of_the_model_are_declared_for_the_next_steps(ws, world):
    g = {
        "inputs": schema(),
        "outputs": {"t": "{{ steps.m.output.text }}", "h": "{{ steps.m.output.html }}"},
        "nodes": [node("m", "model", markdown="Salut")],
        "edges": [],
    }
    assert [i for i in validate(g, ws, "x") if i["level"] == "error"] == []


def test_file_and_mail_accept_a_model_instead_of_a_body(ws, world):
    g = {
        "inputs": schema(),
        "outputs": {},
        "edges": [edge("m", "f")],
        "nodes": [
            node("m", "model", markdown="Salut"),
            node("f", "file", format="docx", filename="x", model="{{ steps.m.output }}"),
        ],
    }
    assert [i for i in validate(g, ws, "x") if i["level"] == "error"] == []
    bad = {
        **g,
        "nodes": [
            node("m", "model", markdown="Salut"),
            node("f", "file", format="docx", filename="x", model="pas une expression"),
        ],
    }
    assert any("Modèle de document" in i["message"] for i in validate(bad, ws, "x") if i["level"] == "error")


def test_a_dry_run_renders_without_filling_expressions():
    from ai import dryrun

    kind, message = dryrun._FIXED_STEPS["model"]
    out = kind({"markdown": "# Salut {{ inputs.nom }}", "title": "T"})
    assert callable(kind) and "Modèle simulé" in message
    assert out["markdown"] == "# Salut {{ inputs.nom }}" and "<h1>" in out["html"] and out["images"] == []


def test_the_design_guide_documents_the_model_with_a_valid_example(ws, world):
    from workflows.mcp_design_guide import OTHER_NODES, guide

    example = OTHER_NODES["modele (un modèle de document en Markdown, réutilisé par un fichier et un e-mail)"]
    graph = {"inputs": schema(["mois"], mois=STR), "nodes": [example], "edges": [], "outputs": {}}
    assert [i for i in validate(graph, ws, "autre") if i["level"] == "error"] == []
    text = str(guide("modeles"))
    assert (
        "MODÈLE DE DOCUMENT" in text
        and "JAMAIS de secret" in text
        and "n'invente" in text.replace("’", "'")
        or "inventez" in text
    )


def zlib_all(pdf: bytes) -> bytes:
    import re

    return b"".join(zlib.decompress(m) for m in re.findall(rb"stream\n(.*?)\nendstream", pdf, re.S) if m[:1] == b"x")


# ------------------------------------------------------------------ PDF et PowerPoint riches


def jpeg(w=8, h=4, comps=3) -> bytes:
    return (
        b"\xff\xd8"
        + b"\xff\xc0"
        + struct.pack(">HBHHB", 8 + 3 * comps, 8, h, w, comps)
        + b"\x01\x11\x00" * comps
        + b"\xff\xd9"
    )


def test_the_pdf_measures_text_with_the_real_font_widths():
    from runs import pdfdoc

    assert (
        round(pdfdoc.text_width("Hello", (False, False), 10), 2) == 22.78
    )  # H 722 + e 556 + l 222 + l 222 + o 556 = 2278 millièmes d'em
    bold, regular = (
        pdfdoc.text_width("Disponibilité", (True, False), 10),
        pdfdoc.text_width("Disponibilité", (False, False), 10),
    )
    assert bold > regular
    assert pdfdoc.text_width("é", (False, False), 10) == pdfdoc.text_width(
        "e", (False, False), 10
    )  # accents : même largeur
    lines = pdfdoc.wrap([pdfdoc.Span("un deux trois quatre cinq six sept huit")], 60, 10)
    assert len(lines) > 1 and all(sum(p.width for p in line) <= 60.1 for line in lines)
    assert all(not line[-1].text.isspace() for line in lines)  # pas d'espace en fin de ligne
    huge = pdfdoc.wrap([pdfdoc.Span("x" * 200)], 50, 10)
    assert len(huge) > 1  # un mot plus large que la ligne est coupé


def test_the_pdf_renders_the_model_like_the_html_page(ws, world):
    pk = with_image(world)
    md = (
        f"![logo](image:{pk})\n\n# Titre\n\nUn **gras**, un [lien](https://example.org/a) et `code`.\n\n"
        "| Service | Dispo |\n|---|--:|\n| Réseau | 99,99 % |\n\n> citation\n\n- un\n  - sous\n\n```\nx = 1\n```\n\n---\n\nFin"
    )
    run = model_flow(
        ws,
        world["designer"],
        [node("m", "model", markdown=md), node("p", "file", format="pdf", filename="r", model="{{ steps.m.output }}")],
        [edge("m", "p")],
    )
    pdf = files_of(run)["r.pdf"]
    body, text = zlib_all(pdf), pdf_text(pdf)
    assert pdf.count(b"/Subtype /Image") == 1 and b"/URI (https://example.org/a)" in pdf and b"/Subtype /Link" in pdf
    for word in ("Titre", "gras", "lien", "Réseau", "99,99 %", "citation", "sous", "x = 1", "Fin", "Page 1 / 1"):
        assert word in text, word
    assert (
        b"/F2" in body and b"/F3" not in body.replace(b"/F3 ", b"") or b"re B" in body
    )  # gras posé, cadres de tableau tracés
    assert b" re B Q" in body or b" re S Q" in body  # bordures du tableau
    assert b"%PDF-1.4" == pdf[:8]


def test_the_title_is_shown_once_even_when_the_text_starts_with_it(ws, world):
    run = model_flow(
        ws,
        world["designer"],
        [
            node("m", "model", title="Rapport de mars", markdown="# Rapport de mars\n\nCorps"),
            node("h", "file", format="html", filename="a", model="{{ steps.m.output }}"),
            node("o", "model", title="Autre titre", markdown="# Rapport de mars\n\nCorps"),
            node("k", "file", format="html", filename="b", model="{{ steps.o.output }}"),
        ],
        [edge("m", "h"), edge("h", "o"), edge("o", "k")],
    )
    files = files_of(run)
    assert files["a.html"].decode().count("Rapport de mars") == 2  # <title> + un seul <h1>
    assert files["b.html"].decode().count("<h1>") == 2  # un titre différent reste affiché en plus


def test_pdf_images_png_rgba_keeps_its_transparency_and_jpeg_is_embedded_as_is():
    from runs import pdfdoc

    plain = pdfdoc.prepare_image("image/png", png(4, 2))
    assert plain and plain.filter == "/FlateDecode" and "Predictor 15" in plain.parms and plain.smask is None
    raw = b"".join(b"\x00" + bytes((10, 20, 30, 255 if x < 2 else 40)) * 1 for x in range(4) for _ in [0])
    rgba_rows = b"".join(
        b"\x00" + b"".join(bytes((10, 20, 30, 255 if x < 2 else 40)) for x in range(4)) for _ in range(2)
    )

    def chunk(kind, d):
        return struct.pack(">I", len(d)) + kind + d + struct.pack(">I", zlib.crc32(kind + d))

    rgba = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 2, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rgba_rows))
        + chunk(b"IEND", b"")
    )
    image = pdfdoc.prepare_image("image/png", rgba)
    assert (
        image
        and image.colorspace == "/DeviceRGB"
        and image.smask is not None
        and zlib.decompress(image.smask.data) == bytes([255, 255, 40, 40] * 2)
    )
    assert zlib.decompress(image.data) == bytes((10, 20, 30)) * 8 and raw
    j = pdfdoc.prepare_image("image/jpeg", jpeg(8, 4))
    assert j and (j.width, j.height, j.filter, j.colorspace) == (8, 4, "/DCTDecode", "/DeviceRGB")
    assert pdfdoc.prepare_image("image/jpeg", jpeg(comps=4)) is None  # CMJN : texte alternatif
    assert (
        pdfdoc.prepare_image("image/gif", gif()) is None
        and pdfdoc.prepare_image("image/png", b"\x89PNG\r\n\x1a\nxx") is None
    )


def test_an_unsupported_image_falls_back_to_its_alt_text_in_the_pdf(ws, world):
    pk = upload(world["designer"], gif(), "a.gif").json()["id"]
    run = model_flow(
        ws,
        world["designer"],
        [
            node("m", "model", markdown=f"![mon schéma](image:{pk})\n\nTexte"),
            node("p", "file", format="pdf", filename="r", model="{{ steps.m.output }}"),
        ],
        [edge("m", "p")],
    )
    pdf = files_of(run)["r.pdf"]
    assert pdf.count(b"/Subtype /Image") == 0 and "[mon schéma]" in pdf_text(pdf)


def test_a_long_document_is_paginated_and_a_huge_one_is_refused(ws, world):
    long = "\n\n".join(f"Paragraphe {i} " + "mot " * 40 for i in range(120))
    run = model_flow(
        ws,
        world["designer"],
        [
            node("m", "model", markdown=long),
            node("p", "file", format="pdf", filename="r", model="{{ steps.m.output }}"),
        ],
        [edge("m", "p")],
    )
    pdf = files_of(run)["r.pdf"]
    pages = pdf.count(b"/Type /Page ")
    assert pages > 3 and f"Page {pages} / {pages}" in pdf_text(pdf)
    from runs import pdfdoc

    with pytest.raises(pdfdoc.PdfError):
        pdfdoc.render("", model_md.parse_blocks("\n\n".join("x " * 30 for _ in range(30000))), {})


def test_the_powerpoint_keeps_formatting_links_tables_and_images(ws, world):
    pk = with_image(world)
    md = f"# Bilan\n\nBonjour **Alice**, voir [guide](https://example.org/g).\n\n![logo](image:{pk})\n\n| a | b |\n|---|--:|\n| 1 | 2 |\n\n---\n\n# Suite\n\n> citation\n\n1. a\n2. b"
    run = model_flow(
        ws,
        world["designer"],
        [
            node("m", "model", title="Rapport", markdown=md),
            node("s", "file", format="pptx", filename="d", model="{{ steps.m.output }}"),
        ],
        [edge("m", "s")],
    )
    z = zipfile.ZipFile(io.BytesIO(files_of(run)["d.pptx"]))
    names = z.namelist()
    assert z.testzip() is None and names[0] == "[Content_Types].xml"
    assert [n for n in names if n.startswith("ppt/slides/slide")] == [
        "ppt/slides/slide1.xml",
        "ppt/slides/slide2.xml",
        "ppt/slides/slide3.xml",
    ]
    first, second = z.read("ppt/slides/slide1.xml").decode(), z.read("ppt/slides/slide2.xml").decode()
    assert (
        "Rapport" in first and "Bilan" in second and 'b="1"' in second and "<a:tbl>" in second and "<p:pic>" in second
    )
    assert (
        any(n.startswith("ppt/media/") for n in names)
        and "https://example.org/g" in z.read("ppt/slides/_rels/slide2.xml.rels").decode()
    )
    assert "1. " in z.read("ppt/slides/slide3.xml").decode() and "citation" in z.read("ppt/slides/slide3.xml").decode()


def test_a_hard_line_break_needs_two_trailing_spaces_or_a_backslash():
    assert "ligne un<br>ligne deux" in model_md.to_html("ligne un  \nligne deux")
    assert "un<br>deux" in model_md.to_html("un\\\ndeux")
    assert "<br>" not in model_md.to_html("un\ndeux")
