"""AR1.4 — stockage des artefacts sur un service compatible S3.

Deux familles de tests :
- un faux serveur S3 en mémoire (rapide, sans Docker) pour les cas d'erreur : 404, 5xx réessayés, 4xx non réessayés, objet tronqué, hôte non autorisé,
  secrets absents des messages, configuration invalide, erreurs HTTP propres (404 / 503), « API et worker sur deux hôtes », migration depuis le volume local ;
- un vrai serveur (SeaweedFS, Apache-2.0, image épinglée) quand Docker et l'image sont déjà présents : seule preuve que la signature SigV4 est acceptée
  par un service réel et pas seulement cohérente avec elle-même. Sans l'image, ces tests sont sautés (jamais de téléchargement automatique).
"""

import hashlib
import json
import re
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError

from bundles.models import Package, PackageVersion
from catalog.demo_actions import MAKE_REPORT
from runs.artifacts import FileSystemStore, fetch, get_store
from runs.models import Artifact
from runs.s3store import ArtifactStoreError, S3Store
from tests.conftest import client_for
from tests.test_artifacts import run_demo

ACCESS, SECRET = "testaccess", "testsecret0123456789"
BUCKET = "fluxonata-test"


# ----------------------------------------------------------------------------------------------------------- faux serveur
class FakeS3:
    """Un S3 minimal : PUT / GET / DELETE d'objets et PUT de bucket. Vérifie que la requête est signée par la bonne identité et que l'empreinte
    de contenu annoncée (`x-amz-content-sha256`) est bien celle du corps reçu."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.requests: list[tuple[str, str]] = []
        self.fail_next: list[int] = []  # statuts à renvoyer avant de répondre normalement
        self.always: int | None = None
        self.truncate = False
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def _auth_ok(self, body: bytes) -> bool:
                auth = self.headers.get("Authorization", "")
                good_id = auth.startswith(f"AWS4-HMAC-SHA256 Credential={ACCESS}/") and "/s3/aws4_request" in auth
                declared = self.headers.get("x-amz-content-sha256")
                return good_id and declared == hashlib.sha256(body).hexdigest() and bool(self.headers.get("x-amz-date"))

            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                fake.requests.append((self.command, self.path))
                if not self._auth_ok(body):
                    return self._reply(403, b"<Error><Code>SignatureDoesNotMatch</Code></Error>")
                status = fake.always or (fake.fail_next.pop(0) if fake.fail_next else None)
                if status:
                    return self._reply(status, b"<Error><Code>InternalError</Code></Error>")
                parts = self.path.split("/", 2)  # ['', bucket, key]
                if len(parts) < 3 or not parts[2]:  # PUT bucket
                    return self._reply(200, b"")
                key = parts[2]
                if self.command == "PUT":
                    fake.objects[key] = body
                    return self._reply(200, b"")
                if self.command == "GET":
                    if key not in fake.objects:
                        return self._reply(404, b"<Error><Code>NoSuchKey</Code></Error>")
                    data = fake.objects[key]
                    if fake.truncate:
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(data) + 50))  # annonce plus que ce qui sera envoyé
                        self.end_headers()
                        self.wfile.write(data)
                        self.close_connection = True
                        return None
                    return self._reply(200, data)
                if self.command == "DELETE":
                    existed = fake.objects.pop(key, None) is not None
                    return self._reply(204 if existed else 404, b"")
                return self._reply(405, b"")

            def _reply(self, status, body):
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_PUT = do_GET = do_DELETE = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake(settings):
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    f = FakeS3()
    yield f
    f.stop()


@pytest.fixture
def s3(fake):
    return S3Store(fake.url, BUCKET, access_key=ACCESS, secret_key=SECRET, prefix="fluxonata", timeout=5)


@pytest.fixture
def s3_settings(settings, fake, tmp_path):
    settings.ORCH_ARTIFACTS_STORE = "s3"
    settings.ORCH_S3_ENDPOINT, settings.ORCH_S3_BUCKET, settings.ORCH_S3_PREFIX = fake.url, BUCKET, ""
    settings.ORCH_S3_ACCESS_KEY, settings.ORCH_S3_SECRET_KEY = ACCESS, SECRET
    settings.ORCH_S3_REGION, settings.ORCH_S3_TIMEOUT, settings.ORCH_S3_MAX_OBJECT_MB = "us-east-1", 5.0, 64
    settings.ORCH_ARTIFACTS_DIR = str(tmp_path / "local-disk")
    return settings


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("runs.s3store.time.sleep", lambda s: None)


# ------------------------------------------------------------------------------------------------------------- le client
def test_roundtrip_prefix_and_signed_requests(s3, fake):
    s3.put("12/abc/def", b"bonjour")
    assert fake.objects == {"fluxonata/12/abc/def": b"bonjour"}  # préfixe appliqué, adressage path-style
    assert s3.get("12/abc/def") == b"bonjour"
    s3.delete("12/abc/def")
    s3.delete("12/abc/def")  # supprimer deux fois n'est pas une erreur (comme FileSystemStore)
    assert fake.objects == {}


def test_a_missing_object_is_file_not_found_like_the_filesystem_store(s3):
    with pytest.raises(FileNotFoundError):
        s3.get("1/absent/x")


def test_server_errors_are_retried_then_succeed(s3, fake):
    fake.fail_next = [503, 500]
    s3.put("1/a/b", b"x")
    assert s3.get("1/a/b") == b"x" and [m for m, _ in fake.requests].count("PUT") == 3


def test_persistent_failure_gives_a_clear_error_without_any_secret(s3, fake):
    fake.always = 500
    with pytest.raises(ArtifactStoreError) as e:
        s3.put("1/a/b", b"x")
    msg = str(e.value)
    assert "HTTP 500" in msg and "InternalError" in msg and SECRET not in msg and ACCESS not in msg
    assert len(fake.requests) == 3  # 1 essai + 2 reprises, puis on renonce


def test_client_errors_are_not_retried(fake):
    bad = S3Store(fake.url, BUCKET, access_key=ACCESS + "x", secret_key="autre-secret-123456")
    with pytest.raises(ArtifactStoreError, match="403 SignatureDoesNotMatch"):
        bad.put("1/a/b", b"x")
    assert len(fake.requests) == 1


def test_a_truncated_download_is_refused_not_silently_accepted(s3, fake):
    s3.put("1/a/b", b"0123456789")
    fake.truncate = True
    with pytest.raises(ArtifactStoreError, match="tronqué"):
        s3.get("1/a/b")


def test_size_limit_applies_both_ways(fake):
    small = S3Store(fake.url, BUCKET, access_key=ACCESS, secret_key=SECRET, max_bytes=10)
    with pytest.raises(ArtifactStoreError, match="plus grand"):
        small.put("1/a/b", b"x" * 11)
    fake.objects["1/a/b"] = b"x" * 11
    with pytest.raises(ArtifactStoreError, match="tronqué|plus grand"):
        small.get("1/a/b")


def test_a_host_outside_the_egress_allowlist_is_refused_with_the_fix(settings, fake):
    settings.ORCH_EGRESS_ALLOWLIST = ""
    store = S3Store(fake.url, BUCKET, access_key=ACCESS, secret_key=SECRET)
    with pytest.raises(ArtifactStoreError, match="ORCH_EGRESS_ALLOWLIST"):
        store.put("1/a/b", b"x")
    assert fake.requests == []  # rien n'est parti


@pytest.mark.parametrize("key", ["../etc/passwd", "a/../b", "/abs", "a//b", "", "a b", "é"])
def test_unsafe_keys_are_refused_before_any_request(s3, fake, key):
    with pytest.raises(ValueError):
        s3.put(key, b"x")
    assert fake.requests == []


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"endpoint": "ftp://x"}, "ORCH_S3_ENDPOINT"),
        ({"endpoint": "http://user:pw@host"}, "ORCH_S3_ENDPOINT"),
        ({"bucket": "Pas Valide"}, "ORCH_S3_BUCKET"),
        ({"access_key": ""}, "ORCH_S3_ACCESS_KEY"),
        ({"secret_key": ""}, "ORCH_S3_ACCESS_KEY"),
    ],
)
def test_invalid_configuration_fails_at_startup_with_the_setting_name(kwargs, message):
    args = {"endpoint": "http://127.0.0.1:9", "bucket": BUCKET, "access_key": ACCESS, "secret_key": SECRET} | kwargs
    with pytest.raises(ImproperlyConfigured, match=message):
        S3Store(args.pop("endpoint"), args.pop("bucket"), **args)


def test_get_store_selects_the_backend_from_settings(settings, s3_settings, tmp_path):
    assert isinstance(get_store(), S3Store)
    s3_settings.ORCH_ARTIFACTS_STORE = "fs"
    assert isinstance(get_store(), FileSystemStore)
    s3_settings.ORCH_ARTIFACTS_STORE = "ftp"
    with pytest.raises(ImproperlyConfigured, match="fs ou s3"):
        get_store()


# --------------------------------------------------------------------- à travers le produit : API, worker, deux hôtes
def test_worker_stores_in_s3_and_another_host_serves_the_download(world, s3_settings, fake, tmp_path):
    """Le worker (hôte 1, volume local A) produit le rapport ; l'API (hôte 2, volume local B, vide) le sert. Rien ne doit rester sur disque."""
    host_a, host_b = tmp_path / "host-a", tmp_path / "host-b"
    s3_settings.ORCH_ARTIFACTS_DIR = str(host_a)
    ex = run_demo(world, MAKE_REPORT, {"title": "Bilan", "lines": ["un", "deux"]})
    assert ex.status == "succeeded"
    art = Artifact.objects.get()
    assert art.storage_key in fake.objects  # dans le service...
    assert not host_a.exists() or not any(host_a.rglob("*"))  # ...et pas sur le disque du worker

    s3_settings.ORCH_ARTIFACTS_DIR = str(host_b)  # « autre hôte » : un autre volume, vide
    r = client_for(world["designer"]).get(f"/api/v1/workspaces/team-a/artifacts/{art.pk}/download/")
    assert r.status_code == 200 and b"# Bilan" in r.content
    assert not host_b.exists() or not any(host_b.rglob("*"))


