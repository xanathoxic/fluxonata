"""Runtime « ansible » (ADR 0037) : validation des entrées, exécution réelle dans le conteneur, mode vérification, échecs, masquage des secrets,
runner isolé, et un essai SSH complet contre un serveur jetable (réseau Docker dédié, clé et clés d'hôte synthétiques).

Les essais d'exécution demandent Docker et l'image `fluxonata-runner-ansible:local` (scripts/build-ansible-runner.sh) ; sans eux ils sont ignorés
avec cette raison, jamais comptés comme réussis."""

import base64
import importlib.util
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from runs import runners
from runs.runners import RUNTIMES, DockerRunner, build_docker_command

HARNESS = Path(__file__).resolve().parents[2] / "workers" / "ansible" / "harness.py"
spec = importlib.util.spec_from_file_location("ansible_harness", HARNESS)
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)

LOCAL = {"all": {"hosts": {"machine": {"ansible_connection": "local"}}}}


def _image_ready() -> bool:
    from django.conf import settings

    if not shutil.which("docker"):
        return False
    return (
        subprocess.run(["docker", "image", "inspect", settings.ORCH_ANSIBLE_IMAGE], capture_output=True).returncode == 0
    )


needs_ansible_image = pytest.mark.skipif(
    not _image_ready(), reason="docker ou image Ansible indisponible (scripts/build-ansible-runner.sh)"
)


def version(source: str, network_policy: str = "none", memory_mb: int = 128):
    return SimpleNamespace(
        runtime="ansible",
        source=source,
        entrypoint="run",
        network_policy=network_policy,
        limits={"memory_mb": memory_mb},
    )


def play(inputs: dict, source: str, env: dict | None = None, **kw):
    return DockerRunner().run(version(source, **kw), inputs, 120, env=env or {})


# --- validation des entrées (sans Docker) ------------------------------------------------------------------------------------------


def test_inputs_get_safe_defaults():
    cfg = harness.check_inputs({"inventory": LOCAL})
    assert cfg["check"] is False and cfg["forks"] == 5 and cfg["timeout"] == 30
    assert cfg["host_key_checking"] is True  # vérification des clés d'hôte par défaut
    assert cfg["fail_on_error"] is True


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {},  # pas d'inventaire
        {"inventory": {}},
        {"inventory": "all"},
        {"inventory": LOCAL, "inventroy": {}},  # faute de frappe : refusée, pas ignorée
        {"inventory": LOCAL, "limit": "web; rm -rf /"},
        {"inventory": LOCAL, "limit": 'a"b'},
        {"inventory": LOCAL, "limit": "a" * 201},
        {"inventory": LOCAL, "tags": ["ok", "a b"]},
        {"inventory": LOCAL, "tags": ["t"] * 21},
        {"inventory": LOCAL, "forks": 0},
        {"inventory": LOCAL, "forks": 11},
        {"inventory": LOCAL, "forks": True},
        {"inventory": LOCAL, "timeout": 4},
        {"inventory": LOCAL, "check": "oui"},
        {"inventory": LOCAL, "user": "root; id"},
        {"inventory": LOCAL, "become_user": "-x"},
        {"inventory": LOCAL, "extra_vars": []},
        {"inventory": LOCAL, "known_hosts": "x" * (64 * 1024 + 1)},
        {"inventory": {"all": {"vars": {"x": "y" * (256 * 1024)}}}},
    ],
)
def test_bad_inputs_are_refused(bad):
    with pytest.raises(harness.InvalidInput):
        harness.check_inputs(bad)


def test_limits_accept_normal_values():
    cfg = harness.check_inputs(
        {
            "inventory": LOCAL,
            "limit": "web*:!db01",
            "tags": ["deploy", "v1.2"],
            "forks": 10,
            "timeout": 120,
            "user": "deploy",
            "become": True,
            "become_user": "root",
        }
    )
    assert cfg["limit"] == "web*:!db01" and cfg["forks"] == 10 and cfg["become"] is True


