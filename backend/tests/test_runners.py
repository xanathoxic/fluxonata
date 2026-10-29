import hashlib
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from audit.models import AuditEvent
from catalog.models import Action, ActionVersion
from runs import services
from runs.models import Attempt, Execution, Runner
from runs.runner_auth import RunnerThrottle
from tenancy.models import Environment, Role
from tests.conftest import add_action, client_for, make_user, make_workspace
from vault.models import Connection, Secret

P = "/api/v1/platform/runners/"
R = "/api/v1/runner"
WS = "/api/v1/workspaces/team-a"


@pytest.fixture
def boss(db):
    u = make_user("root")
    u.is_superuser = True
    u.save()
    return u


def new_runner(boss, **body):
    r = client_for(boss).post(P, {"name": "agent-1", "scope": "shared", **body}, format="json")
    assert r.status_code == 201, r.content
    return r.json()


def agent(token):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return c


def queue(world, ws_key="ws_a", version=None, inputs=None):
    ws = world[ws_key]
    env = Environment.objects.get(workspace=ws, kind="test")
    return services.enqueue(
        workspace=ws,
        environment=env,
        action_version=version or world["version_a" if ws_key == "ws_a" else "version_b"],
        user=world["designer"],
        inputs=inputs or {"text": "a b"},
    )


def ask(c, wait=0):
    return c.post(f"{R}/jobs/request/", {"wait": wait}, format="json")


# ------------------------------------------------------------------ tokens
def test_token_is_shown_once_and_only_its_hash_is_stored(world, boss):
    data = new_runner(boss)
    token = data["token"]
    assert token.startswith("orr_") and "plus jamais" in data["token_notice"]
    r = Runner.objects.get(name="agent-1")
    assert r.token_hash == hashlib.sha256(token.encode()).hexdigest() and token not in (r.token_hash, r.token_prefix)
    assert r.token_prefix == token[:12]
    listing = client_for(boss).get(P).json()["results"]
    assert "token" not in listing[0] and listing[0]["token_prefix"] == token[:12]
    assert AuditEvent.objects.filter(action="runner.create").exists()
    assert token not in str(list(AuditEvent.objects.values_list("data", flat=True)))


def test_rotation_invalidates_the_old_token_and_deletion_revokes(world, boss):
    d = new_runner(boss)
    old = d["token"]
    assert agent(old).get(f"{R}/me/").status_code == 200
    new = client_for(boss).post(f"{P}{d['id']}/rotate-token/").json()["token"]
    assert (
        new != old and agent(old).get(f"{R}/me/").status_code == 401 and agent(new).get(f"{R}/me/").status_code == 200
    )
    assert client_for(boss).delete(f"{P}{d['id']}/").status_code == 204
    assert agent(new).get(f"{R}/me/").status_code == 401  # revoked at once
    assert {"runner.rotate_token", "runner.delete"} <= set(AuditEvent.objects.values_list("action", flat=True))


def test_runner_endpoints_refuse_everything_but_a_runner_token(world, boss):
    assert APIClient().get(f"{R}/me/").status_code in (401, 403)
    assert agent("orr_nope").get(f"{R}/me/").status_code == 401
    assert client_for(boss).get(f"{R}/me/").status_code in (401, 403)  # a platform administrator is not a runner
    assert client_for(world["designer"]).post(f"{R}/jobs/request/", {}, format="json").status_code in (401, 403)
    token = new_runner(boss)["token"]
    # …and a runner token opens nothing else
    assert agent(token).get("/api/v1/auth/me/").status_code in (401, 403)
    assert agent(token).get(f"{WS}/workflows/").status_code in (401, 403)
    assert agent(token).get(P).status_code in (401, 403)


def test_contact_information_is_recorded(world, boss):
    token = new_runner(boss)["token"]
    c = agent(token)
    assert c.post(f"{R}/me/", {"version": "0.1.0", "platform": "linux/amd64"}, format="json").status_code == 200
    r = Runner.objects.get(name="agent-1")
    assert r.agent_version == "0.1.0" and r.platform == "linux/amd64" and r.last_contact_at is not None
    row = client_for(boss).get(P).json()["results"][0]
    assert row["status"] == "online"


