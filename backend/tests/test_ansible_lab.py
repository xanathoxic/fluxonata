"""Laboratoire Ansible (ADR 0037) : le script des machines jetables, la commande qui installe le workflow de validation, et le test de bout en bout — de VRAIES
machines SSH (conteneurs), le runner dédié (agent réel), le coffre, les rôles, l'inventaire géré — jusqu'au bilan du workflow.

Les essais qui lancent des conteneurs demandent Docker, l'image `fluxonata-runner-ansible:local` et de quoi construire l'image des machines (réseau pour apk) :
sans eux ils sont ignorés avec cette raison, jamais comptés comme réussis."""

import importlib.util
import json
import stat
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from catalog import ansible_roles as roles_lib
from catalog.management.commands import check_ansible_lab as check
from catalog.management.commands import seed_ansible_lab as seed
from catalog.models import Action, ActionVersion, AnsibleRole, Inventory
from runs import engine
from runs.models import Approval, Runner, WorkflowRun
from runs.runners import DockerRunner, SubprocessRunner
from runs.worker import process_one
from tenancy.models import Environment, Role
from tests.conftest import make_user, make_workspace
from tests.test_ansible_runtime import needs_ansible_image
from workflows.models import Workflow
from workflows.validation import validate

pytestmark = pytest.mark.django_db(transaction=True)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ansible_lab.py"
spec = importlib.util.spec_from_file_location("ansible_lab", SCRIPT)
labmod = importlib.util.module_from_spec(spec)
sys.modules["ansible_lab"] = labmod
spec.loader.exec_module(labmod)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runner"))
import agent as agent_mod  # noqa: E402

needs_ssh_keygen = pytest.mark.skipif(labmod.shutil.which("ssh-keygen") is None, reason="ssh-keygen indisponible")


@pytest.fixture
def lab(tmp_path):
    return labmod.Lab(
        directory=tmp_path / "lab", network=f"flxt-net-{uuid.uuid4().hex[:6]}", prefix=f"flxt{uuid.uuid4().hex[:5]}-"
    )


@pytest.fixture
def files_only_lab(lab):
    """Un laboratoire sans conteneur : clés et lab.json, tout ce que lit la commande d'installation."""
    labmod.ensure_keys(lab)
    labmod.write_info(lab)
    return lab


@pytest.fixture
def space():
    ws = make_workspace("lab-ws")
    return type(
        "S",
        (),
        {
            "ws": ws,
            "designer": make_user("dana", ws, Role.DESIGNER),
            "approver": make_user("claire", ws, Role.APPROVER),
        },
    )


# --- le script du laboratoire (sans Docker) ---------------------------------------------------------------------------------------------------


@needs_ssh_keygen
def test_keys_are_created_once_and_known_hosts_lists_every_machine(lab):
    labmod.ensure_keys(lab)
    first = (lab.key.read_text(), lab.known_hosts.read_text())
    assert stat.S_IMODE(lab.key.stat().st_mode) == 0o600 and stat.S_IMODE(lab.directory.stat().st_mode) == 0o700
    lines = lab.known_hosts.read_text().splitlines()
    assert [ln.split()[0] for ln in lines] == [lab.container(s) for s in lab.machines]
    assert all(ln.split()[1] == "ssh-ed25519" and len(ln.split()) == 3 for ln in lines)
    labmod.ensure_keys(lab)  # relancé : les clés ne changent pas (la liste known_hosts reste valable)
    assert (lab.key.read_text(), lab.known_hosts.read_text()) == first
    assert len({ln.split()[2] for ln in lines}) == len(lines)  # une clé d'hôte par machine


@needs_ssh_keygen
def test_lab_json_describes_what_the_seed_needs(files_only_lab):
    info = json.loads(files_only_lab.info.read_text())
    assert info["network"] == files_only_lab.network and info["user"] == "deploy"
    assert {(m["short"], m["group"]) for m in info["machines"]} == {("web1", "web"), ("web2", "web"), ("db1", "db")}
    assert info["known_hosts"] == files_only_lab.known_hosts.read_text() and Path(info["key"]).is_file()


