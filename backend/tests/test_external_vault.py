"""External secrets manager (HashiCorp Vault) against an in-process look-alike: platform setup and test, KV v2 read and
write, references and grants, dynamic credentials with leases, token renewal, isolation between workspaces, and that no
value ever reaches an API answer, the audit trail or the logs."""

import json
import logging
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from audit.models import AuditEvent
from tests.conftest import client_for
from tests.fake_vault import ROLE_ID, SECRET_ID, FakeVault
from vault import auth, external
from vault.models import Connection, Secret, VaultConfig, VaultLease

BASE = "/api/v1/workspaces/team-a"
VALUE = "s3cr3t-value-123456"


@pytest.fixture
def fv(settings):
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    v = FakeVault()
    yield v
    v.close()
    external.forget()


@pytest.fixture
def admin(db):
    return get_user_model().objects.create_user("root", password="x", is_superuser=True)


def configure(admin, fv, **over):
    body = {"address": fv.address, "role_id": ROLE_ID, "secret_id": SECRET_ID, "enabled": True, **over}
    r = client_for(admin).put("/api/v1/platform/vault/", body, format="json")
    assert r.status_code == 200, r.content
    return r.json()


@pytest.fixture
def on(fv, admin, world):
    configure(admin, fv)
    return fv


def create_secret(world, **body):
    return client_for(world["designer"]).post(f"{BASE}/secrets/", body, format="json")


def secret(world, name):
    return Secret.objects.get(workspace=world["ws_a"], name=name)


# ------------------------------------------------------------------------------------------- platform configuration


def test_only_platform_administrators_configure_the_vault(world, fv, admin):
    body = {"address": fv.address, "role_id": ROLE_ID, "secret_id": SECRET_ID}
    assert client_for(world["designer"]).put("/api/v1/platform/vault/", body, format="json").status_code == 403
    assert client_for(world["designer"]).get("/api/v1/platform/vault/").status_code == 403
    assert client_for(world["designer"]).post("/api/v1/platform/vault/test/").status_code == 403
    assert (
        client_for(world["designer"])
        .put("/api/v1/platform/vault/grants/team-a/", {"grants": []}, format="json")
        .status_code
        == 403
    )


def test_the_secret_id_is_stored_encrypted_and_never_returned(world, fv, admin):
    out = configure(admin, fv)
    assert out["secret_id_set"] is True and "secret_id" not in out and SECRET_ID not in json.dumps(out)
    cfg = VaultConfig.objects.get(pk=1)
    assert SECRET_ID not in cfg.secret_id_ciphertext and cfg.secret_id() == SECRET_ID
    again = client_for(admin).get("/api/v1/platform/vault/").json()
    assert again["secret_id_set"] is True and SECRET_ID not in json.dumps(again)
    # a later save without secret_id keeps the stored one
    configure(admin, fv, secret_id="")
    assert VaultConfig.objects.get(pk=1).secret_id() == SECRET_ID
    audit = json.dumps(list(AuditEvent.objects.values_list("data", flat=True)), default=str)
    assert SECRET_ID not in audit and "platform.vault_configure" in {e.action for e in AuditEvent.objects.all()}


def test_plain_http_needs_an_allowed_host(world, admin, settings):
    settings.ORCH_EGRESS_ALLOWLIST = ""
    r = client_for(admin).put(
        "/api/v1/platform/vault/",
        {"address": "http://vault.example.org", "role_id": "r", "secret_id": "s"},
        format="json",
    )
    assert r.status_code == 400 and "https" in json.dumps(r.json(), ensure_ascii=False)


def test_connection_test_reports_every_step(world, on, admin):
    r = client_for(admin).post("/api/v1/platform/vault/test/")
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True
    assert [s["id"] for s in body["steps"]] == ["reach", "login", "write", "read", "delete"]
    assert VaultConfig.objects.get(pk=1).last_test_ok is True
    assert not [k for k in on.kv if "_fluxonata_check" in k]  # the probe secret is removed


