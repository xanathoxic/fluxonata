import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APIClient

from catalog.demo_actions import TEXT_STATS
from catalog.models import Action, ActionVersion
from tenancy.models import Environment, Membership, Organization, Role, Workspace
from tests.ssh_helpers import ssh_ws, sshd, target  # noqa: F401 - fixtures of the SSH step tests (disposable server, synthetic connection)


@pytest.fixture(autouse=True, scope="session")
def _vault_keys(tmp_path_factory):
    import os

    os.environ["ORCH_KEY_DIR"] = str(tmp_path_factory.mktemp("keys"))


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


@pytest.fixture(autouse=True)
def _no_ambient_egress_allowlist(settings):
    """The developer's .env may allow some hosts (a local Vault, an Ollama server): tests must not depend on it. A test that
    needs a host allowed sets `settings.ORCH_EGRESS_ALLOWLIST` itself."""
    settings.ORCH_EGRESS_ALLOWLIST = ""


@pytest.fixture(autouse=True)
def _light_dry_run_runner(monkeypatch):
    """The assistant's dry run executes scripts: tests use the in-host runner (fast, no container)."""
    from ai import service
    from runs.runners import SubprocessRunner

    monkeypatch.setattr(service, "RUNNER_FACTORY", SubprocessRunner)


def make_workspace(slug):
    org = Organization.objects.create(name=slug, slug=f"org-{slug}")
    ws = Workspace.objects.create(organization=org, name=slug, slug=slug)
    for kind in ("test", "prod"):
        Environment.objects.create(workspace=ws, kind=kind, name=kind)
    return ws


def make_user(name, ws=None, role=Role.DESIGNER):
    user = get_user_model().objects.create_user(name, password="pw-test-123")
    if ws:
        Membership.objects.create(user=user, workspace=ws, role=role)
    return user


def add_action(ws, spec=TEXT_STATS, **overrides):
    action = Action.objects.create(workspace=ws, slug=spec["slug"], name=spec["name"])
    fields = {k: spec[k] for k in ("source", "input_schema", "output_schema", "limits")} | overrides
    return ActionVersion.objects.create(action=action, version=spec["version"], **fields)


@pytest.fixture
def world(db):
    ws_a, ws_b = make_workspace("team-a"), make_workspace("team-b")
    return {
        "ws_a": ws_a,
        "ws_b": ws_b,
        "designer": make_user("dana", ws_a, Role.DESIGNER),
        "viewer": make_user("vic", ws_a, Role.VIEWER),
        "outsider": make_user("otto", ws_b, Role.DESIGNER),
        "version_a": add_action(ws_a),
        "version_b": add_action(ws_b),
    }


def client_for(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def ai_ws(world):
    from ai.models import AiPolicy, AiProvider
    from catalog.demo_actions import DEMO_ACTIONS
    from catalog.models import Action, ActionVersion
    from tests.wf_helpers import node, publish
    from workflows.models import Workflow

    ws = world["ws_a"]
    ts = DEMO_ACTIONS[0]
    Action.objects.filter(workspace=ws, slug="text-stats").update(description=ts["description"], tags=ts["tags"])
    for spec in DEMO_ACTIONS[3:5]:
        a = Action.objects.create(
            workspace=ws, slug=spec["slug"], name=spec["name"], description=spec["description"], tags=spec["tags"]
        )
        ActionVersion.objects.create(
            action=a, version="1.0.0", **{k: spec[k] for k in ("source", "input_schema", "output_schema", "limits")}
        )
    # what the catalog offers are finished workflows, not their building blocks
    for slug, name, description, action in (
        ("statistiques-de-texte", "Statistiques de texte", ts["description"], "text-stats"),
        (
            "analyse-validee",
            "Analyse avec validation",
            "Analyse un texte puis demande une validation s'il est long.",
            "text-stats",
        ),
    ):
        publish(ws, slug, [node("a", action=action, inputs={"text": "x"})], [], user=world["designer"])
        Workflow.objects.filter(workspace=ws, slug=slug).update(name=name, description=description, in_catalog=True)
    admin = make_user("ada", ws, Role.ADMIN)
    AiPolicy.objects.update_or_create(
        workspace=ws, defaults={"mode": "local_strict", "dry_run": False}
    )  # on in its own tests
    AiProvider.objects.create(workspace=ws, name="fake", kind="fake")
    return ws, admin


@pytest.fixture
def ws(world):
    from tests.wf_helpers import ACTIONS, make_action

    for slug in ACTIONS:
        make_action(world["ws_a"], slug) if slug != "echo" else None
    from catalog.models import ActionVersion

    if not ActionVersion.objects.filter(action__workspace=world["ws_a"], action__slug="echo").exists():
        make_action(world["ws_a"], "echo")
    return world["ws_a"]
