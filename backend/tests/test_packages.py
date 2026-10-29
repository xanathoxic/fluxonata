import io
import json
import os
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from audit.models import AuditEvent
from bundles import archive, service
from catalog.models import ActionVersion
from tests.conftest import client_for
from tests.wf_helpers import edge, make_action, node, publish

A = "/api/v1/workspaces/team-a"
B = "/api/v1/workspaces/team-b"


@pytest.fixture(autouse=True)
def _store(settings, tmp_path):
    settings.ORCH_ARTIFACTS_DIR = str(tmp_path / "artifacts")


@pytest.fixture
def exported(world):
    ws = world["ws_a"]
    make_action(ws, "echo")
    publish(
        ws,
        "greet",
        [
            node("a", action="echo", inputs={"value": "hi"}),
            node("b", action="echo", inputs={"value": "{{ steps.a.output.value }}"}),
        ],
        [edge("a", "b")],
        user=world["designer"],
    )
    c = client_for(world["designer"])
    r = c.post(
        f"{A}/packages/export/",
        {
            "name": "Démo",
            "slug": "demo-pkg",
            "version": "1.0.0",
            "description": "d",
            "actions": [{"slug": "echo", "version": "1.0.0"}],
            "workflows": [{"slug": "greet", "version": "1.0.0"}],
        },
        format="json",
    )
    assert r.status_code == 201, r.content
    data = c.get(r.json()["download_url"]).content
    return data, r.json()


def upload(c, base, data, **form):
    return c.post(f"{base}/packages/import/", {"file": SimpleUploadedFile("p.zip", data), **form}, format="multipart")


def craft(files: dict, manifest_over=None, sums=True, raw_entries=None):
    """Build a zip with a consistent manifest unless told otherwise."""
    manifest = {
        "format": archive.FORMAT,
        "name": "x",
        "slug": "x",
        "version": "1.0.0",
        "description": "",
        "license": "Apache-2.0",
        "contents": {"actions": [], "workflows": []},
        "checksums": {p: archive.sha(d) for p, d in files.items()} if sums else {},
    }
    manifest.update(manifest_over or {})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest))
        for p, d in files.items():
            z.writestr(p, d)
        for info, d in raw_entries or []:
            z.writestr(info, d)
    return buf.getvalue()


