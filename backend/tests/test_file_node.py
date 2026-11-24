"""Nœud « Créer un fichier » (ADR 0035) : texte, CSV, PDF, ZIP gardés avec l'exécution et téléchargeables."""

import io
import zipfile
from xml.etree import ElementTree

import pytest

from runs import files
from runs.models import RunFile
from tests.conftest import client_for
from tests.wf_helpers import STR, drain, edge, launch, node, publish, schema
from workflows.validation import validate

URL = "/api/v1/workspaces/team-a"


def run_nodes(ws, user, nodes, edges=(), inputs=None, values=None):
    wf = publish(ws, f"fichiers-{len(nodes)}-{nodes[0]['id']}", nodes, list(edges), inputs=inputs, user=user)
    return drain(launch(ws, wf, user, values or {}))


def data(run, nid="f"):
    return bytes(RunFile.objects.get(pk=run.steps.get(node_id=nid).output["file"]["file_id"]).data)


def test_a_text_file_is_created_with_a_name_and_a_type(ws, world):
    run = run_nodes(
        ws,
        world["designer"],
        [node("f", "file", format="text", filename="note.md", content="# Bonjour {{ inputs.nom }}")],
        inputs=schema(["nom"], nom=STR),
        values={"nom": "Alice"},
    )
    out = run.steps.get(node_id="f").output
    assert run.status == "succeeded" and out["name"] == "note.md" and out["file"]["content_type"] == "text/markdown"
    assert data(run) == b"# Bonjour Alice"
    assert RunFile.objects.get(pk=out["file"]["file_id"]).run_id == run.pk


def test_a_text_file_without_extension_gets_txt(ws, world):
    run = run_nodes(ws, world["designer"], [node("f", "file", format="text", filename="rapport", content="x")])
    assert run.steps.get(node_id="f").output["name"] == "rapport.txt"


def test_a_csv_is_built_from_rows_with_accents_and_formula_protection(ws, world):
    run = run_nodes(
        ws,
        world["designer"],
        [
            node(
                "t",
                "transform",
                op="set",
                fields={"rows": [{"nom": "Élodie", "note": 5}, {"nom": "=1+1", "note": -3}]},
            ),
            node("f", "file", format="csv", filename="notes", rows="{{ steps.t.output.rows }}"),
        ],
        [edge("t", "f")],
    )
    raw = data(run).decode("utf-8-sig")
    assert run.steps.get(node_id="f").output["name"] == "notes.csv"
    assert raw.splitlines() == ["nom,note", "Élodie,5", "'=1+1,-3"]


def test_csv_rejects_something_that_is_not_a_list_of_objects(ws, world):
    run = run_nodes(
        ws,
        world["designer"],
        [node("f", "file", format="csv", filename="x", rows="{{ inputs.v }}")],
        inputs=schema(["v"], v=STR),
        values={"v": "texte"},
    )
    assert run.steps.get(node_id="f").error["code"] == "FILE_CONTENT"


def pdf_text(pdf: bytes) -> str:
    """Tout le texte posé dans le PDF (flux décompressés, chaînes entre parenthèses), pour vérifier ce qui y est écrit."""
    import re
    import zlib

    streams = [zlib.decompress(m) for m in re.findall(rb"stream\n(.*?)\nendstream", pdf, re.S) if m[:1] == b"x"]
    return "".join(chunk.decode("cp1252") for s in streams for chunk in re.findall(rb"\((.*?)(?<!\\)\) Tj", s))


def test_a_pdf_is_a_valid_document_with_the_text_and_pages(ws, world):
    long = "\n".join(f"Ligne {i} avec des accents éèà" for i in range(130))
    run = run_nodes(
        ws, world["designer"], [node("f", "file", format="pdf", filename="rapport", title="Mon rapport", content=long)]
    )
    pdf = data(run)
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    assert (
        pdf.count(b"/Type /Page ") == 3
        and "Mon rapport" in pdf_text(pdf)
        and "Ligne 129 avec des accents éèà" in pdf_text(pdf)
    )
    assert run.steps.get(node_id="f").output["file"]["content_type"] == "application/pdf"
    start = int(pdf.rsplit(b"startxref\n", 1)[1].split()[0])
    assert pdf[start : start + 4] == b"xref"


def test_pdf_escapes_parentheses_and_wraps_long_lines(ws, world):
    run = run_nodes(
        ws, world["designer"], [node("f", "file", format="pdf", filename="p", content="(a) \\ " + "mot " * 60)]
    )
    text = pdf_text(data(run))
    assert "\\(a\\) \\\\" in text and text.count("mot") == 60


def test_a_zip_gathers_files_of_the_run_and_renames_duplicates(ws, world):
    run = run_nodes(
        ws,
        world["designer"],
        [
            node("a", "file", format="text", filename="a.txt", content="un"),
            node("b", "file", format="text", filename="a.txt", content="deux"),
            node(
                "z",
                "file",
                format="zip",
                filename="lot",
                sources=["{{ steps.a.output.file }}", "{{ steps.b.output.file }}"],
            ),
        ],
        [edge("a", "b"), edge("b", "z")],
    )
    z = zipfile.ZipFile(io.BytesIO(data(run, "z")))
    assert sorted(z.namelist()) == ["a-2.txt", "a.txt"] and z.read("a.txt") == b"un" and z.read("a-2.txt") == b"deux"
    assert run.steps.get(node_id="z").output["name"] == "lot.zip"