def test_secrets_are_masked_and_the_key_is_recognised_line_by_line(monkeypatch):
    pem = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAAB3NzaC1yc2EAAAADAQABAAABAQ\n-----END OPENSSH PRIVATE KEY-----\n"  # scan:allow - faux secret de test
    monkeypatch.setenv("FLX_SSH_KEY_B64", base64.b64encode(pem.encode()).decode())
    monkeypatch.setenv("FLX_SSH_PASSWORD", "mot-de-passe-secret")
    secrets = harness.secret_values()
    out = harness.redact("pw=mot-de-passe-secret line=AAAAB3NzaC1yc2EAAAADAQABAAABAQ", secrets)
    assert "mot-de-passe-secret" not in out and "AAAAB3NzaC1yc2EAAAADAQABAAABAQ" not in out
    assert out.count("***") == 2


def test_no_secret_means_nothing_to_mask(monkeypatch):
    for k in harness.SECRET_ENV:
        monkeypatch.delenv(k, raising=False)
    assert harness.secret_values() == []
    assert harness.redact("texte", []) == "texte"


def test_bad_key_encoding_is_reported(monkeypatch, tmp_path):
    monkeypatch.setenv("FLX_SSH_KEY_B64", "pas du base64 !!")
    cfg = harness.check_inputs({"inventory": LOCAL})
    with pytest.raises(harness.InvalidInput):
        harness.prepare(cfg, str(tmp_path))


def test_private_files_are_not_world_readable(monkeypatch, tmp_path):
    monkeypatch.setenv("FLX_SSH_PASSWORD", "x-secret-x")
    monkeypatch.setattr(harness.shutil, "which", lambda _: "/usr/bin/ansible-playbook")
    cfg = harness.check_inputs({"inventory": LOCAL, "known_hosts": "h ssh-ed25519 AAAA"})
    cmd, env, _ = harness.prepare(cfg, str(tmp_path))
    for name in ("vars.json", "inventory.json", "known_hosts"):
        assert (tmp_path / name).stat().st_mode & 0o077 == 0
    assert (
        "x-secret-x" not in " ".join(cmd) and "FLX_SSH_PASSWORD" not in env
    )  # ni sur la ligne de commande, ni dans l'environnement d'Ansible
    assert "--check" not in cmd


def test_ansible_variables_of_the_host_cannot_reach_the_child(monkeypatch, tmp_path):
    monkeypatch.setenv("ANSIBLE_CONFIG", "/etc/evil.cfg")
    monkeypatch.setattr(harness.shutil, "which", lambda _: "/usr/bin/ansible-playbook")
    _, env, _ = harness.prepare(harness.check_inputs({"inventory": LOCAL}), str(tmp_path))
    assert "ANSIBLE_CONFIG" not in env


def test_command_line_follows_the_options(monkeypatch, tmp_path):
    monkeypatch.setattr(harness.shutil, "which", lambda _: "/usr/bin/ansible-playbook")
    cfg = harness.check_inputs(
        {
            "inventory": LOCAL,
            "check": True,
            "diff": True,
            "limit": "machine",
            "tags": ["a", "b"],
            "skip_tags": ["c"],
            "user": "deploy",
            "become": True,
        }
    )
    cmd, _, _ = harness.prepare(cfg, str(tmp_path))
    assert "--check" in cmd and "--diff" in cmd and "--become" in cmd
    assert cmd[cmd.index("--limit") + 1] == "machine"
    assert cmd[cmd.index("--tags") + 1] == "a,b" and cmd[cmd.index("--skip-tags") + 1] == "c"
    assert cmd[cmd.index("--user") + 1] == "deploy"


