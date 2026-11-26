"""Un runner dédié aux playbooks (ADR 0037) : l'agent réel, le protocole réel, le bac à sable Docker et un serveur SSH jetable.

Vérifie aussi la chaîne des identifiants : secret du coffre → connexion → variable d'environnement de l'action → clé SSH utilisée, sans jamais
paraître dans les journaux ni dans le résultat."""

import sys
import threading
import uuid
from pathlib import Path

import pytest

from catalog.models import Action, ActionVersion
from runs import services
from runs.models import Attempt, Runner
from runs.runner_auth import new_token
from runs.runners import DockerRunner
from tenancy.models import Environment
from tests.conftest import make_user, make_workspace
from tests.test_ansible_runtime import SSH_PLAY, needs_ansible_image, ssh_target  # noqa: F401 - fixture de pytest
from vault.models import Connection, Secret

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runner"))
import agent as agent_mod  # noqa: E402

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def site(live_server, settings):
    settings.ORCH_LEASE_SECONDS = 3
    ws = make_workspace("ansible-ws")
    user = make_user("ines", ws)
    raw, digest, prefix = new_token()
    runner = Runner.objects.create(
        name="site-paris",
        scope="shared",
        runtimes=["ansible"],
        tags=["ansible", "site-paris"],
        token_hash=digest,
        token_prefix=prefix,
        max_concurrent=2,
    )
    return type("S", (), {"ws": ws, "user": user, "runner": runner, "url": live_server.url, "token": raw})


def playbook_version(ws, tags, network="targets", source=SSH_PLAY):
    a = Action.objects.create(workspace=ws, slug=f"deploy-{uuid.uuid4().hex[:6]}", name="Déployer")
    return ActionVersion.objects.create(
        action=a,
        version="1.0.0",
        runtime="ansible",
        source=source,
        input_schema={
            "type": "object",
            "properties": {
                "inventory": {"type": "object"},
                "user": {"type": "string"},
                "known_hosts": {"type": "string"},
            },
        },
        output_schema={"type": "object"},
        limits={"timeout_seconds": 120},
        network_policy=network,
        side_effects="external",
        required_tags=tags,
        secrets_required=[{"slot": "ssh", "env": "FLX_SSH_KEY_B64"}],
    )


def enqueue(site, version, inputs, connections=None):
    return services.enqueue(
        workspace=site.ws,
        environment=Environment.objects.get(workspace=site.ws, kind="test"),
        action_version=version,
        user=site.user,
        inputs=inputs,
        connections=connections or {},
    )


def run_agent(site, idle_exit=15):
    agent = agent_mod.Agent(
        agent_mod.Client(site.url, site.token), DockerRunner(), concurrency=1, once=True, idle_exit=idle_exit
    )
    out = []
    t = threading.Thread(target=lambda: out.append(agent.run()), daemon=True)
    t.start()
    return agent, t, out


def key_connection(site, key_b64):
    s = Secret(workspace=site.ws, name="cle-ssh")
    s.set_value(key_b64)
    s.save()
    Connection.objects.create(workspace=site.ws, name="ssh-paris", label="SSH Paris", kind="generic", secret=s)


@needs_ansible_image
def test_the_dedicated_runner_plays_a_playbook_over_ssh_with_the_vault_key(site, ssh_target):  # noqa: F811
    key_connection(site, ssh_target.key_b64)
    v = playbook_version(site.ws, ["ansible"])
    ex = enqueue(
        site,
        v,
        {"inventory": ssh_target.inventory, "user": "deploy", "known_hosts": ssh_target.known_hosts},
        {"ssh": "ssh-paris"},
    )
    agent, t, out = run_agent(site)
    t.join(180)
    ex.refresh_from_db()
    logs = Attempt.objects.get(execution=ex).logs
    assert ex.status == "succeeded", (ex.error, logs)
    assert ex.runner_id == site.runner.pk and out == [0]
    assert ex.result["tasks"][-1]["msg"] == "deploy" and ex.result["stats"][ssh_target.name]["unreachable"] == 0
    assert ssh_target.key_b64 not in logs and ssh_target.key_b64 not in str(ex.result) and "PRIVATE KEY" not in logs


def test_a_runner_without_the_playbooks_tag_or_runtime_never_receives_it(site):
    v = playbook_version(site.ws, ["site-lyon"])  # un autre site
    ex = enqueue(site, v, {"inventory": {"all": {"hosts": {"x": {}}}}})
    agent, t, _ = run_agent(site, idle_exit=4)
    t.join(60)
    ex.refresh_from_db()
    assert ex.status == "queued" and agent.done == 0  # le runner de Paris ne joue pas les playbooks de Lyon
    site.runner.runtimes = ["python"]
    site.runner.save()
    v2 = playbook_version(site.ws, ["site-paris"])
    ex2 = enqueue(site, v2, {"inventory": {"all": {"hosts": {"x": {}}}}})
    agent, t, _ = run_agent(site, idle_exit=4)
    t.join(60)
    ex2.refresh_from_db()
    assert ex2.status == "queued"  # sans le langage « ansible », non plus


@needs_ansible_image
def test_the_dedicated_runner_receives_and_plays_the_roles_of_the_action(site):
    """Les rôles figés de l'action voyagent avec la tâche (protocole, champ `support_files`) et l'agent les dépose dans /work/roles."""
    from catalog.models import AnsibleRole, AnsibleRoleVersion
    from tests.test_ansible_roles import COMMON, ROLES_PLAYBOOK, WEB_REAL
    from tests.test_ansible_runtime import LOCAL

    for name, files in (("common", COMMON), ("web", WEB_REAL)):
        AnsibleRoleVersion.objects.create(
            role=AnsibleRole.objects.create(workspace=site.ws, name=name), version="1.0.0", files=files
        )
    v = playbook_version(
        site.ws, ["ansible"], network="none", source=ROLES_PLAYBOOK
    )  # sert de modèle : une version publiée ne change plus, on en crée une 1.1.0 avec les rôles
    pinned = ActionVersion.objects.create(
        action=v.action,
        version="1.1.0",
        runtime="ansible",
        source=ROLES_PLAYBOOK,
        input_schema=v.input_schema,
        output_schema=v.output_schema,
        required_tags=["ansible"],
        ansible_roles=[{"role": "web", "version": "1.0.0"}, {"role": "common", "version": "1.0.0"}],
    )
    ex = enqueue(site, pinned, {"inventory": LOCAL, "extra_vars": {"message": "depuis l'agent"}})
    agent, t, out = run_agent(site)
    t.join(180)
    ex.refresh_from_db()
    logs = Attempt.objects.get(execution=ex).logs
    assert ex.status == "succeeded", (ex.error, logs)
    assert ex.runner_id == site.runner.pk and out == [0]
    assert ex.result["data"] == {"page": "page: depuis l'agent"}
    site.runner.refresh_from_db()
    assert (
        site.runner.agent_version == agent_mod.VERSION
    )  # l'agent annonce une version qui sait déposer des fichiers d'appui
