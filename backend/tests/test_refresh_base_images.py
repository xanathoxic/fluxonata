"""scripts/refresh-base-images.py : épinglage des images de base des Dockerfiles par condensat (résolveur simulé, sans réseau)."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "refresh-base-images.py"
OLD, NEW = "sha256:" + "a" * 64, "sha256:" + "b" * 64
DOCKERFILE = """FROM python:3.13-slim AS runtime
RUN echo un
FROM runtime AS exec
FROM nginx:1.27-alpine
"""


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("refresh_base_images", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_scan_ignores_internal_stages(mod):
    assert [(img, d) for _, img, d, _ in mod.scan(DOCKERFILE)] == [
        ("python:3.13-slim", None),
        ("nginx:1.27-alpine", None),
    ]


def test_write_pins_external_images_and_keeps_stage_names(mod):
    new, report = mod.process(DOCKERFILE, resolver=lambda image: NEW, write=True)
    assert f"FROM python:3.13-slim@{NEW} AS runtime" in new
    assert f"FROM nginx:1.27-alpine@{NEW}" in new
    assert "FROM runtime AS exec" in new and "RUN echo un" in new
    assert [state for _, state in report] == ["non épinglée", "non épinglée"]


def test_a_pinned_up_to_date_file_is_left_unchanged(mod):
    pinned, _ = mod.process(DOCKERFILE, resolver=lambda image: NEW, write=True)
    again, report = mod.process(pinned, resolver=lambda image: NEW, write=True)
    assert again == pinned and {state for _, state in report} == {"à jour"}


def test_outdated_pin_is_reported_without_writing_and_rewritten_with_write(mod):
    pinned = DOCKERFILE.replace("FROM nginx:1.27-alpine", f"FROM nginx:1.27-alpine@{OLD}")
    text, report = mod.process(pinned, resolver=lambda image: NEW)
    assert text == pinned and ("nginx:1.27-alpine", "périmée") in report
    text, _ = mod.process(pinned, resolver=lambda image: NEW, write=True)
    assert f"nginx:1.27-alpine@{NEW}" in text and OLD not in text


def test_unresolvable_image_fails_loudly_and_does_not_rewrite(mod):
    def boom(image):
        raise RuntimeError("condensat introuvable")

    with pytest.raises(RuntimeError):
        mod.process(DOCKERFILE, resolver=boom, write=True)


COMPOSE = """services:
  db:
    image: postgres:17-alpine   # base
  api:
    image: ${ORCH_BACKEND_IMAGE:-fluxonata-backend:local}
    build: { context: .. }
  web:
    image: fluxonata-web-roles:local
  pooler:
    image: edoburu/pgbouncer@sha256:%s
  helper:
    image: python:3.13-slim
  second:
    image: postgres:17-alpine
""" % ("c" * 64)


def test_compose_scan_keeps_only_external_tagged_images(mod):
    assert [(img, d) for _, img, d, _, _ in mod.scan_compose(COMPOSE)] == [
        ("postgres:17-alpine", None),
        ("python:3.13-slim", None),
        ("postgres:17-alpine", None),
    ]  # ni ${...}, ni :local, ni l'image déjà épinglée sans étiquette


def test_compose_write_pins_external_images_and_keeps_comments_and_the_rest(mod):
    calls = []

    def resolver(image):
        calls.append(image)
        return NEW

    new, report = mod.process(COMPOSE, resolver=resolver, write=True, compose=True)
    assert f"    image: postgres:17-alpine@{NEW}   # base" in new  # le commentaire de fin de ligne est conservé
    assert f"    image: python:3.13-slim@{NEW}" in new
    assert "${ORCH_BACKEND_IMAGE:-fluxonata-backend:local}" in new and "fluxonata-web-roles:local" in new
    assert f"edoburu/pgbouncer@sha256:{'c' * 64}" in new  # épinglée à la main : intacte
    assert calls.count("postgres:17-alpine") == 1  # une seule résolution par image, même si elle apparaît deux fois
    assert [state for _, state in report] == ["non épinglée"] * 3
    again, report2 = mod.process(new, resolver=lambda image: NEW, write=True, compose=True)
    assert again == new and {state for _, state in report2} == {"à jour"}


def test_every_external_image_of_the_shipped_compose_files_is_pinned(mod):
    """Garde contre la dérive (sans réseau) : toute image externe à étiquette des fichiers Compose livrés porte un condensat."""
    unpinned = []
    for rel in mod.COMPOSE_FILES:
        text = (mod.ROOT / rel).read_text(encoding="utf-8")
        unpinned += [(rel, img) for _, img, digest, _, _ in mod.scan_compose(text) if digest is None]
    assert mod.COMPOSE_FILES and unpinned == [], (
        f"images non épinglées : {unpinned} (scripts/refresh-base-images.py --write)"
    )
