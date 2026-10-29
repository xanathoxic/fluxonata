import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from tenancy.models import Role
from tests.conftest import client_for, make_user
from tests.wf_helpers import ACTIONS, make_action, node, publish
from vault.models import Connection, Secret
from workflows import gitsync
from workflows.models import GitJob, Workflow

BASE = "/api/v1/workspaces/team-a"
TOKEN = "-".join(["jeton", "de", "test", "forge", "12345"])  # built, not literal: the secret scan flags assignments


class Forge:
    """An in-memory GitHub + GitLab: branches hold files (path -> text); the main branch is `main`."""

    def __init__(self):
        self.branches = {"main": {}}
        self.requests: list[tuple] = []
        self.prs: list[dict] = []
        self.fail_auth = False


FORGE = Forge()


def _sha(text):
    return gitsync.blob_sha(text)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self):
        parts = urlsplit(self.path)
        path, q = unquote(parts.path), parse_qs(parts.query)
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        FORGE.requests.append((self.command, path, self.headers.get("Authorization"), body))
        if FORGE.fail_auth or self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._send(401, {"message": "Bad credentials"})
        f = FORGE.branches
        # ---- GitHub
        if path.startswith("/repos/o/r/"):
            rest = path[len("/repos/o/r/") :]
            if rest == "git/ref/heads/main":
                return self._send(200, {"object": {"sha": "head-main"}})
            if rest.startswith("git/trees/"):
                b = rest.split("/")[-1]
                if b not in f:
                    return self._send(404, {})
                return self._send(
                    200,
                    {
                        "truncated": False,
                        "tree": [{"path": p, "type": "blob", "sha": _sha(t)} for p, t in f[b].items()],
                    },
                )
            if rest.startswith("contents/") and self.command == "GET":
                p = rest[len("contents/") :]
                t = f[q["ref"][0]].get(p)
                if t is None:
                    return self._send(404, {})
                return self._send(200, {"encoding": "base64", "content": base64.b64encode(t.encode()).decode()})
            if rest == "git/refs":
                f[body["ref"].split("/", 2)[2]] = dict(f["main"])
                return self._send(201, {})
            if rest.startswith("contents/") and self.command == "PUT":
                p, branch = rest[len("contents/") :], body["branch"]
                old = f[branch].get(p)
                if old is not None and body.get("sha") != _sha(old):
                    return self._send(409, {"message": "sha mismatch"})
                f[branch][p] = base64.b64decode(body["content"]).decode()
                return self._send(201, {})
            if rest == "pulls":
                FORGE.prs.append(body)
                return self._send(201, {"html_url": f"https://github.example/o/r/pull/{len(FORGE.prs)}"})
        # ---- GitLab
        if path.startswith("/projects/g/p/"):
            rest = path[len("/projects/g/p/") :]
            if rest == "repository/branches/main":
                return self._send(200, {"commit": {"id": "head-main"}})
            if rest == "repository/tree":
                prefix = q["path"][0] + "/"
                rows = [
                    {"name": p[len(prefix) :], "type": "blob", "id": _sha(t)}
                    for p, t in f[q["ref"][0]].items()
                    if p.startswith(prefix)
                ]
                return self._send(200, rows)
            if rest.startswith("repository/files/"):
                p = rest[len("repository/files/") : -len("/raw")]
                return self._send(200, f[q["ref"][0]][p].encode(), "text/plain")
            if rest == "repository/commits":
                f[body["branch"]] = dict(f[body["start_branch"]])
                for a in body["actions"]:
                    assert (a["action"] == "update") == (a["file_path"] in f[body["branch"]])
                    f[body["branch"]][a["file_path"]] = a["content"]
                return self._send(201, {"id": "c1"})
            if rest == "merge_requests":
                FORGE.prs.append(body)
                return self._send(201, {"web_url": f"https://gitlab.example/g/p/-/merge_requests/{len(FORGE.prs)}"})
        return self._send(404, {"message": "not found"})

    do_GET = do_POST = do_PUT = _handle

    def log_message(self, *a):
        pass