# ------------------------------------------------------------------ management and who may do what
def test_only_platform_admins_manage_platform_runners(world, boss):
    d = new_runner(boss)
    for who in ("designer", "viewer", "outsider"):
        c = client_for(world[who])
        assert c.get(P).status_code == 403
        assert c.post(P, {"name": "x-runner"}, format="json").status_code == 403
        assert c.patch(f"{P}{d['id']}/", {"paused": True}, format="json").status_code == 403
        assert c.delete(f"{P}{d['id']}/").status_code == 403
        assert c.post(f"{P}{d['id']}/rotate-token/").status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {"name": "AB"},
        {"name": "Bad Name"},
        {"name": "ok-name", "scope": "dedicated"},  # dedicated needs a space
        {"name": "ok-name", "scope": "dedicated", "workspaces": ["nope"]},
        {"name": "ok-name", "tags": ["Bad Tag"]},
        {"name": "ok-name", "runtimes": ["ai"]},
        {"name": "ok-name", "runtimes": []},
        {"name": "ok-name", "max_concurrent": 99},
    ],
)
def test_runner_validation(world, boss, body):
    assert client_for(boss).post(P, body, format="json").status_code == 400


def test_unique_name_and_dedicated_runner_creation(world, boss):
    new_runner(boss)
    assert client_for(boss).post(P, {"name": "agent-1"}, format="json").status_code == 400
    d = (
        client_for(boss)
        .post(
            P,
            {
                "name": "pour-a",
                "scope": "dedicated",
                "workspaces": ["team-a"],
                "tags": ["gpu", "gpu"],
                "runtimes": ["python"],
            },
            format="json",
        )
        .json()
    )
    assert (
        d["workspaces"] == ["team-a"]
        and d["tags"] == ["gpu"]
        and d["runtimes"] == ["python"]
        and d["status"] == "never"
    )


def test_platform_can_pause_edit_and_retarget(world, boss):
    d = new_runner(boss)
    c = client_for(boss)
    r = c.patch(f"{P}{d['id']}/", {"paused": True, "tags": ["linux"], "max_concurrent": 4}, format="json").json()
    assert (
        r["paused"]
        and r["status"] == "paused"
        and r["tags"] == ["linux"]
        and r["max_concurrent"] == 4
        and r["scope"] == "shared"
    )
    r = c.patch(f"{P}{d['id']}/", {"scope": "dedicated", "workspaces": ["team-b"]}, format="json").json()
    assert r["scope"] == "dedicated" and r["workspaces"] == ["team-b"]
    assert c.patch(f"{P}{d['id']}/", {"scope": "dedicated", "workspaces": []}, format="json").status_code == 400


def test_space_administrators_manage_only_their_own_dedicated_runners(world, boss):
    admin_a = make_user("ada", world["ws_a"], Role.ADMIN)
    admin_b = make_user("ben", world["ws_b"], Role.ADMIN)
    c = client_for(admin_a)
    created = c.post(
        f"{WS}/runners/", {"name": "runner-de-a", "scope": "shared", "workspaces": ["team-b"]}, format="json"
    )  # asked for more: ignored
    assert created.status_code == 201
    d = created.json()
    assert d["scope"] == "dedicated" and d["workspaces"] == ["team-a"] and d["token"].startswith("orr_")
    listing = c.get(f"{WS}/runners/").json()
    assert [r["name"] for r in listing["results"]] == ["runner-de-a"] and listing["results"][0]["managed"] is True
    # edit / rotate / delete own runner; cannot widen it
    assert c.patch(
        f"{WS}/runners/{d['id']}/", {"paused": True, "scope": "shared", "workspaces": ["team-b"]}, format="json"
    ).json()["workspaces"] == ["team-a"]
    assert c.post(f"{WS}/runners/{d['id']}/rotate-token/").status_code == 200
    # another space's administrator does not even see it; a platform shared runner is invisible too
    shared = new_runner(boss)
    assert client_for(admin_b).get("/api/v1/workspaces/team-b/runners/").json()["results"] == []
    assert (
        client_for(admin_b)
        .patch(f"/api/v1/workspaces/team-b/runners/{d['id']}/", {"paused": False}, format="json")
        .status_code
        == 404
    )
    assert c.patch(f"{WS}/runners/{shared['id']}/", {"paused": True}, format="json").status_code == 404
    # a runner the platform dedicated to several spaces is read-only for a space administrator
    multi = (
        client_for(boss)
        .post(P, {"name": "multi-ab", "scope": "dedicated", "workspaces": ["team-a", "team-b"]}, format="json")
        .json()
    )
    row = [r for r in c.get(f"{WS}/runners/").json()["results"] if r["name"] == "multi-ab"][0]
    assert row["managed"] is False
    assert c.patch(f"{WS}/runners/{multi['id']}/", {"paused": True}, format="json").status_code == 403
    assert c.delete(f"{WS}/runners/{multi['id']}/").status_code == 403
    assert c.delete(f"{WS}/runners/{d['id']}/").status_code == 204


