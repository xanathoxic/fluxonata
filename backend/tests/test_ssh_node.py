"""SSH step, stage 1: direct connection and `exec` mode (authentication, host keys, results, limits, errors, simulation)."""

import time

import pytest

from runs import ssh_core, ssh_node
from tests.ssh_helpers import (
    KEY_PASSPHRASE,
    PASSWORD,
    FakeSshd,
    drain,
    engine,
    http_node,
    launch,
    make_conn,
    make_secret,
    new_encrypted_pem,
    new_key,
    node,
    publish,
    run_graph,
    run_ssh,
    schema,
    step,
)
from tests.ssh_helpers import (
    ssh_node as ssh,
)
from vault.models import Secret

pytest.importorskip("paramiko")


# ------------------------------------------------------------------ nominal
def test_exec_runs_commands_separately_and_collects_streams_status_and_duration(ssh_ws, world, sshd, target):
    run, sr = run_ssh(ssh_ws, world, commands=["echo bonjour", "err oups", "exit 7", "whoami"], stop_on_error=False)
    out = sr.output
    assert out["connected"] is True and out["simulated"] is False and out["session"] == "exec"
    by = {r["id"]: r for r in out["results"]}
    assert by["c1"]["stdout"] == "bonjour\n" and by["c1"]["exit_status"] == 0 and by["c1"]["status"] == "ok"
    assert by["c2"]["stderr"] == "oups\n" and by["c2"]["stdout"] == ""
    assert (
        by["c3"]["exit_status"] == 7 and by["c3"]["status"] == "failed" and by["c3"]["error"]["type"] == "remote_status"
    )
    assert by["c4"]["stdout"] == "robot\n" and all(r["duration_ms"] >= 0 for r in out["results"])
    assert out["hops"] == [{"connection": "cible", "host_fingerprint": sshd.fingerprint}]
    assert out["status"] == "failed" and out["failed_step"] == "c3" and out["error_type"] == "remote_status"
    assert run.status == "failed" and sr.status == "failed" and sr.error["code"] == "SSH_REMOTE_STATUS"
    assert [c for _, c in sshd.exec_log] == [
        "echo bonjour",
        "err oups",
        "exit 7",
        "whoami",
    ]  # one channel each, in order


def test_stop_on_error_default_stops_and_marks_the_rest_not_run(ssh_ws, world, sshd, target):
    _, sr = run_ssh(ssh_ws, world, commands=["echo a", "fail", "echo jamais"])
    assert [r["status"] for r in sr.output["results"]] == ["ok", "failed", "not_run"]
    assert [c for _, c in sshd.exec_log] == ["echo a", "fail"]  # the third never reached the server
    assert sr.output["failed_step"] == "c2" and sr.output["failed_count"] == 1


def test_ignore_error_keeps_the_step_successful(ssh_ws, world, target):
    run, sr = run_ssh(
        ssh_ws, world, commands=[{"id": "x", "run": "fail", "ignore_error": True}, "echo suite"], stop_on_error=True
    )
    assert run.status == "succeeded" and sr.output["status"] == "succeeded"
    assert sr.output["results"][0]["status"] == "failed" and sr.output["results"][0]["ignored"] is True
    assert sr.output["results"][1]["stdout"] == "suite\n"


def test_expect_exit_lists_the_accepted_codes(ssh_ws, world, target):
    run, sr = run_ssh(ssh_ws, world, commands=[{"id": "x", "run": "exit 3", "expect_exit": [0, 3]}])
    assert run.status == "succeeded" and sr.output["results"][0]["exit_status"] == 3


def test_a_missing_exit_status_is_null_never_invented(ssh_ws, world, target):
    _, sr = run_ssh(ssh_ws, world, commands=["noexit fin"])
    r = sr.output["results"][0]
    assert r["exit_status"] is None and r["stdout"] == "fin\n" and r["status"] == "ok"


def test_commands_come_from_an_expression_over_a_previous_value(ssh_ws, world, target):
    inputs = schema(["cmds"], cmds={"type": "array", "items": {"type": "string"}})
    run = run_graph(
        ssh_ws, world, [ssh(commands="{{ inputs.cmds }}")], inputs=inputs, run_inputs={"cmds": ["echo un", "echo deux"]}
    )
    assert [r["stdout"] for r in step(run).output["results"]] == ["un\n", "deux\n"]