def test_wrong_role_secret_unreachable_and_sealed_are_explained(world, fv, admin):
    configure(admin, fv, secret_id="faux")
    body = client_for(admin).post("/api/v1/platform/vault/test/").json()
    assert body["ok"] is False and body["steps"][-1]["code"] == "VAULT_AUTH_FAILED"
    configure(admin, fv)
    fv.sealed = True
    assert client_for(admin).post("/api/v1/platform/vault/test/").json()["steps"][0]["code"] == "VAULT_SEALED"
    fv.sealed = False
    configure(admin, fv, address="http://127.0.0.1:9")
    assert client_for(admin).post("/api/v1/platform/vault/test/").json()["steps"][0]["code"] == "VAULT_UNREACHABLE"


def test_an_internal_address_is_refused_unless_the_operator_allowed_it(world, fv, admin, settings):
    configure(admin, fv)
    settings.ORCH_EGRESS_ALLOWLIST = ""
    body = client_for(admin).post("/api/v1/platform/vault/test/").json()
    assert body["ok"] is False and body["steps"][0]["code"] == "VAULT_BLOCKED"


def test_the_namespace_is_sent(world, fv, admin):
    configure(admin, fv, namespace="equipe-a")
    client_for(admin).post("/api/v1/platform/vault/test/")
    assert {ns for _, _, ns in fv.requests} == {"equipe-a"}


# ---------------------------------------------------------------------------------------------------- managed secret


def test_a_managed_secret_lives_only_in_vault(world, on):
    r = create_secret(world, name="api-key", provider="vault", value=VALUE)
    assert r.status_code == 201 and VALUE not in r.content.decode()
    s = secret(world, "api-key")
    assert s.provider == "vault" and s.ciphertext == "" and s.vault_path == "fluxonata/team-a/api-key"
    assert on.kv["secret/fluxonata/team-a/api-key"] == [{"value": VALUE}]
    assert s.reveal() == VALUE
    rows = client_for(world["designer"]).get(f"{BASE}/secrets/").json()["results"]
    assert (
        rows[0]["provider"] == "vault"
        and rows[0]["vault_path"] == "fluxonata/team-a/api-key"
        and VALUE not in json.dumps(rows)
    )


def test_rotating_and_deleting_a_managed_secret_go_through_vault(world, on):
    create_secret(world, name="k", provider="vault", value="v1-aaaa")
    c = client_for(world["designer"])
    assert c.put(f"{BASE}/secrets/k/", {"value": "v2-bbbb"}, format="json").status_code == 204
    assert secret(world, "k").reveal() == "v2-bbbb" and len(on.kv["secret/fluxonata/team-a/k"]) == 2
    assert c.delete(f"{BASE}/secrets/k/?purge=1").status_code == 204
    assert "secret/fluxonata/team-a/k" not in on.kv and not Secret.objects.filter(name="k").exists()


def test_deleting_without_purge_leaves_vault_untouched(world, on):
    create_secret(world, name="k2", provider="vault", value="v1-aaaa")
    assert client_for(world["designer"]).delete(f"{BASE}/secrets/k2/").status_code == 204
    assert "secret/fluxonata/team-a/k2" in on.kv


def test_internal_secrets_are_unchanged(world, on):
    assert create_secret(world, name="local", value="plain-value-1").status_code == 201
    s = secret(world, "local")
    assert s.provider == "internal" and s.ciphertext and s.reveal() == "plain-value-1" and not on.kv


def test_creating_in_vault_needs_a_working_vault(world, fv):
    r = create_secret(world, name="x", provider="vault", value=VALUE)
    assert r.status_code == 400 and r.json()["error"]["details"]["code"] == "VAULT_NOT_CONFIGURED"
    assert not Secret.objects.filter(name="x").exists()


# -------------------------------------------------------------------------------------------- references and grants


