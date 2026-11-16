"""SSH step et assistants (MCP de conception, assistant IA, essai à blanc).

Politique : un assistant LIT une vue expurgée ; il peut créer ou modifier une étape SSH SEULEMENT en mode test, avec des connexions SSH
existantes de CET espace (revérifiées par le serveur à chaque écriture et à chaque essai), sans secret ni champ inconnu ; il ne touche pas
une étape en mode réel ; l'essai à blanc simule toujours (aucun réseau, aucun nom résolu, aucun secret chargé). Valeurs sentinelles synthétiques."""

import json

import pytest

from ai import dryrun, service
from audit.models import AuditEvent
from tests.conftest import client_for
from tests.ssh_helpers import make_secret
from tests.test_mcp_design_write import call, error_text
from tests.wf_helpers import ACTIONS, make_action, node, schema
from vault.models import Connection
from workflows.models import Workflow

SENSITIVE_TEXT = "jeton-confidentiel-5150"
SENTINEL_HOST = "hote-sentinelle.interne.test"
SENTINEL_USER = "utilisateur-sentinelle"
SENTINEL_SECRET = "secret-sentinelle-9137"
FP = "SHA256:" + "A" * 43


def make_ssh_conn(ws, name, *, kind="ssh", host=SENTINEL_HOST, secret_name=None):
    secret = make_secret(ws, secret_name or f"s-{name}", SENTINEL_SECRET)
    return Connection.objects.create(
        workspace=ws,
        name=name,
        label=f"Libellé de {name}",
        kind=kind,
        secret=secret,
        config={"host": host, "port": "22", "username": SENTINEL_USER, "host_key_sha256": FP},
    )


def ssh_step(nid="s", **fields):
    base = {
        "id": nid,
        "type": "ssh",
        "name": "Étape SSH",
        "connection": "equip",
        "session": "exec",
        "commands": ["show version"],
    }
    return {**base, **fields}


def graph_of(*nodes, edges=()):
    return {"inputs": schema(), "nodes": list(nodes), "edges": list(edges), "outputs": {}}


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    make_ssh_conn(world["ws_a"], "equip")
    make_ssh_conn(world["ws_a"], "bastion", host="bastion-sentinelle.interne.test")
    make_ssh_conn(world["ws_b"], "etrangere", secret_name="s-etr")
    make_ssh_conn(world["ws_a"], "depot-sftp", kind="sftp", secret_name="s-sftp")
    return world["ws_a"]


def stored(ws, slug="w"):
    return Workflow.objects.get(workspace=ws, slug=slug)


def create(c, graph, slug="w"):
    return call(c, "create_workflow", {"slug": slug, "name": "W", "graph": graph})


def update(c, graph, revision, slug="w"):
    return call(c, "update_workflow", {"slug": slug, "revision": revision, "graph": graph})


def assert_no_leak(*texts):
    blob = " ".join(str(t) for t in texts)
    for sentinel in (SENTINEL_HOST, SENTINEL_USER, SENTINEL_SECRET, SENSITIVE_TEXT, FP):
        assert sentinel not in blob, sentinel


# ---- list_connections keeps its public contract and shows no parameter
def test_list_connections_keeps_name_label_kind_and_adds_only_whether_an_ssh_host_is_required(ws, world):
    _, out = call(client_for(world["designer"]), "list_connections", {})
    rows = {r["name"]: r for r in out["results"]}
    assert (
        set(rows["equip"]) == {"name", "label", "kind", "host_required"}
        and rows["equip"]["host_required"] is False
        and "etrangere" not in rows
    )
    assert_no_leak(out)


# ---- creation and modification: test mode only, authorized connections only
def test_an_assistant_creates_an_ssh_step_in_test_mode_with_explicit_connections(ws, world):
    c = client_for(world["designer"])
    res, out = create(c, graph_of(ssh_step(jumps=["bastion"], mocks=[{"id": "c1", "stdout": "ok", "exit_status": 0}])))
    assert out["status"] == "brouillon" and [i for i in out["issues"] if i["level"] == "error"] == []
    s = next(n for n in stored(ws).draft_graph["nodes"] if n["id"] == "s")
    assert s["mode"] == "test" and s["jumps"] == ["bastion"]  # the mode is made explicit, never left to a default
    assert AuditEvent.objects.filter(action="mcp.design.write", data__tool="create_workflow").exists()