def test_a_text_command_may_use_an_expression_and_an_empty_list_is_refused(ssh_ws, world, target):
    inputs = schema(["who"], who={"type": "string"})
    run = run_graph(ssh_ws, world, [ssh(commands=["echo {{ inputs.who }}"])], inputs=inputs, run_inputs={"who": "moi"})
    assert step(run).output["results"][0]["stdout"] == "moi\n"
    inputs = schema(["cmds"], cmds={"type": "array", "items": {"type": "string"}})
    run = run_graph(
        ssh_ws, world, [ssh(commands="{{ inputs.cmds }}")], inputs=inputs, run_inputs={"cmds": []}, slug="vide"
    )
    assert run.status == "failed" and step(run).error["code"] == "SSH_CONFIG"


def test_a_following_step_and_the_error_branch_read_the_results(ssh_ws, world, target):
    nodes = [
        ssh(commands=["echo valeur"]),
        node("e", action="echo", inputs={"value": "{{ steps.s.output.results[0].stdout }}"}),
    ]
    run = run_graph(ssh_ws, world, nodes, [{"from": "s", "to": "e"}], outputs={"v": "{{ steps.e.output.value }}"})
    assert run.status == "succeeded" and run.outputs == {"v": "valeur\n"}
    nodes = [
        ssh(commands=["echo avant", "fail"]),
        node("h", action="echo", inputs={"value": "{{ steps.s.output.failed_step }}"}),
    ]
    run = run_graph(
        ssh_ws,
        world,
        nodes,
        [{"from": "s", "to": "h", "label": "error"}],
        outputs={"p": "{{ steps.h.output.value }}"},
        slug="err",
    )
    assert run.status == "succeeded" and run.outputs == {"p": "c2"}  # partial results reached the error branch
    assert step(run).output["results"][0]["stdout"] == "avant\n"


# ------------------------------------------------------------------ authentication and host keys
def test_password_authentication_uses_the_declared_secret_only(ssh_ws, world, sshd, target):
    _, sr = run_ssh(ssh_ws, world, commands=["whoami"])
    assert sr.output["results"][0]["stdout"] == "robot\n"
    assert sshd.connections[0]["auth"] == "password" and sshd.connections[0]["attempts"] == 1


def test_private_key_authentication_with_a_passphrase_secret(ssh_ws, world):
    key, pem = new_encrypted_pem(KEY_PASSPHRASE)
    with FakeSshd(users={"robot": {"password": None, "keys": [key]}}) as d:
        make_secret(ssh_ws, "phrase", KEY_PASSPHRASE)
        make_conn(ssh_ws, "cle", d, secret=make_secret(ssh_ws, "cle-privee", pem), passphrase_secret="phrase")
        run, sr = run_ssh(ssh_ws, world, connection="cle", commands=["whoami"])
        assert run.status == "succeeded" and d.connections[0]["auth"] == "publickey"
        text = repr(sr.output)
        assert KEY_PASSPHRASE not in text and "BEGIN OPENSSH" not in text


def test_a_wrong_passphrase_or_unreadable_key_is_an_authentication_error_without_retry(ssh_ws, world):
    key, pem = new_encrypted_pem(KEY_PASSPHRASE)
    with FakeSshd(users={"robot": {"password": None, "keys": [key]}}) as d:
        make_secret(ssh_ws, "mauvaise", "autre-phrase-0000")
        make_conn(ssh_ws, "cle", d, secret=make_secret(ssh_ws, "cle-privee", pem), passphrase_secret="mauvaise")
        _, sr = run_ssh(ssh_ws, world, connection="cle", commands=["whoami"], connect_retries=3)
        assert sr.error["code"] == "SSH_AUTH" and sr.output["connected"] is False and len(d.connections) == 1


def test_wrong_password_is_refused_and_never_retried(ssh_ws, world, sshd):
    make_conn(ssh_ws, "cible", sshd, secret=make_secret(ssh_ws, "mauvais", "mauvais-mot-de-passe"))
    run, sr = run_ssh(ssh_ws, world, commands=["echo x"], connect_retries=3)
    assert run.status == "failed" and sr.error["code"] == "SSH_AUTH"
    assert sr.output["connected"] is False and sr.output["error_type"] == "auth" and sr.output["results"] == []
    assert len(sshd.connections) == 1 and sshd.exec_log == []  # a refused login is tried once


def test_a_server_key_that_differs_from_the_pinned_fingerprint_is_refused_before_any_login(ssh_ws, world, sshd):
    other, _ = new_key()
    make_conn(
        ssh_ws, "cible", sshd, secret=make_secret(ssh_ws, "pw", PASSWORD), fingerprint=ssh_core.fingerprint(other)
    )
    _, sr = run_ssh(ssh_ws, world, commands=["echo x"], connect_retries=2)
    assert sr.error["code"] == "SSH_HOSTKEY" and sshd.exec_log == []
    assert sshd.connections[0]["auth"] is None  # no credential was sent to an unverified server