def test_a_reference_outside_the_workspace_area_needs_a_grant(world, on, admin):
    on.kv["secret/equipe-b/db"] = [{"password": "pw-from-b"}]
    body = {
        "name": "ref",
        "provider": "vault",
        "vault": {"mode": "reference", "path": "equipe-b/db", "key": "password"},
    }
    r = create_secret(world, **body)
    assert r.status_code == 400 and r.json()["error"]["details"]["code"] == "VAULT_PATH_REFUSED"
    g = client_for(admin).put(
        "/api/v1/platform/vault/grants/team-a/", {"grants": [{"pattern": "secret/equipe-b/*"}]}, format="json"
    )
    assert g.status_code == 200
    assert create_secret(world, **body).status_code == 201
    assert secret(world, "ref").reveal() == "pw-from-b"
    # revoking the grant stops the reads at once
    client_for(admin).put("/api/v1/platform/vault/grants/team-a/", {"grants": []}, format="json")
    with pytest.raises(external.ExternalVaultError) as e:
        secret(world, "ref").reveal()
    assert e.value.code == "VAULT_PATH_REFUSED"


def test_workspaces_cannot_reach_each_others_areas(world, on, admin):
    on.kv["secret/fluxonata/team-b/db"] = [{"value": "b-only"}]
    r = create_secret(world, name="steal", provider="vault", vault={"mode": "reference", "path": "fluxonata/team-b/db"})
    assert r.status_code == 400 and r.json()["error"]["details"]["code"] == "VAULT_PATH_REFUSED"
    # a grant given to team-b does not help team-a
    client_for(admin).put(
        "/api/v1/platform/vault/grants/team-b/", {"grants": [{"pattern": "secret/fluxonata/team-b/*"}]}, format="json"
    )
    assert (
        create_secret(
            world, name="steal", provider="vault", vault={"mode": "reference", "path": "fluxonata/team-b/db"}
        ).status_code
        == 400
    )


@pytest.mark.parametrize(
    "path", ["../team-b/db", "fluxonata/team-a/../team-b/db", "a//b", "/etc", "fluxonata/team-a/x?y=1", "a b"]
)
def test_paths_are_normalised_and_traversal_refused(world, on, path):
    r = create_secret(world, name="t", provider="vault", vault={"mode": "reference", "path": path})
    assert r.status_code == 400 and r.json()["error"]["details"]["code"] == "VAULT_PATH_REFUSED"


def test_grant_patterns_are_validated(world, on, admin):
    for bad in ("*", "secret", "*/x", "secret/../x", "secret/a b", ""):
        r = client_for(admin).put(
            "/api/v1/platform/vault/grants/team-a/", {"grants": [{"pattern": bad}]}, format="json"
        )
        assert r.status_code == 400, bad


def test_read_only_grants_refuse_writes_and_writable_ones_keep_other_keys(world, on, admin):
    on.kv["secret/shared/app"] = [{"password": "p1", "user": "bob"}]
    c = client_for(admin)
    c.put(
        "/api/v1/platform/vault/grants/team-a/",
        {"grants": [{"pattern": "secret/shared/*", "writable": False}]},
        format="json",
    )
    body = {
        "name": "shared",
        "provider": "vault",
        "vault": {"mode": "reference", "path": "shared/app", "key": "password"},
    }
    assert create_secret(world, **body).status_code == 201
    assert (
        client_for(world["designer"])
        .put(f"{BASE}/secrets/shared/", {"value": "p2"}, format="json")
        .json()["error"]["details"]["code"]
        == "VAULT_PATH_REFUSED"
    )
    c.put(
        "/api/v1/platform/vault/grants/team-a/",
        {"grants": [{"pattern": "secret/shared/*", "writable": True}]},
        format="json",
    )
    assert (
        client_for(world["designer"]).put(f"{BASE}/secrets/shared/", {"value": "p2"}, format="json").status_code == 204
    )
    assert on.kv["secret/shared/app"][-1] == {"password": "p2", "user": "bob"}  # the other key survived
    assert secret(world, "shared").reveal() == "p2"


