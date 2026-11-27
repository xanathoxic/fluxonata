"""L'assistant peut écrire l'objet et le corps (Markdown) d'une étape « Interaction utilisateur », avec les mêmes limites que l'éditeur."""

from tests.conftest import client_for
from tests.test_mcp_design_write import call, error_text
from tests.wf_helpers import node, schema
from workflows.models import Workflow

BODY = "## Dépense\n\n- **Montant** : {{ inputs.montant }} €\n\nVérifiez le budget."


def graph(**fields):
    return {
        "inputs": schema(montant={"type": "integer"}),
        "nodes": [node("v", "approval", title="Valider la dépense", **fields)],
        "edges": [],
        "outputs": {},
    }


def test_an_assistant_writes_the_subject_and_the_markdown_body_of_an_approval(world):
    c = client_for(world["designer"])
    res, out = call(c, "create_workflow", {"slug": "w", "name": "W", "graph": graph(subject="Dépense", body=BODY)})
    assert out["status"] == "brouillon", error_text(res)
    assert [i for i in out["issues"] if i["level"] == "error"] == []
    stored = Workflow.objects.get(slug="w").draft_graph["nodes"][0]
    assert stored["subject"] == "Dépense" and stored["body"] == BODY


def test_the_limits_of_the_editor_apply_to_the_assistant(world):
    c = client_for(world["designer"])
    _, out = call(c, "create_workflow", {"slug": "w", "name": "W", "graph": graph(body="x" * 20001, subject="s" * 201)})
    assert [i["code"] for i in out["issues"] if i["level"] == "error"] == ["bad_approval", "bad_approval"]


def test_the_authoring_guide_documents_the_body_field(world):
    c = client_for(world["designer"])
    _, out = call(c, "get_authoring_guide", {"topic": "workflow"})
    assert "body?" in str(out) and "Markdown" in str(out)