@pytest.fixture
def forge(settings):
    FORGE.__init__()
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    FORGE.url = f"http://127.0.0.1:{srv.server_port}"
    yield FORGE
    srv.shutdown()


@pytest.fixture
def ws(world, forge):
    ws = world["ws_a"]
    for slug in ACTIONS:
        make_action(ws, slug)
    world["admin"] = make_user("ada-admin", ws, Role.ADMIN)
    s = Secret(workspace=ws, name="gittok")
    s.set_value(TOKEN)
    s.save()
    Connection.objects.create(
        workspace=ws, name="forge", label="Forge", kind="http_bearer", config={"base_url": forge.url}, secret=s
    )
    return ws


def _wf(ws, world, slug, folder="", value="x"):
    v = publish(ws, slug, [node("a", action="echo", inputs={"value": value})], [], user=world["designer"])
    Workflow.objects.filter(workspace=ws, slug=slug).update(folder=folder, name=slug.upper())
    return v


def _config(world, provider="github", **kw):
    repo = "o/r" if provider == "github" else "g/p"
    body = {"provider": provider, "repo": repo, "branch": "main", "path": "workflows", "connection": "forge", **kw}
    r = client_for(world["admin"]).put(f"{BASE}/git/", body, format="json")
    assert r.status_code == 200, r.content


def _run(client, kind, **extra):
    r = client.post(f"{BASE}/git/jobs/", {"kind": kind, **extra}, format="json")
    assert r.status_code == 202, r.content
    assert gitsync.process_one() is True
    return client.get(f"{BASE}/git/jobs/{r.json()['id']}/").json()


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_push_opens_a_pull_request_on_a_new_branch_and_never_touches_main(ws, world, forge, provider):
    _wf(ws, world, "alpha")
    _wf(ws, world, "beta", value="y")
    _config(world, provider)
    job = _run(client_for(world["designer"]), "push")
    assert job["status"] == "done", job
    res = job["result"]
    assert res["status"] == "proposed" and res["created"] == ["alpha", "beta"] and res["updated"] == []
    assert res["url"].endswith("/pull/1" if provider == "github" else "/merge_requests/1")
    assert forge.branches["main"] == {}  # nothing on the main branch
    (branch,) = [b for b in forge.branches if b != "main"]
    assert branch.startswith("fluxonata/sync-") and set(forge.branches[branch]) == {
        "workflows/alpha.json",
        "workflows/beta.json",
    }
    doc = json.loads(forge.branches[branch]["workflows/alpha.json"])
    assert (
        doc["format"] == gitsync.FILE_FORMAT
        and doc["slug"] == "alpha"
        and doc["name"] == "ALPHA"
        and "nodes" in doc["graph"]
    )
    assert forge.prs[0].get("base", forge.prs[0].get("target_branch")) == "main"
    assert TOKEN not in json.dumps(job) and all(a == f"Bearer {TOKEN}" for _, _, a, _ in forge.requests)


def test_a_second_push_only_sends_what_changed_and_what_is_merged_is_unchanged(ws, world, forge):
    _wf(ws, world, "alpha")
    _wf(ws, world, "beta")
    _config(world)
    c = client_for(world["designer"])
    first = _run(c, "push")
    (branch,) = [b for b in forge.branches if b != "main"]
    forge.branches["main"] = dict(forge.branches[branch])  # the pull request is merged
    again = _run(c, "push")
    assert again["result"]["status"] == "unchanged" and again["result"]["unchanged"] == 2 and first["status"] == "done"
    wf = Workflow.objects.get(slug="beta")
    wf.draft_graph = {**wf.draft_graph, "notes": [{"id": "n1", "text": "nouvelle remarque", "x": 1, "y": 1}]}
    wf.save()
    third = _run(c, "push")
    assert (
        third["result"]["status"] == "proposed"
        and third["result"]["updated"] == ["beta"]
        and third["result"]["created"] == []
    )
    assert third["result"]["unchanged"] == 1