def test_several_pinned_fingerprints_are_accepted(ssh_ws, world, sshd):
    other, _ = new_key()
    both = f"{ssh_core.fingerprint(other)} {sshd.fingerprint}"
    make_conn(ssh_ws, "cible", sshd, secret=make_secret(ssh_ws, "pw", PASSWORD), fingerprint=both)
    run, _ = run_ssh(ssh_ws, world, commands=["echo x"])
    assert run.status == "succeeded"


def test_a_connection_without_a_pinned_key_is_refused_at_creation_and_at_run_time(ssh_ws, world, sshd):
    from rest_framework.test import APIClient

    c = APIClient()
    c.force_authenticate(world["designer"])
    body = {
        "name": "sans-cle",
        "label": "x",
        "kind": "ssh",
        "secret_value": "pw-pw-pw-pw",
        "config": {"host": "127.0.0.1", "port": "22", "username": "robot"},
    }
    from tenancy.models import Membership, Role

    Membership.objects.filter(user=world["designer"]).update(role=Role.ADMIN)
    r = c.post("/api/v1/workspaces/team-a/connections/", body, format="json")
    assert (
        r.status_code == 400
        and "empreinte" in r.json()["error"]["message"].lower()
        or "empreinte" in str(r.json()).lower()
    )
    body["config"]["host_key_sha256"] = "SHA256:" + "A" * 43
    assert c.post("/api/v1/workspaces/team-a/connections/", body, format="json").status_code == 201
    # a stored connection that lost its fingerprint (edited in the database) cannot connect: nothing is trusted on first use
    make_conn(ssh_ws, "cible", sshd, secret=make_secret(ssh_ws, "pw", PASSWORD), fingerprint="")
    from vault.models import Connection

    Connection.objects.filter(name="cible").update(
        config={"host": "127.0.0.1", "port": str(sshd.port), "username": "robot"}
    )
    _, sr = run_ssh(ssh_ws, world, commands=["echo x"])
    assert sr.error["code"] == "SSH_HOSTKEY" and sshd.exec_log == []


def test_no_agent_no_ssh_dir_no_forwarding(ssh_ws, world, sshd, monkeypatch, tmp_path):
    """Only the declared credential is offered: a key in ~/.ssh or an agent socket in the environment is never used."""
    key, pem = new_key()
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_ed25519").write_text(pem)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SSH_AUTH_SOCK", str(tmp_path / "agent.sock"))
    sshd.users["robot"]["keys"].append(key)  # the server would accept that key — and must not be offered it
    make_conn(ssh_ws, "cible", sshd, secret=make_secret(ssh_ws, "mauvais2", "pas-le-bon-mot-de-passe"))
    _, sr = run_ssh(ssh_ws, world, commands=["echo x"])
    assert sr.error["code"] == "SSH_AUTH" and sshd.connections[0]["auth"] is None


# ------------------------------------------------------------------ limits and errors
def test_command_timeout_marks_the_result_uncertain_and_later_commands_still_run_independently(
    ssh_ws, world, sshd, target
):
    started = time.monotonic()
    _, sr = run_ssh(
        ssh_ws, world, commands=[{"id": "lent", "run": "hang", "timeout_seconds": 1}, "echo apres"], stop_on_error=False
    )
    r = sr.output["results"]
    assert (
        r[0]["status"] == "timeout" and r[0]["timed_out"] and r[0]["uncertain"] and r[0]["error"]["type"] == "timeout"
    )
    assert r[1]["stdout"] == "apres\n" and sr.output["uncertain"] is True
    assert "pu se poursuivre" in sr.error["message"] and time.monotonic() - started < 15


def test_output_limit_truncates_and_is_its_own_error(ssh_ws, world, target):
    _, sr = run_ssh(ssh_ws, world, commands=["big 200000"], max_output_bytes=1000)
    r = sr.output["results"][0]
    assert r["truncated"] and len(r["stdout"]) == 1000 and r["error"]["type"] == "output_limit" and r["uncertain"]
    assert sr.output["truncated"] is True and sr.output["error_type"] == "output_limit"


