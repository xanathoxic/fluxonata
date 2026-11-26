"""Rôles Ansible de la bibliothèque (ADR 0037, lot 6) : validation des fichiers, API (droits, isolation, audit, immuabilité), versions figées dans les actions,
fichiers remis au runner (worker et agent), et un playbook qui appelle de vrais rôles dans le bac à sable."""

import uuid
from pathlib import Path

import pytest
from rest_framework import serializers

from audit.models import AuditEvent
from catalog import ansible_roles as lib
from catalog.models import Action, ActionVersion, AnsibleRole, AnsibleRoleVersion
from runs import runners, services
from runs.inventory_inputs import InventoryError, effective_support_files
from runs.runners import DockerRunner
from tenancy.models import Environment
from tests.conftest import client_for
from tests.test_ansible_runtime import LOCAL, needs_ansible_image
from tests.wf_helpers import schema

BASE = "/api/v1/workspaces/team-a"
FAKE_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----"  # scan:allow - faux secret de test

WEB = {
    "tasks/main.yml": "- name: Ecrire la page\n  ansible.builtin.copy:\n    dest: /tmp/page-{{ role_name }}.txt\n    content: \"{{ web_message }}\\n\"\n    mode: '0600'\n  notify: signaler\n",
    "handlers/main.yml": "- name: signaler\n  ansible.builtin.debug:\n    msg: page écrite\n",
    "defaults/main.yml": "web_message: par défaut\n",
    "templates/page.j2": "bonjour {{ web_message }}\n",
    "meta/main.yml": "dependencies: []\n",
}


def body(name="web", **over):
    return {"name": name, "description": "Serveur web", "files": WEB, **over}


def create(c, name="web", **over):
    return c.post(f"{BASE}/ansible-roles/", body(name, **over), format="json")


# --- fichiers ----------------------------------------------------------------------------------------------------------------------------


def refused(files, text=None):
    with pytest.raises(serializers.ValidationError) as e:
        lib.check_files(files)
    if text:
        assert text in str(e.value.detail)


def test_a_valid_role_is_accepted_unchanged():
    assert lib.check_files(WEB) == WEB


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/etc/passwd",
        "../x.yml",
        "tasks/../../x.yml",
        "tasks//main.yml",
        "tasks\\main.yml",
        "tasks/.cache.yml",
        "tasks/ma in.yml",
        ".git/config",
        "a/" * 8 + "b.yml",
        "x" * 201,
        None,
        3,
    ],
)
def test_dangerous_or_odd_paths_are_refused(path):
    with pytest.raises(serializers.ValidationError):
        lib.check_path(path)


@pytest.mark.parametrize(
    ("files", "text"),
    [
        ([], None),
        ({}, None),
        ("tasks/main.yml", None),
        ({"handlers/main.yml": "[]"}, "tasks/main.yml"),  # point d'entrée absent
        ({"tasks/main.yml": "[]", "library/mod.py": "print(1)"}, "dossiers"),  # pas de code Python embarqué
        ({"tasks/main.yml": "[]", "plugins/x.py": "x"}, "dossiers"),
        ({"tasks/main.yml": "[]", "main.yml": "[]"}, "dossiers"),  # à la racine
        ({"tasks/main.yml": "- a: [oups"}, "YAML invalide"),
        ({"tasks/main.yml": "[]", "defaults/main.txt": "x"}, "YAML"),
        ({"tasks/main.yml": 12}, "texte"),
        ({"tasks/main.yml": "a\x00b"}, "texte"),
        ({"tasks/main.yml": f"- debug: {{msg: '{FAKE_KEY}'}}"}, "secret"),
        ({"tasks/main.yml": "[]", "files/cle.pem": FAKE_KEY}, "secret"),
        ({"tasks/main.yml": "[]", "files/gros.txt": "x" * (lib.MAX_FILE_BYTES + 1)}, "Ko"),
        ({"tasks/main.yml": "[]", **{f"files/f{i}.txt": "x" for i in range(lib.MAX_FILES)}}, "fichiers"),
    ],
)
def test_bad_roles_are_refused_with_the_reason(files, text):
    refused(files, text)


def test_the_total_size_is_bounded():
    big = {"tasks/main.yml": "[]", **{f"files/f{i}.txt": "x" * (lib.MAX_FILE_BYTES - 10) for i in range(5)}}
    refused(big, "Ko au plus")


