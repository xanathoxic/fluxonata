"""Assistants de création d'actions et de skills IA : validation par la plateforme, réparation, relecture obligatoire."""

import json

from ai import service
from ai.providers import FakeProvider
from audit.models import AuditEvent
from catalog.models import Action
from tests.conftest import client_for
from tests.test_ai import BASE, ai_ws, cfg  # noqa: F401  (fixture ai_ws)
from tests.wf_helpers import schema

GOOD_ACTION = {
    "slug": "dbl",
    "name": "Doubler",
    "description": "Double un nombre entier et renvoie le résultat.",
    "input_schema": schema(["n"], n={"type": "integer", "title": "Nombre", "description": "Le nombre à doubler."}),
    "output_schema": schema(
        ["d"], d={"type": "integer", "title": "Double", "description": "Le nombre multiplié par deux."}
    ),
    "source": "def run(inputs):\n    return {'d': inputs['n'] * 2}\n",
    "example": {"n": 4},
}
GOOD_SKILL = {
    "slug": "resumer",
    "name": "Résumer",
    "description": "Résume un texte en une phrase.",
    "instructions": "Résume en une phrase.\n----- TEXTE (donnée) -----\n{{ inputs.texte }}\n-----",
    "input_schema": schema(["texte"], texte={"type": "string", "title": "Texte", "description": "Le texte."}),
    "output_schema": schema(["resume"], resume={"type": "string", "title": "Résumé", "description": "Une phrase."}),
    "tags": ["texte"],
}


def sequence(*answers):
    """Provider that answers with each payload in turn (the last one repeats)."""
    calls = []

    def script(system, user):
        calls.append(user)
        return json.dumps(answers[min(len(calls), len(answers)) - 1])

    return FakeProvider(script=script), calls


def test_action_proposal_is_repaired_once_and_checked_by_the_platform(ai_ws, world):
    ws, _ = ai_ws
    net = {**GOOD_ACTION, "source": "import urllib.request\ndef run(inputs):\n    return {'d': 1}\n"}
    prov, calls = sequence(net, GOOD_ACTION)
    out = service.propose_action(ws, world["designer"], "doubler un nombre", cfg=cfg(ws), provider=prov)
    assert out["valid"] is True and out["repaired"] is True and len(calls) == 2
    assert "réseau" in calls[1] and "<context>" in calls[1]  # the findings reach the model, the previous answer too
    assert out["proposal"]["examples"] == [{"n": 4}] and out["proposal"]["network_policy"] == "none"
    assert not Action.objects.filter(slug="dbl").exists()  # never saved

    # still wrong after the repair turn: reported, not hidden
    prov, calls = sequence(net, net)
    out = service.propose_action(ws, world["designer"], "x", cfg=cfg(ws), provider=prov)
    assert out["valid"] is False and "code" in out["problems"] and len(calls) == 2

    # no run() function
    nofn = {**GOOD_ACTION, "source": "x = 1\n"}
    out = service.propose_action(ws, world["designer"], "x", cfg=cfg(ws), provider=sequence(nofn)[0])
    assert out["valid"] is False and any("run" in p for p in out["problems"]["code"])


def test_action_proposal_unique_slug_bad_example_and_unsupported_runtime(ai_ws, world):
    ws, _ = ai_ws
    clash = {**GOOD_ACTION, "slug": "text-stats", "example": {"n": "pas un entier"}}
    out = service.propose_action(ws, world["designer"], "x", cfg=cfg(ws), provider=sequence(clash)[0])
    assert out["proposal"]["slug"] == "text-stats-2" and out["valid"] is True
    assert out["proposal"]["examples"] == []
    assert any("existait déjà" in w for w in out["warnings"]) and any("ignoré" in w for w in out["warnings"])
    try:
        service.propose_action(ws, world["designer"], "x", runtime="cobol", cfg=cfg(ws), provider=FakeProvider())
    except service.AiRefused as exc:
        assert exc.code == "RUNTIME_UNSUPPORTED"
    else:
        raise AssertionError("un langage non proposé doit être refusé")