def test_host_key_checking_off_is_explicit(monkeypatch, tmp_path):
    monkeypatch.setattr(harness.shutil, "which", lambda _: "/usr/bin/ansible-playbook")
    _, env, variables = harness.prepare(
        harness.check_inputs({"inventory": LOCAL, "host_key_checking": False}), str(tmp_path)
    )
    assert (
        env["ANSIBLE_HOST_KEY_CHECKING"] == "False"
        and "StrictHostKeyChecking=no" in variables["ansible_ssh_common_args"]
    )
    strict = tmp_path / "strict"
    strict.mkdir()
    _, env, variables = harness.prepare(harness.check_inputs({"inventory": LOCAL}), str(strict))
    assert (
        env["ANSIBLE_HOST_KEY_CHECKING"] == "True"
        and "StrictHostKeyChecking=yes" in variables["ansible_ssh_common_args"]
    )


# --- le bac à sable --------------------------------------------------------------------------------------------------------------------


def test_the_runtime_is_registered_with_larger_limits():
    assert RUNTIMES["ansible"]["file"] == "playbook.yml" and RUNTIMES["ansible"]["memory_floor_mb"] == 256
    assert "targets" in runners.NETWORK_POLICIES


def test_docker_command_gives_ansible_a_bigger_tmpfs_and_keeps_the_isolation():
    cmd = build_docker_command("n", "img", "none", 256, Path("/w"), None, RUNTIMES["ansible"])
    assert "/tmp:rw,noexec,nosuid,size=64m" in cmd
    for flag in ("--read-only", "--cap-drop", "no-new-privileges"):
        assert flag in cmd
    assert cmd[cmd.index("--user") + 1] == "65534:65534"
    default = build_docker_command("n", "img", "none", 128, Path("/w"), None, RUNTIMES["python"])
    assert "/tmp:rw,noexec,nosuid,size=16m" in default  # les autres runtimes ne changent pas


def test_prepare_copies_the_callback_plugin_readable_only(tmp_path):
    work = runners._prepare(version("- hosts: all\n"), {"inventory": LOCAL})
    try:
        plugin = work / "callback_plugins" / "fluxonata_callback.py"
        assert plugin.is_file() and not plugin.stat().st_mode & 0o222 & 0o022
        assert (work / "playbook.yml").read_text() == "- hosts: all\n" and (work / "harness.py").is_file()
        assert (
            work / "callback_plugins"
        ).stat().st_mode & 0o555 == 0o555  # le dossier reste traversable par l'utilisateur du conteneur
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --- exécution réelle dans le conteneur ------------------------------------------------------------------------------------------------

BASIC = """
- hosts: all
  gather_facts: false
  tasks:
    - name: dire bonjour
      ansible.builtin.debug:
        msg: "bonjour {{ nom }}"
    - name: publier une valeur
      ansible.builtin.set_stats:
        data:
          total: 3
"""


@needs_ansible_image
def test_playbook_runs_in_the_container_and_returns_a_structured_result():
    out = play({"inventory": LOCAL, "extra_vars": {"nom": "monde"}}, BASIC)
    assert out.ok, (out.error, out.logs)
    r = out.result
    assert r["ok"] is True and r["check"] is False and r["hosts"] == 1
    assert r["stats"]["machine"]["ok"] == 2 and r["stats"]["machine"]["failures"] == 0
    assert [t["task"] for t in r["tasks"]] == ["dire bonjour", "publier une valeur"]
    assert r["tasks"][0]["msg"] == "bonjour monde"
    assert r["data"] == {"total": 3}
    assert "PLAY RECAP" in out.logs  # la sortie d'Ansible va dans les journaux


@needs_ansible_image
def test_check_mode_reports_what_would_change_without_changing_it():
    src = """
- hosts: all
  gather_facts: false
  tasks:
    - name: créer un fichier
      ansible.builtin.file:
        path: /tmp/essai-flx
        state: touch
    - name: le fichier existe-t-il ?
      ansible.builtin.stat:
        path: /tmp/essai-flx
      register: s
    - name: le dire
      ansible.builtin.debug:
        msg: "existe={{ s.stat.exists }}"
"""
    dry = play({"inventory": LOCAL, "check": True}, src)
    assert dry.ok, (dry.error, dry.logs)
    assert dry.result["check"] is True
    tasks = {t["task"]: t for t in dry.result["tasks"]}
    assert tasks["créer un fichier"]["changed"] is True  # « changerait »
    assert tasks["le dire"]["msg"] == "existe=False"  # …mais rien n'a été créé
    real = play({"inventory": LOCAL}, src)
    assert real.ok and {t["task"]: t for t in real.result["tasks"]}["le dire"]["msg"] == "existe=True"


