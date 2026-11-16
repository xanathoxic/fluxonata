"""Synchronisation Git : validation de la configuration, correspondance des réponses de la forge avec des erreurs lisibles (sans jamais
laisser fuir le jeton), et lecture / proposition de fichiers pour GitHub et GitLab. La forge est SIMULÉE (aucun accès réseau)."""

import base64
import json
from types import SimpleNamespace

import pytest

from core import netsec
from workflows import gitsync
from workflows.gitsync import Client, GitError, GitHub, GitLab, blob_sha, parse, render, validate_config


@pytest.mark.parametrize(
    "args,message",
    [
        (("bitbucket", "a/b", "main", "workflows"), "Fournisseur"),
        (("github", "", "main", "workflows"), "Dépôt"),
        (("github", "a/b/../c", "main", "workflows"), "Dépôt"),
        (("github", "a/b", "", "workflows"), "Branche"),
        (("github", "a/b", "a..b", "workflows"), "Branche"),
        (("github", "a/b", "-main", "workflows"), "Branche"),
        (("github", "a/b", "/main", "workflows"), "Branche"),
        (("github", "a/b", "main", "../etc"), "Dossier"),
        (("github", "a/b", "main", "a/../b"), "Dossier"),
        (("github", "a/b", "main", "/absolu"), "Dossier"),
    ],
)
def test_a_configuration_that_could_leave_the_repository_or_is_malformed_is_refused(args, message):
    with pytest.raises(ValueError, match=message):
        validate_config(*args)


def test_a_sound_configuration_is_accepted():
    validate_config("github", "propriétaire/nom".replace("é", "e"), "feature/x", "workflows")
    validate_config("gitlab", "groupe/sous/projet", "main", "flux/prod")


def doc(**kw):
    return {"slug": "mon-flux", "name": "N", "description": "d", "folder": "", "graph": {"nodes": []}, **kw}


def test_a_workflow_file_is_stable_and_reads_back_to_the_same_document():
    text = render(doc())
    assert text == render(doc()) and text.endswith("\n") and '"format"' in text
    assert parse(text, "mon-flux") == doc()
    assert blob_sha(text) == blob_sha(render(doc())) and blob_sha(text) != blob_sha(render(doc(name="autre")))


def test_a_file_that_is_not_ours_is_refused_with_its_name():
    for bad in ("pas du json", "[]", '{"format": "x", "slug": "mon-flux"}', render(doc(slug="autre"))):
        with pytest.raises(GitError) as exc:
            parse(bad, "mon-flux")
        assert exc.value.code == "GIT_BAD_FILE" and "mon-flux.json" in exc.value.message


def make_client(monkeypatch, responder, provider="github", secrets=("jeton-secret-123",)):
    repo = SimpleNamespace(provider=provider, repo="o/r", branch="main", path="workflows")
    creds = SimpleNamespace(headers={"Authorization": "Bearer x"}, secrets=secrets)
    monkeypatch.setattr("vault.auth.credentials", lambda conn: creds)
    monkeypatch.setattr(netsec, "safe_request", responder)
    return Client(repo, SimpleNamespace(config={}))


def reply(status, payload=b"{}"):
    return lambda method, url, **kw: (status, {}, payload)


@pytest.mark.parametrize(
    "status,code",
    [(401, "GIT_AUTH"), (403, "GIT_AUTH"), (404, "GIT_NOT_FOUND"), (500, "GIT_API"), (422, "GIT_API")],
)
def test_each_forge_answer_has_its_own_readable_error(monkeypatch, status, code):
    client = make_client(monkeypatch, reply(status))
    with pytest.raises(GitError) as exc:
        client.call("GET", "/x")
    assert exc.value.code == code and exc.value.next_step


def test_the_token_never_appears_in_an_error_detail(monkeypatch):
    client = make_client(monkeypatch, reply(500, b"erreur avec jeton-secret-123 dedans"))
    with pytest.raises(GitError) as exc:
        client.call("GET", "/x")
    assert "jeton-secret-123" not in exc.value.message and "***" in exc.value.message


def test_a_blocked_address_an_unreachable_server_a_huge_or_unreadable_answer_are_told_apart(monkeypatch):
    def blocked(*a, **k):
        raise netsec.UnsafeURL("interne")

    def down(*a, **k):
        raise OSError("coupé")

    cases = [
        (blocked, "GIT_BLOCKED"),
        (down, "GIT_UNREACHABLE"),
        (reply(200, b"x" * (gitsync.MAX_RESPONSE + 1)), "GIT_TOO_BIG"),
        (reply(200, b"pas json"), "GIT_API"),
    ]
    for responder, code in cases:
        with pytest.raises(GitError) as exc:
            make_client(monkeypatch, responder).call("GET", "/x")
        assert exc.value.code == code