def test_digest_depends_on_content_not_on_order():
    a = {"tasks/main.yml": "[]", "defaults/main.yml": "a: 1\n"}
    assert lib.files_digest(a) == lib.files_digest(dict(reversed(list(a.items()))))
    assert lib.files_digest(a) != lib.files_digest({**a, "defaults/main.yml": "a: 2\n"})


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        ("dependencies: [common, base]\n", ["common", "base"]),
        ("dependencies:\n  - role: common\n    vars: {x: 1}\n  - name: base\n", ["common", "base"]),
        ("galaxy_info: {author: x}\n", []),
        ("dependencies: []\n", []),
        ("- pas un objet\n", []),
        ("a: [oups", []),
        (None, []),
    ],
)
def test_dependencies_are_read_from_meta(meta, expected):
    files = {"tasks/main.yml": "[]", **({"meta/main.yml": meta} if meta is not None else {})}
    assert lib.dependencies(files) == expected


# --- API ----------------------------------------------------------------------------------------------------------------------------------


def test_create_list_get_version_and_files_with_audit(world):
    c = client_for(world["designer"])
    r = create(c)
    assert r.status_code == 201 and r.json()["latest_version"] == "1.0.0" and r.json()["versions"][0]["files"] == 5
    assert c.get(f"{BASE}/ansible-roles/").json()[0]["name"] == "web"
    v = c.get(f"{BASE}/ansible-roles/web/versions/1.0.0/").json()
    assert v["files"] == WEB and v["digest"].startswith("sha256:")
    new = c.post(
        f"{BASE}/ansible-roles/web/versions/",
        {
            "version": "1.1.0",
            "changelog": "message par défaut",
            "files": {**WEB, "defaults/main.yml": "web_message: v2\n"},
        },
        format="json",
    )
    assert new.status_code == 201 and new.json()["latest_version"] == "1.1.0" and len(new.json()["versions"]) == 2
    assert (
        c.get(f"{BASE}/ansible-roles/web/versions/1.0.0/").json()["files"]["defaults/main.yml"]
        == "web_message: par défaut\n"
    )  # l'ancienne reste telle quelle
    events = [e.action for e in AuditEvent.objects.filter(action__startswith="ansible_role.").order_by("id")]
    assert events == ["ansible_role.create", "ansible_role.version"]


def test_a_version_never_changes_and_numbers_only_go_up(world):
    c = client_for(world["designer"])
    create(c)
    for version in ("1.0.0", "0.9.0"):
        r = c.post(f"{BASE}/ansible-roles/web/versions/", {"version": version, "files": WEB}, format="json")
        assert r.status_code == 400 and "supérieure" in str(r.json())
    v = AnsibleRoleVersion.objects.get(role__name="web")
    with pytest.raises(PermissionError):
        v.files = {"tasks/main.yml": "[]"}
        v.save()
    with pytest.raises(PermissionError):
        v.delete()


def test_bad_requests_store_nothing(world):
    c = client_for(world["designer"])
    assert create(c, name="Web").status_code == 400  # majuscule
    assert create(c, name="web-1").status_code == 400  # tiret
    assert create(c, name="1web").status_code == 400
    assert create(c, files={"tasks/main.yml": "- a: [oups"}).status_code == 400
    assert create(c, version="1.0").status_code == 400
    assert AnsibleRole.objects.count() == 0  # pas de rôle à moitié créé
    assert create(c).status_code == 201 and create(c).status_code == 400  # doublon


def test_a_viewer_reads_but_cannot_publish_and_an_outsider_sees_nothing(world):
    create(client_for(world["designer"]))
    v = client_for(world["viewer"])
    assert (
        v.get(f"{BASE}/ansible-roles/").status_code == 200
        and v.get(f"{BASE}/ansible-roles/web/versions/1.0.0/").status_code == 200
    )
    assert create(v, name="autre").status_code == 403
    assert (
        v.post(f"{BASE}/ansible-roles/web/versions/", {"version": "1.1.0", "files": WEB}, format="json").status_code
        == 403
    )
    assert v.patch(f"{BASE}/ansible-roles/web/", {"description": "x"}, format="json").status_code == 403
    assert v.delete(f"{BASE}/ansible-roles/web/").status_code == 403
    o = client_for(world["outsider"])
    assert o.get(f"{BASE}/ansible-roles/").status_code == 404 and o.get(f"{BASE}/ansible-roles/web/").status_code == 404
    assert o.get("/api/v1/workspaces/team-b/ansible-roles/web/").status_code == 404  # même nom, autre espace
    assert AnsibleRole.objects.count() == 1