FAILING = """
- hosts: all
  gather_facts: false
  tasks:
    - name: échouer exprès
      ansible.builtin.fail:
        msg: boom
"""


@needs_ansible_image
def test_a_failing_playbook_fails_the_step_by_default():
    out = play({"inventory": LOCAL}, FAILING)
    assert not out.ok and out.error["code"] == "ANSIBLE_FAILED"
    assert (
        "1 échec" in out.error["message"]
        and "échouer exprès" in out.error["message"]
        and "boom" in out.error["message"]
    )


@needs_ansible_image
def test_fail_on_error_false_returns_the_full_result_instead():
    out = play({"inventory": LOCAL, "fail_on_error": False}, FAILING)
    assert out.ok and out.result["ok"] is False
    assert out.result["stats"]["machine"]["failures"] == 1
    assert out.result["tasks"][0]["status"] == "failed"


@needs_ansible_image
def test_ignored_errors_are_flagged_and_do_not_fail_the_run():
    src = FAILING.replace("      ansible.builtin.fail:", "      ignore_errors: true\n      ansible.builtin.fail:")
    out = play({"inventory": LOCAL}, src)
    assert out.ok and out.result["ok"] is True and out.result["tasks"][0]["ignored"] is True


@needs_ansible_image
def test_invalid_inputs_never_start_ansible():
    out = play({"inventory": LOCAL, "limit": "a b"}, BASIC)
    assert not out.ok and out.error["code"] == "INVALID_INPUT"
    assert "PLAY" not in out.logs


@needs_ansible_image
def test_limit_selects_hosts_and_tags_select_tasks():
    inv = {"all": {"hosts": {"un": {"ansible_connection": "local"}, "deux": {"ansible_connection": "local"}}}}
    src = """
- hosts: all
  gather_facts: false
  tasks:
    - name: a
      ansible.builtin.debug: {msg: a}
      tags: [a]
    - name: b
      ansible.builtin.debug: {msg: b}
      tags: [b]
"""
    out = play({"inventory": inv, "limit": "deux", "tags": ["b"]}, src)
    assert out.ok, (out.error, out.logs)
    assert list(out.result["stats"]) == ["deux"]
    assert [t["task"] for t in out.result["tasks"]] == ["b"]


@needs_ansible_image
def test_secrets_never_appear_in_logs_or_result_and_not_even_in_the_environment():
    src = """
- hosts: all
  gather_facts: false
  tasks:
    - name: tenter de les afficher
      ansible.builtin.debug:
        msg: "mdp={{ ansible_password | default('aucun') }} env={{ lookup('env', 'FLX_SSH_PASSWORD') | default('vide', true) }} bec={{ ansible_become_password | default('aucun') }}"
    - name: masquée
      no_log: true
      ansible.builtin.debug:
        msg: "{{ ansible_password | default('') }}"
"""
    secret, become = "Sup3r-Secret-Value", "Become-Secret-Value"
    out = play({"inventory": LOCAL}, src, env={"FLX_SSH_PASSWORD": secret, "FLX_BECOME_PASSWORD": become})
    assert out.ok, (out.error, out.logs)
    blob = json.dumps(out.result) + out.logs
    assert secret not in blob and become not in blob
    assert (
        "mdp=***" in out.result["tasks"][0]["msg"] and "env=vide" in out.result["tasks"][0]["msg"]
    )  # masqué, et absent de l'environnement d'Ansible
    assert out.result["tasks"][1]["msg"] == "(masqué : no_log)"