def test_the_agent_gets_the_lab_network_and_no_hardening_that_needs_digests(lab):
    env = labmod.agent_env(lab, "http://api.test")
    assert (
        env["ORCH_ANSIBLE_NETWORK"] == lab.network
        and env["ORCH_URL"] == "http://api.test"
        and env["ORCH_RUNNER_EXECUTOR"] == "docker"
    )
    assert Path(env["ORCH_WORKERS_DIR"]).is_dir() and Path(env["ORCH_BACKEND_DIR"]).is_dir()


def test_the_agent_needs_the_token_from_the_seed(lab):
    lab.directory.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="runner.token"):
        labmod.agent_up(lab, "http://x")
    assert labmod.agent_down(lab) is False  # rien à arrêter


# --- la commande d'installation (sans Docker) ------------------------------------------------------------------------------------------------


@needs_ssh_keygen
def test_the_seed_installs_every_piece_of_the_lab(space, files_only_lab):
    call_command("seed_ansible_lab", workspace="lab-ws", user="dana", lab_dir=str(files_only_lab.directory))
    ws = space.ws
    runner = Runner.objects.get(name=seed.RUNNER)
    assert (
        runner.scope == "dedicated"
        and runner.runtimes == ["ansible"]
        and seed.RUNNER_TAG in runner.tags
        and list(runner.workspaces.all()) == [ws]
    )
    token = files_only_lab.directory / "runner.token"
    assert token.read_text().startswith("orr_") and stat.S_IMODE(token.stat().st_mode) == 0o600
    assert runner.token_hash != token.read_text()  # seul le condensat est en base
    assert {i.slug for i in Inventory.objects.filter(workspace=ws)} == {seed.INVENTORY, seed.INVENTORY_PANNE}
    assert set(AnsibleRole.objects.filter(workspace=ws).values_list("name", flat=True)) == set(seed.ROLES)
    for slug in seed.ACTIONS:
        v = ActionVersion.objects.get(action__workspace=ws, action__slug=slug)
        assert v.runtime == "ansible" and v.required_tags == [seed.RUNNER_TAG] and v.network_policy == "targets"
        assert v.secrets_required == [{"slot": "ssh", "env": "FLX_SSH_KEY_B64"}] and v.side_effects == "external"
    assert [r["role"] for r in ActionVersion.objects.get(action__slug="lab-deployer").ansible_roles] == [
        "common",
        "web",
        "db",
    ]
    assert Workflow.objects.get(workspace=ws, slug=seed.WORKFLOW).versions.count() == 1


@needs_ssh_keygen
def test_the_workflow_is_valid_and_wires_the_connection_inventory_and_runner_tag(space, files_only_lab):
    call_command("seed_ansible_lab", workspace="lab-ws", user="dana", lab_dir=str(files_only_lab.directory))
    graph = seed.Command().graph(json.loads(files_only_lab.info.read_text()))
    assert [i for i in validate(graph, space.ws, seed.WORKFLOW) if i["level"] == "error"] == []
    steps = [n for n in graph["nodes"] if n["type"] == "action"]
    assert all(
        n["connections"] == {"ssh": seed.CONNECTION}
        and n["inputs"]["inventory_ref"] in (seed.INVENTORY, seed.INVENTORY_PANNE)
        for n in steps
    )
    assert all(
        n["inputs"]["known_hosts"] == files_only_lab.known_hosts.read_text() for n in steps
    )  # la liste de clés vient du laboratoire
    assert "ansible_password" not in json.dumps(graph) and "PRIVATE KEY" not in json.dumps(
        graph
    )  # aucune clé dans le workflow
    assert [n["id"] for n in graph["nodes"]] == [
        "ssh",
        "panne",
        "reset",
        "essai",
        "accord",
        "deploiement",
        "idempotence",
        "controle",
        "cible",
        "bilan",
    ]


@needs_ssh_keygen
def test_the_inline_playbook_workflow_is_valid_pins_its_roles_and_holds_no_secret(space, files_only_lab):
    call_command("seed_ansible_lab", workspace="lab-ws", user="dana", lab_dir=str(files_only_lab.directory))
    graph = seed.Command().graph_inline(json.loads(files_only_lab.info.read_text()))
    assert [i for i in validate(graph, space.ws, seed.WORKFLOW_INLINE) if i["level"] == "error"] == []
    (step,) = graph["nodes"]
    assert (
        step["type"] == "ansible"
        and step["required_tags"] == [seed.RUNNER_TAG]
        and step["connections"] == {"ssh": seed.CONNECTION}
    )
    version = Workflow.objects.get(workspace=space.ws, slug=seed.WORKFLOW_INLINE).versions.get()
    assert [r["role"] for r in version.graph["nodes"][0]["roles"]] == ["common", "web"]
    assert all(r["version"] != "latest" for r in version.graph["nodes"][0]["roles"])  # figées à la publication
    assert "PRIVATE KEY" not in json.dumps(graph)