def test_global_timeout_stops_the_rest_and_says_so(ssh_ws, world, target):
    _, sr = run_ssh(ssh_ws, world, commands=["sleep 3", "echo non"], timeout_seconds=1, command_timeout_seconds=30)
    assert sr.output["results"][0]["status"] == "timeout" and sr.output["results"][1]["status"] == "not_run"
    assert sr.output["uncertain"] is True


def test_dns_failure_and_network_policy_are_distinct_errors(ssh_ws, world, monkeypatch):
    from core import netsec
    from tests.ssh_fake import FakeSshd as _F

    with _F(users={"robot": {"password": PASSWORD, "keys": []}}) as d:
        make_conn(ssh_ws, "ailleurs", d, secret=make_secret(ssh_ws, "pw", PASSWORD), host="introuvable.example")
        monkeypatch.setattr(
            netsec, "resolve", lambda host: (_ for _ in ()).throw(netsec.UnsafeURL(ssh_core.DNS_MESSAGE))
        )
        _, sr = run_ssh(ssh_ws, world, connection="ailleurs", commands=["echo x"])
        assert sr.error["code"] == "SSH_DNS"
        make_conn(ssh_ws, "prive", d, secret=make_secret(ssh_ws, "pw2", PASSWORD), host="10.255.255.1")
        _, sr = run_ssh(ssh_ws, world, connection="prive", commands=["echo x"])
        assert sr.error["code"] == "SSH_BLOCKED"


def test_a_closed_port_is_a_connection_error_and_only_that_is_retried(ssh_ws, world, sshd):
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    make_conn(ssh_ws, "ferme", sshd, secret=make_secret(ssh_ws, "pw", PASSWORD), port=str(free))
    started = time.monotonic()
    _, sr = run_ssh(ssh_ws, world, connection="ferme", commands=["echo x"], connect_retries=1)
    assert sr.error["code"] == "SSH_CONNECT" and time.monotonic() - started >= 1  # one more attempt after a short pause
    assert sshd.exec_log == []


def test_connection_retries_apply_before_the_first_command_only(ssh_ws, world, sshd, target):
    attempts = []
    real = ssh_core._open_once

    def flaky(hops, deadline, cancel_check, connector):
        attempts.append(1)
        if len(attempts) < 3:
            raise ssh_core.SshStepError("SSH_CONNECT", "transitoire")
        return real(hops, deadline, cancel_check, connector)

    spec = {"jumps": [], "connect_retries": 2, "connection": "cible"}
    import unittest.mock as m

    with m.patch.object(ssh_core, "_open_once", flaky):
        session = ssh_core.open_chain(ssh_core.Hops([_hop(sshd)]), spec, time.monotonic() + 30, lambda: False)
    session.close()
    assert len(attempts) == 3
    attempts.clear()
    with m.patch.object(ssh_core, "_open_once", flaky), pytest.raises(ssh_core.SshStepError):
        ssh_core.open_chain(
            ssh_core.Hops([_hop(sshd)]), {**spec, "connect_retries": 1}, time.monotonic() + 30, lambda: False
        )
    assert len(attempts) == 2


def _hop(d):
    return ssh_core.Hop("cible", "127.0.0.1", d.port, "robot", PASSWORD, "", [d.fingerprint], 5, 5)


# ------------------------------------------------------------------ interruption, cancellation
def test_cancel_stops_waiting_and_marks_the_command_uncertain(ssh_ws, world, sshd, target):
    import threading

    from workflows.expressions import resolve

    spec = ssh_node.prepare(ssh(commands=[{"id": "c", "run": "hang"}]), resolve, {}, ssh_ws.pk, None)
    flag = threading.Event()
    threading.Timer(0.6, flag.set).start()
    started = time.monotonic()
    out, err = ssh_node.execute_spec(spec, ssh_ws, None, None, cancel_check=flag.is_set)
    assert time.monotonic() - started < 5
    assert (
        out["results"][0]["error"]["type"] == "cancelled"
        and out["results"][0]["uncertain"]
        and err["code"] == "SSH_CANCELLED"
    )


def test_an_interrupted_running_step_is_failed_as_uncertain_and_never_replayed(ssh_ws, world, sshd, target):
    from datetime import timedelta

    from django.utils import timezone

    v = publish(ssh_ws, "wf-int", [ssh(commands=["echo une-fois"])], [], user=world["designer"])
    run = launch(ssh_ws, v, world["designer"])
    engine.advance(run.pk)
    sr = step(run)
    assert sr.http_job["state"] == "queued"
    claimed = http_node.claim_next()
    assert claimed.pk == sr.pk  # a worker took it ... and died: no result, the lease runs out
    claimed.http_job = {**claimed.http_job, "claimed_at": (timezone.now() - timedelta(hours=1)).isoformat()}
    claimed.save()
    drain(run)
    sr.refresh_from_db()
    assert sr.status == "failed" and sr.error["code"] == "SSH_INTERRUPTED" and sr.error["details"]["uncertain"] is True
    assert sshd.exec_log == []  # nothing was replayed, whatever the engine's retry rules
    http_node.process_one()
    assert sshd.exec_log == []


