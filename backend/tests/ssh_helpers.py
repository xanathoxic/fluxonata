"""Helpers shared by the SSH step tests: synthetic connections to disposable servers and workflow runners (no real equipment, no real secret)."""

import pytest

from runs import engine, http_node
from runs.models import StepRun
from tests.ssh_fake import FakeSshd, new_encrypted_pem, new_key
from tests.wf_helpers import ACTIONS, drain, launch, make_action, node, publish, schema
from vault.models import Connection, Secret

PASSWORD = "pw-synthetique-1234"
KEY_PASSPHRASE = "phrase-synthetique-5678"


def make_secret(ws, name, value):
    s = Secret(workspace=ws, name=name)
    s.set_value(value)
    s.save()
    return s


def make_conn(ws, name, daemon, *, user="robot", secret=None, fingerprint=None, kind="ssh", **config):
    cfg = {
        "host": "127.0.0.1",
        "port": str(daemon.port),
        "username": user,
        "host_key_sha256": fingerprint or daemon.fingerprint,
        **config,
    }
    return Connection.objects.create(workspace=ws, name=name, label=name, kind=kind, secret=secret, config=cfg)


@pytest.fixture
def ssh_ws(world, settings):
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    ws = world["ws_a"]
    for slug in ACTIONS:
        make_action(ws, slug)
    return ws


@pytest.fixture
def sshd():
    with FakeSshd(users={"robot": {"password": PASSWORD, "keys": []}}) as d:
        yield d


@pytest.fixture
def target(ssh_ws, sshd):
    make_conn(ssh_ws, "cible", sshd, secret=make_secret(ssh_ws, "pw-cible", PASSWORD))
    return "cible"


def ssh_node(nid="s", **fields):
    base = {"connection": "cible", "mode": "reel", "session": "exec"}
    return node(nid, "ssh", **{**base, **fields})


def run_graph(ws, world, nodes, edges=(), inputs=None, run_inputs=None, outputs=None, slug="wf-ssh", **kw):
    v = publish(ws, slug, nodes, list(edges), inputs=inputs, outputs=outputs, user=world["designer"])
    return drain(launch(ws, v, world["designer"], inputs=run_inputs), **kw)


def step(run, nid="s"):
    return run.steps.get(node_id=nid)


def run_ssh(ws, world, **fields):
    run = run_graph(ws, world, [ssh_node(**fields)])
    return run, step(run)


__all__ = [
    "FakeSshd",
    "KEY_PASSPHRASE",
    "PASSWORD",
    "StepRun",
    "drain",
    "engine",
    "http_node",
    "launch",
    "make_action",
    "make_conn",
    "make_secret",
    "new_encrypted_pem",
    "new_key",
    "node",
    "publish",
    "run_graph",
    "run_ssh",
    "schema",
    "ssh_node",
    "ssh_ws",
    "sshd",
    "step",
    "target",
]