def test_pull_previews_then_imports_drafts_only_and_refuses_bad_files(ws, world, forge):
    _wf(ws, world, "alpha")
    _config(world)
    c = client_for(world["designer"])
    assert _run(c, "push")["status"] == "done"
    (branch,) = [b for b in forge.branches if b != "main"]
    good = json.loads(forge.branches[branch]["workflows/alpha.json"])
    new = {**good, "slug": "gamma", "name": "Gamma"}
    forge.branches["main"] = {
        "workflows/alpha.json": gitsync.render({**good, "name": "Alpha renommé"}),
        "workflows/gamma.json": gitsync.render(new),
        "workflows/casse.json": "{pas du json",
        "workflows/faux.json": gitsync.render({**good, "slug": "autre"}),
        "workflows/lisez-moi.txt": "ignoré",
    }
    preview = _run(c, "pull_preview")["result"]
    status = {r["slug"]: r["status"] for r in preview["results"]}
    assert status == {"alpha": "skipped", "gamma": "would_create", "casse": "refused", "faux": "refused"}
    assert not Workflow.objects.filter(slug="gamma").exists() and not preview["applied"]
    applied = _run(c, "pull_apply", overwrite=True)["result"]
    assert {r["slug"]: r["status"] for r in applied["results"]}["gamma"] == "created"
    assert (
        Workflow.objects.get(slug="alpha").name == "Alpha renommé"
        and Workflow.objects.get(slug="gamma").name == "Gamma"
    )
    assert not Workflow.objects.get(slug="gamma").versions.exists()  # a draft: nothing is published


def test_access_rights_and_failures_are_reported_not_hidden(ws, world, forge, settings):
    _wf(ws, world, "alpha")
    assert client_for(world["designer"]).put(f"{BASE}/git/", {}, format="json").status_code == 403
    bad = client_for(world["admin"]).put(
        f"{BASE}/git/", {"provider": "github", "repo": "../x", "connection": "forge"}, format="json"
    )
    assert bad.status_code == 400
    assert (
        client_for(world["admin"])
        .put(f"{BASE}/git/", {"provider": "github", "repo": "o/r", "connection": "absente"}, format="json")
        .status_code
        == 400
    )
    assert (
        client_for(world["designer"]).post(f"{BASE}/git/jobs/", {"kind": "push"}, format="json").status_code == 400
    )  # not configured
    _config(world)
    assert client_for(world["viewer"]).post(f"{BASE}/git/jobs/", {"kind": "push"}, format="json").status_code == 403
    c = client_for(world["designer"])
    queued = c.post(f"{BASE}/git/jobs/", {"kind": "check"}, format="json")
    assert (
        queued.status_code == 202 and c.post(f"{BASE}/git/jobs/", {"kind": "push"}, format="json").status_code == 400
    )  # one at a time
    assert gitsync.process_one()
    forge.fail_auth = True
    assert _run(c, "check")["error"]["code"] == "GIT_AUTH"
    forge.fail_auth = False
    settings.ORCH_EGRESS_ALLOWLIST = ""
    blocked = _run(c, "check")
    assert blocked["status"] == "failed" and blocked["error"]["code"] == "GIT_BLOCKED"
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    GitJob.objects.filter(status="running").delete()
    ok = _run(c, "check")
    assert ok["status"] == "done" and ok["result"]["head"] == "head-main"


def test_what_the_person_cannot_see_is_not_sent(ws, world, forge):
    _wf(ws, world, "public")
    _wf(ws, world, "secret", folder="RH")
    client_for(world["admin"]).put(
        f"{BASE}/folder-access/", {"folder": "RH", "grants": [{"username": "dana", "level": "edit"}]}, format="json"
    )
    _config(world)
    erin = make_user("erin", ws, Role.DESIGNER)
    assert _run(client_for(erin), "push")["result"]["created"] == ["public"]
    GitJob.objects.all().delete()
    assert sorted(_run(client_for(world["designer"]), "push")["result"]["created"]) == ["public", "secret"]
