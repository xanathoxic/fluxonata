"""La demande d'approbation : objet et corps (Markdown) définis dans l'étape, résolus au lancement, vérifiés à la publication,
rendus tels quels par l'API (le rendu Markdown est fait par l'interface)."""

from runs.models import Approval
from tests.conftest import client_for
from tests.wf_helpers import drain, launch, node, publish, schema
from workflows.validation import validate_graph

BASE = "/api/v1/workspaces/team-a/approvals/"


def _graph(**fields):
    return {
        "inputs": schema(["n"], n={"type": "integer"}),
        "nodes": [node("ap", "approval", title="Valider", **fields)],
        "edges": [],
    }


def _errors(ws, **fields):
    return {i["code"] for i in validate_graph(_graph(**fields), ws) if i["level"] == "error"}


def test_subject_and_body_are_bounded_text(ws):
    assert _errors(ws, subject="Achat", body="# Titre\n\n- un\n- deux") == set()
    assert "bad_approval" in _errors(ws, subject="x" * 201)
    assert "bad_approval" in _errors(ws, body="x" * 20001)
    assert "bad_approval" in _errors(ws, body=["pas du texte"])


def test_references_in_the_request_are_checked_like_any_other_expression(ws):
    assert "unknown_input" in _errors(ws, body="Montant : {{ inputs.absent }}")
    assert "unknown_input" in _errors(ws, subject="{{ inputs.absent }}")
    assert _errors(ws, subject="Achat de {{ inputs.n }}", body="**{{ inputs.n }}** euros") == set()


def test_the_request_is_resolved_when_the_step_starts_and_served_by_the_api(ws, world):
    user = world["designer"]
    v = publish(
        ws,
        "demande",
        [
            node(
                "ap",
                "approval",
                title="Valider l'achat",
                subject="Achat n°{{ inputs.n }}",
                body="## Détail\n\n**{{ inputs.n }}** euros",
            )
        ],
        [],
        inputs=schema(["n"], n={"type": "integer"}),
        user=user,
    )
    run = drain(launch(ws, v, user, {"n": 42}), until=lambda r: Approval.objects.exists())
    ap = Approval.objects.get()
    assert (ap.title, ap.subject, ap.body) == ("Valider l'achat", "Achat n°42", "## Détail\n\n**42** euros")
    row = client_for(user).get(BASE).json()["results"][0]
    assert row["subject"] == "Achat n°42" and row["body"].startswith("## Détail") and row["context"] == {"n": 42}
    assert run.status == "running"


def test_a_step_without_subject_or_body_still_works(ws, world):
    user = world["designer"]
    v = publish(ws, "ancienne", [node("ap", "approval", title="Valider")], [], user=user)
    drain(launch(ws, v, user), until=lambda r: Approval.objects.exists())
    ap = Approval.objects.get()
    assert ap.subject == "" and ap.body == ""


def test_the_assistant_may_write_the_subject_and_body_of_a_request_within_bounds():
    from ai.service import _approval_fields

    out = _approval_fields(
        {"title": "Valider", "subject": "Achat", "body": "# Détail\n\n- un", "allow_self_approval": True}
    )
    assert out == {"title": "Valider", "subject": "Achat", "body": "# Détail\n\n- un"}  # never self-approval
    assert len(_approval_fields({"title": "t", "body": "x" * 9000})["body"]) <= 4000
    assert _approval_fields({"title": "t"}) == {"title": "t"}