def test_space_runner_routes_need_the_administer_right_and_membership(world):
    for who, code in (("designer", 403), ("viewer", 403), ("outsider", 404)):
        c = client_for(world[who])
        assert c.get(f"{WS}/runners/").status_code == code
        assert c.post(f"{WS}/runners/", {"name": "runner-x"}, format="json").status_code == code
        assert c.patch(f"{WS}/runner-settings/", {"use_shared_runners": False}, format="json").status_code == code


def test_shared_runner_setting_of_a_space(world):
    admin = make_user("ada", world["ws_a"], Role.ADMIN)
    c = client_for(admin)
    assert c.get(f"{WS}/runner-settings/").json() == {"use_shared_runners": True}
    assert c.patch(f"{WS}/runner-settings/", {"use_shared_runners": False}, format="json").json() == {
        "use_shared_runners": False
    }
    assert c.patch(f"{WS}/runner-settings/", {"use_shared_runners": "non"}, format="json").status_code == 400
    assert AuditEvent.objects.filter(action="workspace.runner_settings").exists()


# ------------------------------------------------------------------ who may claim what
def runner_obj(name="x", **kw):
    token = "orr_" + name + "0" * 40
    defaults = dict(
        scope="shared",
        runtimes=["python", "node", "typescript", "shell", "powershell"],
        tags=[],
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        token_prefix=token[:12],
    )
    return Runner.objects.create(name=name, **{**defaults, **kw})


def test_dedicated_runner_serves_only_its_spaces_shared_runner_only_spaces_that_allow_it(world):
    ex_a, ex_b = queue(world, "ws_a"), queue(world, "ws_b")
    only_a = runner_obj(name="only-a", scope="dedicated")
    only_a.workspaces.add(world["ws_a"])
    got, _ = services.claim_next("runner:only-a", only_a)
    assert got.pk == ex_a.pk and got.runner_id == only_a.pk and got.worker_id == "runner:only-a"
    assert services.claim_next("runner:only-a", only_a) is None  # team-b is not its business
    # a space that refuses shared runners is invisible to the shared pool (runners AND built-in workers)…
    world["ws_b"].use_shared_runners = False
    world["ws_b"].save()
    shared = runner_obj(name="shared")
    assert services.claim_next("runner:shared", shared) is None and services.claim_next("w1") is None
    # …but its dedicated runner serves it
    only_b = runner_obj(name="only-b", scope="dedicated")
    only_b.workspaces.add(world["ws_b"])
    assert services.claim_next("runner:only-b", only_b)[0].pk == ex_b.pk


def test_shared_runner_and_builtin_workers_share_the_pool(world):
    queue(world, "ws_a")
    queue(world, "ws_b")
    shared = runner_obj(name="shared")
    first, _ = services.claim_next("runner:shared", shared)
    second, _ = services.claim_next("w1")  # the built-in worker takes the other one
    assert {first.workspace.slug, second.workspace.slug} == {"team-a", "team-b"} and second.runner_id is None


def test_runtimes_tags_pause_concurrency_and_ai(world):
    ex = queue(world)
    node_only = runner_obj(name="node-only", runtimes=["node"])
    assert services.claim_next("x", node_only) is None  # python job, node-only runner
    needs_gpu = add_action(
        world["ws_a"],
        {
            "slug": "gpu",
            "name": "gpu",
            "version": "1.0.0",
            "source": "def run(i): return {}",
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {"type": "object", "properties": {}},
            "limits": {},
        },
        required_tags=["gpu"],
    )
    gpu_ex = queue(world, version=needs_gpu)
    plain = runner_obj(name="plain")
    assert services.claim_next("x", plain)[0].pk == ex.pk  # takes the untagged job…
    plain2 = runner_obj(name="plain2")
    assert services.claim_next("x", plain2) is None  # …the tagged one needs a tag no runner here has
    assert services.claim_next("w1") is None  # built-in workers carry no tag either
    tagged = runner_obj(name="tagged", tags=["gpu", "linux"])
    assert services.claim_next("x", tagged)[0].pk == gpu_ex.pk
    # paused and over-concurrency runners get nothing
    busy = runner_obj(name="busy", max_concurrent=1)
    queue(world)
    queue(world)
    assert services.claim_next("x", busy) is not None and services.claim_next("x", busy) is None
    paused = runner_obj(name="paused", paused=True)
    assert services.claim_next("x", paused) is None
    # AI steps never leave the platform
    from ai.builtin import get_ai_version

    ai_ex = services.enqueue(
        workspace=world["ws_a"],
        environment=Environment.objects.get(workspace=world["ws_a"], kind="test"),
        action_version=get_ai_version(world["ws_a"]),
        user=world["designer"],
        inputs={"prompt": "x", "output_schema": {}, "provider": "p"},
    )
    anyone = runner_obj(name="anyone")
    Execution.objects.filter(status="queued").exclude(pk=ai_ex.pk).update(status="cancelled")
    assert services.claim_next("x", anyone) is None
    assert services.claim_next("w1")[0].pk == ai_ex.pk  # a built-in worker runs it