@pytest.mark.parametrize("where", ["create", "update"])
def test_an_explicit_real_mode_is_refused_clearly_and_nothing_is_stored(ws, world, where):
    c = client_for(world["designer"])
    if where == "create":
        res, _ = create(c, graph_of(ssh_step(mode="reel")))
        assert not Workflow.objects.filter(slug="w").exists()
    else:
        call(c, "create_workflow", {"slug": "w", "name": "W"})
        res, _ = update(c, graph_of(ssh_step(mode="reel")), 1)
        assert stored(ws).draft_revision == 1
    assert "mode « test »" in error_text(res) and "réel" in error_text(res)


def test_switching_a_stored_test_step_to_real_mode_is_refused_too(ws, world):
    c = client_for(world["designer"])
    create(c, graph_of(ssh_step(mode="test")))
    _, read = call(c, "get_workflow", {"slug": "w"})
    g = read["draft_graph"]
    g["nodes"][0]["mode"] = "reel"
    res, _ = update(c, g, read["draft_revision"])
    assert "mode « test »" in error_text(res) and stored(ws).draft_graph["nodes"][0]["mode"] == "test"


@pytest.mark.parametrize(
    "fields, why",
    [
        ({"connection": "etrangere"}, "autre espace"),
        ({"connection": "n-existe-pas"}, "inexistante"),
        ({"connection": "depot-sftp"}, "autre type"),
        ({"jumps": ["etrangere"]}, "rebond d'un autre espace"),
        ({"jumps": ["depot-sftp"]}, "rebond d'un autre type"),
        ({"connection": "{{ inputs.c }}"}, "connexion dynamique"),
    ],
)
def test_every_connection_is_checked_by_the_server_with_one_answer_that_reveals_nothing(ws, world, fields, why):
    c = client_for(world["designer"])
    res, _ = create(c, graph_of(ssh_step(**fields)))
    text = error_text(res)
    assert "type SSH" in text and "n'existe pas" in text, why
    assert_no_leak(text)
    assert not Workflow.objects.filter(slug="w").exists()
    messages = {
        error_text(create(c, graph_of(ssh_step(**{"connection": k})), slug=f"x{i}")[0])
        for i, k in enumerate(("etrangere", "n-existe-pas", "depot-sftp"))
    }
    assert len(messages) == 1  # missing, foreign and wrongly typed are indistinguishable


def test_a_connection_that_disappeared_after_discovery_is_refused_at_write_time(ws, world):
    c = client_for(world["designer"])
    _, found = call(c, "list_connections", {})
    assert "equip" in {r["name"] for r in found["results"]}  # discovered...
    Connection.objects.filter(workspace=ws, name="equip").delete()  # ...then gone: discovery proves nothing
    res, _ = create(c, graph_of(ssh_step()))
    assert "n'existe pas" in error_text(res) and not Workflow.objects.filter(slug="w").exists()


def test_insufficient_rights_and_other_workspaces_are_refused_before_anything(ws, world):
    res, _ = call(
        client_for(world["viewer"]), "create_workflow", {"slug": "v", "name": "V", "graph": graph_of(ssh_step())}
    )
    assert "Droit de modifier" in error_text(res)
    resp = client_for(world["outsider"]).post(
        "/api/v1/workspaces/team-a/mcp/design/",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "create_workflow",
                "arguments": {"slug": "o", "name": "O", "graph": graph_of(ssh_step())},
            },
        },
        format="json",
    )
    assert resp.status_code in (403, 404) and not Workflow.objects.filter(slug__in=["v", "o"]).exists()