def test_a_pinned_version_and_a_missing_key(world, on, admin):
    on.kv["secret/shared/v"] = [{"value": "old"}, {"value": "new"}]
    client_for(admin).put(
        "/api/v1/platform/vault/grants/team-a/", {"grants": [{"pattern": "secret/shared/*"}]}, format="json"
    )
    ok = create_secret(
        world, name="pinned", provider="vault", vault={"mode": "reference", "path": "shared/v", "version": 1}
    )
    assert ok.status_code == 201 and secret(world, "pinned").reveal() == "old"
    bad = create_secret(
        world, name="nokey", provider="vault", vault={"mode": "reference", "path": "shared/v", "key": "absent"}
    )
    assert bad.status_code == 400 and bad.json()["error"]["details"]["code"] == "VAULT_KEY_MISSING"
    missing = create_secret(world, name="nopath", provider="vault", vault={"mode": "reference", "path": "shared/zzz"})
    assert missing.status_code == 400 and missing.json()["error"]["details"]["code"] == "VAULT_NOT_FOUND"


def test_the_secret_test_endpoint_lists_field_names_only(world, on, admin):
    on.kv["secret/shared/multi"] = [{"user": "u-secret", "password": "p-secret"}]
    client_for(admin).put(
        "/api/v1/platform/vault/grants/team-a/", {"grants": [{"pattern": "secret/shared/*"}]}, format="json"
    )
    create_secret(
        world, name="multi", provider="vault", vault={"mode": "reference", "path": "shared/multi", "key": "password"}
    )
    r = client_for(world["designer"]).post(f"{BASE}/secrets/multi/test/")
    assert r.json() == {"ok": True, "fields": ["password", "user"], "mode": "reference"}
    assert "p-secret" not in r.content.decode() and "u-secret" not in r.content.decode()


# ------------------------------------------------------------------------------------------------ dynamic secrets


def dynamic_connection(world, on, admin, **over):
    client_for(admin).put(
        "/api/v1/platform/vault/grants/team-a/", {"grants": [{"pattern": "database/creds/*"}]}, format="json"
    )
    body = {
        "name": "bdd",
        "label": "Base",
        "kind": "postgres",
        "config": {"host": "127.0.0.1", "database": "app"},
        "secret_provider": "vault",
        "vault": {"mode": "dynamic", "mount": "database", "path": "creds/ro-team-a"},
        **over,
    }
    return client_for(world["designer"]).post(f"{BASE}/connections/", body, format="json")


def test_dynamic_credentials_provide_both_user_and_password(world, on, admin):
    r = dynamic_connection(
        world, on, admin, secret_value=""
    )  # no username in the settings: Vault supplies it (a blank value is fine)
    assert r.status_code == 201, r.content
    conn = Connection.objects.get(workspace=world["ws_a"], name="bdd")
    user, password = conn.login()
    assert user.startswith("v-app-") and password.startswith("pw-dyn-")
    assert conn.secret.provider == "vault" and conn.secret.vault_mode == "dynamic" and conn.secret.ciphertext == ""


def test_dynamic_credentials_are_shared_until_their_lease_nears_its_end(world, on, admin):
    dynamic_connection(world, on, admin)
    conn = Connection.objects.get(workspace=world["ws_a"], name="bdd")
    first = conn.login()
    assert conn.login() == first and on.creds_issued == 1  # one lease serves the steps of a run
    lease = VaultLease.objects.get(secret=conn.secret)
    assert "pw-dyn-1" not in lease.ciphertext  # stored encrypted
    VaultLease.objects.filter(pk=lease.pk).update(expires_at=timezone.now() + timedelta(seconds=5))
    second = conn.login()
    assert second != first and on.creds_issued == 2


def test_deleting_a_dynamic_secret_revokes_its_leases(world, on, admin):
    dynamic_connection(world, on, admin)
    conn = Connection.objects.get(workspace=world["ws_a"], name="bdd")
    conn.login()
    name = conn.secret.name
    client_for(world["designer"]).delete(f"{BASE}/connections/{conn.pk}/")
    assert client_for(world["designer"]).delete(f"{BASE}/secrets/{name}/").status_code == 204
    assert on.revoked and on.revoked[0].startswith("database/creds/ro-team-a/")