@needs_ssh_keygen
def test_the_lab_inventory_has_groups_variables_and_a_disabled_machine(space, files_only_lab):
    from catalog import inventory as inv

    call_command("seed_ansible_lab", workspace="lab-ws", user="dana", lab_dir=str(files_only_lab.directory))
    out = inv.render(Inventory.objects.get(workspace=space.ws, slug=seed.INVENTORY))["all"]
    assert out["vars"]["ansible_user"] == "deploy" and out["vars"]["lab_env"] == "laboratoire"
    assert out["children"]["web"]["vars"] == {"http_port": 8080} and out["children"]["db"]["vars"] == {"db_port": 5433}
    assert sorted(out["children"]["web"]["hosts"]) == [
        files_only_lab.container("web1"),
        files_only_lab.container("web2"),
    ]  # « flx-lab-eteinte » (désactivée) absente
    assert "flx-lab-eteinte" not in json.dumps(out)
    panne = inv.render(Inventory.objects.get(workspace=space.ws, slug=seed.INVENTORY_PANNE))["all"]["children"]["web"][
        "hosts"
    ]
    assert "flx-lab-absente" in panne


@needs_ssh_keygen
def test_the_seed_never_puts_the_private_key_in_the_database_in_clear(space, files_only_lab):
    from vault.models import Connection, Secret

    call_command("seed_ansible_lab", workspace="lab-ws", user="dana", lab_dir=str(files_only_lab.directory))
    secret = Secret.objects.get(workspace=space.ws, name=seed.SECRET)
    private = files_only_lab.key.read_text()
    assert private.splitlines()[1] not in str(secret.__dict__) and "PRIVATE KEY" not in str(
        secret.__dict__
    )  # chiffré au repos, comme tout secret
    assert Connection.objects.get(workspace=space.ws, name=seed.CONNECTION).secret_id == secret.pk


@needs_ssh_keygen
def test_running_the_seed_twice_changes_nothing_and_purge_removes_everything(space, files_only_lab):
    d = str(files_only_lab.directory)
    call_command("seed_ansible_lab", workspace="lab-ws", user="dana", lab_dir=d)
    token = (files_only_lab.directory / "runner.token").read_text()
    call_command("seed_ansible_lab", workspace="lab-ws", user="dana", lab_dir=d)
    assert (files_only_lab.directory / "runner.token").read_text() == token and Runner.objects.filter(
        name=seed.RUNNER
    ).count() == 1
    assert (
        Action.objects.filter(workspace=space.ws, slug__in=seed.ACTIONS).count() == len(seed.ACTIONS)
        and Workflow.objects.get(slug=seed.WORKFLOW).versions.count() == 1
    )
    call_command("seed_ansible_lab", workspace="lab-ws", lab_dir=d, purge=True)
    assert (
        not Workflow.objects.filter(slug=seed.WORKFLOW).exists()
        and not Runner.objects.filter(name=seed.RUNNER).exists()
    )
    assert (
        not AnsibleRole.objects.filter(name__in=seed.ROLES).exists()
        and not Inventory.objects.filter(slug=seed.INVENTORY).exists()
    )
    assert (
        not Action.objects.filter(slug__in=seed.ACTIONS).exists()
        and not (files_only_lab.directory / "runner.token").exists()
    )


def test_the_seed_refuses_a_missing_lab_an_unknown_workspace_and_a_stranger(space, files_only_lab, tmp_path):
    with pytest.raises(CommandError, match="ansible-lab.sh up"):
        call_command("seed_ansible_lab", workspace="lab-ws", lab_dir=str(tmp_path / "vide"))
    with pytest.raises(CommandError, match="inconnu"):
        call_command("seed_ansible_lab", workspace="nulle-part", lab_dir=str(files_only_lab.directory))
    if (files_only_lab.directory / "lab.json").exists():
        with pytest.raises(CommandError, match="n'est pas membre"):
            call_command("seed_ansible_lab", workspace="lab-ws", user="inconnu", lab_dir=str(files_only_lab.directory))