@pytest.mark.parametrize(
    "fields",
    [
        {"secrets": {"pw": "s-equip"}},
        {"session": "shell", "commands": None, "steps": [{"send_secret": "pw", "expect": ["x"]}]},
        {"inconnu": 1},
        {"commands": [{"run": "x", "shell": "bash"}]},
        {
            "session": "shell",
            "steps": [{"send": "x", "expect": ["y"], "responses": [{"expect": "a", "send": "b", "extra": 1}]}],
        },
        {"pty": {"enabled": True, "autre": 1}},
        {"mocks": [{"id": "c1", "stdout": "x", "cache": True}]},
        {"commands": [{"run": "<masqué>"}]},
    ],
)
def test_secret_references_unknown_nested_fields_and_masked_values_are_refused(ws, world, fields):
    step = {k: v for k, v in ssh_step(**fields).items() if v is not None}
    res, _ = create(client_for(world["designer"]), graph_of(step))
    text = error_text(res)
    assert "SSH" in text and not Workflow.objects.filter(slug="w").exists()
    assert_no_leak(text)


# ---- what an assistant reads, and what it cannot touch
def test_an_assistant_reads_the_step_without_the_text_of_sensitive_items(ws, world):
    Workflow.objects.create(
        workspace=ws,
        owner=world["designer"],
        slug="w",
        name="W",
        draft_revision=2,
        draft_graph=graph_of(
            ssh_step(
                mode="test",
                commands=[{"id": "t", "run": f"echo {SENSITIVE_TEXT}", "sensitive": True}, "uptime"],
                mocks=[{"id": "t", "stdout": SENSITIVE_TEXT}, {"id": "c2", "stdout": "ok"}],
            )
        ),
    )
    _, out = call(client_for(world["designer"]), "get_workflow", {"slug": "w"})
    s = out["draft_graph"]["nodes"][0]
    assert (
        s["commands"][0]["run"] == "<masqué>"
        and s["commands"][1] == "uptime"
        and [m["id"] for m in s["mocks"]] == ["c2"]
    )
    assert_no_leak(out)
    assert SENSITIVE_TEXT in repr(stored(ws).draft_graph)  # the stored step is untouched


def test_sending_back_what_was_read_keeps_the_real_step_even_when_it_is_in_real_mode(ws, world):
    Workflow.objects.create(
        workspace=ws,
        owner=world["designer"],
        slug="w",
        name="W",
        draft_revision=2,
        draft_graph=graph_of(
            node("e", action="echo", inputs={"value": "x"}),
            ssh_step(mode="reel", commands=[{"id": "t", "run": f"echo {SENSITIVE_TEXT}", "sensitive": True}]),
        ),
    )
    c = client_for(world["designer"])
    _, read = call(c, "get_workflow", {"slug": "w"})
    g = read["draft_graph"]
    g["nodes"][0]["inputs"]["value"] = "modifie"  # an ordinary edit next to the person's real SSH step
    _, out = update(c, g, read["draft_revision"])
    assert out["status"] == "brouillon"
    ssh_stored = next(n for n in stored(ws).draft_graph["nodes"] if n["id"] == "s")
    assert ssh_stored["mode"] == "reel" and ssh_stored["commands"][0]["run"] == f"echo {SENSITIVE_TEXT}"
    # ...but it cannot modify that real step, nor remove it
    g["nodes"][1]["commands"].append("rm -rf /tmp/x")
    res, _ = update(c, g, stored(ws).draft_revision)
    assert "mode réel" in error_text(res)
    g["nodes"] = [n for n in g["nodes"] if n["id"] != "s"]
    res, _ = update(c, g, stored(ws).draft_revision)
    assert "retirée" in error_text(res) and any(n["id"] == "s" for n in stored(ws).draft_graph["nodes"])