def test_a_dynamic_secret_cannot_be_written(world, on, admin):
    dynamic_connection(world, on, admin)
    name = Connection.objects.get(name="bdd").secret.name
    r = client_for(world["designer"]).put(f"{BASE}/secrets/{name}/", {"value": "x"}, format="json")
    assert r.status_code == 400 and r.json()["error"]["details"]["code"] == "VAULT_READ_ONLY"


# ------------------------------------------------------------------------------------- token, errors and the rest


def test_the_client_token_is_reused_then_renewed_when_vault_refuses_it(world, on):
    create_secret(world, name="t1", provider="vault", value="value-one-1")
    assert on.logins == 1
    external._kv_cache.clear()
    assert secret(world, "t1").reveal() == "value-one-1" and on.logins == 1  # the token is reused
    on.expire_tokens()  # Vault no longer knows it: one fresh login, then the call succeeds
    external._kv_cache.clear()
    assert secret(world, "t1").reveal() == "value-one-1" and on.logins == 2


def test_permission_denied_by_the_vault_policy_is_explained(world, on):
    create_secret(world, name="p1", provider="vault", value="value-pol-1")
    on.forbidden.add("secret/fluxonata/team-a/p1")
    external._kv_cache.clear()
    with pytest.raises(external.ExternalVaultError) as e:
        secret(world, "p1").reveal()
    assert e.value.code == "VAULT_FORBIDDEN" and "politique" in e.value.message


def test_a_disabled_vault_stops_external_reads_but_not_internal_ones(world, on, admin):
    create_secret(world, name="d1", provider="vault", value="value-dis-1")
    create_secret(world, name="d2", value="value-int-1")
    configure(admin, on, enabled=False)
    with pytest.raises(external.ExternalVaultError) as e:
        secret(world, "d1").reveal()
    assert e.value.code == "VAULT_DISABLED" and secret(world, "d2").reveal() == "value-int-1"


def test_an_unreachable_vault_is_a_clear_error_never_a_fallback(world, on, admin):
    create_secret(world, name="u1", provider="vault", value="value-unr-1")
    configure(admin, on, address="http://127.0.0.1:9")
    with pytest.raises(external.ExternalVaultError) as e:
        secret(world, "u1").reveal()
    assert e.value.code == "VAULT_UNREACHABLE"


def test_connections_use_the_vault_value_and_report_vault_problems(world, on):
    r = client_for(world["designer"]).post(
        f"{BASE}/connections/",
        {
            "name": "svc",
            "label": "Service",
            "kind": "http_bearer",
            "config": {"base_url": "http://127.0.0.1:1/"},
            "secret_provider": "vault",
            "secret_value": "bearer-token-abc",
        },
        format="json",
    )
    assert (
        r.status_code == 201 and r.json()["secret_provider"] == "vault" and "bearer-token-abc" not in r.content.decode()
    )
    conn = Connection.objects.get(name="svc")
    assert auth.credentials(conn).headers == {"Authorization": "Bearer bearer-token-abc"}
    test = client_for(world["designer"]).post(f"{BASE}/connections/{conn.pk}/test/")
    assert test.status_code == 200
    on.forbidden.add("secret/fluxonata/team-a/")
    external._kv_cache.clear()
    with pytest.raises(auth.AuthError) as e:
        auth.credentials(conn)
    assert e.value.code == "VAULT_FORBIDDEN"


def test_no_value_in_responses_audit_or_logs(world, on, caplog, admin):
    caplog.set_level(logging.DEBUG)
    r = create_secret(world, name="leak", provider="vault", value=VALUE)
    client_for(world["designer"]).put(f"{BASE}/secrets/leak/", {"value": VALUE + "2"}, format="json")
    secret(world, "leak").reveal()
    client_for(world["designer"]).post(f"{BASE}/secrets/leak/test/")
    blob = (
        r.content.decode()
        + json.dumps(list(AuditEvent.objects.values_list("data", flat=True)), default=str)
        + caplog.text
    )
    assert VALUE not in blob and SECRET_ID not in blob


