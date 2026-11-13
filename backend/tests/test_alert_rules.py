"""Règles d'alerte Prometheus (infra/monitoring) : elles ne citent que des séries réellement exposées par `/api/v1/metrics/`.

Le déclenchement lui-même est éprouvé par `scripts/alerts-test.sh` (promtool, séries synthétiques) ; ici on garantit que les
règles ne dérivent pas des métriques de l'application (renommage, étiquette retirée)."""

import re
from pathlib import Path

import pytest
import yaml

RULES = Path(__file__).resolve().parents[2] / "infra" / "monitoring" / "fluxonata.rules.yml"
SELECTOR = re.compile(r"(fluxonata_[a-z_]+)(?:\{([^}]*)\})?")
MATCHER = re.compile(r'(\w+)="([^"]*)"')


def _rules():
    doc = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    return [r for g in doc["groups"] for r in g["rules"]]


def test_chaque_regle_a_une_gravite_un_resume_et_une_description():
    seen = set()
    for r in _rules():
        assert r["alert"] not in seen
        seen.add(r["alert"])
        assert r["labels"]["severity"] in ("critical", "warning"), r["alert"]
        assert r["annotations"]["summary"] and r["annotations"]["description"], r["alert"]
        assert "for" in r, r["alert"]
    assert len(seen) == 7


def _exposee(lines, name, wanted):
    return any(ln.startswith(name) and all(f'{k}="{v}"' in ln.split(" ")[0] for k, v in wanted.items()) for ln in lines)


@pytest.mark.django_db
def test_les_series_citees_par_les_regles_sont_exposees_par_les_metriques(client, settings):
    from django.utils import timezone

    from runs.models import ServiceInstance

    settings.ORCH_METRICS_TOKEN = "t0ken"
    for kind in ("worker", "worker-aux", "orchestrator"):
        ServiceInstance.objects.create(kind=kind, name=kind, hostname="h", last_seen_at=timezone.now())
    text = client.get("/api/v1/metrics/", HTTP_AUTHORIZATION="Bearer t0ken").content.decode()
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    cited = 0
    for r in _rules():
        for name, labels in SELECTOR.findall(r["expr"]):
            wanted = dict(MATCHER.findall(labels))
            ok = _exposee(lines, name, wanted)
            assert ok, f"{r['alert']} : la série {name}{{{labels}}} n'est pas exposée par /api/v1/metrics/"
            cited += 1
    assert cited == 6  # les 6 règles qui s'appuient sur une métrique de l'application (la 7e utilise `up`)


def test_le_controle_detecte_une_serie_ou_une_etiquette_absente():
    lines = ['fluxonata_service_instances{kind="worker"} 1', "fluxonata_workers_alive 1"]
    assert _exposee(lines, "fluxonata_service_instances", {"kind": "worker"})
    assert not _exposee(lines, "fluxonata_service_instances", {"kind": "orchestrator"})  # étiquette absente
    assert not _exposee(lines, "fluxonata_file_inexistante", {})  # série absente