def test_the_ai_assistant_sees_an_ssh_step_only_as_a_locked_placeholder_and_cannot_touch_it(db):
    graph = graph_of(
        node("e", action="echo", inputs={"value": "x"}),
        ssh_step(commands=[{"id": "t", "run": f"echo {SENSITIVE_TEXT}", "sensitive": True}]),
    )
    compact = service._compact_graph(graph)
    assert next(n for n in compact["nodes"] if n["id"] == "s") == {
        "id": "s",
        "type": "ssh",
        "name": "Étape SSH",
        "locked": True,
    }
    assert SENSITIVE_TEXT not in repr(compact) and "ssh" not in service.EDIT_NODE_TYPES
    raw = {
        "nodes": [
            {"id": "e", "type": "action", "action": "echo", "inputs": {"value": "x"}},
            {"id": "s", "type": "ssh", "commands": ["rm -rf /"]},
        ],
        "edges": [],
    }
    kept = service.sanitize_edit(raw, graph, None, "w")[0]["nodes"]
    assert next(n for n in kept if n["id"] == "s")["commands"][0]["run"] == f"echo {SENSITIVE_TEXT}"
    raw["nodes"].append({"id": "n2", "type": "ssh", "commands": ["id"]})
    with pytest.raises(service.AiRefused):
        service.sanitize_edit(raw, graph, None, "w")


# ---- dry run: authorization re-checked, strictly simulated, partial results reach the error branch
def draft_with_error_branch(ws, world, **ssh_fields):
    nodes = [
        ssh_step(
            mode="test",
            mocks=[{"id": "c1", "stdout": "demo\n", "exit_status": 0}, {"id": "c2", "stderr": "non", "exit_status": 2}],
            commands=["show version", "show status"],
            stop_on_error=False,
            **ssh_fields,
        ),
        node(
            "h",
            "transform",
            op="set",
            fields={"echec": "{{ steps.s.output.failed_step }}", "premier": "{{ steps.s.output.results[0].stdout }}"},
        ),
    ]
    graph = graph_of(*nodes, edges=[{"from": "s", "to": "h", "label": "error"}])
    return Workflow.objects.create(
        workspace=ws, owner=world["designer"], slug="w", name="W", draft_revision=2, draft_graph=graph
    )


def test_start_dry_run_rechecks_the_connections_and_queues_nothing_when_one_is_gone(ws, world):
    draft_with_error_branch(ws, world)
    c = client_for(world["designer"])
    Connection.objects.filter(workspace=ws, name="equip").delete()
    res, _ = call(c, "start_dry_run", {"slug": "w"})
    assert "type « ssh »" in error_text(res) or "SSH" in error_text(res)
    assert_no_leak(error_text(res))
    from ai.models import AiJob

    assert not AiJob.objects.filter(kind="dryrun").exists()


def test_the_dry_run_is_strictly_simulated_and_gives_the_error_branch_the_partial_results(ws, world, monkeypatch):
    import socket

    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from core import netsec
    from runs.runners import SubprocessRunner
    from vault.models import Secret

    wf = draft_with_error_branch(ws, world)

    def boom(*a, **k):
        raise AssertionError("réseau, DNS ou secret touché pendant un essai à blanc")

    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(netsec, "resolve", boom)
    monkeypatch.setattr(Secret, "reveal", boom)
    monkeypatch.setattr(Secret, "reveal_fields", boom)
    with CaptureQueriesContext(connection) as q:
        result = dryrun.dry_run(wf.draft_graph, ws, SubprocessRunner(), [{"label": "nominal", "inputs": {}}])
    assert not [x for x in q.captured_queries if "vault_secret" in x["sql"]]  # no secret row is even loaded
    text = json.dumps(result, ensure_ascii=False, default=str)
    assert "Commandes SSH simulées" in text and '"simulated": true' in text.lower().replace("true", "true")
    steps = {s["id"]: s for sc in result["scenarios"] for s in sc["steps"]}
    assert steps["s"]["state"] == "simulated" and steps["h"]["state"] in ("ok", "simulated")  # the error branch ran...
    assert_no_leak(text)
    handler_out = next(
        s for sc in result["scenarios"] for s in sc["steps"] if s["id"] == "h" and s["state"] != "skipped"
    )
    assert "c2" in text and handler_out  # ...after the partial results of the failing command were produced