def test_the_workspace_sees_what_it_may_use(world, on, admin):
    client_for(admin).put(
        "/api/v1/platform/vault/grants/team-a/",
        {"grants": [{"pattern": "database/creds/*", "writable": False}]},
        format="json",
    )
    info = client_for(world["designer"]).get(f"{BASE}/vault/").json()
    assert info == {
        "available": True,
        "mount": "secret",
        "own_prefix": "fluxonata/team-a/",
        "grants": [{"pattern": "database/creds/*", "writable": False}],
    }
    assert client_for(world["viewer"]).get(f"{BASE}/vault/").status_code == 403
    assert client_for(world["outsider"]).get(f"{BASE}/vault/").status_code == 404


def test_vault_not_available_is_reported_to_the_workspace(world, fv):
    assert client_for(world["designer"]).get(f"{BASE}/vault/").json() == {"available": False}


# --------------------------------------------------------------------------------------------- webhooks and the rest


def test_a_webhook_is_verified_with_a_secret_held_in_vault(world, on):
    import hashlib
    import hmac
    import time

    from rest_framework.test import APIClient

    from tests.wf_helpers import ACTIONS, make_action, node, publish

    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    publish(world["ws_a"], "reagit", [node("e", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    create_secret(world, name="hook-key", provider="vault", value="whsec_held_in_vault_123")
    c = client_for(world["designer"])
    r = c.post(
        f"{BASE}/webhooks/",
        {"name": "h", "workflow": "reagit", "environment": "test", "secret": "hook-key"},
        format="json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert "secret" not in body and body["signing_secret"] == {"name": "hook-key", "provider": "vault"}
    hook = type(secret(world, "hook-key")).webhooks.rel.related_model.objects.get(pk=body["id"])
    assert hook.secret_ciphertext == "" and hook.reveal_secret() == "whsec_held_in_vault_123"

    def call(secret_value):
        raw = b"{}"
        ts = str(int(time.time()))
        sig = hmac.new(secret_value.encode(), ts.encode() + b"." + raw, hashlib.sha256).hexdigest()
        return APIClient().post(
            body["url"],
            data=raw,
            content_type="application/json",
            HTTP_X_FLUXONATA_TIMESTAMP=ts,
            HTTP_X_FLUXONATA_SIGNATURE=f"v1={sig}",
        )

    assert call("whsec_held_in_vault_123").status_code in (200, 202)
    assert call("wrong-secret").status_code == 401
    # the secret is in use: it cannot be deleted, and the webhook is not rotated by Fluxonata
    assert c.delete(f"{BASE}/secrets/hook-key/").status_code == 400
    assert c.post(f"{BASE}/webhooks/{body['id']}/").status_code == 400
    # the vault stops answering: the sender gets a retryable 503, not a rejection
    on.forbidden.add("secret/fluxonata/team-a/hook-key")
    external._kv_cache.clear()
    assert call("whsec_held_in_vault_123").status_code == 503


def test_key_rotation_reencrypts_the_approle_secret_and_leases_and_skips_external_secrets(world, on, admin):
    from django.core.management import call_command

    create_secret(world, name="ext", provider="vault", value="value-ext-1234")
    create_secret(world, name="int", value="value-int-1234")
    dynamic_connection(world, on, admin)
    Connection.objects.get(name="bdd").login()
    before = (VaultConfig.objects.get(pk=1).key_id, list(VaultLease.objects.values_list("key_id", flat=True)))
    call_command("rotate_keys", stdout=__import__("io").StringIO())
    after = VaultConfig.objects.get(pk=1)
    assert after.key_id != before[0] and after.secret_id() == SECRET_ID
    assert {lease.key_id for lease in VaultLease.objects.all()} == {after.key_id}
    assert secret(world, "int").reveal() == "value-int-1234" and secret(world, "ext").reveal() == "value-ext-1234"
