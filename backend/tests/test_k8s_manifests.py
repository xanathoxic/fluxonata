"""AR6.3 — manifestes Kubernetes du plan de contrôle : vérification STRUCTURELLE seulement (aucun cluster n'est disponible ici : rien n'est appliqué)."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

K8S = Path(__file__).resolve().parents[2] / "infra" / "k8s"


def render():
    if not shutil.which("kubectl"):
        pytest.skip("kubectl indisponible")
    out = subprocess.run(["kubectl", "kustomize", str(K8S)], capture_output=True, text=True, timeout=60)
    if out.returncode:
        pytest.skip(out.stderr[:150])
    return [d for d in yaml.safe_load_all(out.stdout) if d]


@pytest.fixture(scope="module")
def docs():
    return render()


def by_kind(docs, kind):
    return {d["metadata"]["name"]: d for d in docs if d["kind"] == kind}


def pods(docs):
    return {
        n: d["spec"]["template"]["spec"] | {"_labels": d["spec"]["template"]["metadata"]["labels"]}
        for n, d in by_kind(docs, "Deployment").items()
    }


def test_the_expected_workloads_exist_and_the_migration_job_is_applied_separately(docs):
    assert set(by_kind(docs, "Deployment")) == {
        "pgbouncer",
        "api-ui",
        "api-ingest",
        "runner-gw",
        "orchestrator",
        "worker-aux",
    }
    assert "fluxonata-migrate" not in by_kind(docs, "Job")  # exécuté AVANT le changement d'image, jamais avec lui
    assert (K8S / "base" / "migrate-job.yaml").exists()


def test_there_is_no_execution_plane_and_no_docker_socket_anywhere(docs):
    text = yaml.safe_dump(docs)
    assert (
        "docker.sock" not in text
        and "hostPath" not in text
        and "hostNetwork" not in text
        and "privileged: true" not in text
    )
    assert "worker-exec" not in by_kind(
        docs, "Deployment"
    )  # voir README : les actions passent par des runners distants


@pytest.mark.parametrize("name", ["api-ui", "api-ingest", "runner-gw", "orchestrator", "worker-aux"])
def test_application_pods_are_locked_down(docs, name):
    spec = pods(docs)[name]
    assert spec["automountServiceAccountToken"] is False
    for c in spec["containers"]:
        sc = c["securityContext"]
        assert (
            sc["allowPrivilegeEscalation"] is False
            and sc["readOnlyRootFilesystem"] is True
            and sc["runAsNonRoot"] is True
        )
        assert sc["capabilities"]["drop"] == ["ALL"] and sc["seccompProfile"]["type"] == "RuntimeDefault"
        assert c["resources"]["requests"] and c["resources"]["limits"]


def test_the_orchestrator_has_no_vault_key_and_ingest_has_no_way_out(docs):
    p = pods(docs)
    assert not [v for v in p["orchestrator"]["volumes"] if v["name"] == "keys"]  # AR4.2
    assert (
        p["orchestrator"]["_labels"]["fluxonata/egress"] == "denied"
        and p["api-ingest"]["_labels"]["fluxonata/egress"] == "denied"
    )
    assert [n for n, s in p.items() if s["_labels"].get("fluxonata/egress") == "allowed"] == [
        "api-ui",
        "runner-gw",
        "worker-aux",
    ]
    for n in ("api-ui", "api-ingest", "runner-gw", "worker-aux"):
        assert any(
            v["name"] == "keys" and v["secret"]["defaultMode"] == 0o400 for v in p[n]["volumes"]
        )  # lecture seule, jamais écrit par un pod


def test_network_policies_deny_egress_by_default_then_reopen_case_by_case(docs):
    nps = by_kind(docs, "NetworkPolicy")
    assert nps["deny-all-egress"]["spec"]["podSelector"] == {} and nps["deny-all-egress"]["spec"]["policyTypes"] == [
        "Egress"
    ]
    internet = nps["allow-internet-egress-for-labelled-roles"]["spec"]
    assert internet["podSelector"]["matchLabels"] == {
        "fluxonata/egress": "allowed"
    }  # api-ingest et orchestrator ne portent pas cette étiquette
    assert {"allow-dns", "allow-pgbouncer", "ingress-from-controller-only"} <= set(nps)


def test_api_roles_have_probes_pdb_and_a_zero_unavailable_rollout(docs):
    for n in ("api-ui", "api-ingest", "runner-gw"):
        c = pods(docs)[n]["containers"][0]
        assert (
            "/health/ready/" in c["readinessProbe"]["httpGet"]["path"]
            and "/health/live/" in c["livenessProbe"]["httpGet"]["path"]
        )
        assert by_kind(docs, "PodDisruptionBudget")[n]["spec"]["minAvailable"] == 1
        d = by_kind(docs, "Deployment")[n]
        assert d["spec"]["replicas"] >= 2 and d["spec"]["strategy"]["rollingUpdate"]["maxUnavailable"] == 0
    for n, kind in (("orchestrator", "orchestrator"), ("worker-aux", "worker-aux")):
        assert pods(docs)[n]["containers"][0]["livenessProbe"]["exec"]["command"][-3:] == [
            "healthcheck",
            "--kind",
            kind,
        ]
        assert pods(docs)[n]["terminationGracePeriodSeconds"] >= 120


def test_configuration_has_no_secret_and_never_creates_the_vault_key(docs):
    cfg = by_kind(docs, "ConfigMap")["fluxonata-config"]["data"]
    assert (
        cfg["ORCH_KEY_AUTOCREATE"] == "0"
        and cfg["ORCH_ARTIFACTS_STORE"] == "s3"
        and cfg["ORCH_DB_HOST"] == "pgbouncer"
        and cfg["ORCH_DEBUG"] == "0"
    )
    assert not by_kind(docs, "Secret")  # les Secrets se créent hors Git (modèle : secrets.example.yaml)
    for f in K8S.rglob("*.yaml"):
        assert not re.search(r"(?i)(password|secret_key)\s*:\s*['\"]?[A-Za-z0-9+/=_-]{12,}", f.read_text()), f


def test_ingress_prefixes_match_the_application_roles(docs):
    from fluxonata.apiroles import INGEST_PREFIXES, RUNNER_PREFIXES

    paths = {
        p["path"]: p["backend"]["service"]["name"]
        for r in by_kind(docs, "Ingress")["fluxonata"]["spec"]["rules"]
        for p in r["http"]["paths"]
    }
    assert {p.removeprefix("/api/v1/") for p, s in paths.items() if s == "api-ingest"} == set(INGEST_PREFIXES)
    assert {p.removeprefix("/api/v1/") for p, s in paths.items() if s == "runner-gw"} == set(RUNNER_PREFIXES)
    assert paths["/api/"] == "api-ui"