def test_a_forced_failure_scenario_exercises_the_error_branch_even_with_no_failing_mock(ws, world):
    from runs.runners import SubprocessRunner

    nodes = [
        ssh_step(mode="test", mocks=[{"id": "c1", "stdout": "ok", "exit_status": 0}]),
        node("h", "transform", op="set", fields={"t": "{{ steps.s.output.error_type }}"}),
    ]
    graph = graph_of(*nodes, edges=[{"from": "s", "to": "h", "label": "error"}])
    result = dryrun.dry_run(graph, ws, SubprocessRunner(), [{"label": "nominal", "inputs": {}}])
    labels = [sc["label"] for sc in result["scenarios"]]
    assert any("en échec (simulé)" in lab for lab in labels)
    failed = next(sc for sc in result["scenarios"] if "en échec (simulé)" in sc["label"])
    assert any(s["id"] == "h" and s["state"] != "skipped" for s in failed["steps"])


def test_audit_and_responses_of_a_refused_write_carry_no_connection_parameter(ws, world):
    c = client_for(world["designer"])
    res, _ = create(c, graph_of(ssh_step(connection="etrangere")))
    events = AuditEvent.objects.filter(action="mcp.design.write")
    assert_no_leak(error_text(res), [e.data for e in events])


# ---- the whole MCP path: discover, write, validate, dry-run (worker), read the result
def test_the_mcp_path_end_to_end_stays_simulated_and_reaches_the_error_branch(ws, world, monkeypatch):
    import socket
    from types import SimpleNamespace

    from ai.models import AiJob, AiPolicy
    from core import netsec
    from tests.test_mcp_dryrun import run_worker_once
    from vault.models import Secret

    AiPolicy.objects.update_or_create(workspace=ws, defaults={"mode": "local_strict", "dry_run": True})
    user = world["designer"]
    user._token = SimpleNamespace(scopes=["view", "edit"], workspace_id=ws.id, workspace=ws, environments=[])
    try:
        c = client_for(user)
        _, found = call(c, "list_connections", {"q": "ssh"})
        name = next(r["name"] for r in found["results"] if r["kind"] == "ssh" and r["name"] == "equip")
        steps = [
            {"id": "invite", "expect": ["[#>$] ?$"]},
            {"id": "privilege", "send": "enable", "expect": ["[#>$] ?$"]},
            {"id": "etat", "send": "show status", "expect": ["[#>$] ?$"], "failure": ["(?i)error"]},
        ]
        mocks = [
            {"id": "invite", "stdout": "Bienvenue\n> "},
            {"id": "privilege", "stdout": "enable\n# "},
            {"id": "etat", "stdout": "ERROR simulée\n# ", "failure": True},
        ]
        graph = graph_of(
            ssh_step(connection=name, session="shell", commands=None, steps=steps, mocks=mocks, mode="test"),
            node("h", "transform", op="set", fields={"echec": "{{ steps.s.output.failed_step }}"}),
            edges=[{"from": "s", "to": "h", "label": "error"}],
        )
        graph["nodes"][0].pop("commands")
        _, out = create(c, graph)
        assert [i for i in out["issues"] if i["level"] == "error"] == []
        _, started = call(c, "start_dry_run", {"slug": "w", "samples": [{"label": "démo", "inputs": {}}]})
        job = AiJob.objects.get(pk=started["job_id"])
        assert job.status == "queued"

        def boom(*a, **k):
            raise AssertionError("réseau, DNS ou secret touché")

        monkeypatch.setattr(socket, "create_connection", boom)
        monkeypatch.setattr(netsec, "resolve", boom)
        monkeypatch.setattr(Secret, "reveal", boom)
        done = run_worker_once()
        assert done.status == "succeeded" and "graph" not in done.payload
        _, res = call(c, "get_dry_run", {"job_id": started["job_id"]})
        scenario = res["result"]["scenarios"][0]
        by_id = {s["id"]: s for s in scenario["steps"]}
        assert (
            by_id["s"]["state"] == "simulated"
            and str(by_id["s"]["output"]["simulated"]).lower() == "true"  # (the MCP shows values as text)
            and by_id["s"]["output"]["failed_step"] == "etat"
        )
        assert by_id["h"]["state"] != "skipped"  # the error branch received the partial results
        assert_no_leak(res)
    finally:
        del user._token


