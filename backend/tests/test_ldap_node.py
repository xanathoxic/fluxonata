import shutil
import socket
import subprocess
import time

import pytest

from tests.wf_helpers import ACTIONS, drain, launch, make_action, node, publish
from vault.models import Connection, Secret

ldap3 = pytest.importorskip("ldap3")
ADMIN_DN, ADMIN_PW, BASE = "cn=admin,dc=exemple,dc=org", "admin-pw-12345", "dc=exemple,dc=org"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def directory():
    if shutil.which("docker") is None:
        pytest.skip("docker indisponible")
    port = _free_port()
    cid = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "-p",
            f"127.0.0.1:{port}:389",
            "-e",
            "LDAP_ORGANISATION=Exemple",
            "-e",
            "LDAP_DOMAIN=exemple.org",
            "-e",
            f"LDAP_ADMIN_PASSWORD={ADMIN_PW}",
            "osixia/openldap:1.5.0",
        ],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not cid:
        pytest.skip("image osixia/openldap indisponible")
    try:
        deadline = time.time() + 120
        while True:
            try:
                c = ldap3.Connection(
                    ldap3.Server("127.0.0.1", port=port, connect_timeout=3),
                    ADMIN_DN,
                    ADMIN_PW,
                    auto_bind=True,
                    raise_exceptions=True,
                )
                break
            except Exception:  # noqa: BLE001  (the server is still starting)
                if time.time() > deadline:
                    pytest.skip("OpenLDAP ne démarre pas")
                time.sleep(1)
        for uid, cn, mail in (("ada", "Ada Lovelace", "ada@exemple.org"), ("bob", "Bob (équipe) *", "bob@exemple.org")):
            c.add(f"uid={uid},{BASE}", ["inetOrgPerson"], {"cn": cn, "sn": uid.title(), "mail": mail, "uid": uid})
        c.unbind()
        yield port
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)


@pytest.fixture
def ws(world, directory, settings):
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    ws = world["ws_a"]
    for slug in ACTIONS:
        make_action(ws, slug)
    s = Secret(workspace=ws, name="ldappw")
    s.set_value(ADMIN_PW)
    s.save()
    Connection.objects.create(
        workspace=ws,
        name="annuaire",
        label="Annuaire",
        kind="ldap",
        secret=s,
        config={"host": "127.0.0.1", "port": str(directory), "bind_dn": ADMIN_DN, "base_dn": BASE, "tls": "none"},
    )
    return ws


def _run(ws, world, slug, **fields):
    user = world["designer"]
    v = publish(ws, slug, [node("l", "ldap", connection="annuaire", mode="reel", **fields)], [], user=user)
    run = drain(launch(ws, v, user), max_rounds=1500)
    return run, run.steps.get(node_id="l")


def test_search_returns_plain_rows_with_escaped_filter_values(ws, world):
    run, sr = _run(
        ws,
        world,
        "a",
        filter="(&(objectClass=inetOrgPerson)(mail={mail}))",
        params={"mail": "ada@exemple.org"},
        attributes=["cn", "mail", "uid"],
    )
    assert (
        run.status == "succeeded"
        and sr.output["count"] == 1
        and sr.output["rows"][0]["cn"] == "Ada Lovelace"
        and sr.output["rows"][0]["dn"].startswith("uid=ada,")
    )
    # LDAP special characters in a value are escaped: the « * » of this name is matched literally, never as a wildcard
    run, sr = _run(ws, world, "b", filter="(cn={nom})", params={"nom": "*"}, attributes=["cn"])
    assert sr.output["count"] == 0
    run, sr = _run(ws, world, "c", filter="(cn={nom})", params={"nom": "Bob (équipe) *"}, attributes=["cn"])
    assert sr.output["count"] == 1
    run, sr = _run(ws, world, "d", filter="(objectClass=inetOrgPerson)", attributes=["uid"], max_rows=1)
    assert sr.output["count"] == 1 and sr.output["truncated"] is True


def test_errors_are_explained_and_blocked_hosts_refused(ws, world, settings):
    run, _ = _run(ws, world, "e", filter="(uid=ada)", base="dc=inconnu,dc=org")
    assert run.status == "failed" and run.error["code"] in ("LDAP_BASE", "LDAP_ERROR")
    run, _ = _run(ws, world, "f", filter="(uid={absent})")
    assert run.error["code"] == "LDAP_CONFIG"
    settings.ORCH_EGRESS_ALLOWLIST = ""
    run, _ = _run(ws, world, "h", filter="(uid=ada)")
    assert run.error["code"] == "LDAP_BLOCKED"


def test_bad_bind_password_is_reported_without_echoing_it(ws, world):
    s = Secret.objects.get(name="ldappw")
    s.set_value("mauvais-mot-de-passe")
    s.save()
    run, _ = _run(ws, world, "i", filter="(uid=ada)")
    assert run.error["code"] == "LDAP_AUTH" and "mauvais" not in str(run.error)


def test_validator_and_test_mode(ws, world):
    from workflows.validation import validate_graph

    g = {
        "inputs": {"type": "object"},
        "nodes": [node("l", "ldap", connection="annuaire", filter="uid=ada")],
        "edges": [],
    }
    assert "bad_ldap" in {i["code"] for i in validate_graph(g, ws, "t")}
    g["nodes"][0].update(filter="(uid=ada)", connection="absente")
    assert "unknown_connection" in {i["code"] for i in validate_graph(g, ws, "t")}
    v = publish(
        ws, "t", [node("l", "ldap", connection="annuaire", mode="test", filter="(uid=ada)")], [], user=world["designer"]
    )
    run = drain(launch(ws, v, world["designer"]))
    assert run.steps.get(node_id="l").output["simulated"] is True