# ------------------------------------------------------------------ sensitive data
def test_sensitive_commands_hide_text_streams_and_never_reach_the_error_branch(ssh_ws, world, sshd, target):
    nodes = [
        ssh(commands=[{"id": "t", "run": "echo jeton-confidentiel-9999", "sensitive": True}, "fail"]),
        node("h", action="echo", inputs={"value": "{{ default(steps.s.output.results[0].stdout, 'rien') }}"}),
    ]
    run = run_graph(
        ssh_ws,
        world,
        nodes,
        [{"from": "s", "to": "h", "label": "error"}],
        outputs={"p": "{{ steps.h.output.value }}"},
        slug="sens",
    )
    sr = step(run)
    first = sr.output["results"][0]
    assert first["redacted"] and first["stdout"] is None and first["stderr"] is None and first["command"] == "<masqué>"
    assert first["exit_status"] == 0  # the status is not secret
    dump = repr(sr.output) + repr(sr.error) + repr(sr.http_job)
    assert "jeton-confidentiel-9999" not in dump  # not in the results, the error, nor the job spec kept after the run
    assert run.outputs == {"p": "rien"}  # the error branch sees the redaction (null), not the text


def test_known_secret_values_are_masked_in_any_output(ssh_ws, world, sshd, target):
    _, sr = run_ssh(ssh_ws, world, commands=[f"echo le mot de passe est {PASSWORD}"])
    assert PASSWORD not in repr(sr.output) and "***" in sr.output["results"][0]["stdout"]


# ------------------------------------------------------------------ simulated mode
def test_test_mode_opens_no_connection_resolves_no_name_and_reveals_no_secret(ssh_ws, world, sshd, target, monkeypatch):
    import socket

    from core import netsec

    def boom(*a, **k):
        raise AssertionError("réseau ou secret touché en mode test")

    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(netsec, "resolve", boom)
    monkeypatch.setattr(Secret, "reveal", boom)
    monkeypatch.setattr(Secret, "reveal_fields", boom)
    run, sr = run_ssh(
        ssh_ws,
        world,
        mode="test",
        commands=["echo a", "echo b"],
        mocks=[{"id": "c1", "stdout": "simulé\n", "exit_status": 0}],
    )
    out = sr.output
    assert run.status == "succeeded" and out["simulated"] is True and out["connected"] is False
    assert out["results"][0]["stdout"] == "simulé\n" and out["results"][0]["simulated"] is True
    assert out["results"][1]["stdout"] == "" and sshd.connections == []


def test_a_simulated_failure_exercises_the_error_branch(ssh_ws, world, target):
    nodes = [
        ssh(mode="test", commands=["echo a"], mocks=[{"id": "c1", "exit_status": 2, "stderr": "non"}]),
        node("h", action="echo", inputs={"value": "{{ steps.s.output.error_type }}"}),
    ]
    run = run_graph(
        ssh_ws,
        world,
        nodes,
        [{"from": "s", "to": "h", "label": "error"}],
        outputs={"t": "{{ steps.h.output.value }}"},
        slug="sim-err",
    )
    assert run.status == "succeeded" and run.outputs == {"t": "remote_status"} and step(run).output["simulated"] is True


def test_a_sensitive_mock_is_not_returned(ssh_ws, world, target):
    _, sr = run_ssh(
        ssh_ws,
        world,
        mode="test",
        commands=[{"id": "x", "run": "secret", "sensitive": True}],
        mocks=[{"id": "x", "stdout": "valeur-secrete-777"}],
    )
    assert "valeur-secrete-777" not in repr(sr.output)


