"""The external vault client against a REAL HashiCorp Vault (official image, dev mode) with a real AppRole + policy, KV v2
and the database secrets engine backed by a real PostgreSQL: the dynamic credentials are then used to log in for real.
Skipped when Docker or the images are not available."""

import json
import shutil
import subprocess
import time
import urllib.request
import uuid

import psycopg
import pytest
from django.contrib.auth import get_user_model

from tests.conftest import client_for
from vault import external
from vault.models import Connection, Secret

BASE = "/api/v1/workspaces/team-a"
POLICY = """
path "secret/data/fluxonata/*" { capabilities = ["create", "read", "update", "delete"] }
path "secret/metadata/fluxonata/*" { capabilities = ["read", "delete", "list"] }
path "secret/data/shared/*" { capabilities = ["create", "read", "update"] }
path "database/creds/ro" { capabilities = ["read"] }
path "sys/leases/revoke" { capabilities = ["update"] }
"""
ROOT = "root-token-test"


def _sh(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check, timeout=120)


def _api(address, method, path, body=None, token=ROOT):
    req = urllib.request.Request(
        f"{address}/v1/{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Vault-Token": token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
        raw = resp.read()
    return json.loads(raw) if raw else {}


@pytest.fixture(scope="module")
def real():
    if shutil.which("docker") is None:
        pytest.skip("docker indisponible")
    tag = uuid.uuid4().hex[:6]
    net, pg, vt = f"vt-net-{tag}", f"vt-pg-{tag}", f"vt-vault-{tag}"
    started: list[str] = []
    try:
        _sh("docker", "network", "create", net)
        _sh(
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            pg,
            "--network",
            net,
            "-p",
            "127.0.0.1::5432",
            "-e",
            "POSTGRES_PASSWORD=rootpw",
            "postgres:16-alpine",
        )
        started.append(pg)
        _sh(
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            vt,
            "--network",
            net,
            "--cap-add",
            "IPC_LOCK",
            "-p",
            "127.0.0.1::8200",
            "-e",
            f"VAULT_DEV_ROOT_TOKEN_ID={ROOT}",
            "-e",
            "VAULT_DEV_LISTEN_ADDRESS=0.0.0.0:8200",
            "hashicorp/vault:1.17",
        )
        started.append(vt)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        for c in started:
            _sh("docker", "rm", "-f", c, check=False)
        _sh("docker", "network", "rm", net, check=False)
        pytest.skip("images hashicorp/vault ou postgres indisponibles")
    port = _sh("docker", "port", vt, "8200/tcp").stdout.strip().split(":")[-1]
    pg_port = int(_sh("docker", "port", pg, "5432/tcp").stdout.strip().split(":")[-1])
    address = f"http://127.0.0.1:{port}"
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            _api(address, "GET", "sys/health")
            if _sh("docker", "exec", pg, "pg_isready", "-U", "postgres", check=False).returncode == 0:
                break
        except OSError:
            pass
        time.sleep(1)
    else:
        pytest.skip("Vault ou PostgreSQL n'a pas démarré à temps")
    _api(address, "POST", "sys/auth/approle", {"type": "approle"})
    _api(address, "PUT", "sys/policies/acl/fluxonata", {"policy": POLICY})
    _api(address, "POST", "auth/approle/role/fluxonata", {"token_policies": "fluxonata", "token_ttl": "1h"})
    role_id = _api(address, "GET", "auth/approle/role/fluxonata/role-id")["data"]["role_id"]
    secret_id = _api(address, "POST", "auth/approle/role/fluxonata/secret-id")["data"]["secret_id"]
    _api(address, "POST", "sys/mounts/database", {"type": "database"})
    _api(
        address,
        "POST",
        "database/config/pg",
        {
            "plugin_name": "postgresql-database-plugin",
            "allowed_roles": "ro",
            "username": "postgres",
            "password": "rootpw",
            "connection_url": f"postgresql://{{{{username}}}}:{{{{password}}}}@{pg}:5432/postgres?sslmode=disable",
        },
    )
    _api(
        address,
        "POST",
        "database/roles/ro",
        {
            "db_name": "pg",
            "default_ttl": "2m",
            "max_ttl": "5m",
            "creation_statements": "CREATE ROLE \"{{name}}\" WITH LOGIN PASSWORD '{{password}}' VALID UNTIL '{{expiration}}'; GRANT CONNECT ON DATABASE postgres TO \"{{name}}\";",
        },
    )
    _api(address, "POST", "secret/data/shared/app", {"data": {"password": "pw-shared-1", "user": "app"}})
    _api(address, "POST", "secret/data/other/x", {"data": {"value": "not-for-fluxonata"}})
    yield {"address": address, "role_id": role_id, "secret_id": secret_id, "pg_port": pg_port}
    for c in started:
        _sh("docker", "rm", "-f", c, check=False)
    _sh("docker", "network", "rm", net, check=False)


@pytest.fixture
def live(real, world, settings):
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    admin = get_user_model().objects.create_user("root", password="x", is_superuser=True)
    c = client_for(admin)
    r = c.put(
        "/api/v1/platform/vault/",
        {"address": real["address"], "role_id": real["role_id"], "secret_id": real["secret_id"], "enabled": True},
        format="json",
    )
    assert r.status_code == 200, r.content
    c.put(
        "/api/v1/platform/vault/grants/team-a/",
        {
            "grants": [
                {"pattern": "secret/shared/*", "writable": True},
                {"pattern": "database/creds/*"},
                {"pattern": "secret/other/*"},
            ]
        },
        format="json",
    )
    yield {**real, "admin": c}
    external.forget()


def test_the_real_vault_passes_every_step_of_the_connection_test(live):
    body = live["admin"].post("/api/v1/platform/vault/test/").json()
    assert body["ok"] is True, body
    assert [s["id"] for s in body["steps"]] == ["reach", "login", "write", "read", "delete"]
    assert "Vault 1.17" in body["steps"][0]["message"]


def test_a_managed_secret_is_written_read_rotated_and_purged_for_real(live, world):
    c = client_for(world["designer"])
    assert (
        c.post(
            f"{BASE}/secrets/", {"name": "tok", "provider": "vault", "value": "real-value-1"}, format="json"
        ).status_code
        == 201
    )
    stored = _api(live["address"], "GET", "secret/data/fluxonata/team-a/tok")["data"]["data"]
    assert stored == {"value": "real-value-1"}
    s = Secret.objects.get(name="tok")
    assert s.ciphertext == "" and s.reveal() == "real-value-1"
    assert c.put(f"{BASE}/secrets/tok/", {"value": "real-value-2"}, format="json").status_code == 204
    assert _api(live["address"], "GET", "secret/data/fluxonata/team-a/tok")["data"]["metadata"]["version"] == 2
    external._kv_cache.clear()
    assert s.reveal() == "real-value-2"
    assert c.delete(f"{BASE}/secrets/tok/?purge=1").status_code == 204
    with pytest.raises(urllib.error.HTTPError) as e:
        _api(live["address"], "GET", "secret/data/fluxonata/team-a/tok")
    assert e.value.code == 404


def test_a_reference_reads_and_a_writable_grant_merges_keys(live, world):
    c = client_for(world["designer"])
    r = c.post(
        f"{BASE}/secrets/",
        {
            "name": "shared",
            "provider": "vault",
            "vault": {"mode": "reference", "path": "shared/app", "key": "password"},
        },
        format="json",
    )
    assert r.status_code == 201, r.content
    assert Secret.objects.get(name="shared").reveal() == "pw-shared-1"
    assert c.put(f"{BASE}/secrets/shared/", {"value": "pw-shared-2"}, format="json").status_code == 204
    assert _api(live["address"], "GET", "secret/data/shared/app")["data"]["data"] == {
        "password": "pw-shared-2",
        "user": "app",
    }


def test_the_approle_policy_is_the_last_word(live, world):
    r = client_for(world["designer"]).post(
        f"{BASE}/secrets/",
        {"name": "other", "provider": "vault", "vault": {"mode": "reference", "path": "other/x"}},
        format="json",
    )
    assert (
        r.status_code == 400 and r.json()["error"]["details"]["code"] == "VAULT_FORBIDDEN"
    )  # granted by Fluxonata, refused by Vault


def test_dynamic_database_credentials_really_log_in_and_are_revoked_with_the_secret(live, world):
    c = client_for(world["designer"])
    r = c.post(
        f"{BASE}/connections/",
        {
            "name": "bdd",
            "label": "Base",
            "kind": "postgres",
            "config": {"host": "127.0.0.1", "database": "postgres"},
            "secret_provider": "vault",
            "vault": {"mode": "dynamic", "mount": "database", "path": "creds/ro"},
        },
        format="json",
    )
    assert r.status_code == 201, r.content
    conn = Connection.objects.get(name="bdd")
    user, password = conn.login()
    assert user.startswith("v-")
    assert conn.login() == (user, password)  # one lease, shared
    with psycopg.connect(
        host="127.0.0.1", port=live["pg_port"], dbname="postgres", user=user, password=password, connect_timeout=10
    ) as db:
        assert db.execute("select current_user").fetchone()[0] == user
    name = conn.secret.name
    c.delete(f"{BASE}/connections/{conn.pk}/")
    assert c.delete(f"{BASE}/secrets/{name}/").status_code == 204
    with pytest.raises(psycopg.OperationalError):  # the lease was revoked: the role is gone
        psycopg.connect(
            host="127.0.0.1", port=live["pg_port"], dbname="postgres", user=user, password=password, connect_timeout=10
        )
