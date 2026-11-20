"""SSH step, stage 2: ordered jump hosts (direct-tcpip chains), per-hop host keys and credentials, name resolution, loops, closing order."""

import time

import pytest

from runs import ssh_core
from tests.ssh_fake import FakeSshd, new_key
from tests.ssh_helpers import PASSWORD, make_conn, make_secret, run_ssh, schema, ssh_node, step

pytest.importorskip("paramiko")


@pytest.fixture
def chain(ssh_ws):
    """Two bastions and a target, each with its own host key and its own account: nothing is shared between hops."""
    key, pem = new_key()
    b1 = FakeSshd(users={"alice": {"password": None, "keys": [key]}})
    b2 = FakeSshd(users={"bob": {"password": "pw-bob-synthetique", "keys": []}})
    tgt = FakeSshd(users={"robot": {"password": PASSWORD, "keys": []}})
    make_conn(ssh_ws, "b1", b1, user="alice", secret=make_secret(ssh_ws, "k-b1", pem))
    make_conn(ssh_ws, "b2", b2, user="bob", secret=make_secret(ssh_ws, "p-b2", "pw-bob-synthetique"))
    make_conn(ssh_ws, "cible", tgt, secret=make_secret(ssh_ws, "p-cible", PASSWORD))
    yield b1, b2, tgt
    for d in (b1, b2, tgt):
        d.stop()


def test_one_jump_runs_the_commands_on_the_target_not_on_the_bastion(ssh_ws, world, chain):
    b1, _, tgt = chain
    run, sr = run_ssh(ssh_ws, world, jumps=["b1"], commands=["whoami"])
    assert run.status == "succeeded" and sr.output["results"][0]["stdout"] == "robot\n"
    assert b1.forwards == [("127.0.0.1", tgt.port)] and b1.exec_log == []  # the bastion only relayed the connection
    assert [h["connection"] for h in sr.output["hops"]] == ["b1", "cible"]
    assert [h["host_fingerprint"] for h in sr.output["hops"]] == [b1.fingerprint, tgt.fingerprint]


def test_several_jumps_are_used_in_the_declared_order_with_each_hops_own_credentials(ssh_ws, world, chain):
    b1, b2, tgt = chain
    run, sr = run_ssh(ssh_ws, world, jumps=["b1", "b2"], commands=["whoami", "echo fin"])
    assert run.status == "succeeded"
    assert b1.forwards == [("127.0.0.1", b2.port)] and b2.forwards == [("127.0.0.1", tgt.port)]
    assert b1.connections[0]["auth"] == "publickey" and b1.connections[0]["user"] == "alice"
    assert b2.connections[0]["auth"] == "password" and b2.connections[0]["user"] == "bob"
    assert tgt.connections[0]["user"] == "robot" and [c for _, c in tgt.exec_log] == ["whoami", "echo fin"]
    assert [h["connection"] for h in sr.output["hops"]] == ["b1", "b2", "cible"]


def test_the_order_matters_a_reversed_chain_cannot_reach_the_target(ssh_ws, world, chain):
    b1, b2, tgt = chain
    b1.forward, b2.forward = {b2.port}, {tgt.port}  # each bastion only relays to the next server of the real path
    _, sr = run_ssh(ssh_ws, world, jumps=["b2", "b1"], commands=["whoami"])
    assert sr.error["code"] == "SSH_JUMP" and sr.output["connected"] is False and tgt.connections == []


def test_each_hops_key_is_verified_and_nothing_goes_further_after_a_mismatch(ssh_ws, world, chain):
    b1, b2, tgt = chain
    wrong, _ = new_key()
    from vault.models import Connection

    cfg = Connection.objects.get(name="b2").config
    Connection.objects.filter(name="b2").update(config={**cfg, "host_key_sha256": ssh_core.fingerprint(wrong)})
    _, sr = run_ssh(ssh_ws, world, jumps=["b1", "b2"], commands=["whoami"], connect_retries=2)
    assert sr.error["code"] == "SSH_HOSTKEY" and sr.error["details"]["error_type"] == "hostkey"
    assert sr.output["error"]["hop"] == 1 and sr.output["error"]["connection"] == "b2"
    assert (
        b2.connections[0]["auth"] is None and tgt.connections == []
    )  # no credential sent to b2, the target never contacted
    assert len(b1.connections) == 1  # a host-key mismatch is never retried


