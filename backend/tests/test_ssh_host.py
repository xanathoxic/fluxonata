"""SSH step: the host is chosen in the step, the connection may be a reusable IDENTITY (user + private key, no host).

Rules: a hostless connection is used with a host named by the step, whose server-key fingerprint is mandatory; only a private key may be used
that way (never a password); the same identity may serve several hops; a connection that has its own host keeps it."""

import pytest
from rest_framework.test import APIClient

from runs import ssh_core, ssh_node
from tenancy.models import Membership, Role
from tests.ssh_fake import FakeSshd, new_key
from tests.ssh_helpers import PASSWORD, make_secret, run_ssh, schema
from tests.ssh_helpers import ssh_node as ssh
from vault.models import Connection
from workflows.expressions import resolve
from workflows.validation import validate


def identity(ws, name, pem, *, user="robot"):
    return Connection.objects.create(
        workspace=ws,
        name=name,
        label=name,
        kind="ssh",
        secret=make_secret(ws, f"s-{name}", pem),
        config={"username": user},
    )


def at(daemon, **extra):
    return {"host": "127.0.0.1", "port": daemon.port, "host_key_sha256": daemon.fingerprint, **extra}


@pytest.fixture
def keyed(ssh_ws):
    """Two servers that accept the same synthetic key, and a hostless identity holding it."""
    key, pem = new_key()
    users = {"robot": {"password": None, "keys": [key]}}
    with FakeSshd(users=users) as one, FakeSshd(users=users) as two:
        identity(ssh_ws, "cle", pem)
        yield one, two


def errors(ws, node_):
    graph = {"inputs": schema(), "nodes": [node_], "edges": [], "outputs": {}}
    return " | ".join(i["message"] for i in validate(graph, ws, "w") if i["level"] == "error")


# ---- the connection
def test_a_connection_without_a_host_is_valid_and_its_fingerprint_belongs_to_the_host():
    ssh_node.check_connection_config({"username": "robot"})
    with pytest.raises(ssh_core.SshStepError, match="Sans hôte"):
        ssh_node.check_connection_config({"username": "robot", "host_key_sha256": "SHA256:" + "A" * 43})
    with pytest.raises(ssh_core.SshStepError, match="empreinte"):
        ssh_node.check_connection_config(
            {"username": "robot", "host": "h.example"}
        )  # with a host, the fingerprint stays mandatory


def post(user, body):
    c = APIClient()
    c.force_authenticate(user)
    return c.post("/api/v1/workspaces/team-a/connections/", body, format="json")


def test_creating_a_hostless_connection_needs_a_private_key_never_a_password(ssh_ws, world):
    Membership.objects.filter(user=world["designer"]).update(role=Role.ADMIN)
    _, pem = new_key()
    base = {"name": "identite", "label": "Identité", "kind": "ssh", "config": {"username": "robot"}}
    ok = post(world["designer"], {**base, "secret_value": pem})
    assert ok.status_code == 201, ok.content
    refused = post(world["designer"], {**base, "name": "mdp", "secret_value": "un-mot-de-passe-1234"})
    assert refused.status_code == 400 and "clé privée" in str(refused.json())
    assert "un-mot-de-passe-1234" not in str(refused.json())
    with_host = post(
        world["designer"],
        {
            **base,
            "name": "avec-hote",
            "config": {"username": "robot", "host": "h.example", "host_key_sha256": "SHA256:" + "A" * 43},
            "secret_value": "mdp-1234-5678",
        },
    )
    assert with_host.status_code == 201  # a password stays possible when the connection carries its own host


# ---- the step names the host
def test_the_step_names_the_host_and_pins_its_key(ssh_ws, world, keyed):
    one, _ = keyed
    run, sr = run_ssh(ssh_ws, world, connection="cle", commands=["whoami"], **at(one))
    assert run.status == "succeeded" and sr.output["results"][0]["stdout"] == "robot\n"
    assert sr.output["hops"] == [{"connection": "cle", "host_fingerprint": one.fingerprint, "host": "127.0.0.1"}]
    assert one.connections[0]["auth"] == "publickey"


def test_one_identity_serves_several_hosts_in_a_chain_each_with_its_own_key_check(ssh_ws, world, keyed):
    one, two = keyed
    jump = {"connection": "cle", **at(one)}
    run, sr = run_ssh(ssh_ws, world, connection="cle", jumps=[jump], commands=["whoami"], **at(two))
    assert run.status == "succeeded" and [h["connection"] for h in sr.output["hops"]] == [
        "cle",
        "cle",
    ]  # the same connection twice: two servers
    assert one.forwards == [("127.0.0.1", two.port)] and [c for _, c in two.exec_log] == ["whoami"]
    wrong, _ = new_key()
    before = (len(one.connections), len(two.connections))
    _, sr = run_ssh(
        ssh_ws,
        world,
        connection="cle",
        jumps=[{**jump, "host_key_sha256": ssh_core.fingerprint(wrong)}],
        commands=["whoami"],
        slug="x",
        **at(two),
    )
    assert sr.error["code"] == "SSH_HOSTKEY" and sr.output["error"]["hop"] == 0
    assert (
        len(one.connections) == before[0] + 1 and len(two.connections) == before[1]
    )  # no credential went to the first server, the second was never contacted
    assert one.connections[-1]["auth"] is None