@needs_ansible_image
def test_the_container_has_no_network_by_default():
    src = """
- hosts: all
  gather_facts: false
  tasks:
    - name: joindre l'extérieur
      ansible.builtin.command: python3 -c "import socket; socket.create_connection(('1.1.1.1', 53), 3)"
      register: r
      failed_when: false
    - name: conclure
      ansible.builtin.debug:
        msg: "rc={{ r.rc }}"
"""
    out = play({"inventory": LOCAL}, src)
    assert out.ok, (out.error, out.logs)
    assert out.result["tasks"][-1]["msg"] != "rc=0"  # injoignable : politique « none »


@needs_ansible_image
def test_the_root_filesystem_is_read_only_and_the_user_is_not_root():
    src = """
- hosts: all
  gather_facts: false
  tasks:
    - name: écrire hors du tmpfs
      ansible.builtin.shell: "id -u; touch /etc/flx-test"
      register: r
      failed_when: false
    - name: conclure
      ansible.builtin.debug:
        msg: "{{ r.stdout_lines[0] }}|{{ r.rc }}"
"""
    out = play({"inventory": LOCAL}, src)
    assert out.ok, (out.error, out.logs)
    assert out.result["tasks"][-1]["msg"].startswith("65534|") and not out.result["tasks"][-1]["msg"].endswith("|0")


@needs_ansible_image
def test_memory_floor_applies_even_when_the_action_asks_for_less(monkeypatch):
    seen = {}
    real = runners.build_docker_command

    def spy(name, image, network, memory, *a, **kw):
        seen["memory"] = memory
        return real(name, image, network, memory, *a, **kw)

    monkeypatch.setattr(runners, "build_docker_command", spy)
    out = play({"inventory": LOCAL, "extra_vars": {"nom": "x"}}, BASIC, memory_mb=64)
    assert out.ok and seen["memory"] == 256


# --- SSH réel contre un serveur jetable --------------------------------------------------------------------------------------------------

SSHD_DOCKERFILE = """FROM alpine:latest
RUN apk add --no-cache openssh python3 && ssh-keygen -A && adduser -D deploy && passwd -u deploy && mkdir -p /home/deploy/.ssh
COPY authorized_keys /home/deploy/.ssh/authorized_keys
RUN chown -R deploy:deploy /home/deploy/.ssh && chmod 700 /home/deploy/.ssh && chmod 600 /home/deploy/.ssh/authorized_keys
CMD ["/usr/sbin/sshd", "-D", "-e"]
"""


def _docker(*args, check=True, **kw):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, **kw)


@pytest.fixture
def ssh_target(tmp_path, settings):
    if not (shutil.which("ssh-keygen") and _image_ready()):
        pytest.skip("ssh-keygen, docker ou image Ansible indisponible")
    key = tmp_path / "id"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "authorized_keys").write_text((tmp_path / "id.pub").read_text())
    (ctx / "Dockerfile").write_text(SSHD_DOCKERFILE)
    tag = f"flx-test-sshd-{uuid.uuid4().hex[:6]}"
    built = _docker("build", "-q", "-t", tag, str(ctx), check=False)
    if built.returncode != 0:
        pytest.skip(f"image de test SSH non constructible : {built.stderr[-200:]}")
    net, name = f"flx-test-net-{uuid.uuid4().hex[:6]}", f"flx-test-host-{uuid.uuid4().hex[:6]}"
    _docker("network", "create", net)
    _docker("run", "-d", "--rm", "--name", name, "--network", net, tag)
    try:
        host_key = ""
        for _ in range(40):  # le serveur démarre en quelques secondes
            r = _docker("exec", name, "cat", "/etc/ssh/ssh_host_ed25519_key.pub", check=False)
            if r.returncode == 0 and r.stdout.strip():
                host_key = " ".join(r.stdout.split()[:2])
                break
            subprocess.run(["sleep", "0.5"])
        assert host_key, "clé d'hôte du serveur de test introuvable"
        settings.ORCH_ANSIBLE_NETWORK = net
        yield SimpleNamespace(
            name=name,
            known_hosts=f"{name} {host_key}\n",
            key_b64=base64.b64encode(key.read_bytes()).decode(),
            inventory={"all": {"hosts": {name: {"ansible_python_interpreter": "/usr/bin/python3"}}}},
        )
    finally:
        _docker("rm", "-f", name, check=False)
        _docker("network", "rm", net, check=False)
        _docker("rmi", "-f", tag, check=False)