def test_describe_and_delete_a_role_that_nothing_uses(world):
    c = client_for(world["designer"])
    create(c)
    assert (
        c.patch(f"{BASE}/ansible-roles/web/", {"description": "Nouveau texte"}, format="json").json()["description"]
        == "Nouveau texte"
    )
    assert c.delete(f"{BASE}/ansible-roles/web/").status_code == 204
    assert c.get(f"{BASE}/ansible-roles/web/").status_code == 404 and not AnsibleRoleVersion.objects.exists()


# --- déclarées par une action ---------------------------------------------------------------------------------------------------------------------------


def make_role(ws, name, files=None, version="1.0.0"):
    role = AnsibleRole.objects.create(workspace=ws, name=name)
    AnsibleRoleVersion.objects.create(
        role=role, version=version, files=files or {"tasks/main.yml": "- ansible.builtin.debug: {msg: ok}\n"}
    )
    return role


def action_body(slug="deploy", **over):
    version = {
        "runtime": "ansible",
        "source": "- hosts: all\n  gather_facts: false\n  roles: [web]\n",
        "input_schema": {
            "type": "object",
            "required": ["inventory"],
            "properties": {
                "inventory": {"type": "object", "title": "Inventaire", "description": "L'inventaire Ansible à cibler."}
            },
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "ok": {"type": "boolean", "title": "Réussi", "description": "Vrai si aucune machine n'est en échec."}
            },
        },
        "required_tags": ["ansible"],
        **over,
    }
    return {
        "slug": slug,
        "name": "Déployer",
        "description": "Déploie la page web sur les machines choisies.",
        "version": version,
    }


def test_an_action_pins_the_exact_role_versions_at_publication(world):
    ws = world["ws_a"]
    make_role(ws, "web", version="1.0.0")
    AnsibleRoleVersion.objects.create(
        role=AnsibleRole.objects.get(name="web"), version="1.2.0", files={"tasks/main.yml": "[]"}
    )
    c = client_for(world["designer"])
    r = c.post(
        f"{BASE}/library/actions/", action_body(ansible_roles=[{"role": "web", "version": "latest"}]), format="json"
    )
    assert r.status_code == 201, r.json()
    v = ActionVersion.objects.get(action__slug="deploy")
    assert v.ansible_roles == [{"role": "web", "version": "1.2.0"}]  # « latest » est devenu une version exacte
    make_role(ws, "autre")  # une publication ultérieure du rôle ne change pas l'action déjà publiée
    AnsibleRoleVersion.objects.create(
        role=AnsibleRole.objects.get(name="web"), version="2.0.0", files={"tasks/main.yml": "[]"}
    )
    assert ActionVersion.objects.get(pk=v.pk).ansible_roles == [{"role": "web", "version": "1.2.0"}]


@pytest.mark.parametrize(
    ("roles", "text"),
    [
        ([{"role": "inconnu", "version": "1.0.0"}], "introuvable"),
        ([{"role": "web", "version": "9.9.9"}], "pas de version"),
        ([{"role": "web", "version": "1.0.0"}, {"role": "web", "version": "1.0.0"}], "deux fois"),
        ([{"role": "Web-1"}], "écrit {"),
        ("web", "liste"),
        ([{"role": f"r{i}"} for i in range(lib.MAX_ROLES_PER_ACTION + 1)], "au plus"),
    ],
)
def test_bad_role_declarations_are_refused_and_nothing_is_created(world, roles, text):
    make_role(world["ws_a"], "web")
    r = client_for(world["designer"]).post(f"{BASE}/library/actions/", action_body(ansible_roles=roles), format="json")
    assert r.status_code == 400 and text in str(r.json())
    assert not Action.objects.filter(slug="deploy").exists()


