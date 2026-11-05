"""AR2.5 — matrice rôle × privilège de la pile de production, vérifiée sur `infra/docker-compose.prod.yml` et `infra/Dockerfile.backend`.

Un changement de privilège d'un service doit être UNE DÉCISION : ces tests échouent quand la matrice change, et il faut alors mettre à jour à la fois ce
fichier et `docs/ARCHITECTURE.md` (§ 2 et 5). Les invariants encore non tenus (I1) sont marqués `xfail(strict=True)` : ils feront échouer la suite le jour où
ils deviendront vrais, pour qu'on les promeuve en invariants durs au lieu de les oublier.
"""

from pathlib import Path

import pytest
import yaml

INFRA = Path(__file__).resolve().parents[2] / "infra"
STACK = yaml.safe_load((INFRA / "docker-compose.prod.yml").read_text(encoding="utf-8"))["services"]
DOCKERFILE = (INFRA / "Dockerfile.backend").read_text(encoding="utf-8")
BACKEND = ("api", "orchestrator", "worker", "migrate")


def volumes(name):
    return [str(v) for v in (STACK[name].get("volumes") or [])]


def has_keys(name):
    return any(v.startswith("keys:") for v in volumes(name))


def has_artifacts(name):
    return any(v.startswith("artifacts:") for v in volumes(name))


def has_socket(name):
    return any("docker.sock" in v for v in volumes(name))


def hardening(name):
    s = STACK[name]
    return {
        "cap_drop_all": "ALL" in (s.get("cap_drop") or []),
        "no_new_privileges": "no-new-privileges:true" in (s.get("security_opt") or []),
        "read_only": bool(s.get("read_only")),
    }


def env_of(name):
    e = STACK[name].get("environment") or {}
    return e if isinstance(e, dict) else dict(x.split("=", 1) for x in e)


def stage(name):
    """Texte d'une étape du Dockerfile (de `FROM … AS name` à la `FROM` suivante)."""
    parts = DOCKERFILE.split("\nFROM ")
    return next(p for p in parts if f" AS {name}\n" in p + "\n")


# --- matrice exacte d'aujourd'hui ------------------------------------------------------------------------------------------
EXPECTED = {
    #            clés   artefacts  socket  cap_drop  no-new-priv  lecture seule
    "api": (True, True, False, True, True, True),
    "orchestrator": (False, False, False, True, True, True),
    "worker": (True, True, True, False, True, False),
    "migrate": (False, False, False, True, True, True),
    "pgbouncer": (False, False, False, False, False, False),
    "db": (False, False, False, False, False, False),
    "web": (False, False, False, False, False, False),
    "testsvc": (False, False, False, True, False, True),
}


def test_every_service_is_in_the_matrix():
    assert set(STACK) == set(EXPECTED), (
        "service ajouté ou retiré : décider de ses privilèges et mettre à jour ce test et docs/ARCHITECTURE.md"
    )


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_privileges_of_each_service_are_exactly_the_documented_ones(name):
    h = hardening(name)
    got = (
        has_keys(name),
        has_artifacts(name),
        has_socket(name),
        h["cap_drop_all"],
        h["no_new_privileges"],
        h["read_only"],
    )
    assert got == EXPECTED[name], (
        f"{name} : (clés, artefacts, socket, cap_drop, no-new-privileges, lecture seule) = {got}"
    )


# --- invariants tenus (docs/ARCHITECTURE.md § 5) ----------------------------------------------------------------------------
def test_I2_only_the_worker_holds_the_docker_socket():
    assert [n for n in STACK if has_socket(n)] == ["worker"]


def test_I5_migrate_has_neither_the_keys_nor_the_artifacts():
    assert not has_keys("migrate") and not has_artifacts("migrate") and not volumes("migrate")


def test_I6_the_docker_cli_exists_only_in_the_worker_image():
    assert "docker-cli" not in stage("runtime") and "docker-cli" in stage("exec")
    assert DOCKERFILE.rstrip().splitlines()[-3:].count("USER fluxonata") == 1  # l'étape finale ne laisse pas root
    targets = {n: (STACK[n].get("build") or {}).get("target") for n in ("migrate", "worker")}
    assert targets == {"migrate": "runtime", "worker": "exec"}
    assert (
        STACK["worker"]["image"] != STACK["api"]["image"]
        and STACK["api"]["image"] == STACK["migrate"]["image"] == STACK["orchestrator"]["image"]
    )


def test_the_exec_stage_is_the_last_one_so_a_plain_build_keeps_the_historical_image():
    assert DOCKERFILE.split("\nFROM ")[-1].split("\n")[0].endswith("AS exec")


def test_the_vault_key_is_never_created_implicitly_in_production():
    assert all(env_of(n).get("ORCH_KEY_AUTOCREATE") == "0" for n in BACKEND)


def test_only_the_worker_runs_as_root():
    assert STACK["worker"]["user"] == "0:0"
    assert all("user" not in STACK[n] for n in ("api", "orchestrator", "migrate"))  # l'image impose l'utilisateur 10001


# --- invariants PAS encore tenus : ils doivent échouer franchement le jour où ils le deviennent ---------------------------------
@pytest.mark.xfail(
    strict=True,
    reason="I1 : le worker détient encore le socket Docker ET la clé du coffre (AR3 : agent d'exécution sans clé)",
)
def test_I1_nobody_holds_both_the_docker_socket_and_the_vault_key():
    assert not [n for n in STACK if has_socket(n) and has_keys(n)]


def test_I4_the_orchestrator_has_neither_the_vault_key_nor_the_artifacts():
    assert not has_keys("orchestrator") and not has_artifacts(
        "orchestrator"
    )  # AR4.2 : les rappels sortants partent du rôle « aux »


# --- I7 : sans le privilège, le rôle échoue franchement (côté code) --------------------------------------------------------------
def test_I7_without_a_docker_cli_the_image_digest_is_none_and_nothing_raises(monkeypatch):
    from runs import engine

    def no_docker(*a, **k):
        raise FileNotFoundError("docker")

    engine._digest_cache.clear()
    monkeypatch.setattr("subprocess.run", no_docker)
    assert engine._image_digest("python:3.13-slim") is None
    engine._digest_cache.clear()