def test_the_lab_roles_pass_the_library_rules():
    for files in (seed.COMMON, seed.WEB, seed.DB, seed.CONTROLE):
        assert roles_lib.check_files(files) == files
    assert (
        roles_lib.dependencies(seed.WEB) == ["common"]
        and roles_lib.dependencies(seed.DB) == ["common"]
        and roles_lib.dependencies(seed.CONTROLE) == []
    )


# --- de bout en bout : de vraies machines, le runner dédié, le workflow ------------------------------------------------------------------------


def docker_out(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False).stdout  # noqa: S603,S607


def run_to_the_end(run, approver, timeout=600):
    """Orchestrateur et worker intégré (pour le script de bilan) dans ce fil ; les playbooks, eux, sont joués par l'agent. Valide l'accord demandé."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        engine.advance(run.pk)
        run = WorkflowRun.objects.get(pk=run.pk)
        for approval in Approval.objects.filter(step_run__run=run, status="pending"):
            engine.decide_approval(approval.id, approver, True, "validé par le test")
        if run.status in ("succeeded", "failed", "cancelled"):
            return run
        if not process_one("test-worker", SubprocessRunner()):
            time.sleep(0.5)
    return WorkflowRun.objects.get(pk=run.pk)


@needs_ansible_image
def test_the_lab_workflow_validates_every_point_on_real_machines(space, lab, live_server, settings):
    if not labmod.have_docker():
        pytest.skip("Docker indisponible")
    try:
        labmod.up(lab)
    except Exception as exc:  # noqa: BLE001 - construction de l'image des machines (réseau), ports, droits : on dit pourquoi
        labmod.down(lab)
        pytest.skip(f"laboratoire non démarrable : {str(exc)[:200]}")
    agent = None
    try:
        settings.ORCH_ANSIBLE_NETWORK = lab.network
        settings.ORCH_LEASE_SECONDS = 10
        call_command("seed_ansible_lab", workspace="lab-ws", user="dana", lab_dir=str(lab.directory))
        token = (lab.directory / "runner.token").read_text()
        agent = agent_mod.Agent(
            agent_mod.Client(live_server.url, token), DockerRunner(), concurrency=2, once=False, idle_exit=0
        )
        thread = threading.Thread(target=agent.run, daemon=True)
        thread.start()
        version = Workflow.objects.get(workspace=space.ws, slug=seed.WORKFLOW).versions.get()
        run, _ = engine.start_run(
            workspace=space.ws,
            environment=Environment.objects.get(workspace=space.ws, kind="test"),
            workflow_version=version,
            user=space.designer,
            inputs={"message": "bonjour du test"},
        )
        run = run_to_the_end(run, space.approver)
        assert run.status == "succeeded", (run.error, [(s.node_id, s.status, s.error) for s in run.steps.all()])
        assert run.outputs["valide"] is True, run.outputs
        points = {p["nom"]: p for p in run.outputs["points"]}
        assert len(points) == 9 and all(p["ok"] for p in points.values()), [p for p in points.values() if not p["ok"]]
        # les points que seule la plate-forme peut juger
        extra = check.platform_points(run, lab.key)
        assert all(ok for _, ok, _ in extra), [e for e in extra if not e[1]]
        # …et l'état RÉEL des machines, relu hors d'Ansible
        web1, web2, db1 = (lab.container(s) for s in ("web1", "web2", "db1"))
        assert f"machine: {web1}" in docker_out("exec", web1, "cat", "/opt/lab/identite.txt")
        assert "message: bonjour du test" in docker_out("exec", web2, "cat", "/opt/lab/identite.txt")
        assert "port 8080" in docker_out("exec", web1, "cat", "/opt/lab/web/index.html")
        assert "Disallow: /" in docker_out("exec", web2, "cat", "/opt/lab/web/robots.txt")
        assert "port = 5433" in docker_out("exec", db1, "cat", "/opt/lab/db/db.conf")
        assert docker_out("exec", db1, "ls", "/opt/lab").split() == [
            "db",
            "identite.txt",
        ]  # pas de répertoire web sur la machine de base
        # le runner dédié a bien pris les exécutions, et le worker intégré aucune
        assert Runner.objects.get(name=seed.RUNNER).agent_version == agent_mod.VERSION
    finally:
        if agent is not None:
            agent.stop.set()
        labmod.down(lab)
