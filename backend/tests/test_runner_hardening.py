"""Garanties du mode agents, établies par des essais : attribution unique, jamais de reprise silencieuse, résultats tardifs, secrets limités à la tâche,
artefacts isolés. Chaque test décrit un scénario d'attaque ou de panne et ce que la plate-forme doit refuser ou conserver."""

import json
import threading
from datetime import timedelta

import pytest
from django.db import connections
from django.utils import timezone

from audit.models import AuditEvent
from runs import services
from runs.models import Artifact, Attempt, Execution, ExecutionEvent
from tenancy.models import Environment
from tests.conftest import make_user
from tests.test_runners import (
    P,
    R,
    agent,
    ask,
    client_for,
    new_runner,
    queue,
    runner_obj,
    secret_action,
)


@pytest.fixture
def boss(db):
    u = make_user("root")
    u.is_superuser = True
    u.save()
    return u


SENTINEL = "sk-live-very-secret-123456"  # la valeur posée par `secret_action`


def complete(c, ex, attempt=1, **body):
    return c.post(f"{R}/jobs/{ex.id}/complete/", {"attempt": attempt, "ok": True, "result": {}, **body}, format="json")


def expire(ex):
    Execution.objects.filter(pk=ex.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
    assert services.reconcile_expired() == 1


def secret_job(world, c):
    version = secret_action(world)
    env = Environment.objects.get(workspace=world["ws_a"], kind="test")
    ex = services.enqueue(
        workspace=world["ws_a"],
        environment=env,
        action_version=version,
        user=world["designer"],
        inputs={},
        connections={"api": "api"},
    )
    assert ask(c).json()["env"] == {"API_TOKEN": SENTINEL}
    return ex


def database_text():
    """Tout le texte que la plate-forme garde d'une exécution : exécutions, tentatives, événements, audit."""
    rows = []
    for model in (Execution, Attempt, ExecutionEvent, AuditEvent):
        rows += list(model.objects.values())
    return json.dumps(rows, default=str)


# --- attribution unique : worker intégré et runners distants ------------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_a_builtin_worker_and_remote_runners_never_claim_the_same_attempt(world, settings):
    settings.ORCH_MAX_CONCURRENT_PER_WORKSPACE = 100  # le plafond par espace n'est pas l'objet de ce test
    queued = [queue(world, "ws_a") for _ in range(3)] + [queue(world, "ws_b") for _ in range(3)]
    runners = [runner_obj(name=f"r{i}", max_concurrent=20) for i in range(4)]
    claimed, errors, start = [], [], threading.Barrier(12)

    def claim(who, runner):
        try:
            start.wait(10)
            for _ in range(3):
                got = services.claim_next(who, runner)
                if got:
                    claimed.append((str(got[0].pk), got[1], who))
        except Exception as exc:  # noqa: BLE001 - rapporté par l'assertion ci-dessous
            errors.append(repr(exc))
        finally:
            connections.close_all()

    threads = [threading.Thread(target=claim, args=(f"runner:r{i}", runners[i % 4])) for i in range(8)]
    threads += [threading.Thread(target=claim, args=(f"worker-{i}", None)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors
    while got := services.claim_next(
        "drain", None
    ):  # SKIP LOCKED peut laisser passer une tâche : on termine sans concurrence
        claimed.append((str(got[0].pk), got[1], "drain"))
    ids = [c[0] for c in claimed]
    assert sorted(ids) == sorted(str(e.pk) for e in queued)  # chaque tâche attribuée, et une seule fois
    assert {c[1] for c in claimed} == {1} and len(
        {c[2] for c in claimed}
    ) > 1  # une seule tentative par tâche, prises par plusieurs
    for e in queued:
        assert Attempt.objects.filter(execution=e).count() == 1 and Execution.objects.get(pk=e.pk).attempt_count == 1


# --- aucune reprise silencieuse -------------------------------------------------------------------------------------------------------------
def test_a_dedicated_space_is_never_taken_over_by_anyone_who_is_not_entitled(world):
    from tests.conftest import add_action

    ws_a, ws_b = world["ws_a"], world["ws_b"]
    ws_a.use_shared_runners = False
    ws_a.save()
    gpu = add_action(
        ws_a,
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
    ex = queue(world, "ws_a", version=gpu)
    dedicated_b = runner_obj(name="dedicated-b", scope="dedicated", tags=["gpu"])  # dédié à un AUTRE espace
    dedicated_b.workspaces.add(ws_b)
    no_tag = runner_obj(name="no-tag", scope="dedicated")  # dédié au bon espace mais sans l'étiquette
    no_tag.workspaces.add(ws_a)
    wrong_runtime = runner_obj(name="node-only", scope="dedicated", runtimes=["node"], tags=["gpu"])
    wrong_runtime.workspaces.add(ws_a)
    shared = runner_obj(name="shared", tags=["gpu"])
    for who, runner in (
        ("runner:dedicated-b", dedicated_b),
        ("runner:no-tag", no_tag),
        ("runner:node-only", wrong_runtime),
        ("runner:shared", shared),
        ("worker-intégré", None),
    ):
        assert services.claim_next(who, runner) is None, who
    ex.refresh_from_db()
    assert ex.status == "queued" and ex.runner_id is None and ex.attempt_count == 0  # elle attend, rien n'a démarré
    ok = runner_obj(name="ok", scope="dedicated", tags=["gpu"])
    ok.workspaces.add(ws_a)
    assert services.claim_next("runner:ok", ok)[0].pk == ex.pk  # dès qu'un runner compatible existe


def test_a_paused_or_deleted_dedicated_runner_does_not_hand_its_space_to_the_pool(world):
    world["ws_a"].use_shared_runners = False
    world["ws_a"].save()
    ex = queue(world, "ws_a")
    mine = runner_obj(name="mine", scope="dedicated", paused=True)
    mine.workspaces.add(world["ws_a"])
    assert services.claim_next("runner:mine", mine) is None and services.claim_next("w") is None
    mine.delete()
    assert services.claim_next("w") is None and services.claim_next("runner:shared", runner_obj(name="s")) is None
    ex.refresh_from_db()
    assert ex.status == "queued"


# --- résultats tardifs et artefacts ---------------------------------------------------------------------------------------------------------
@pytest.fixture
def store(settings, tmp_path):
    settings.ORCH_ARTIFACTS_DIR = tmp_path / "artefacts"
    settings.ORCH_ARTIFACTS_STORE = "fs"
    return settings.ORCH_ARTIFACTS_DIR


def files_in(store):
    return sorted(p for p in store.rglob("*") if p.is_file()) if store.exists() else []


VALID = {"characters": 1, "words": 1, "longest_word": "a"}  # respecte le contrat de sortie de l'action de test


def one(name="rapport.txt", text="bonjour"):
    return {"result": {**VALID, "__artifacts__": [{"name": name, "text": text}]}}


def test_a_late_result_cannot_add_files_to_the_attempt_that_replaced_it(world, boss, store):
    c = agent(new_runner(boss)["token"])
    ex = queue(world)
    assert ask(c).json()["attempt"] == 1
    expire(ex)
    assert (
        ask(c).json()["attempt"] == 2
    )  # le MÊME runner reprend la tâche : sa réponse de la tentative 1 arrive trop tard
    assert complete(c, ex, 1, **one("tardif.txt")).status_code == 409
    assert Artifact.objects.filter(execution=ex).count() == 0 and files_in(store) == []
    assert complete(c, ex, 2, **one("bon.txt")).status_code == 200
    assert [a.name for a in Artifact.objects.filter(execution=ex)] == ["bon.txt"] and len(files_in(store)) == 1


def test_a_late_result_for_a_requeued_or_finished_run_adds_nothing(world, boss, store):
    c = agent(new_runner(boss)["token"])
    ex = queue(world)
    ask(c)
    expire(ex)  # remise en file, pas encore reprise
    assert complete(c, ex, 1, **one()).status_code == 409 and Artifact.objects.count() == 0 and files_in(store) == []
    ask(c)
    assert complete(c, ex, 2, **one("a.txt")).status_code == 200
    assert complete(c, ex, 2, **one("b.txt")).status_code == 409  # envoyé deux fois
    assert complete(c, ex, 1, **one("c.txt")).status_code == 409  # tentative périmée, exécution déjà terminée
    assert [a.name for a in Artifact.objects.all()] == ["a.txt"] and len(files_in(store)) == 1


def test_a_cancelled_run_keeps_no_late_artifacts(world, boss, store):
    c = agent(new_runner(boss)["token"])
    ex = queue(world)
    ask(c)
    services.request_cancel(Execution.objects.get(pk=ex.pk))
    assert complete(c, ex, 1, cancelled=True, ok=False, result=None).status_code == 200
    assert Execution.objects.get(pk=ex.pk).status == "cancelled"
    assert complete(c, ex, 1, **one()).status_code == 409 and Artifact.objects.count() == 0


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        ("../../etc/passwd", "passwd"),
        ("/etc/shadow", "shadow"),
        ("..\\..\\windows\\system32", "system32"),
        ("a/b/c.txt", "c.txt"),
        ("....", "artefact"),
        ("sub dir/rapport final?.csv", "rapport_final_.csv"),
    ],
)
def test_an_artifact_name_can_never_leave_the_run_directory(world, boss, store, given, kept):
    c = agent(new_runner(boss)["token"])
    ex = queue(world)
    ask(c)
    assert complete(c, ex, **one(given)).status_code == 200
    art = Artifact.objects.get(execution=ex)
    assert art.name == kept
    assert art.storage_key.startswith(f"{ex.workspace_id}/{ex.id}/") and ".." not in art.storage_key
    (path,) = files_in(store)
    assert (
        store.resolve() in path.resolve().parents
        and path.resolve().parent == (store / str(ex.workspace_id) / str(ex.id)).resolve()
    )


def test_artifact_count_and_size_are_bounded_for_a_remote_runner_too(world, boss, store):
    c = agent(new_runner(boss)["token"])
    ex = queue(world)
    ask(c)
    too_many = {"result": {**VALID, "__artifacts__": [{"name": f"f{i}", "text": "x"} for i in range(11)]}}
    assert complete(c, ex, **too_many).status_code == 200
    ex.refresh_from_db()
    assert ex.status == "failed" and ex.error["code"] == "ARTIFACT_INVALID" and files_in(store) == []
    ex2 = queue(world)
    ask(c)
    big = {
        "result": {**VALID, "__artifacts__": [{"name": "gros", "text": "x" * 1_200_000}]}
    }  # au-delà des 1 Mo de résultat
    assert complete(c, ex2, **big).status_code == 200
    ex2.refresh_from_db()
    assert ex2.status == "failed" and ex2.error["code"] == "RESULT_TOO_LARGE" and files_in(store) == []


def test_a_runner_cannot_read_artifacts_of_any_run(world, boss, store):
    c = agent(new_runner(boss)["token"])
    ex = queue(world)
    ask(c)
    complete(c, ex, **one())
    art = Artifact.objects.get(execution=ex)
    for url in (
        f"/api/v1/workspaces/team-a/executions/{ex.id}/artifacts/",
        f"/api/v1/workspaces/team-a/artifacts/{art.id}/",
        f"/api/v1/workspaces/team-a/artifacts/{art.id}/download/",
        f"{R}/artifacts/{art.id}/",
    ):
        assert c.get(url).status_code in (401, 403, 404), url


# --- jeton : rotation et révocation en cours de tâche ---------------------------------------------------------------------------------------
def test_rotation_stops_the_old_token_everywhere_at_once_and_the_new_one_finishes_the_job(world, boss):
    d = new_runner(boss)
    old = agent(d["token"])
    ex = queue(world)
    assert ask(old).status_code == 200
    new_token = client_for(boss).post(f"{P}{d['id']}/rotate-token/").json()["token"]
    assert ask(old).status_code == 401  # plus aucune attribution avec l'ancien jeton
    assert old.post(f"{R}/jobs/{ex.id}/heartbeat/", {"attempt": 1}, format="json").status_code == 401
    assert complete(old, ex).status_code == 401
    new = agent(new_token)
    assert new.post(f"{R}/jobs/{ex.id}/heartbeat/", {"attempt": 1}, format="json").status_code == 200
    assert complete(new, ex).status_code == 200


def test_revocation_during_a_job_requeues_a_safe_action_and_marks_an_unsafe_one_unknown(world, boss):
    d = new_runner(boss)
    c = agent(d["token"])
    safe, unsafe = (
        queue(world, "ws_a"),
        queue(world, "ws_b"),
    )  # deux versions distinctes : seule la seconde a un effet externe
    from catalog.models import ActionVersion

    ActionVersion.objects.filter(pk=unsafe.action_version_id).update(side_effects="external", idempotent=False)
    ask(c)
    ask(c)
    assert client_for(boss).delete(f"{P}{d['id']}/").status_code == 204
    assert c.post(f"{R}/jobs/{safe.id}/heartbeat/", {"attempt": 1}, format="json").status_code == 401
    assert complete(c, safe).status_code == 401
    safe.refresh_from_db()
    unsafe.refresh_from_db()
    assert safe.status == "running" and safe.runner_id is None  # plus rien ne prolonge son bail
    for ex in (safe, unsafe):
        Execution.objects.filter(pk=ex.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
    assert services.reconcile_expired() == 2
    safe.refresh_from_db()
    unsafe.refresh_from_db()
    assert safe.status == "queued"  # sûre : reprise
    assert (
        unsafe.status == "failed" and unsafe.error["code"] == "OUTCOME_UNKNOWN"
    )  # à effet externe : jamais rejouée à l'aveugle


# --- secrets limités à la tâche -------------------------------------------------------------------------------------------------------------
def test_the_job_receives_only_the_secrets_of_its_own_task_and_space(world, boss):
    from vault.models import Secret

    other = Secret(workspace=world["ws_b"], name="api-secret")  # même nom dans un autre espace
    other.set_value("valeur-de-l-autre-espace-777")
    other.save()
    leftover = Secret(workspace=world["ws_a"], name="non-demande")  # présent dans l'espace, non demandé par l'action
    leftover.set_value("valeur-non-demandee-888")
    leftover.save()
    c = agent(new_runner(boss)["token"])
    secret_job(world, c)  # `ask` vérifie env == {"API_TOKEN": SENTINEL} exactement
    blob = database_text()
    assert "valeur-de-l-autre-espace-777" not in blob and "valeur-non-demandee-888" not in blob


@pytest.mark.parametrize("outcome", ["succes", "echec", "annulation", "delai"])
def test_a_secret_is_masked_and_never_stored_after_any_outcome(world, boss, outcome):
    c = agent(new_runner(boss)["token"])
    ex = secret_job(world, c)
    assert SENTINEL not in database_text()  # rien de déchiffré n'est écrit à l'attribution
    leak = {"message": f"échec avec {SENTINEL}", "code": "X"}
    body = {
        "succes": {"ok": True, "result": {"echo": SENTINEL}, "logs": f"jeton {SENTINEL}"},
        "echec": {"ok": False, "result": None, "error": leak, "logs": f"jeton {SENTINEL}"},
        "annulation": {"ok": False, "result": None, "cancelled": True, "logs": f"jeton {SENTINEL}"},
        "delai": {"ok": False, "result": None, "error": {**leak, "code": "TIMEOUT"}, "logs": SENTINEL},
    }[outcome]
    assert complete(c, ex, **body).status_code == 200
    assert SENTINEL not in database_text()
    for response in (
        c.get(f"{R}/me/"),
        c.post(f"{R}/jobs/{ex.id}/heartbeat/", {"attempt": 1}, format="json"),
        ask(c),
    ):
        assert SENTINEL not in response.content.decode()


def test_a_late_result_cannot_change_a_new_attempt_nor_leak_its_secret(world, boss):
    c = agent(new_runner(boss)["token"])
    ex = secret_job(world, c)
    expire(ex)
    assert ask(c).json()["attempt"] == 2
    assert complete(c, ex, 1, ok=True, result={"echo": SENTINEL}, logs=SENTINEL).status_code == 409
    ex.refresh_from_db()
    assert ex.status == "running" and ex.result is None and ex.attempt_count == 2
    assert SENTINEL not in database_text()  # même la tentative perdue n'enregistre pas la valeur en clair