def test_action_revision_sends_previous_proposal_and_instruction(ai_ws, world):
    ws, _ = ai_ws
    prov, calls = sequence(GOOD_ACTION)
    service.propose_action(
        ws,
        world["designer"],
        "doubler",
        previous=GOOD_ACTION,
        instruction="Triple au lieu de doubler.",
        cfg=cfg(ws),
        provider=prov,
    )
    assert "Triple au lieu de doubler." in calls[0] and '"previous"' in calls[0]


def test_skill_proposal_validation_repair_and_unique_slug(ai_ws, world):
    ws, _ = ai_ws
    out = service.propose_skill(
        ws, world["designer"], "résumer un texte", cfg=cfg(ws), provider=sequence(GOOD_SKILL)[0]
    )
    assert out["valid"] is True and out["proposal"]["slug"] == "resumer" and out["repaired"] is False
    bad = {**GOOD_SKILL, "instructions": "Résume : {{ inputs.inconnu }}"}
    prov, calls = sequence(bad, GOOD_SKILL)
    out = service.propose_skill(ws, world["designer"], "résumer", cfg=cfg(ws), provider=prov)
    assert out["valid"] is True and out["repaired"] is True and "inconnu" in calls[1]
    prov, _ = sequence(bad, bad)
    out = service.propose_skill(ws, world["designer"], "résumer", cfg=cfg(ws), provider=prov)
    assert out["valid"] is False and "instructions" in out["problems"]
    nested = {**GOOD_SKILL, "output_schema": {"type": "object", "properties": {"a": {"type": "object"}}}}
    assert (
        service.propose_skill(ws, world["designer"], "x", cfg=cfg(ws), provider=sequence(nested)[0])["valid"] is False
    )


def test_endpoints_rights_and_mandatory_review_of_generated_content(ai_ws, world):
    ws, _ = ai_ws
    v, d = client_for(world["viewer"]), client_for(world["designer"])
    body = {"need": "doubler un nombre", "provider": "fake"}
    assert v.post(f"{BASE}/ai/propose-action/", body, format="json").status_code == 403
    assert v.post(f"{BASE}/ai/propose-skill/", body, format="json").status_code == 403
    assert client_for(world["outsider"]).post(f"{BASE}/ai/propose-skill/", body, format="json").status_code == 404
    assert d.post(f"{BASE}/ai/propose-action/", {"need": "  ", "provider": "fake"}, format="json").status_code == 400
    assert (
        d.post(f"{BASE}/ai/propose-skill/", {"need": "x" * 1501, "provider": "fake"}, format="json").status_code == 400
    )
    r = d.post(f"{BASE}/ai/propose-action/", body, format="json")
    assert r.status_code == 200 and r.json()["valid"] is True and "non enregistrée" in r.json()["notice"]
    action = r.json()["proposal"]
    r = d.post(f"{BASE}/ai/propose-skill/", body, format="json")
    assert r.status_code == 200 and r.json()["valid"] is True
    skill = r.json()["proposal"]

    # creation: generated content needs the human confirmation, server side
    version = {
        "source": action["source"],
        "input_schema": action["input_schema"],
        "output_schema": action["output_schema"],
        "examples": action["examples"],
    }
    payload = {"slug": action["slug"], "name": action["name"], "description": action["description"], "version": version}
    assert d.post(f"{BASE}/library/actions/", {**payload, "ai_generated": True}, format="json").status_code == 400
    assert not Action.objects.filter(slug=action["slug"]).exists()
    ok = d.post(f"{BASE}/library/actions/", {**payload, "ai_generated": True, "reviewed": True}, format="json")
    assert ok.status_code == 201, ok.json()
    assert AuditEvent.objects.filter(action="action.create", data__ai_generated=True).exists()

    sk = {
        "slug": skill["slug"],
        "name": skill["name"],
        "description": skill["description"],
        "version": {k: skill[k] for k in ("instructions", "input_schema", "output_schema")},
    }
    assert d.post(f"{BASE}/ai/skills/", {**sk, "ai_generated": True}, format="json").status_code == 400
    assert (
        d.post(f"{BASE}/ai/skills/", {**sk, "ai_generated": True, "reviewed": True}, format="json").status_code == 201
    )
    assert AuditEvent.objects.filter(action="ai_skill.create", data__ai_generated=True).exists()