def test_a_missing_dependency_must_be_declared_too(world):
    ws = world["ws_a"]
    make_role(ws, "common")
    make_role(ws, "web", {"tasks/main.yml": "[]", "meta/main.yml": "dependencies: [common]\n"})
    c = client_for(world["designer"])
    refused_ = c.post(
        f"{BASE}/library/actions/", action_body(ansible_roles=[{"role": "web", "version": "1.0.0"}]), format="json"
    )
    assert refused_.status_code == 400 and "dépend de « common »" in str(refused_.json())
    ok = c.post(
        f"{BASE}/library/actions/",
        action_body(ansible_roles=[{"role": "web", "version": "1.0.0"}, {"role": "common", "version": "1.0.0"}]),
        format="json",
    )
    assert ok.status_code == 201


def test_only_ansible_actions_declare_roles(world):
    make_role(world["ws_a"], "web")
    r = client_for(world["designer"]).post(
        f"{BASE}/library/actions/",
        action_body(
            runtime="python",
            source="def run(i):\n    return {}\n",
            required_tags=[],
            ansible_roles=[{"role": "web", "version": "1.0.0"}],
        ),
        format="json",
    )
    assert r.status_code == 400 and "ansible_roles" in str(r.json())


def test_a_role_used_by_an_action_cannot_be_deleted(world):
    make_role(world["ws_a"], "web")
    c = client_for(world["designer"])
    assert (
        c.post(
            f"{BASE}/library/actions/", action_body(ansible_roles=[{"role": "web", "version": "1.0.0"}]), format="json"
        ).status_code
        == 201
    )
    r = c.delete(f"{BASE}/ansible-roles/web/")
    assert r.status_code == 400 and "1 version(s) d'action" in str(r.json())
    assert AnsibleRole.objects.filter(name="web").exists() and c.get(f"{BASE}/ansible-roles/").json()[0]["used_by"] == 1


def test_a_role_of_another_workspace_is_never_found(world):
    make_role(world["ws_b"], "secret_b")
    r = client_for(world["designer"]).post(
        f"{BASE}/library/actions/", action_body(ansible_roles=[{"role": "secret_b", "version": "1.0.0"}]), format="json"
    )
    assert r.status_code == 400 and "introuvable" in str(r.json())


# --- fichiers remis au runner -------------------------------------------------------------------------------------------------------------


def playbook_version(ws, roles, tags=("ansible",), source="- hosts: all\n  gather_facts: false\n  roles: [web]\n"):
    a = Action.objects.create(workspace=ws, slug=f"pb-{uuid.uuid4().hex[:6]}", name="Playbook")
    return ActionVersion.objects.create(
        action=a,
        version="1.0.0",
        runtime="ansible",
        source=source,
        input_schema=schema(),
        output_schema={"type": "object"},
        required_tags=list(tags),
        ansible_roles=roles,
    )


def execution(world, version, inputs=None):
    ws = world["ws_a"]
    ex, _ = services.create_execution(
        workspace=ws,
        environment=Environment.objects.get(workspace=ws, kind="test"),
        action_version=version,
        user=world["designer"],
        inputs=inputs or {},
    )
    return ex


def test_support_files_are_the_pinned_roles_under_roles_slash_name(world):
    ws = world["ws_a"]
    make_role(ws, "web", WEB)
    make_role(ws, "common")
    v = playbook_version(ws, [{"role": "web", "version": "1.0.0"}, {"role": "common", "version": "1.0.0"}])
    files = lib.support_files(v)
    assert (
        files["roles/web/tasks/main.yml"] == WEB["tasks/main.yml"]
        and files["roles/web/templates/page.j2"] == WEB["templates/page.j2"]
    )
    assert "roles/common/tasks/main.yml" in files and len(files) == len(WEB) + 1


def test_no_roles_no_support_files_and_other_runtimes_never_get_any(world):
    ws = world["ws_a"]
    assert lib.support_files(playbook_version(ws, [])) == {}
    ex = execution(world, playbook_version(ws, []))
    assert effective_support_files(ex) == {}


def test_missing_role_files_become_a_clear_execution_error(world):
    v = playbook_version(
        world["ws_a"], [{"role": "web", "version": "1.0.0"}]
    )  # écriture directe : le rôle n'existe pas
    with pytest.raises(InventoryError) as e:
        effective_support_files(execution(world, v))
    assert e.value.code == "ANSIBLE_ROLES" and "introuvable" in e.value.message


