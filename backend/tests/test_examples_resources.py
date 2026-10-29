"""Les ressources de examples/tests-automatisation (paquet d'actions, skills IA) s'installent sur un espace vierge."""

import importlib.util
import json
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile

from ai.models import AiSkill
from catalog.models import Action
from tests.conftest import client_for, make_user, make_workspace

OUTILS = Path(__file__).resolve().parents[2] / "examples" / "tests-automatisation"
EXPECTED_ACTIONS = {
    "text-stats",
    "slow-wait",
    "send-notification",
    "json-transform",
    "make-report",
    "http-test-service",
}


def installer():
    spec = importlib.util.spec_from_file_location("installer_ressources", OUTILS / "outils" / "installer_ressources.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_package_and_skills_install_on_a_blank_workspace_and_twice(db):
    ws = make_workspace("vierge")
    c = client_for(make_user("dana", ws))
    base = "/api/v1/workspaces/vierge"
    data = installer().paquet_zip()

    def importer(mode="apply"):
        return c.post(
            f"{base}/packages/import/",
            {"file": SimpleUploadedFile("p.zip", data), "mode": mode, "strategy": "skip"},
            format="multipart",
        )

    preview = importer("preview")
    assert preview.status_code == 200 and not preview.json()["errors"], preview.json()
    assert not Action.objects.filter(workspace=ws).exists()  # l'aperçu n'écrit rien
    applied = importer()
    assert applied.status_code == 200 and applied.json()["applied"] is True, applied.json()
    assert set(Action.objects.filter(workspace=ws).values_list("slug", flat=True)) == EXPECTED_ACTIONS
    assert importer().json()["applied"] is False  # deuxième passage : rien de plus (déjà importé)

    for f in sorted((OUTILS / "ressources" / "skills").glob("*.json")):
        body = json.loads(f.read_text(encoding="utf-8"))
        r = c.post(f"{base}/ai/skills/", body, format="json")
        assert r.status_code == 201, (f.name, r.json())
    assert set(AiSkill.objects.filter(workspace=ws).values_list("slug", flat=True)) == {
        "classer-message",
        "resumer-alerte",
    }


def test_installer_multipart_body_is_accepted_by_the_api(db):
    mod = installer()
    ws = make_workspace("vierge2")
    c = client_for(make_user("dora", ws))
    body, ctype = mod.multipart({"mode": "apply", "strategy": "skip"}, ("p.zip", mod.paquet_zip()))
    r = c.generic("POST", "/api/v1/workspaces/vierge2/packages/import/", body, content_type=ctype)
    assert r.status_code == 200 and r.json()["applied"] is True, r.content[:300]
    assert Action.objects.filter(workspace=ws, slug="text-stats").exists()