def test_a_zip_refuses_a_file_from_another_run_or_nothing(ws, world):
    other = run_nodes(ws, world["designer"], [node("f", "file", format="text", filename="secret.txt", content="s")])
    fid = other.steps.get(node_id="f").output["file"]["file_id"]
    run = run_nodes(ws, world["designer"], [node("z", "file", format="zip", filename="x", sources=[fid])])
    assert run.steps.get(node_id="z").error["code"] == "FILE_MISSING"
    empty = run_nodes(
        ws,
        world["designer"],
        [node("z", "file", format="zip", filename="x", sources=["{{ inputs.l }}"])],
        inputs=schema(["l"], l={"type": "array"}),
        values={"l": []},
    )
    assert empty.steps.get(node_id="z").error["code"] == "FILE_CONTENT"


def test_size_limits_are_enforced(ws, world, monkeypatch):
    monkeypatch.setattr(files, "MAX_FILE", 50)
    run = run_nodes(ws, world["designer"], [node("f", "file", format="text", filename="gros", content="x" * 100)])
    assert run.steps.get(node_id="f").error["code"] == "FILE_TOO_BIG"


@pytest.mark.parametrize("name", ["", "../etc/passwd", "a/b"])
def test_a_bad_name_fails_the_step(ws, world, name):
    run = run_nodes(
        ws,
        world["designer"],
        [node("f", "file", format="text", filename="{{ inputs.n }}", content="x")],
        inputs=schema(["n"], n=STR),
        values={"n": name},
    )
    assert run.steps.get(node_id="f").error["code"] == "FILE_NAME"


def test_the_file_is_downloadable_by_members_only(ws, world):
    run = run_nodes(ws, world["designer"], [node("f", "file", format="text", filename="n.txt", content="bonjour")])
    fid = run.steps.get(node_id="f").output["file"]["file_id"]
    ok = client_for(world["designer"]).get(f"{URL}/run-files/{fid}/download/")
    assert ok.status_code == 200 and b"".join(ok.streaming_content if ok.streaming else [ok.content]) == b"bonjour"
    assert "attachment" in ok["Content-Disposition"] and ok["X-Content-Type-Options"] == "nosniff"
    assert client_for(world["outsider"]).get(f"{URL}/run-files/{fid}/download/").status_code in (403, 404)


def check(ws, **fields):
    g = {"inputs": schema(), "nodes": [node("f", "file", **fields)], "edges": [], "outputs": {}}
    return [i["message"] for i in validate(g, ws, "x") if i["level"] == "error"]


@pytest.mark.parametrize(
    ("fields", "needle"),
    [
        ({"format": "odt", "filename": "x"}, "Format du fichier"),
        ({"format": "text", "filename": "", "content": "x"}, "nom du fichier"),
        ({"format": "text", "filename": "a/b", "content": "x"}, "Nom de fichier invalide"),
        ({"format": "pdf", "filename": "x"}, "contenu"),
        ({"format": "csv", "filename": "x", "rows": "pas une expression"}, "Lignes du tableau"),
        ({"format": "csv", "filename": "x", "rows": "{{ inputs.a }}", "columns": []}, "Colonnes"),
        ({"format": "zip", "filename": "x", "files": []}, "Archive"),
    ],
)
def test_validation_rejects_bad_configuration(ws, fields, needle):
    assert any(needle in m for m in check(ws, **fields))


def test_validation_accepts_good_configuration_and_declares_outputs(ws, world):
    assert check(ws, format="pdf", filename="rapport", content="Bonjour") == []
    g = {
        "inputs": schema(),
        "outputs": {"f": "{{ steps.f.output.file }}", "n": "{{ steps.f.output.size }}"},
        "nodes": [node("f", "file", format="text", filename="a", content="x")],
        "edges": [],
    }
    assert [i for i in validate(g, ws, "x") if i["level"] == "error"] == []


def office(run, nid="f"):
    """L'archive Office : chaque partie est du XML bien formé, le contenu attendu s'y trouve."""
    z = zipfile.ZipFile(io.BytesIO(data(run, nid)))
    assert z.testzip() is None and z.namelist()[0] == "[Content_Types].xml"
    for n in z.namelist():
        ElementTree.fromstring(z.read(n))  # noqa: S314 — fichiers que ce test vient de fabriquer
    return z