def test_the_fingerprint_of_a_host_chosen_by_the_step_is_never_optional(ssh_ws, world, keyed):
    one, _ = keyed
    bare = {k: v for k, v in at(one).items() if k != "host_key_sha256"}
    node_ = ssh(connection="cle", commands=["whoami"], **bare)
    assert "empreinte" in errors(ssh_ws, node_)
    with pytest.raises(ssh_core.SshStepError, match="empreinte"):
        ssh_node.prepare(node_, resolve, {}, ssh_ws.pk, None)
    assert one.connections == []


@pytest.mark.parametrize(
    "fields, needle",
    [
        ({}, "n'a pas d'hôte"),
        ({"host": "bad host", "host_key_sha256": "SHA256:" + "A" * 43}, "invalide"),
        ({"host": "a.example", "host_key_sha256": "pas-une-empreinte"}, "empreinte invalide"),
        ({"host": "a.example", "host_key_sha256": "SHA256:" + "A" * 43, "port": "abc"}, "Port"),
        ({"host": "2852039166", "host_key_sha256": "SHA256:" + "A" * 43}, "invalide"),
    ],
)
def test_the_hostless_identity_needs_a_valid_host_in_the_step(ssh_ws, keyed, fields, needle):
    assert needle in errors(ssh_ws, ssh(connection="cle", commands=["x"], **fields))


def test_a_connection_that_has_its_own_host_keeps_it(ssh_ws, world, sshd, target):
    node_ = ssh(connection="cible", commands=["x"], host="autre.example", host_key_sha256="SHA256:" + "A" * 43)
    assert "définit déjà son hôte" in errors(ssh_ws, node_)
    with pytest.raises(ssh_core.SshStepError, match="définit déjà son hôte"):
        ssh_node.prepare(node_, resolve, {}, ssh_ws.pk, None)


def test_a_password_identity_is_never_sent_to_a_host_named_by_the_step(ssh_ws, world):
    with FakeSshd(users={"robot": {"password": PASSWORD, "keys": []}}) as d:
        identity(ssh_ws, "mdp", PASSWORD)  # created directly (the API refuses it): the run-time check holds on its own
        run, sr = run_ssh(ssh_ws, world, connection="mdp", commands=["whoami"], **at(d))
        assert run.status == "failed" and sr.error["code"] == "SSH_CONFIG" and "mot de passe" in sr.error["message"]
        assert d.connections == [] and PASSWORD not in repr(sr.output) + repr(
            sr.error
        )  # nothing was opened, nothing leaked


def test_the_network_policy_still_guards_a_host_named_by_the_step(ssh_ws, world, keyed, settings):
    one, _ = keyed
    settings.ORCH_EGRESS_ALLOWLIST = ""
    _, sr = run_ssh(ssh_ws, world, connection="cle", commands=["whoami"], **at(one))
    assert (
        sr.error["code"] == "SSH_BLOCKED" and one.connections == []
    )  # 127.0.0.1 is not allowed: the first hop is filtered as ever


def test_the_host_may_come_from_an_expression_and_is_validated_after_evaluation(ssh_ws, world, keyed):
    one, _ = keyed
    spec = ssh_node.prepare(
        ssh(connection="cle", commands=["whoami"], host="{{ inputs.h }}", port="{{ inputs.p }}", host_key_sha256="{{ inputs.f }}"),
        resolve, {"inputs": {"h": "127.0.0.1", "p": one.port, "f": one.fingerprint}}, ssh_ws.pk, None,
    )  # fmt: skip
    assert spec["endpoints"] == [{"host": "127.0.0.1", "port": one.port, "fingerprints": [one.fingerprint]}]
    with pytest.raises(ssh_core.SshStepError, match="invalide"):
        ssh_node.prepare(
            ssh(connection="cle", commands=["x"], host="{{ inputs.h }}", host_key_sha256=one.fingerprint),
            resolve,
            {"inputs": {"h": "bad host"}},
            ssh_ws.pk,
            None,
        )


def test_test_mode_with_a_step_host_opens_nothing_and_shows_the_host(ssh_ws, world, keyed, monkeypatch):
    import socket

    from core import netsec
    from vault.models import Secret

    one, _ = keyed

    def boom(*a, **k):
        raise AssertionError("réseau, DNS ou secret touché")

    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(netsec, "resolve", boom)
    monkeypatch.setattr(Secret, "reveal", boom)
    run, sr = run_ssh(ssh_ws, world, connection="cle", mode="test", commands=["whoami"], **at(one))
    assert run.status == "succeeded" and sr.output["simulated"] is True and sr.output["hops"][0]["host"] == "127.0.0.1"
    assert one.connections == []


def test_the_chain_loop_rule_is_about_servers_not_names(ssh_ws, keyed):
    one, _ = keyed
    twice = ssh(connection="cle", jumps=[{"connection": "cle", **at(one)}], commands=["x"], **at(one))
    assert "même serveur" in errors(ssh_ws, twice)  # the same identity at the SAME server twice is a loop
    with pytest.raises(ssh_core.SshStepError, match="même serveur"):
        ssh_node.prepare(twice, resolve, {}, ssh_ws.pk, None)