SSH_PLAY = """
- hosts: all
  gather_facts: false
  tasks:
    - name: qui suis-je ?
      ansible.builtin.command: id -un
      register: who
      changed_when: false
    - name: le dire
      ansible.builtin.debug:
        msg: "{{ who.stdout }}"
"""


@needs_ansible_image
def test_ssh_with_a_key_and_strict_host_key_checking(ssh_target):
    out = play(
        {"inventory": ssh_target.inventory, "user": "deploy", "known_hosts": ssh_target.known_hosts},
        SSH_PLAY,
        env={"FLX_SSH_KEY_B64": ssh_target.key_b64},
        network_policy="targets",
    )
    assert out.ok, (out.error, out.logs)
    assert out.result["tasks"][-1]["msg"] == "deploy"
    assert out.result["stats"][ssh_target.name]["unreachable"] == 0
    assert ssh_target.key_b64 not in out.logs and "PRIVATE KEY" not in out.logs


@needs_ansible_image
def test_ssh_is_refused_when_the_host_key_is_not_the_expected_one(ssh_target):
    wrong = f"{ssh_target.name} ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UoOqmNHXFVhYVx4Fr3NpYtnX8s1h\n"
    out = play(
        {"inventory": ssh_target.inventory, "user": "deploy", "known_hosts": wrong},
        SSH_PLAY,
        env={"FLX_SSH_KEY_B64": ssh_target.key_b64},
        network_policy="targets",
    )
    assert not out.ok and out.error["code"] == "ANSIBLE_FAILED" and "injoignable" in out.error["message"]


@needs_ansible_image
def test_ssh_without_known_hosts_is_refused_by_default_and_allowed_when_explicit(ssh_target):
    env = {"FLX_SSH_KEY_B64": ssh_target.key_b64}
    strict = play({"inventory": ssh_target.inventory, "user": "deploy"}, SSH_PLAY, env=env, network_policy="targets")
    assert not strict.ok and strict.error["code"] == "ANSIBLE_FAILED"
    lax = play(
        {"inventory": ssh_target.inventory, "user": "deploy", "host_key_checking": False},
        SSH_PLAY,
        env=env,
        network_policy="targets",
    )
    assert lax.ok, (lax.error, lax.logs)


@needs_ansible_image
def test_ssh_is_impossible_without_the_targets_network(ssh_target):
    out = play(
        {"inventory": ssh_target.inventory, "user": "deploy", "host_key_checking": False, "timeout": 5},
        SSH_PLAY,
        env={"FLX_SSH_KEY_B64": ssh_target.key_b64},
        network_policy="none",
    )
    assert not out.ok and out.error["code"] == "ANSIBLE_FAILED"


# --- par le catalogue et le worker, comme n'importe quelle action -------------------------------------------------------------------------


@needs_ansible_image
def test_an_ansible_action_runs_through_the_catalog_and_the_worker(world, settings):
    from catalog.models import Action, ActionVersion
    from runs import services
    from runs.worker import process_one
    from tenancy.models import Environment
    from tests.wf_helpers import schema

    settings.ORCH_WORKER_TAGS = ["ansible"]
    ws = world["ws_a"]
    action = Action.objects.create(workspace=ws, slug=f"ansible-{uuid.uuid4().hex[:6]}", name="Playbook")
    v = ActionVersion.objects.create(
        action=action,
        version="1.0.0",
        runtime="ansible",
        source=BASIC,
        input_schema=schema(required=["inventory"], inventory={"type": "object"}, extra_vars={"type": "object"}),
        output_schema={"type": "object"},
        limits={"timeout_seconds": 120},
        side_effects="external",
        idempotent=True,
        required_tags=["ansible"],
    )
    ex, _ = services.create_execution(
        workspace=ws,
        environment=Environment.objects.get(workspace=ws, kind="test"),
        action_version=v,
        user=world["designer"],
        inputs={"inventory": LOCAL, "extra_vars": {"nom": "plateforme"}},
    )
    process_one("w", DockerRunner())
    ex.refresh_from_db()
    assert ex.status == "succeeded", ex.error
    assert ex.result["stats"]["machine"]["ok"] == 2 and ex.result["data"] == {"total": 3}