def test_the_total_size_of_the_roles_of_one_run_is_bounded(world, monkeypatch):
    make_role(world["ws_a"], "web", WEB)
    monkeypatch.setattr(lib, "MAX_SUPPORT_BYTES", 50)
    with pytest.raises(serializers.ValidationError):
        lib.support_files(playbook_version(world["ws_a"], [{"role": "web", "version": "1.0.0"}]))


def test_the_runner_writes_the_roles_and_refuses_any_escape(tmp_path):
    runners._write_support_files(tmp_path, {"roles/web/tasks/main.yml": "- a: 1\n", "roles/web/templates/p.j2": "x"})
    assert (tmp_path / "roles/web/tasks/main.yml").read_text() == "- a: 1\n" and (
        tmp_path / "roles/web/templates/p.j2"
    ).is_file()
    for bad in (
        "roles/../evil.yml",
        "../evil.yml",
        "/tmp/evil.yml",
        "autre/x.yml",
        "roles/../../evil",
        "roles/web/../../x",
    ):
        with pytest.raises(ValueError):
            runners._write_support_files(tmp_path, {bad: "x"})
    assert not (tmp_path.parent / "evil.yml").exists()


def test_prepare_gives_the_container_user_read_access_to_the_roles():
    v = type(
        "V",
        (),
        {
            "runtime": "ansible",
            "source": "- hosts: all\n",
            "entrypoint": "run",
            "support_files": {"roles/web/tasks/main.yml": "[]"},
        },
    )()
    work = runners._prepare(v, {"inventory": LOCAL})
    try:
        f = work / "roles" / "web" / "tasks" / "main.yml"
        assert (
            f.is_file() and f.stat().st_mode & 0o444 == 0o444 and not f.stat().st_mode & 0o022
        )  # lisible par tous, modifiable par le seul propriétaire
        assert (work / "roles" / "web").stat().st_mode & 0o555 == 0o555
    finally:
        import shutil

        shutil.rmtree(work, ignore_errors=True)


def test_ansible_finds_the_roles_next_to_the_playbook(monkeypatch, tmp_path):
    import importlib.util

    p = Path(__file__).resolve().parents[2] / "workers" / "ansible" / "harness.py"
    spec = importlib.util.spec_from_file_location("ansible_harness_roles", p)
    h = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(h)
    monkeypatch.setattr(h.shutil, "which", lambda _: "/usr/bin/ansible-playbook")
    _, env, _ = h.prepare(h.check_inputs({"inventory": LOCAL}), str(tmp_path))
    assert env["ANSIBLE_ROLES_PATH"].endswith("/roles") and "ANSIBLE_ROLES_PATH" in env


# --- agents : un agent ancien ne reçoit pas un playbook qui a des rôles ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("agent", "gets_it"),
    [("0.1.0", False), ("", False), ("0.1.9", False), ("0.2.0", True), ("0.10.0", True), ("1.0.0", True)],
)
def test_only_agents_that_can_write_support_files_receive_role_jobs(world, agent, gets_it):
    from runs.models import Runner
    from runs.runner_auth import new_token

    _, digest, prefix = new_token()
    runner = Runner.objects.create(
        name="site",
        scope="shared",
        runtimes=["ansible"],
        tags=["ansible"],
        token_hash=digest,
        token_prefix=prefix,
        agent_version=agent,
    )
    make_role(world["ws_a"], "web")
    ex = execution(world, playbook_version(world["ws_a"], [{"role": "web", "version": "1.0.0"}]))
    claimed = services.claim_next("runner:site", runner)
    assert (claimed is not None and claimed[0].id == ex.id) is gets_it


def test_an_old_agent_still_gets_playbooks_without_roles(world):
    from runs.models import Runner
    from runs.runner_auth import new_token

    _, digest, prefix = new_token()
    runner = Runner.objects.create(
        name="vieux",
        scope="shared",
        runtimes=["ansible"],
        tags=["ansible"],
        token_hash=digest,
        token_prefix=prefix,
        agent_version="0.1.0",
    )
    ex = execution(world, playbook_version(world["ws_a"], []))
    claimed = services.claim_next("runner:vieux", runner)
    assert claimed is not None and claimed[0].id == ex.id


# --- un vrai playbook qui appelle de vrais rôles ----------------------------------------------------------------------------------------------

