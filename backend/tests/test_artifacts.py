import base64

import pytest

from catalog.demo_actions import HTTP_TEST, JSON_TRANSFORM, MAKE_REPORT
from catalog.models import Action, ActionVersion
from runs import services
from runs.artifacts import FileSystemStore, extract_artifacts, safe_name
from runs.models import Artifact
from runs.runners import DockerRunner, SubprocessRunner
from runs.worker import process_one
from tenancy.models import Environment
from tests.conftest import client_for
from tests.test_worker import docker_ready


@pytest.fixture(autouse=True)
def _artifact_dir(settings, tmp_path):
    settings.ORCH_ARTIFACTS_DIR = str(tmp_path / "artifacts")


def demo(ws, spec):
    action = Action.objects.create(workspace=ws, slug=spec["slug"], name=spec["name"])
    keys = ("source", "input_schema", "output_schema", "limits", "examples", "network_policy")
    return ActionVersion.objects.create(action=action, version="1.0.0", **{k: spec[k] for k in keys if k in spec})


def run_demo(world, spec, inputs, runner=None):
    ws = world["ws_a"]
    v = demo(ws, spec)
    ex, _ = services.create_execution(
        workspace=ws,
        environment=Environment.objects.get(workspace=ws, kind="test"),
        action_version=v,
        user=world["designer"],
        inputs=inputs,
    )
    process_one("w", runner or SubprocessRunner())
    ex.refresh_from_db()
    return ex


def test_json_transform_demo_connector(world):
    ex = run_demo(world, JSON_TRANSFORM, {"data": {"a": 1, "b": 2, "c": 3}, "fields": ["a", "c"]})
    assert ex.status == "succeeded" and ex.result == {"result": {"a": 1, "c": 3}, "kept": 2, "dropped": 1}


def test_report_action_creates_a_downloadable_artifact(world):
    ex = run_demo(world, MAKE_REPORT, {"title": "Bilan", "lines": ["un", "deux"]})
    assert ex.status == "succeeded" and ex.result == {"lines": 2}  # artifact key is not part of the result
    art = Artifact.objects.get()
    assert art.name == "rapport.md" and art.size > 0 and len(art.sha256) == 64
    c = client_for(world["designer"])
    listing = c.get(f"/api/v1/workspaces/team-a/executions/{ex.id}/artifacts/").json()["results"]
    assert listing[0]["name"] == "rapport.md"
    r = c.get(listing[0]["download_url"])
    assert r.status_code == 200 and b"# Bilan" in r.content and "attachment" in r["Content-Disposition"]
    assert r["X-Content-Type-Options"] == "nosniff" and "sandbox" in r["Content-Security-Policy"]


def test_artifact_access_is_workspace_scoped(world):
    ex = run_demo(world, MAKE_REPORT, {"title": "t", "lines": ["x"]})
    art = Artifact.objects.get()
    url = f"/api/v1/workspaces/team-a/artifacts/{art.id}/download/"
    assert client_for(world["viewer"]).get(url).status_code == 200  # member, view right
    assert client_for(world["outsider"]).get(url).status_code == 404  # other team
    # correct id but other workspace's URL: still not found (the id alone grants nothing)
    assert (
        client_for(world["outsider"]).get(f"/api/v1/workspaces/team-b/artifacts/{art.id}/download/").status_code == 404
    )
    assert (
        client_for(world["outsider"]).get(f"/api/v1/workspaces/team-a/executions/{ex.id}/artifacts/").status_code == 404
    )


def test_html_artifact_is_never_served_inline_as_html(world):
    ws = world["ws_a"]
    src = "def run(i):\n    return {'__artifacts__': [{'name': '../../evil.html', 'content_type': 'text/html', 'text': '<script>alert(1)</script>'}]}\n"
    spec = {**MAKE_REPORT, "slug": "evil", "source": src, "output_schema": {"type": "object"}}
    ex = run_demo(world, spec, {"title": "t", "lines": []})
    art = Artifact.objects.get(execution=ex)
    assert art.name == "evil.html"  # path components stripped
    r = client_for(world["designer"]).get(f"/api/v1/workspaces/{ws.slug}/artifacts/{art.id}/download/")
    assert r["Content-Type"] == "application/octet-stream" and "attachment" in r["Content-Disposition"]


def test_artifact_limits_and_validation():
    big = base64.b64encode(b"x" * (5 * 1024 * 1024 + 1)).decode()
    with pytest.raises(ValueError, match="taille totale"):
        extract_artifacts({"__artifacts__": [{"name": "a", "base64": big}]})
    with pytest.raises(ValueError, match="Au plus"):
        extract_artifacts({"__artifacts__": [{"name": f"{i}", "text": "x"} for i in range(11)]})
    with pytest.raises(ValueError):
        extract_artifacts({"__artifacts__": [{"name": "a", "base64": "!!!"}]})
    with pytest.raises(ValueError):
        extract_artifacts({"__artifacts__": [{"name": "a"}]})
    assert extract_artifacts({"x": 1}) == ({"x": 1}, [])
    assert (
        safe_name("../../etc/passwd") == "passwd"
        and safe_name("a b\\c.txt") == "c.txt"
        and safe_name("...") == "artefact"
    )


def test_filesystem_store_blocks_traversal(tmp_path):
    store = FileSystemStore(tmp_path / "s")
    store.put("a/b", b"1")
    assert store.get("a/b") == b"1"
    with pytest.raises(ValueError):
        store.put("../outside", b"x")
    with pytest.raises(ValueError):
        store.get("../../etc/passwd")
    store.delete("a/b")
    with pytest.raises(FileNotFoundError):
        store.get("a/b")


def test_invalid_artifact_fails_the_step(world):
    spec = {
        **MAKE_REPORT,
        "slug": "badart",
        "source": "def run(i):\n    return {'__artifacts__': 'nope'}\n",
        "output_schema": {"type": "object"},
    }
    ex = run_demo(world, spec, {"title": "t", "lines": []})
    assert ex.status == "failed" and ex.error["code"] == "ARTIFACT_INVALID"


@pytest.mark.skipif(not docker_ready(), reason="docker unavailable")
def test_http_connector_reaches_only_the_internal_test_service(world):
    import subprocess

    if subprocess.run(["docker", "network", "inspect", "fluxonata-egress-test"], capture_output=True).returncode != 0:
        pytest.skip("testsvc network not running (scripts/dev-up.sh)")
    ex = run_demo(world, HTTP_TEST, {"path": "/echo?x=7"}, DockerRunner())
    assert ex.status == "succeeded", ex.error
    assert ex.result["status"] == 200 and ex.result["body"]["query"] == {"x": ["7"]}
    bad = run_demo(
        world,
        {**HTTP_TEST, "slug": "http2", "source": HTTP_TEST["source"].replace("http://testsvc:8080", "http://1.1.1.1")},
        {"path": "/x"},
        DockerRunner(),
    )
    assert bad.status == "failed"  # no route to the Internet from the egress-test network


def test_unknown_network_policy_is_refused(world):
    from django.conf import settings

    ws = world["ws_a"]
    v = demo(ws, {**MAKE_REPORT, "slug": "np", "network_policy": "anything"})
    ex, _ = services.create_execution(
        workspace=ws,
        environment=Environment.objects.get(workspace=ws, kind="test"),
        action_version=v,
        user=world["designer"],
        inputs={"title": "t", "lines": []},
    )
    settings.ORCH_RUNNER = "docker"
    process_one("w", DockerRunner())
    ex.refresh_from_db()
    assert ex.status == "failed" and ex.error["code"] == "RUNNER_ERROR"
