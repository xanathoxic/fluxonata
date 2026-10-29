"""The plan the assistant envisages BEFORE generating: cheap, plain words, validated/edited/refused by the person."""

import json

import pytest

from ai import jobs, service
from ai.models import AiJob, AiPolicy
from ai.providers import FakeProvider
from ai.speclint import NAME_TYPED
from tests.conftest import client_for
from tests.test_ai import BASE, ai_ws, cfg  # noqa: F401
from tests.test_ai_edit import Scripted

OUTLINE = {
    "title": "Création d'une VM",
    "summary": "Crée une machine virtuelle après contrôle du nom.",
    "inputs": [
        {"name": "Nom VM!", "type": "texte", "purpose": "nom de la machine"},
        {"name": "cpu", "type": "gros", "purpose": "nombre de cœurs"},
    ],
    "steps": [
        {"text": "On vérifie le nom ; sinon exception « Nom invalide »."},
        {"text": "Approbation « Autoriser la création » ; si refusée, exception « Création refusée »."},
        {"text": " "},
    ],
    "uses": [{"kind": "action", "name": "Envoyer une notification (simulé)", "note": "prévenir"}],
    "assumptions": ["Le demandeur est prévenu."],
    "questions": [{"question": "Que faire si la création échoue ?", "proposed": "Ouvrir un incident."}],
}


def outline_of(ws, user, model, **kw):
    return service.make_outline(
        ws, user, "je veux créer une vm", cfg=cfg(ws), provider=FakeProvider(script=model), **kw
    )


def test_the_plan_is_cleaned_into_a_bounded_plain_structure(ai_ws, world):
    ws, _ = ai_ws
    out = outline_of(ws, world["designer"], Scripted(outline_workflow=OUTLINE))
    assert [i["name"] for i in out["inputs"]] == ["nom_vm", "cpu"] and out["inputs"][1][
        "type"
    ] == "texte"  # normalised, unknown type → texte
    assert [s["text"] for s in out["steps"]][0].startswith("On vérifie") and len(
        out["steps"]
    ) == 2  # empty step dropped
    assert out["questions"][0]["proposed"] == "Ouvrir un incident." and out["uses"][0]["name"].startswith("Envoyer")
    huge = {**OUTLINE, "steps": [{"text": "x" * 900}] * 40, "assumptions": ["y"] * 30}
    big = outline_of(ws, world["designer"], Scripted(outline_workflow=huge))
    assert len(big["steps"]) == 16 and len(big["steps"][0]["text"]) == 260 and len(big["assumptions"]) == 8


def test_a_plan_without_any_step_is_refused_with_a_way_out(ai_ws, world):
    ws, _ = ai_ws
    with pytest.raises(service.AiRefused, match="générez sans plan"):
        outline_of(ws, world["designer"], Scripted(outline_workflow={"title": "x", "steps": []}))


def test_a_revision_gets_the_plan_as_edited_and_the_instruction(ai_ws, world):
    ws, _ = ai_ws
    model = Scripted(outline_workflow=OUTLINE)
    outline_of(ws, world["designer"], model, previous=OUTLINE, feedback="ajoute une notification à la fin")
    sent = model.data(0)
    assert sent["feedback"] == "ajoute une notification à la fin" and sent["previous"]["steps"][0]["text"].startswith(
        "On vérifie"
    )
    assert sent["request"] == "je veux créer une vm"


def test_the_validated_plan_becomes_the_precise_request(ai_ws, world):
    ws, _ = ai_ws
    out = outline_of(ws, world["designer"], Scripted(outline_workflow=OUTLINE))
    text = service.outline_to_request(
        "je veux créer une vm",
        {
            **out,
            "answers": [
                {"question": out["questions"][0]["question"], "answer": "Prévenir l'équipe"},
                {"question": "x", "answer": ""},
            ],
        },
    )
    assert (
        "Crée un workflow « Création d'une VM »." in text
        and "Demande d'origine de l'utilisateur : je veux créer une vm" in text
    )
    assert (
        "1. On vérifie le nom" in text
        and "2. Approbation « Autoriser la création »" in text
        and "exception « Création refusée »" in text
    )
    assert "Que faire si la création échoue ? → Prévenir l'équipe" in text and "Hypothèses validées" in text
    assert "action « Envoyer une notification (simulé) »" in text
    # the names the person validated are the names the generation will be checked against
    assert {m.group(1) for m in NAME_TYPED.finditer(text)} >= {"nom_vm", "cpu"}
    assert len(text) <= service.MAX_REQUEST


URL = f"{BASE}/ai/outline-jobs/"
DRAFT = f"{BASE}/ai/draft-jobs/"


def test_the_plan_job_is_run_by_the_worker_and_followed_like_a_draft_job(ai_ws, world):
    ws, _ = ai_ws
    c = client_for(world["designer"])
    r = c.post(URL, {"request": "je veux créer une vm", "provider": "fake"}, format="json")
    assert r.status_code == 202
    job = jobs.claim_next("w1")
    assert job.kind == "outline"
    jobs.process(job)
    out = c.get(f"{DRAFT}{r.json()['id']}/").json()
    assert out["status"] == "succeeded" and out["result"]["outline"]["steps"] and "usage" in out["result"]
    assert AiJob.objects.get(pk=job.pk).payload == {"slug": None, "name": None}  # the request text is not kept


def test_asking_for_a_plan_is_validated_and_needs_the_right_to_edit(ai_ws, world):
    ws, _ = ai_ws
    c = client_for(world["designer"])
    assert c.post(URL, {"request": ""}, format="json").status_code == 400
    assert c.post(URL, {"request": "x" * 7000}, format="json").status_code == 400
    assert c.post(URL, {"request": "x", "previous": "pas un objet"}, format="json").status_code == 400
    assert client_for(world["viewer"]).post(URL, {"request": "x"}, format="json").status_code == 403
    AiPolicy.objects.filter(workspace=ws).update(mode="off")
    assert c.post(URL, {"request": "x"}, format="json").status_code in (400, 403)


def test_a_draft_job_started_from_a_validated_plan_uses_the_composed_request(ai_ws, world):
    ws, _ = ai_ws
    c = client_for(world["designer"])
    out = outline_of(ws, world["designer"], Scripted(outline_workflow=OUTLINE))
    body = {
        "request": "je veux créer une vm",
        "name": "Plan validé",
        "slug": "plan-valide",
        "provider": "fake",
        "outline": {**out, "answers": []},
    }
    r = c.post(DRAFT, body, format="json")
    assert r.status_code == 202
    sent = AiJob.objects.get(pk=r.json()["id"]).payload["request"]
    assert sent.startswith("Crée un workflow « Création d'une VM »") and "1. On vérifie le nom" in sent
    assert c.post(DRAFT, {**body, "slug": "autre", "outline": "pas un objet"}, format="json").status_code == 400
    assert c.post(
        DRAFT, {**body, "slug": "vide", "outline": {"title": "x", "steps": []}}, format="json"
    ).status_code in (400, 502)