def test_a_spreadsheet_has_typed_cells_a_bold_header_and_never_a_formula(ws, world):
    run = run_nodes(
        ws,
        world["designer"],
        [
            node(
                "t",
                "transform",
                op="set",
                fields={
                    "rows": [{"nom": "Élodie & <Co>", "note": 5, "ok": True}, {"nom": "=1+1", "note": 2.5, "ok": None}]
                },
            ),
            node("f", "file", format="xlsx", filename="bilan", rows="{{ steps.t.output.rows }}", sheet="Bilan: 2026"),
        ],
        [edge("t", "f")],
    )
    z = office(run)
    sheet = z.read("xl/worksheets/sheet1.xml").decode()
    assert run.steps.get(node_id="f").output["name"] == "bilan.xlsx"
    assert "Élodie &amp; &lt;Co&gt;" in sheet and "<v>5</v>" in sheet and "<v>2.5</v>" in sheet and 't="b"' in sheet
    assert "<f>" not in sheet and "=1+1" in sheet and 't="inlineStr"' in sheet  # du texte, jamais une formule
    assert 'name="Bilan  2026"' in z.read("xl/workbook.xml").decode()
    assert run.steps.get(node_id="f").output["file"]["content_type"].endswith("spreadsheetml.sheet")


def test_a_word_document_has_title_headings_and_bullets(ws, world):
    run = run_nodes(
        ws,
        world["designer"],
        [
            node(
                "f",
                "file",
                format="docx",
                filename="note",
                title="Rapport",
                content="# Intro\nBonjour <b> é\n- un point\n## Détail",
            )
        ],
    )
    doc = office(run).read("word/document.xml").decode()
    assert 'w:val="Title"' in doc and 'w:val="Heading1"' in doc and 'w:val="Heading2"' in doc
    assert "Bonjour &lt;b&gt; é" in doc and "• un point" in doc
    assert run.steps.get(node_id="f").output["name"] == "note.docx"


def test_a_presentation_has_one_slide_per_block(ws, world):
    run = run_nodes(
        ws,
        world["designer"],
        [
            node(
                "f",
                "file",
                format="pptx",
                filename="deck",
                title="Bilan 2026",
                content="Résultats\n- ventes en hausse\n- coûts stables\n---\n# Suite\nmerci",
            )
        ],
    )
    z = office(run)
    slides = sorted(n for n in z.namelist() if n.startswith("ppt/slides/slide"))
    assert slides == ["ppt/slides/slide1.xml", "ppt/slides/slide2.xml", "ppt/slides/slide3.xml"]
    assert "Bilan 2026" in z.read(slides[0]).decode() and "ventes en hausse" in z.read(slides[1]).decode()
    assert z.read("ppt/presentation.xml").decode().count("<p:sldId ") == 3


def test_a_presentation_without_any_text_is_refused(ws, world):
    run = run_nodes(ws, world["designer"], [node("f", "file", format="pptx", filename="vide", content="  \n ")])
    assert run.steps.get(node_id="f").error["code"] == "FILE_CONTENT"


def test_all_the_files_of_a_run_can_be_downloaded_in_one_zip(ws, world):
    run = run_nodes(
        ws,
        world["designer"],
        [
            node("a", "file", format="text", filename="a.txt", content="un"),
            node("b", "file", format="text", filename="a.txt", content="deux"),
        ],
        [edge("a", "b")],
    )
    url = f"{URL}/workflow-runs/{run.pk}/files/archive/"
    r = client_for(world["designer"]).get(url)
    assert r.status_code == 200 and r["Content-Type"] == "application/zip" and "attachment" in r["Content-Disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(z.namelist()) == ["a-2.txt", "a.txt"]
    assert client_for(world["outsider"]).get(url).status_code in (403, 404)
    empty = run_nodes(ws, world["designer"], [node("c", "transform", op="set", fields={"a": 1})])
    assert client_for(world["designer"]).get(f"{URL}/workflow-runs/{empty.pk}/files/archive/").status_code == 404


@pytest.mark.parametrize("fmt", ["xlsx", "docx", "pptx"])
def test_validation_requires_what_each_office_format_needs(ws, fmt):
    msgs = check(ws, format=fmt, filename="x")
    assert msgs and (any("Lignes" in m for m in msgs) if fmt == "xlsx" else any("contenu" in m for m in msgs))


def test_the_design_guide_documents_the_node_with_a_valid_example(ws, world):
    from workflows.mcp_design_guide import OTHER_NODES, guide

    example = OTHER_NODES["fichier (créer un PDF, un tableur, une présentation… à télécharger)"]
    graph = {"inputs": schema(["mois"], mois=STR), "nodes": [example], "edges": [], "outputs": {}}
    assert [i for i in validate(graph, ws, "autre") if i["level"] == "error"] == []
    text = str(guide("fichiers"))
    assert "CRÉER UN FICHIER" in text and "pptx" in text and "xlsx" in text and "JAMAIS de secret" in text


def test_a_dry_run_simulates_the_step_and_creates_nothing(ws, world):
    from ai import dryrun

    kind, message = dryrun._FIXED_STEPS["file"]
    out = kind({"filename": "rapport.pdf"})
    assert callable(kind) and "rien n'est créé" in message
    assert out["name"] == "rapport.pdf" and out["size"] == 0 and not RunFile.objects.exists()