# ------------------------------------------------------------------ the job protocol
def test_job_lifecycle_through_the_http_protocol(world, boss):
    d = new_runner(boss)
    c = agent(d["token"])
    assert ask(c).status_code == 204  # nothing to do
    ex = queue(world)
    r = ask(c)
    assert r.status_code == 200
    job = r.json()
    assert (
        job["job_id"] == str(ex.id)
        and job["attempt"] == 1
        and job["runtime"] == "python"
        and job["inputs"] == {"text": "a b"}
    )
    assert job["source"] and job["env"] == {} and job["timeout_seconds"] >= 1 and job["workspace"] == "team-a"
    hb = c.post(f"{R}/jobs/{ex.id}/heartbeat/", {"attempt": 1}, format="json")
    assert hb.status_code == 200 and hb.json() == {"cancel": False}
    done = c.post(
        f"{R}/jobs/{ex.id}/complete/",
        {
            "attempt": 1,
            "ok": True,
            "result": {"characters": 3, "words": 2, "longest_word": "a", "uppercase": None},
            "logs": "ok",
            "exit_code": 0,
        },
        format="json",
    )
    assert done.status_code == 200
    ex.refresh_from_db()
    assert ex.status == "succeeded" and ex.runner.name == "agent-1" and ex.result["words"] == 2
    att = Attempt.objects.get(execution=ex)
    assert att.worker_id == "runner:agent-1" and att.logs == "ok"
    # it cannot be completed twice, nor after the fact
    again = c.post(f"{R}/jobs/{ex.id}/complete/", {"attempt": 1, "ok": True, "result": {}}, format="json")
    assert again.status_code == 409
    assert c.post(f"{R}/jobs/{ex.id}/heartbeat/", {"attempt": 1}, format="json").status_code == 409


def test_a_runner_never_sees_another_runners_job(world, boss):
    a, b = new_runner(boss), client_for(boss).post(P, {"name": "agent-2", "scope": "shared"}, format="json").json()
    ex = queue(world)
    assert ask(agent(a["token"])).status_code == 200
    other = agent(b["token"])
    assert other.post(f"{R}/jobs/{ex.id}/heartbeat/", {"attempt": 1}, format="json").status_code == 404
    assert (
        other.post(f"{R}/jobs/{ex.id}/complete/", {"attempt": 1, "ok": True, "result": {}}, format="json").status_code
        == 404
    )
    ex.refresh_from_db()
    assert ex.status == "running"


def test_output_is_validated_by_the_platform_not_trusted_from_the_runner(world, boss):
    c = agent(new_runner(boss)["token"])
    ex = queue(world)
    ask(c)
    c.post(f"{R}/jobs/{ex.id}/complete/", {"attempt": 1, "ok": True, "result": {"unexpected": 1}}, format="json")
    ex.refresh_from_db()
    assert ex.status == "failed" and ex.error["code"] == "OUTPUT_INVALID"
    ex2 = queue(world)
    ask(c)
    c.post(
        f"{R}/jobs/{ex2.id}/complete/",
        {
            "attempt": 1,
            "ok": False,
            "error": {"code": "BOOM", "message": "x" * 900, "evil": "ignored"},
            "logs": "l" * 200_000,
        },
        format="json",
    )
    ex2.refresh_from_db()
    assert (
        ex2.status == "failed"
        and ex2.error["code"] == "BOOM"
        and len(ex2.error["message"]) == 300
        and "evil" not in ex2.error
    )
    assert len(Attempt.objects.get(execution=ex2).logs) <= 64 * 1024