def test_an_empty_answer_is_an_empty_object_and_raw_mode_returns_the_bytes(monkeypatch):
    assert make_client(monkeypatch, reply(200, b"")).call("GET", "/x") == {}
    assert make_client(monkeypatch, reply(200, b"brut")).call("GET", "/x", raw=True) == b"brut"


def test_the_request_carries_the_forge_headers_and_a_json_body(monkeypatch):
    seen = {}

    def spy(method, url, **kw):
        seen.update(method=method, url=url, **kw)
        return 200, {}, b"{}"

    make_client(monkeypatch, spy).call("POST", "/repos/o/r/git/refs", {"ref": "r"})
    assert seen["url"].startswith("https://api.github.com") and seen["headers"]["X-GitHub-Api-Version"] == "2022-11-28"
    assert seen["headers"]["Content-Type"] == "application/json" and seen["body"] == b'{"ref": "r"}'
    make_client(monkeypatch, spy, provider="gitlab").call("GET", "/projects/x")
    assert seen["url"].startswith("https://gitlab.com") and "X-GitHub-Api-Version" not in seen["headers"]


def test_github_lists_only_json_files_directly_under_the_folder(monkeypatch):
    tree = {
        "tree": [
            {"path": "workflows/a.json", "type": "blob", "sha": "1"},
            {"path": "workflows/sub/b.json", "type": "blob", "sha": "2"},
            {"path": "workflows/c.txt", "type": "blob", "sha": "3"},
            {"path": "autre/d.json", "type": "blob", "sha": "4"},
            {"path": "workflows/e.json", "type": "tree", "sha": "5"},
        ]
    }
    gh = GitHub(make_client(monkeypatch, reply(200, json.dumps(tree).encode())))
    assert gh.files() == {"a.json": "1"}
    truncated = GitHub(make_client(monkeypatch, reply(200, b'{"truncated": true, "tree": []}')))
    with pytest.raises(GitError, match="trop volumineux"):
        truncated.files()


def test_github_reads_a_base64_file_and_refuses_another_encoding(monkeypatch):
    content = base64.b64encode("héllo".encode()).decode()
    ok = GitHub(make_client(monkeypatch, reply(200, json.dumps({"encoding": "base64", "content": content}).encode())))
    assert ok.read("a.json") == "héllo"
    with pytest.raises(GitError) as exc:
        GitHub(make_client(monkeypatch, reply(200, b'{"encoding": "none"}'))).read("a.json")
    assert exc.value.code == "GIT_BAD_FILE"


def test_github_proposes_a_branch_with_one_commit_per_file_and_a_pull_request(monkeypatch):
    calls = []

    def forge(method, url, **kw):
        calls.append((method, url.split("api.github.com")[1], json.loads(kw["body"] or b"{}")))
        if url.endswith("/pulls"):
            return 201, {}, b'{"html_url": "https://github.com/o/r/pull/1"}'
        return 200, {}, b'{"object": {"sha": "tete"}}'

    gh = GitHub(make_client(monkeypatch, forge))
    url = gh.propose(
        "fluxonata/x", {"a.json": ("texte", None), "b.json": ("autre", "ancien-sha")}, "msg", "Titre", "Corps"
    )
    assert url == "https://github.com/o/r/pull/1"
    assert calls[1] == ("POST", "/repos/o/r/git/refs", {"ref": "refs/heads/fluxonata/x", "sha": "tete"})
    assert "sha" not in calls[2][2] and calls[3][2]["sha"] == "ancien-sha"
    assert calls[3][1] == "/repos/o/r/contents/workflows/b.json" and calls[-1][2]["base"] == "main"


def test_gitlab_pages_through_the_tree_and_reads_raw_files(monkeypatch):

    pages = {
        1: [{"type": "blob", "name": f"f{i}.json", "id": str(i)} for i in range(100)],
        2: [{"type": "blob", "name": "z.json", "id": "z"}, {"type": "tree", "name": "d"}],
    }

    def forge(method, url, **kw):
        if "/repository/tree" in url:
            page = int(url.rsplit("page=", 1)[1])
            return 200, {}, json.dumps(pages[page]).encode()
        if url.endswith("/raw?ref=main") or "/files/" in url:
            return 200, {}, "contenu é".encode()
        return 200, {}, b'{"commit": {"id": "abc"}}'

    gl = GitLab(make_client(monkeypatch, forge, provider="gitlab"))
    files = gl.files()
    assert len(files) == 101 and files["z.json"] == "z"
    assert gl.read("a.json") == "contenu é" and gl.check() == {"head": "abc"}