def test_download_errors_are_http_errors_not_500s(world, s3_settings, fake):
    ex = run_demo(world, MAKE_REPORT, {"title": "T", "lines": ["x"]})
    art = Artifact.objects.get()
    c = client_for(world["designer"])
    url = f"/api/v1/workspaces/team-a/artifacts/{art.pk}/download/"
    assert c.get(url).status_code == 200
    fake.objects.clear()  # l'objet a disparu du service
    r = c.get(url)
    assert r.status_code == 404 and "plus présent" in json.dumps(r.json(), ensure_ascii=False)
    fake.always = 500  # le service est en panne
    r = c.get(url)
    assert r.status_code == 503 and SECRET not in r.content.decode() and ex.id
    with pytest.raises(Exception, match="indisponible"):
        fetch("x/y/z")


# --------------------------------------------------------------------------------------------- migration depuis le disque
@pytest.fixture
def local_files(world, settings, tmp_path):
    """Un volume local avec 2 artefacts et 1 archive de paquet, tels que l'ancienne installation les aurait écrits."""
    settings.ORCH_ARTIFACTS_STORE = "fs"
    settings.ORCH_ARTIFACTS_DIR = str(tmp_path / "old-volume")
    ex = run_demo(world, MAKE_REPORT, {"title": "Ancien", "lines": ["a"]})
    first = Artifact.objects.get()
    store = get_store()
    data = b"second fichier"
    store.put("1/exec/second", data)
    Artifact.objects.create(
        workspace=first.workspace, execution=ex, name="second.txt", content_type="text/plain", size=len(data),
        sha256=hashlib.sha256(data).hexdigest(), storage_key="1/exec/second",
    )  # fmt: skip
    pkg = Package.objects.create(workspace=first.workspace, slug="pq", name="Paquet")
    zip_bytes = b"PK-faux-zip"
    store.put("pk/pq/1.0.0", zip_bytes)
    PackageVersion.objects.create(
        package=pkg,
        version="1.0.0",
        manifest={},
        checksum="sha256:" + hashlib.sha256(zip_bytes).hexdigest(),
        archive_key="pk/pq/1.0.0",
    )
    return settings.ORCH_ARTIFACTS_DIR, store