def test_result_size_is_capped(world, boss, settings):
    c = agent(new_runner(boss)["token"])
    ex = queue(world)
    ask(c)
    big = {"characters": 1, "words": 1, "longest_word": "x" * (settings.ORCH_MAX_RESULT_BYTES + 10), "uppercase": None}
    c.post(f"{R}/jobs/{ex.id}/complete/", {"attempt": 1, "ok": True, "result": big}, format="json")
    ex.refresh_from_db()
    assert ex.status == "failed" and ex.error["code"] == "RESULT_TOO_LARGE"


def secret_action(world):
    ws = world["ws_a"]
    action = Action.objects.create(workspace=ws, slug="needs-secret", name="needs-secret")
    version = ActionVersion.objects.create(
        action=action,
        version="1.0.0",
        source="def run(i): return {}",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {"echo": {"type": "string"}}},
        secrets_required=[{"slot": "api", "env": "API_TOKEN"}],
        limits={"timeout_seconds": 20, "memory_mb": 64},
    )
    s = Secret(workspace=ws, name="api-secret")
    s.set_value("sk-live-very-secret-123456")
    s.save()
    Connection.objects.create(workspace=ws, name="api", label="API", kind="generic", secret=s)
    return version


def test_secrets_reach_only_the_runner_that_claimed_the_job_and_are_masked_by_the_platform(world, boss):
    version = secret_action(world)
    c = agent(new_runner(boss)["token"])
    env = Environment.objects.get(workspace=world["ws_a"], kind="test")
    ex = services.enqueue(
        workspace=world["ws_a"],
        environment=env,
        action_version=version,
        user=world["designer"],
        inputs={},
        connections={"api": "api"},
    )
    job = ask(c).json()
    assert job["env"] == {"API_TOKEN": "sk-live-very-secret-123456"}
    assert AuditEvent.objects.filter(action="secret.inject").count() == 1
    # the runner echoes the secret back (a leaking action): the platform masks it in logs, result and error
    c.post(
        f"{R}/jobs/{ex.id}/complete/",
        {
            "attempt": 1,
            "ok": True,
            "result": {"echo": "sk-live-very-secret-123456"},
            "logs": "token sk-live-very-secret-123456",
        },
        format="json",
    )
    ex.refresh_from_db()
    assert ex.result == {"echo": "***"} and "sk-live" not in Attempt.objects.get(execution=ex).logs
    assert (
        AuditEvent.objects.filter(action="secret.inject").count() == 1
    )  # recomputing the mask is not a second injection


def test_missing_connection_fails_the_job_and_the_runner_moves_on(world, boss):
    version = secret_action(world)
    c = agent(new_runner(boss)["token"])
    env = Environment.objects.get(workspace=world["ws_a"], kind="test")
    bad = services.enqueue(
        workspace=world["ws_a"],
        environment=env,
        action_version=version,
        user=world["designer"],
        inputs={},
        connections={},
    )
    good = queue(world)
    job = ask(c).json()
    assert job["job_id"] == str(good.id)
    bad.refresh_from_db()
    assert bad.status == "failed" and bad.error["code"] == "CONNECTION_MISSING"