# ---- the host chosen in the step, through the MCP path
def make_identity(ws, name="cle-id"):
    return Connection.objects.create(
        workspace=ws,
        name=name,
        label=f"Libellé de {name}",
        kind="ssh",
        secret=make_secret(ws, f"s-{name}", SENTINEL_SECRET),
        config={"username": SENTINEL_USER},
    )


def test_list_connections_tells_an_assistant_which_ssh_connection_needs_a_host(ws, world):
    make_identity(world["ws_a"])
    _, out = call(client_for(world["designer"]), "list_connections", {"q": "ssh"})
    rows = {r["name"]: r for r in out["results"]}
    assert rows["cle-id"]["host_required"] is True and rows["equip"]["host_required"] is False
    assert_no_leak(out)  # only that fact: no host, no user, no secret


def test_an_assistant_writes_a_step_with_a_host_chosen_in_the_step_in_test_mode(ws, world):
    make_identity(world["ws_a"])
    step_ = ssh_step(
        connection="cle-id", host="srv.exemple.test", port=22, host_key_sha256=FP, mode="test",
        jumps=[{"connection": "cle-id", "host": "bastion.exemple.test", "host_key_sha256": FP}],
        mocks=[{"id": "c1", "stdout": "ok", "exit_status": 0}],
    )  # fmt: skip
    res, out = create(client_for(world["designer"]), graph_of(step_))
    assert out["status"] == "brouillon" and [i for i in out["issues"] if i["level"] == "error"] == []
    s = next(n for n in stored(ws).draft_graph["nodes"] if n["id"] == "s")
    assert s["mode"] == "test" and s["host"] == "srv.exemple.test" and s["jumps"][0]["host"] == "bastion.exemple.test"


@pytest.mark.parametrize(
    "fields",
    [
        {"jumps": [{"connection": "bastion", "secret": "x"}]},
        {"jumps": [{"connection": "etrangere", "host": "h.example", "host_key_sha256": FP}]},
        {"jumps": [{"connection": 3}]},
    ],
)
def test_jumps_given_as_objects_follow_the_same_mcp_rules(ws, world, fields):
    make_identity(world["ws_a"])
    res, _ = create(client_for(world["designer"]), graph_of(ssh_step(**fields)))
    text = error_text(res)
    assert "SSH" in text and not Workflow.objects.filter(slug="w").exists()
    assert_no_leak(text)


def test_the_validator_reports_a_missing_host_to_the_assistant_so_it_can_correct(ws, world):
    make_identity(world["ws_a"])
    _, out = create(client_for(world["designer"]), graph_of(ssh_step(connection="cle-id", mode="test")))
    messages = " ".join(i["message"] for i in out["issues"] if i["level"] == "error")
    assert "n'a pas d'hôte" in messages and "empreinte" in messages  # actionable, and no parameter of any connection


def test_a_dry_run_of_a_step_with_a_host_chosen_in_the_step_is_simulated(ws, world, monkeypatch):
    import socket

    from core import netsec
    from runs.runners import SubprocessRunner
    from vault.models import Secret

    make_identity(world["ws_a"])

    def boom(*a, **k):
        raise AssertionError("réseau, DNS ou secret touché")

    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(netsec, "resolve", boom)
    monkeypatch.setattr(Secret, "reveal", boom)
    g = graph_of(
        ssh_step(
            connection="cle-id",
            host="srv.exemple.test",
            host_key_sha256=FP,
            mode="test",
            mocks=[{"id": "c1", "stdout": "ok"}],
        )
    )
    result = dryrun.dry_run(g, ws, SubprocessRunner(), [{"label": "nominal", "inputs": {}}])
    text = json.dumps(result, ensure_ascii=False, default=str)
    assert result["ran"] is True and "Commandes SSH simulées" in text and "srv.exemple.test" in text
    assert SENTINEL_SECRET not in text