def configure_s3(settings, fake):
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    settings.ORCH_S3_ENDPOINT, settings.ORCH_S3_BUCKET, settings.ORCH_S3_PREFIX = fake.url, BUCKET, ""
    settings.ORCH_S3_ACCESS_KEY, settings.ORCH_S3_SECRET_KEY = ACCESS, SECRET
    settings.ORCH_S3_REGION, settings.ORCH_S3_TIMEOUT, settings.ORCH_S3_MAX_OBJECT_MB = "us-east-1", 5.0, 64


def test_migration_copies_verifies_and_is_replayable(local_files, settings, fake):
    src_dir, src = local_files
    configure_s3(settings, fake)
    keys = {a.storage_key for a in Artifact.objects.all()} | {"pk/pq/1.0.0"}
    call_command("artifacts_migrate", source_dir=src_dir, dry_run=True)
    assert fake.objects == {}  # --dry-run n'écrit rien
    call_command("artifacts_migrate", source_dir=src_dir, create_bucket=True)
    assert keys <= set(fake.objects) and all(fake.objects[k] == src.get(k) for k in keys)
    assert all(src.get(k) for k in keys)  # la source est conservée par défaut
    call_command("artifacts_migrate", source_dir=src_dir)  # rejouable sans erreur


def test_migration_can_remove_the_source_only_after_a_verified_copy(local_files, settings, fake):
    src_dir, src = local_files
    configure_s3(settings, fake)
    call_command("artifacts_migrate", source_dir=src_dir, delete_source=True)
    for key in ("1/exec/second", "pk/pq/1.0.0"):
        assert key in fake.objects
        with pytest.raises(FileNotFoundError):
            src.get(key)