def test_fencing_cancel_and_lost_runner(world, boss):
    c = agent(new_runner(boss)["token"])
    ex = queue(world)
    ask(c)
    assert (
        c.post(f"{R}/jobs/{ex.id}/complete/", {"attempt": 7, "ok": True, "result": {}}, format="json").status_code
        == 409
    )  # wrong attempt
    services.request_cancel(Execution.objects.get(pk=ex.pk))
    assert c.post(f"{R}/jobs/{ex.id}/heartbeat/", {"attempt": 1}, format="json").json() == {"cancel": True}
    # the runner disappears: the lease expires, the job is queued again and its late answer is ignored
    Execution.objects.filter(pk=ex.pk).update(
        cancel_requested=False, lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert services.reconcile_expired() == 1
    assert c.post(f"{R}/jobs/{ex.id}/heartbeat/", {"attempt": 1}, format="json").status_code == 409
    assert (
        c.post(
            f"{R}/jobs/{ex.id}/complete/",
            {"attempt": 1, "ok": True, "result": {"characters": 1, "words": 1, "longest_word": "a"}},
            format="json",
        ).status_code
        == 409
    )
    assert ask(c).json()["attempt"] == 2  # claimed again, new attempt


def test_pausing_a_runner_stops_new_jobs_immediately(world, boss):
    d = new_runner(boss)
    c = agent(d["token"])
    client_for(boss).patch(f"{P}{d['id']}/", {"paused": True}, format="json")
    queue(world)
    assert ask(c).status_code == 204
    client_for(boss).patch(f"{P}{d['id']}/", {"paused": False}, format="json")
    assert ask(c).status_code == 200


class FakeClock:
    """Replaces the `time` seen by runs.runner_api only (patching the real module would break every other caller)."""

    def __init__(self):
        self.now, self.sleeps = 0.0, 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps += 1
        self.now += seconds


def test_long_poll_waits_then_gives_up_and_deleted_runner_gets_401(world, boss, settings, monkeypatch):
    settings.ORCH_RUNNER_LONG_POLL_SECONDS = 3
    clock = FakeClock()
    monkeypatch.setattr("runs.runner_api.time", clock)
    d = new_runner(boss)
    c = agent(d["token"])
    assert ask(c, wait=60).status_code == 204  # asked for 60 s, the server caps it
    assert clock.now == 3.0 and clock.sleeps == 3  # it really waited (polling once a second), never longer than the cap
    Runner.objects.filter(pk=d["id"]).delete()
    assert ask(c).status_code == 401


def test_requests_are_throttled_per_runner(world, boss, monkeypatch):
    monkeypatch.setattr(RunnerThrottle, "THROTTLE_RATES", {"runner": "3/min"})
    a, b = (
        agent(new_runner(boss)["token"]),
        agent(client_for(boss).post(P, {"name": "agent-2", "scope": "shared"}, format="json").json()["token"]),
    )
    assert [a.get(f"{R}/me/").status_code for _ in range(4)] == [200, 200, 200, 429]
    assert b.get(f"{R}/me/").status_code == 200  # another runner is not affected


def test_runner_health_item_for_a_space_without_runner(world, boss):
    from core.health import summary

    ws = world["ws_a"]
    assert [i for i in summary(ws) if i["key"] == "runners"][0]["status"] == "ok"
    ws.use_shared_runners = False
    ws.save()
    item = [i for i in summary(ws) if i["key"] == "runners"][0]
    assert item["status"] == "error" and "Aucun runner dédié actif" in item["message"]
    r = runner_obj(name="ded", scope="dedicated", last_contact_at=timezone.now())
    r.workspaces.add(ws)
    assert [i for i in summary(ws) if i["key"] == "runners"][0]["status"] == "ok"
    Runner.objects.filter(pk=r.pk).update(paused=True)
    assert [i for i in summary(ws) if i["key"] == "runners"][0]["status"] == "error"


def test_required_tags_on_actions_are_validated_and_survive_rollback(world):
    c = client_for(world["designer"])
    body = {
        "slug": "tagged",
        "name": "T",
        "description": "Action de test qui ne fait rien et renvoie un résultat vide.",
        "version": {
            "source": "def run(i):\n    return {}\n",
            "input_schema": {"type": "object", "properties": {}, "description": "Cette action n'a aucune entrée."},
            "output_schema": {"type": "object", "properties": {}, "description": "Cette action n'a aucune sortie."},
            "required_tags": ["Bad Tag"],
        },
    }
    assert c.post(f"{WS}/library/actions/", body, format="json").status_code == 400
    body["version"]["required_tags"] = ["windows", "windows", "gpu"]
    r = c.post(f"{WS}/library/actions/", body, format="json")
    assert r.status_code == 201
    assert ActionVersion.objects.get(action__slug="tagged").required_tags == ["gpu", "windows"]
    c.post(f"{WS}/actions/tagged/versions/", {**body["version"], "bump": "patch"}, format="json")
    rb = c.post(f"{WS}/actions/tagged/rollback/", {"to_version": "1.0.0"}, format="json")
    assert rb.status_code == 201 and rb.json()["required_tags"] == ["gpu", "windows"]


def test_other_spaces_runner_listing_is_isolated(world, boss):
    other = make_workspace("team-c")
    d = (
        client_for(boss)
        .post(P, {"name": "only-c", "scope": "dedicated", "workspaces": ["team-c"]}, format="json")
        .json()
    )
    admin_a = make_user("ada", world["ws_a"], Role.ADMIN)
    assert client_for(admin_a).get(f"{WS}/runners/").json()["results"] == []
    assert client_for(admin_a).patch(f"{WS}/runners/{d['id']}/", {"paused": True}, format="json").status_code == 404
    assert other and d