# ------------------------------------------------------------------ connections: scope and validation
def test_connections_are_fixed_names_of_the_runs_own_workspace(ssh_ws, world, sshd, target):
    from tests.ssh_helpers import make_conn as mk

    mk(world["ws_b"], "etrangere", sshd, secret=make_secret(world["ws_b"], "pwb", PASSWORD))
    from workflows.validation import validate

    graph = {
        "inputs": schema(),
        "nodes": [ssh(connection="etrangere", commands=["echo x"])],
        "edges": [],
        "outputs": {},
    }
    codes = [i["code"] for i in validate(graph, ssh_ws, "w") if i["level"] == "error"]
    assert "unknown_connection" in codes
    graph["nodes"] = [ssh(connection="{{ inputs.c }}", commands=["echo x"])]
    assert any(
        i["code"] in ("bad_ssh", "unknown_connection") for i in validate(graph, ssh_ws, "w") if i["level"] == "error"
    )
    # at execution time the lookup is workspace-scoped too (a connection deleted or moved after publication is not found)
    assert ssh_node.find_connection(ssh_ws.pk, None, "etrangere") is None


def test_a_connection_removed_after_publication_fails_cleanly(ssh_ws, world, sshd, target):
    from vault.models import Connection

    v = publish(ssh_ws, "wf-gone", [ssh(commands=["echo x"])], [], user=world["designer"])
    Connection.objects.filter(name="cible").delete()
    run = drain(launch(ssh_ws, v, world["designer"]))
    assert run.status == "failed" and step(run).error["code"] == "CONNECTION_MISSING" and sshd.connections == []


@pytest.mark.parametrize(
    "fields, needle",
    [
        ({"commands": []}, "au moins une commande"),
        ({"commands": ["echo x"] * 51}, "Trop de commandes"),
        ({"commands": [{"id": "a", "run": "x"}, {"id": "a", "run": "y"}]}, "identifiant"),
        ({"commands": [{"run": "  "}]}, "non vide"),
        ({"commands": ["echo x"], "timeout_seconds": 0}, "Délai global"),
        ({"commands": ["echo x"], "timeout_seconds": 100000}, "Délai global"),
        ({"commands": ["echo x"], "max_output_bytes": 10**9}, "Taille de sortie"),
        ({"commands": ["echo x"], "session": "autre"}, "Session"),
        ({"commands": [{"id": "a", "run": "x", "expect_exit": [999]}]}, "expect_exit"),
        ({"commands": ["echo x"], "mocks": [{"id": "inconnu"}]}, "inconnue"),
        ({"commands": ["echo x"], "mode": "peut-etre"}, "Mode"),
        ({"commands": ["echo x"], "connect_retries": 9}, "Nouvelles tentatives"),
    ],
)
def test_static_validation_refuses_bad_configurations(ssh_ws, fields, needle, target):
    from workflows.validation import validate

    graph = {"inputs": schema(), "nodes": [ssh(**fields)], "edges": [], "outputs": {}}
    messages = " ".join(i["message"] for i in validate(graph, ssh_ws, "w") if i["level"] == "error")
    assert needle in messages


def test_a_valid_step_only_warns_about_test_mode(ssh_ws, target):
    from workflows.validation import validate

    graph = {"inputs": schema(), "nodes": [ssh(mode="test", commands=["echo x"])], "edges": [], "outputs": {}}
    issues = validate(graph, ssh_ws, "w")
    assert [i["code"] for i in issues if i["level"] == "error"] == []
    assert any(i["code"] == "ssh_test_mode" for i in issues)


def test_a_configuration_refused_at_start_still_gives_the_error_branch_a_minimal_output(ssh_ws, world, sshd, target):
    inputs = schema(["cmds"], cmds={"type": "array", "items": {"type": "string"}})
    nodes = [
        ssh(commands="{{ inputs.cmds }}"),
        node(
            "h",
            "transform",
            op="set",
            fields={"t": "{{ steps.s.output.error_type }}", "j": "{{ steps.s.output.status }}"},
        ),
    ]
    run = run_graph(
        ssh_ws,
        world,
        nodes,
        [{"from": "s", "to": "h", "label": "error"}],
        inputs=inputs,
        run_inputs={"cmds": []},
        outputs={"t": "{{ steps.h.output.t }}", "j": "{{ steps.h.output.j }}"},
        slug="cfg-err",
    )
    assert run.status == "succeeded" and run.outputs == {"t": "config", "j": "failed"}
    assert sshd.connections == []  # nothing was opened for a refused configuration


def test_a_password_pasted_with_a_final_newline_still_authenticates(ssh_ws, world, sshd):
    make_conn(ssh_ws, "cible", sshd, secret=make_secret(ssh_ws, "pw-nl", PASSWORD + "\n"))
    run, sr = run_ssh(ssh_ws, world, commands=["whoami"])
    assert run.status == "succeeded" and sr.output["results"][0]["stdout"] == "robot\n"