COMMON = {
    "tasks/main.yml": "- name: Poser le répertoire commun\n  ansible.builtin.file:\n    path: /tmp/flx-commun\n    state: directory\n    mode: '0700'\n",
}
WEB_REAL = {
    "meta/main.yml": "dependencies:\n  - role: common\n",
    "defaults/main.yml": "web_message: par défaut\n",
    "templates/page.j2": "page: {{ web_message }}\n",
    "tasks/main.yml": (
        "- name: Générer la page depuis le modèle\n"
        "  ansible.builtin.template:\n    src: page.j2\n    dest: /tmp/flx-commun/page.txt\n    mode: '0600'\n"
        "  notify: annoncer\n"
        "- name: Lire la page\n  ansible.builtin.slurp:\n    src: /tmp/flx-commun/page.txt\n  register: page\n"
        '- name: Publier le contenu\n  ansible.builtin.set_stats:\n    data:\n      page: "{{ page.content | b64decode | trim }}"\n'
    ),
    "handlers/main.yml": "- name: annoncer\n  ansible.builtin.debug:\n    msg: handler du rôle exécuté\n",
}
ROLES_PLAYBOOK = '- hosts: all\n  gather_facts: false\n  roles:\n    - role: web\n      vars:\n        web_message: "{{ message }}"\n'
INCLUDE_PLAYBOOK = "- hosts: all\n  gather_facts: false\n  tasks:\n    - ansible.builtin.include_role:\n        name: web\n      vars:\n        web_message: via include_role\n"


@needs_ansible_image
@pytest.mark.parametrize(
    ("source", "expected"), [(ROLES_PLAYBOOK, "page: depuis le rôle"), (INCLUDE_PLAYBOOK, "page: via include_role")]
)
def test_a_playbook_really_calls_the_roles_with_their_dependency_and_handlers(world, settings, source, expected):
    from runs.worker import process_one

    settings.ORCH_WORKER_TAGS = ["ansible"]
    ws = world["ws_a"]
    make_role(ws, "common", COMMON)
    make_role(ws, "web", WEB_REAL)
    v = playbook_version(
        ws, [{"role": "web", "version": "1.0.0"}, {"role": "common", "version": "1.0.0"}], source=source
    )
    ex = execution(world, v, {"inventory": LOCAL, "extra_vars": {"message": "depuis le rôle"}})
    process_one("w", DockerRunner())
    ex.refresh_from_db()
    assert ex.status == "succeeded", ex.error
    assert ex.result["data"] == {"page": expected}
    tasks = [t["task"] for t in ex.result["tasks"]]
    assert (
        "common : Poser le répertoire commun" in tasks or "Poser le répertoire commun" in tasks
    )  # la dépendance a été jouée avant
    assert any("handler du rôle" in t["msg"] for t in ex.result["tasks"])  # le handler du rôle a tourné


@needs_ansible_image
def test_a_role_that_is_not_declared_is_not_found(world, settings):
    from runs.worker import process_one

    settings.ORCH_WORKER_TAGS = ["ansible"]
    make_role(world["ws_a"], "web", WEB_REAL)
    v = playbook_version(world["ws_a"], [])  # le playbook appelle « web » sans l'avoir déclaré
    ex = execution(world, v, {"inventory": LOCAL, "extra_vars": {"message": "x"}})
    process_one("w", DockerRunner())
    ex.refresh_from_db()
    assert ex.status == "failed" and ex.error["code"] == "ANSIBLE_FAILED"


def test_an_action_with_external_effects_can_be_created_and_is_not_idempotent_unless_said_so(world):
    """Régression : la création d'une action avec `side_effects: external` (le défaut du formulaire Ansible) plantait quand `idempotent` n'était pas envoyé."""
    make_role(world["ws_a"], "web")
    c = client_for(world["designer"])
    r = c.post(f"{BASE}/library/actions/", action_body("ext", side_effects="external"), format="json")
    assert r.status_code == 201, r.content
    assert Action.objects.get(slug="ext").latest_version().idempotent is False
    said = c.post(
        f"{BASE}/library/actions/", action_body("ext2", side_effects="external", idempotent=True), format="json"
    )
    assert said.status_code == 201 and Action.objects.get(slug="ext2").latest_version().idempotent is True