def test_the_catalog_accepts_the_ansible_runtime_and_the_targets_network():
    from catalog.library import VersionInput

    data = {
        "bump": "minor",
        "runtime": "ansible",
        "source": BASIC,
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "network_policy": "targets",
        "required_tags": ["ansible"],
    }
    ser = VersionInput(data=data)
    assert ser.is_valid(), ser.errors
    assert not VersionInput(data={**data, "runtime": "puppet"}).is_valid()
    assert not VersionInput(data={**data, "network_policy": "anywhere"}).is_valid()


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"required_tags": []}, "required_tags"),  # un playbook ne part pas sur n'importe quel runner
        ({"required_tags": [""]}, "required_tags"),
        ({"required_tags": "ansible"}, "required_tags"),
        ({"source": "- hosts: [oups"}, "source"),  # YAML invalide
        ({"source": "juste du texte"}, "source"),
        ({"source": "[]"}, "source"),
        ({"source": "- tasks: []"}, "source"),  # un jeu sans hôtes
    ],
)
def test_an_ansible_action_needs_a_runner_tag_and_a_readable_playbook(changes, field):
    from catalog.library import VersionInput

    data = {
        "bump": "minor",
        "runtime": "ansible",
        "source": BASIC,
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "required_tags": ["ansible"],
    }
    assert VersionInput(data=data).is_valid()
    ser = VersionInput(data={**data, **changes})
    assert not ser.is_valid() and field in ser.errors


def test_other_runtimes_do_not_need_a_tag():
    from catalog.library import VersionInput

    ser = VersionInput(
        data={
            "runtime": "python",
            "source": "def run(i):\n    return {}\n",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        }
    )
    assert ser.is_valid(), ser.errors


def _queued_ansible(world, tags):
    from catalog.models import Action, ActionVersion
    from runs import services
    from tenancy.models import Environment
    from tests.wf_helpers import schema

    ws = world["ws_a"]
    action = Action.objects.create(workspace=ws, slug=f"ansible-{uuid.uuid4().hex[:6]}", name="Playbook")
    v = ActionVersion.objects.create(
        action=action,
        version="1.0.0",
        runtime="ansible",
        source=BASIC,
        input_schema=schema(),
        output_schema={"type": "object"},
        required_tags=tags,
    )
    ex, _ = services.create_execution(
        workspace=ws,
        environment=Environment.objects.get(workspace=ws, kind="test"),
        action_version=v,
        user=world["designer"],
        inputs={},
    )
    return ex


def test_a_worker_without_the_tag_never_claims_a_playbook(world, settings):
    from runs import services

    settings.ORCH_WORKER_TAGS = []
    _queued_ansible(world, ["ansible"])
    assert services.claim_next("w") is None


def test_a_playbook_without_any_tag_is_claimed_by_nobody_not_even_a_tagless_worker(world, settings):
    from runs import services

    settings.ORCH_WORKER_TAGS = []
    _queued_ansible(world, [])  # ne peut venir que d'un import ancien ou d'une écriture directe
    assert services.claim_next("w") is None
    settings.ORCH_WORKER_TAGS = ["ansible"]
    assert services.claim_next("w") is None


def test_a_worker_with_the_tag_claims_it(world, settings):
    from runs import services

    settings.ORCH_WORKER_TAGS = ["ansible", "site-paris"]
    ex = _queued_ansible(world, ["ansible"])
    claimed = services.claim_next("w")
    assert claimed is not None and claimed[0].id == ex.id