def test_migration_refuses_a_corrupted_source_and_reports_missing_files(local_files, settings, fake, capsys):
    src_dir, src = local_files
    configure_s3(settings, fake)
    src.put("1/exec/second", b"altere")  # le contenu ne correspond plus à l'empreinte enregistrée
    src.delete("pk/pq/1.0.0")  # et une archive a disparu
    with pytest.raises(CommandError, match="fidèlement"):
        call_command("artifacts_migrate", source_dir=src_dir, delete_source=True)
    err = capsys.readouterr().err
    assert "ALTÉRÉE" in err and "absent de la source" in err
    assert "1/exec/second" not in fake.objects  # rien d'altéré n'est copié
    assert src.get("1/exec/second") == b"altere"  # et la source n'est pas supprimée


def test_migration_stops_cleanly_when_the_service_is_unusable(local_files, settings, fake):
    src_dir, _ = local_files
    configure_s3(settings, fake)
    fake.always = 500
    with pytest.raises(CommandError, match="Stockage S3 inutilisable"):
        call_command("artifacts_migrate", source_dir=src_dir)


# ------------------------------------------------------------------------------------------------- vrai serveur (SeaweedFS)
SEAWEED = "chrislusf/seaweedfs@sha256:c2658820e763294a9e9de7b36c81e59b879e3459148351aee4e31a126ca64860"


def _seaweed_available() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "image", "inspect", SEAWEED], capture_output=True).returncode == 0


@pytest.fixture(scope="module")
def real_s3(tmp_path_factory):
    if not _seaweed_available():
        pytest.skip("docker ou l'image SeaweedFS épinglée absents (pas de téléchargement automatique)")
    cfg = tmp_path_factory.mktemp("s3cfg") / "s3.json"
    cfg.write_text(
        json.dumps(
            {
                "identities": [
                    {
                        "name": "t",
                        "credentials": [{"accessKey": ACCESS, "secretKey": SECRET}],
                        "actions": ["Admin", "Read", "Write", "List"],
                    }
                ]
            }
        )
    )
    name = f"fluxonata-s3-test-{int(time.time())}"
    out = subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name, "-p", "127.0.0.1::8333", "-v", f"{cfg}:/etc/seaweedfs/s3.json:ro", SEAWEED, "server", "-dir=/data", "-s3", "-s3.config=/etc/seaweedfs/s3.json"],
        capture_output=True, text=True,
    )  # fmt: skip
    if out.returncode:
        pytest.skip(f"SeaweedFS n'a pas démarré : {out.stderr[:200]}")
    try:
        port = re.search(
            r":(\d+)$",
            subprocess.run(["docker", "port", name, "8333/tcp"], capture_output=True, text=True).stdout.splitlines()[0],
        ).group(1)
        url = f"http://127.0.0.1:{port}"
        for _ in range(60):
            probe = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", url], capture_output=True, text=True
            ).stdout
            if probe.startswith(("2", "4")):
                break
            time.sleep(1)
        else:
            pytest.skip("SeaweedFS n'a pas répondu à temps")
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.mark.docker
def test_a_real_s3_service_accepts_the_signatures_and_round_trips_bytes(real_s3, settings):
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    store = S3Store(real_s3, "fluxonata-real", access_key=ACCESS, secret_key=SECRET, prefix="essai", timeout=15)
    store.create_bucket()
    store.ping()
    payload = bytes(range(256)) * 400  # 100 Ko binaires : pas de texte, tous les octets
    store.put("7/exec/fichier.bin", payload)
    assert store.get("7/exec/fichier.bin") == payload
    store.delete("7/exec/fichier.bin")
    with pytest.raises(FileNotFoundError):
        store.get("7/exec/fichier.bin")
    wrong = S3Store(real_s3, "fluxonata-real", access_key=ACCESS, secret_key="mauvais-secret-000000", timeout=15)
    with pytest.raises(ArtifactStoreError, match="403"):
        wrong.put("7/exec/x", b"1")