def test_the_target_key_is_verified_too(ssh_ws, world, chain):
    _, _, tgt = chain
    from vault.models import Connection

    wrong, _ = new_key()
    cfg = Connection.objects.get(name="cible").config
    Connection.objects.filter(name="cible").update(config={**cfg, "host_key_sha256": ssh_core.fingerprint(wrong)})
    _, sr = run_ssh(ssh_ws, world, jumps=["b1", "b2"], commands=["whoami"])
    assert sr.output["error"]["hop"] == 2 and sr.error["code"] == "SSH_HOSTKEY" and tgt.exec_log == []


def test_an_authentication_failure_on_a_later_hop_names_the_hop(ssh_ws, world, chain):
    b1, b2, _ = chain
    make_secret(ssh_ws, "mauvais", "pas-le-bon-mot-de-passe")
    from vault.models import Connection, Secret

    Connection.objects.filter(name="b2").update(secret=Secret.objects.get(name="mauvais"))
    _, sr = run_ssh(ssh_ws, world, jumps=["b1", "b2"], commands=["whoami"])
    assert (
        sr.error["code"] == "SSH_AUTH" and sr.output["error"]["connection"] == "b2" and sr.output["error"]["hop"] == 1
    )


def test_a_bastion_that_refuses_forwarding_is_a_jump_error(ssh_ws, world, chain):
    b1, _, tgt = chain
    b1.forward = False
    _, sr = run_ssh(ssh_ws, world, jumps=["b1"], commands=["whoami"])
    assert sr.error["code"] == "SSH_JUMP" and sr.output["error"]["connection"] == "cible" and tgt.connections == []


def test_names_after_the_first_hop_are_resolved_by_the_previous_hop_not_by_the_platform(
    ssh_ws, world, chain, monkeypatch
):
    from core import netsec

    b1, _, tgt = chain
    make_conn(ssh_ws, "interne", tgt, secret=make_secret(ssh_ws, "p-int", PASSWORD), host="service-interne.test")
    seen = []
    real = netsec.resolve
    monkeypatch.setattr(netsec, "resolve", lambda host: seen.append(host) or real(host))
    _, sr = run_ssh(ssh_ws, world, connection="interne", jumps=["b1"], commands=["whoami"])
    assert ("service-interne.test", tgt.port) in b1.forward_requests  # the name was handed over as written
    assert "service-interne.test" not in seen  # the platform did not resolve it (and could not check its address)
    assert sr.error["code"] == "SSH_JUMP"  # the bastion could not resolve it either: the error is the bastion's


def test_a_never_allowed_address_is_refused_even_beyond_the_first_hop(ssh_ws, world, chain):
    b1, _, tgt = chain
    make_conn(ssh_ws, "metadata", tgt, secret=make_secret(ssh_ws, "p-md", PASSWORD), host="169.254.169.254")
    _, sr = run_ssh(ssh_ws, world, connection="metadata", jumps=["b1"], commands=["whoami"])
    assert sr.error["code"] == "SSH_BLOCKED" and b1.forwards == []


def test_the_first_hop_keeps_the_network_policy(ssh_ws, world, chain, settings):
    settings.ORCH_EGRESS_ALLOWLIST = ""  # 127.0.0.1 is no longer allowed: the first hop is refused, whatever follows
    b1, _, _ = chain
    _, sr = run_ssh(ssh_ws, world, jumps=["b1"], commands=["whoami"])
    assert sr.error["code"] == "SSH_BLOCKED" and b1.connections == []