def _without(data: bytes, key: str) -> bytes:
    """Rebuild a zip whose manifest lacks `key`."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            content = zin.read(info)
            if info.filename == "manifest.json":
                m = json.loads(content)
                m.pop(key)
                content = json.dumps(m).encode()
            zout.writestr(info.filename, content)
    return out.getvalue()


def test_export_manifest_checksums_and_no_secrets(exported):
    data, meta = exported
    manifest, files = archive.read(data)
    assert manifest["format"] == "fluxonata-package/1" and meta["checksum"] == archive.sha(data)
    assert set(manifest["checksums"]) == set(files) and "docs/README.md" in files
    assert [a["slug"] for a in manifest["contents"]["actions"]] == ["echo"]
    assert meta["contents"] == {"actions": 1, "workflows": 1}


def test_roundtrip_into_another_workspace_preview_then_apply(exported, world):
    data, _ = exported
    c = client_for(world["outsider"])
    prev = upload(c, B, data, mode="preview")
    assert prev.status_code == 200 and prev.json()["can_apply"] is True
    assert [(i["kind"], i["status"]) for i in prev.json()["items"]] == [("action", "new"), ("workflow", "new")]
    assert not ActionVersion.objects.filter(
        action__workspace=world["ws_b"], action__slug="echo"
    ).exists()  # preview persists nothing
    done = upload(c, B, data, mode="apply")
    assert done.json()["applied"] is True
    imported = ActionVersion.objects.get(action__workspace=world["ws_b"], action__slug="echo")
    assert (
        imported.source_digest
        == ActionVersion.objects.get(action__workspace=world["ws_a"], action__slug="echo").source_digest
    )
    assert c.get(f"{B}/workflows/greet/").json()["latest_version"]["version"] == "1.0.0"
    assert AuditEvent.objects.filter(action="package.import", workspace=world["ws_b"]).exists()
    again = upload(c, B, data, mode="apply")  # same package version twice
    assert again.status_code == 400 and "déjà été importée" in again.json()["errors"][0]


def test_conflict_report_and_identical_detection(exported, world):
    data, _ = exported
    ws_b = world["ws_b"]
    make_action(
        ws_b, "echo"
    )  # same slug@version, same source as the package's (make_action uses the shared echo source)
    c = client_for(world["outsider"])
    r = upload(c, B, data, mode="preview").json()
    assert r["items"][0]["status"] == "identical"
    # different content under the same slug/version -> conflict, nothing applied
    from catalog.models import Action

    Action.objects.filter(workspace=ws_b, slug="echo").delete()
    make_action(ws_b, "echo", source="def run(i):\n    return {'value': 'DIFFERENT'}\n")
    r = upload(c, B, data, mode="apply")
    assert r.status_code == 200 and r.json()["can_apply"] is False and r.json()["applied"] is False
    conflict = [i for i in r.json()["items"] if i["status"] == "conflict"][0]
    assert "immuable" in conflict["messages"][0]
    assert not ActionVersion.objects.filter(
        action__workspace=ws_b, action__slug="echo", version="1.0.0", source__contains="hi"
    ).exists()
    # skip strategy applies what it can (the workflow depends on the conflicting action version -> reported invalid or new)
    skipped = upload(c, B, data, mode="preview", strategy="skip").json()
    assert any(i["status"] == "conflict" for i in skipped["items"])


@pytest.mark.parametrize(
    "name",
    [
        "../evil.py",
        "actions/../../evil.py",
        "/etc/passwd",
        "C:/win.json",
        "actions\\x\\a.json",
        "other/file.json",
        "actions/x/run.exe",
        "actions//x.json",
    ],
)
def test_path_traversal_and_bad_entries_are_rejected(name):
    data = craft({}, sums=False, raw_entries=[(name, b"x")])
    with pytest.raises(archive.PackageError):
        archive.read(data)


def test_symlink_entries_are_rejected():
    info = zipfile.ZipInfo("actions/link.json")
    info.external_attr = 0o120777 << 16
    with pytest.raises(archive.PackageError, match="symbolique"):
        archive.read(craft({}, sums=False, raw_entries=[(info, b"/etc/passwd")]))


def test_zip_bomb_is_rejected_by_ratio_and_size():
    bomb = craft({"actions/x/a.json": b"0" * (900 * 1024)})  # ~900 KB of zeros compresses > 100x
    with pytest.raises(archive.PackageError, match="suspect"):
        archive.read(bomb)
    many = craft({f"docs/{i}.md": b"x" for i in range(archive.MAX_ENTRIES + 1)})
    with pytest.raises(archive.PackageError, match="Trop de fichiers"):
        archive.read(many)
    big = craft({f"docs/{i}.md": os.urandom(900 * 1024) for i in range(4)})  # incompressible, 3.6 MB < 5 MB
    manifest, files = archive.read(big)
    assert len(files) == 4
    with pytest.raises(archive.PackageError, match="volumineuse"):
        archive.read(b"0" * (archive.MAX_ARCHIVE + 1))


def test_tampered_and_undeclared_content_is_rejected():
    ok = {"docs/a.md": b"hello"}
    assert archive.read(craft(ok))
    bad_sum = craft(ok, manifest_over={"checksums": {"docs/a.md": "sha256:" + "0" * 64}})
    with pytest.raises(archive.PackageError, match="Somme de contrôle"):
        archive.read(bad_sum)
    with pytest.raises(archive.PackageError, match="non déclarés"):
        archive.read(craft(ok, manifest_over={"checksums": {}}))
    with pytest.raises(archive.PackageError, match="Format"):
        archive.read(craft(ok, manifest_over={"format": "other/9"}))
    with pytest.raises(archive.PackageError, match="pas une archive"):
        archive.read(b"not a zip")
    with pytest.raises(archive.PackageError, match="incomplet"):
        archive.read(_without(craft(ok), "license"))


def test_import_never_executes_code(world, tmp_path):
    marker = tmp_path / "pwned"
    src = f"open({str(marker)!r}, 'w').write('x')\ndef run(i):\n    return {{}}\n"
    meta = {
        "slug": "evil",
        "name": "evil",
        "description": "",
        "domain": "",
        "tags": [],
        "version": "1.0.0",
        "runtime": "python",
        "entrypoint": "run",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {}},
        "license": "MIT",
        "side_effects": "none",
        "idempotent": True,
        "network_policy": "none",
        "limits": {"timeout_seconds": 5, "memory_mb": 64},
        "examples": [],
        "secrets_required": [],
        "changelog": "",
    }
    files = {"actions/evil/1.0.0/action.json": json.dumps(meta).encode(), "actions/evil/1.0.0/source.py": src.encode()}
    data = craft(
        files,
        manifest_over={
            "contents": {
                "actions": [
                    {
                        "slug": "evil",
                        "version": "1.0.0",
                        "path": "actions/evil/1.0.0/action.json",
                        "source": "actions/evil/1.0.0/source.py",
                    }
                ],
                "workflows": [],
            }
        },
    )
    report = service.import_package(world["ws_a"], world["designer"], data, apply=True)
    assert report["applied"] is True and not marker.exists()  # stored, never run


def test_secret_like_content_blocks_import_and_export(world):
    files = {"docs/n.md": b"token: ghp_" + b"a" * 36}
    report = service.import_package(world["ws_a"], world["designer"], craft(files), apply=True)
    assert report["can_apply"] is False and "Secret probable" in report["errors"][0]
    ws = world["ws_a"]
    make_action(ws, "echo", source="TOKEN = '" + "AKIA" + "ABCDEFGHIJKLMNOP'\ndef run(i):\n    return {'value': 'x'}\n")
    c = client_for(world["designer"])
    r = c.post(
        f"{A}/packages/export/",
        {"name": "n", "slug": "n", "version": "1.0.0", "actions": [{"slug": "echo", "version": "1.0.0"}]},
        format="json",
    )
    assert r.status_code == 400 and "secret" in json.dumps(r.json()).lower()


def test_invalid_action_in_package_is_reported_not_applied(world):
    meta = {
        "slug": "bad",
        "name": "bad",
        "version": "1.0.0",
        "runtime": "python",
        "entrypoint": "run",
        "input_schema": {"type": "string"},
        "output_schema": {"type": "object"},
        "limits": {},
    }
    files = {
        "actions/bad/1.0.0/action.json": json.dumps(meta).encode(),
        "actions/bad/1.0.0/source.py": b"def run(i): return {}",
    }
    data = craft(
        files,
        manifest_over={
            "contents": {
                "actions": [
                    {
                        "slug": "bad",
                        "version": "1.0.0",
                        "path": "actions/bad/1.0.0/action.json",
                        "source": "actions/bad/1.0.0/source.py",
                    }
                ],
                "workflows": [],
            }
        },
    )
    r = service.import_package(world["ws_a"], world["designer"], data, apply=True)
    assert (
        r["items"][0]["status"] == "invalid"
        and r["applied"] is False
        and not ActionVersion.objects.filter(action__slug="bad").exists()
    )


def test_workflow_with_missing_dependency_is_invalid(exported, world):
    data, _ = exported
    manifest, files = archive.read(data)
    only_wf = {p: d for p, d in files.items() if p.startswith("workflows/") or p.startswith("docs/")}
    contents = {"actions": [], "workflows": manifest["contents"]["workflows"]}
    pkg = craft(only_wf, manifest_over={"contents": contents})
    r = service.import_package(world["ws_b"], world["outsider"], pkg, apply=False)
    assert r["items"][0]["status"] == "invalid" and r["can_apply"] is False


def test_permissions_and_listing(exported, world):
    data, meta = exported
    assert upload(client_for(world["viewer"]), A, data).status_code == 403
    assert client_for(world["viewer"]).post(f"{A}/packages/export/", {}, format="json").status_code == 403
    assert client_for(world["outsider"]).get(meta["download_url"]).status_code == 404
    listing = client_for(world["viewer"]).get(f"{A}/packages/").json()["results"]
    assert listing[0]["slug"] == "demo-pkg" and listing[0]["origin"] == "export"
    assert (
        client_for(world["designer"])
        .post(
            f"{A}/packages/export/",
            {"name": "Démo", "slug": "demo-pkg", "version": "1.0.0", "actions": [{"slug": "echo", "version": "1.0.0"}]},
            format="json",
        )
        .status_code
        == 400
    )  # package version is immutable
    r = client_for(world["designer"]).post(f"{A}/packages/import/", {}, format="multipart")
    assert r.status_code == 400


def test_import_older_version_does_not_become_latest(exported, world):
    from tests.conftest import add_action  # noqa: F401

    ws_b = world["ws_b"]
    make_action(ws_b, "echo", version="2.0.0")
    data, _ = exported
    service.import_package(ws_b, world["outsider"], data, apply=True)
    from catalog.models import Action

    assert Action.objects.get(workspace=ws_b, slug="echo").latest_version().version == "2.0.0"


def test_directory_roundtrip_commands(exported, world, tmp_path):
    from django.core.management import call_command

    call_command("package_export", "team-a", "demo-pkg", "1.0.0", str(tmp_path / "repo"))
    assert (tmp_path / "repo" / "manifest.json").exists()
    out = io.StringIO()
    call_command("package_import", "team-b", str(tmp_path / "repo"), "--user", "otto", stdout=out)
    assert "Aperçu : applicable." in out.getvalue()
    call_command("package_import", "team-b", str(tmp_path / "repo"), "--user", "otto", "--apply", stdout=out)
    assert ActionVersion.objects.filter(action__workspace=world["ws_b"], action__slug="echo").exists()


# ------------------------------------------------------------------ provenance / signatures
def test_exports_are_signed_and_trust_is_per_workspace(exported, world):
    data, meta = exported
    manifest, _ = archive.read(data)
    sig = manifest["signature"]
    assert sig["alg"] == "ed25519" and sig["key_id"]
    outsider = client_for(world["outsider"])
    # unknown publisher: valid signature but not trusted
    r = upload(outsider, B, data, mode="preview").json()
    assert r["signature"]["status"] == "untrusted" and r["can_apply"] is True
    # an admin of team-b chooses to trust the publisher key
    from tenancy.models import Role
    from tests.conftest import make_user

    admin_b = make_user("adab", world["ws_b"], Role.ADMIN)
    ca = client_for(admin_b)
    assert (
        ca.post(
            f"{B}/packages/trusted-keys/", {"name": "Équipe A", "public_key": sig["public_key"]}, format="json"
        ).status_code
        == 201
    )
    ok = upload(outsider, B, data, mode="preview").json()["signature"]
    assert ok == {"status": "trusted", "signer": "Équipe A"}
    assert client_for(world["designer"]).get(f"{A}/packages/trusted-keys/").status_code == 403  # admin only
    assert (
        ca.get(f"{B}/packages/trusted-keys/").json()["instance_public_key"] == sig["public_key"]
    )  # this instance signed it


def test_tampered_manifest_signature_is_invalid_and_unsigned_is_reported(exported, world):
    data, _ = exported
    manifest, files = archive.read(data)
    manifest["description"] = "modifiée après signature"
    forged = craft(
        files,
        manifest_over={
            k: v
            for k, v in manifest.items()
            if k not in ("format", "name", "slug", "version", "description", "license", "contents", "checksums")
        }
        | {
            "description": manifest["description"],
            "contents": manifest["contents"],
            "checksums": manifest["checksums"],
            "name": manifest["name"],
            "slug": manifest["slug"],
            "version": manifest["version"],
            "license": manifest["license"],
            "signature": manifest["signature"],
        },
    )
    r = upload(client_for(world["outsider"]), B, forged, mode="preview").json()
    assert r["signature"]["status"] == "invalid"
    unsigned = craft(files, manifest_over={"contents": manifest["contents"], "checksums": manifest["checksums"]})
    assert (
        upload(client_for(world["outsider"]), B, unsigned, mode="preview").json()["signature"]["status"] == "unsigned"
    )


def test_policy_can_require_trusted_signatures(exported, world):
    from tenancy import governance
    from tenancy.models import Role
    from tests.conftest import make_user

    data, _ = exported
    admin_b = make_user("adab", world["ws_b"], Role.ADMIN)
    governance.publish_policy(world["ws_b"], {"require_signed_packages": True}, admin_b)
    c = client_for(world["outsider"])
    blocked = upload(c, B, data, mode="apply")
    assert blocked.status_code == 400 and "exige un paquet signé" in blocked.json()["errors"][0]
    assert not ActionVersion.objects.filter(action__workspace=world["ws_b"], action__slug="echo").exists()
    manifest, _ = archive.read(data)
    client_for(admin_b).post(
        f"{B}/packages/trusted-keys/", {"name": "A", "public_key": manifest["signature"]["public_key"]}, format="json"
    )
    assert upload(c, B, data, mode="apply").json()["applied"] is True


def test_unsigned_export_is_possible_and_bad_keys_are_rejected(world):
    from tenancy.models import Role
    from tests.conftest import make_user

    ws = world["ws_a"]
    make_action(ws, "echo")
    c = client_for(world["designer"])
    r = c.post(
        f"{A}/packages/export/",
        {
            "name": "n",
            "slug": "n",
            "version": "1.0.0",
            "sign": False,
            "actions": [{"slug": "echo", "version": "1.0.0"}],
        },
        format="json",
    )
    assert r.status_code == 201
    manifest, _ = archive.read(c.get(r.json()["download_url"]).content)
    assert "signature" not in manifest
    admin = client_for(make_user("ada", ws, Role.ADMIN))
    assert (
        admin.post(f"{A}/packages/trusted-keys/", {"name": "x", "public_key": "not-base64!"}, format="json").status_code
        == 400
    )
    assert (
        admin.post(f"{A}/packages/trusted-keys/", {"name": "x", "public_key": "QUJD"}, format="json").status_code == 400
    )  # wrong length
