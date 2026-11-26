"""Essai d'un brouillon de version dans le bac à sable (sans rien publier) : version d'essai interne, rôles figés, droits, entrées vérifiées."""

from catalog.models import Action, ActionVersion
from runs.models import Execution
from tests.conftest import client_for
from tests.test_ansible_roles import BASE, action_body, make_role

DRAFT = {
    "runtime": "ansible",
    "source": "- hosts: all\n  gather_facts: false\n  roles: [web]\n",
    "input_schema": {
        "type": "object",
        "required": ["inventory"],
        "properties": {"inventory": {"type": "object"}},
    },
    "output_schema": {"type": "object"},  # contrat incomplet : un essai n'exige pas de contrat publiable
    "required_tags": ["ansible"],
    "ansible_roles": [{"role": "web", "version": "latest"}],
}


def trial(c, slug="deploy", version=None, inputs=None, **extra):
    body = {"version": version or DRAFT, "inputs": {"inventory": {"all": {}}} if inputs is None else inputs, **extra}
    return c.post(f"{BASE}/actions/{slug}/trial/", body, format="json")


def setup(world):
    make_role(world["ws_a"], "web")
    c = client_for(world["designer"])
    assert (
        c.post(
            f"{BASE}/library/actions/", action_body(ansible_roles=[{"role": "web", "version": "latest"}]), format="json"
        ).status_code
        == 201
    )
    return c


def test_a_draft_is_queued_under_a_hidden_trial_version_and_nothing_is_published(world):
    c = setup(world)
    before = list(ActionVersion.objects.filter(action__slug="deploy").values_list("version", flat=True))
    r = trial(c)
    assert r.status_code == 202, r.content
    ex = Execution.objects.get(pk=r.json()["execution_id"])
    assert ex.action_version.action.slug == "__essai__" and ex.status == "queued"
    assert ex.action_version.ansible_roles == [{"role": "web", "version": "1.0.0"}]  # figé comme à la publication
    assert list(ActionVersion.objects.filter(action__slug="deploy").values_list("version", flat=True)) == before
    names = [a["slug"] for a in c.get(f"{BASE}/actions/").json()["results"]]
    assert "__essai__" not in names and "deploy" in names
    assert r.json()["execution_id"] not in [e["id"] for e in c.get(f"{BASE}/executions/").json()["results"]]


def test_the_same_draft_reuses_its_trial_version_and_a_changed_one_gets_another(world):
    c = setup(world)
    trial(c)
    trial(c)
    assert ActionVersion.objects.filter(action__slug="__essai__").count() == 1
    trial(c, version={**DRAFT, "source": DRAFT["source"] + "  tags: [x]\n"})
    assert ActionVersion.objects.filter(action__slug="__essai__").count() == 2
    assert Action.objects.get(slug="deploy").versions.count() == 1


def test_trial_inputs_are_checked_against_the_draft_schema(world):
    c = setup(world)
    r = trial(c, inputs={})
    assert r.status_code == 400 and "inventory" in str(r.json())
    assert trial(c, inputs=[1]).status_code == 400


def test_an_unknown_role_or_a_missing_dependency_is_refused_like_a_publication(world):
    c = setup(world)
    bad = {**DRAFT, "ansible_roles": [{"role": "inconnu", "version": "latest"}]}
    assert trial(c, version=bad).status_code == 400


def test_trial_needs_the_right_to_publish_and_an_existing_action(world):
    c = setup(world)
    assert trial(client_for(world["viewer"])).status_code == 403
    assert trial(client_for(world["outsider"])).status_code == 404
    assert trial(c, slug="absente").status_code == 404
    assert Execution.objects.filter(action_version__action__slug="__essai__").count() == 0