def test_loops_and_repeats_are_refused_at_publication_and_at_run_time(ssh_ws, world, chain):
    from workflows.validation import validate

    for fields in ({"jumps": ["b1", "b1"]}, {"jumps": ["cible"]}, {"jumps": ["b1", "b2", "b1"]}):
        graph = {"inputs": schema(), "nodes": [ssh_node(commands=["echo x"], **fields)], "edges": [], "outputs": {}}
        assert any("boucle" in i["message"] for i in validate(graph, ssh_ws, "w") if i["level"] == "error"), fields
    # two different names for the same server are a loop too (caught when the step starts)
    _, _, tgt = chain
    make_conn(ssh_ws, "meme-serveur", tgt, secret=make_secret(ssh_ws, "p-meme", PASSWORD))
    node_ = ssh_node(connection="cible", jumps=["meme-serveur"], commands=["echo x"])
    graph = {"inputs": schema(), "nodes": [node_], "edges": [], "outputs": {}}
    assert any(
        "même serveur" in i["message"] for i in validate(graph, ssh_ws, "w") if i["level"] == "error"
    )  # now caught at publication
    from runs import ssh_node as engine_ssh
    from workflows.expressions import resolve

    with pytest.raises(ssh_core.SshStepError, match="même serveur"):  # and still refused when the step starts
        engine_ssh.prepare(node_, resolve, {}, ssh_ws.pk, None)


def test_the_number_of_jumps_is_bounded(ssh_ws, world, chain):
    from workflows.validation import validate

    graph = {
        "inputs": schema(),
        "nodes": [ssh_node(commands=["echo x"], jumps=[f"j{i}" for i in range(6)])],
        "edges": [],
        "outputs": {},
    }
    assert any("Rebonds" in i["message"] for i in validate(graph, ssh_ws, "w") if i["level"] == "error")


def test_jump_connections_are_looked_up_in_the_runs_workspace(ssh_ws, world, chain):
    from workflows.validation import validate

    b1, _, _ = chain
    make_conn(world["ws_b"], "etranger", b1, secret=make_secret(world["ws_b"], "p-etr", PASSWORD))
    graph = {
        "inputs": schema(),
        "nodes": [ssh_node(commands=["echo x"], jumps=["etranger"])],
        "edges": [],
        "outputs": {},
    }
    assert any(i["code"] == "unknown_connection" for i in validate(graph, ssh_ws, "w") if i["level"] == "error")
    graph["nodes"] = [ssh_node(commands=["echo x"], jumps=["{{ inputs.j }}"])]
    assert any(i["code"] == "bad_ssh" for i in validate(graph, ssh_ws, "w") if i["level"] == "error")


def test_the_chain_is_closed_from_the_last_hop_back_to_the_first():
    closed = []

    class T:
        def __init__(self, name):
            self.name = name

        def close(self):
            closed.append(self.name)

    ssh_core.Session([T("b1"), T("b2"), T("cible")], []).close()
    assert closed == ["cible", "b2", "b1"]


def test_every_connection_of_the_chain_is_closed_after_the_step(ssh_ws, world, chain):
    b1, b2, tgt = chain
    run_ssh(ssh_ws, world, jumps=["b1", "b2"], commands=["echo x"])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(t.is_active() for d in (b1, b2, tgt) for t in d.transports):
        time.sleep(0.1)
    assert not [t for d in (b1, b2, tgt) for t in d.transports if t.is_active()]


def test_a_partial_result_survives_a_failure_in_the_middle_of_a_chain_session(ssh_ws, world, chain):
    _, _, tgt = chain
    # Seule « hang » a un délai court (1 s) : le délai global et celui de « echo avant » (ouverture du canal comprise) restent larges,
    # sinon une machine chargée les épuise avant la première commande (stdout absent, constaté sur la suite complète).
    _, sr = run_ssh(
        ssh_ws,
        world,
        jumps=["b1"],
        commands=["echo avant", {"id": "lent", "run": "hang", "timeout_seconds": 1}],
        timeout_seconds=30,
    )
    r = sr.output["results"]
    assert r[0]["stdout"] == "avant\n" and r[1]["status"] == "timeout" and sr.output["uncertain"] is True
    assert step(sr.run).status == "failed"
