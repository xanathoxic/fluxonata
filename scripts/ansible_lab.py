#!/usr/bin/env python3
"""Laboratoire Ansible (ADR 0037) : des machines SSH jetables, sur un réseau Docker dédié, pour valider de bout en bout l'exécution de playbooks.

    scripts/ansible-lab.sh up        construit l'image, crée les clés, le réseau et les conteneurs (web1, web2, db1)
    scripts/ansible-lab.sh down      arrête et supprime les conteneurs et le réseau (les clés restent)
    scripts/ansible-lab.sh status    état des conteneurs
    scripts/ansible-lab.sh clean     down + supprime le dossier du laboratoire (clés comprises)
    scripts/ansible-lab.sh agent-up | agent-down   le runner dédié (agent) qui joue les playbooks sur ce réseau

Dossier du laboratoire (var/ansible-lab/, ignoré par Git) : id_lab (clé privée), hostkeys/<machine>/…, known_hosts, lab.json, runner.token, agent.*.
Rien d'ici n'est un secret de production : clés synthétiques, machines éphémères. Uniquement la bibliothèque standard.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "fluxonata-lab-sshd:local"
DEFAULT_NETWORK = "fluxonata-ansible-targets"
DEFAULT_MACHINES = {"web1": "web", "web2": "web", "db1": "db"}  # nom court -> groupe
PREFIX = "flx-lab-"


@dataclass
class Lab:
    directory: Path = ROOT / "var" / "ansible-lab"
    network: str = DEFAULT_NETWORK
    machines: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MACHINES))
    prefix: str = PREFIX

    def container(self, short: str) -> str:
        return f"{self.prefix}{short}"

    @property
    def key(self) -> Path:
        return self.directory / "id_lab"

    @property
    def known_hosts(self) -> Path:
        return self.directory / "known_hosts"

    @property
    def info(self) -> Path:
        return self.directory / "lab.json"


def docker(*args: str, check: bool = True, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=check, **kw
    )  # noqa: S603,S607 - arguments fixes, docker du PATH


def have_docker() -> bool:
    return (
        shutil.which("docker") is not None
        and docker("info", check=False).returncode == 0
    )


def build_image(force: bool = False) -> None:
    if not force and docker("image", "inspect", IMAGE, check=False).returncode == 0:
        return
    docker(
        "build",
        "-q",
        "-f",
        str(ROOT / "infra/lab/ansible/Dockerfile.sshd"),
        "-t",
        IMAGE,
        str(ROOT / "infra/lab/ansible"),
    )


def ensure_keys(lab: Lab) -> None:
    """La paire de clés de connexion et une clé d'hôte par machine (stables : la liste known_hosts reste valable d'un démarrage à l'autre)."""
    lab.directory.mkdir(parents=True, exist_ok=True)
    lab.directory.chmod(0o700)
    if not lab.key.exists():
        subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "fluxonata-lab",
                "-f",
                str(lab.key),
            ],
            check=True,
        )  # noqa: S603,S607
    lines = []
    for short in lab.machines:
        host_dir = lab.directory / "hostkeys" / short
        key = host_dir / "ssh_host_ed25519_key"
        if not key.exists():
            host_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "ssh-keygen",
                    "-q",
                    "-t",
                    "ed25519",
                    "-N",
                    "",
                    "-C",
                    lab.container(short),
                    "-f",
                    str(key),
                ],
                check=True,
            )  # noqa: S603,S607
        pub = (host_dir / "ssh_host_ed25519_key.pub").read_text().split()
        lines.append(f"{lab.container(short)} {pub[0]} {pub[1]}")
    lab.known_hosts.write_text("\n".join(lines) + "\n")


def write_info(lab: Lab) -> None:
    lab.info.write_text(
        json.dumps(
            {
                "network": lab.network,
                "user": "deploy",
                "key": str(lab.key),
                "known_hosts": lab.known_hosts.read_text(),
                "machines": [
                    {"name": lab.container(s), "short": s, "group": g}
                    for s, g in lab.machines.items()
                ],
            },
            indent=2,
        )
        + "\n"
    )


def ensure_network(lab: Lab) -> None:
    if docker("network", "inspect", lab.network, check=False).returncode != 0:
        docker("network", "create", lab.network)


def _start(lab: Lab, short: str) -> None:
    name = lab.container(short)
    docker("rm", "-f", name, check=False)
    docker(
        "create",
        "--name",
        name,
        "--hostname",
        name,
        "--network",
        lab.network,
        "--label",
        "fluxonata.lab=ansible",
        IMAGE,
    )
    host_dir = lab.directory / "hostkeys" / short
    docker(
        "cp",
        str(host_dir / "ssh_host_ed25519_key"),
        f"{name}:/etc/ssh/lab/ssh_host_ed25519_key",
    )  # copiés : propriété root dans le conteneur
    docker(
        "cp", str(lab.directory / "id_lab.pub"), f"{name}:/etc/ssh/lab/authorized_keys"
    )
    docker("start", name)


def wait_ready(lab: Lab, timeout: float = 40.0) -> None:
    """Chaque machine répond-elle en SSH ? (poignée de main sur le port 22 depuis le réseau du laboratoire, avec l'image d'Ansible si présente, sinon le conteneur lui-même)"""
    deadline = time.monotonic() + timeout
    pending = set(lab.machines)
    while pending and time.monotonic() < deadline:
        for short in list(pending):
            r = docker(
                "exec",
                lab.container(short),
                "sh",
                "-c",
                "nc -z 127.0.0.1 22",
                check=False,
            )
            if r.returncode == 0:
                pending.discard(short)
        if pending:
            time.sleep(0.5)
    if pending:
        raise RuntimeError(
            f"machines qui ne répondent pas : {', '.join(sorted(pending))}"
        )


def up(lab: Lab) -> None:
    if not have_docker():
        raise RuntimeError("Docker est indisponible.")
    build_image()
    ensure_keys(lab)
    ensure_network(lab)
    for short in lab.machines:
        _start(lab, short)
    wait_ready(lab)
    write_info(lab)


def down(lab: Lab, remove_network: bool = True) -> None:
    for short in lab.machines:
        docker("rm", "-f", lab.container(short), check=False)
    if remove_network:
        docker("network", "rm", lab.network, check=False)


def status(lab: Lab) -> list[tuple[str, str]]:
    out = []
    for short in lab.machines:
        r = docker(
            "inspect", "-f", "{{.State.Status}}", lab.container(short), check=False
        )
        out.append(
            (lab.container(short), r.stdout.strip() if r.returncode == 0 else "absent")
        )
    return out


# --- le runner dédié : l'agent réel, en processus de l'hôte, qui lance les playbooks dans des conteneurs sur le réseau du laboratoire ------------------


def agent_env(lab: Lab, url: str) -> dict[str, str]:
    return {
        **os.environ,
        "ORCH_URL": url,
        "ORCH_BACKEND_DIR": str(ROOT / "backend"),
        "ORCH_WORKERS_DIR": str(ROOT / "workers"),
        "ORCH_ANSIBLE_NETWORK": lab.network,
        "ORCH_ANSIBLE_IMAGE": os.environ.get(
            "ORCH_ANSIBLE_IMAGE", "fluxonata-runner-ansible:local"
        ),
        "ORCH_RUNNER_EXECUTOR": "docker",
        "ORCH_REQUIRE_IMAGE_DIGEST": "0",
        "ORCH_WORK_DIR": str(lab.directory / "work"),
        "PYTHONUNBUFFERED": "1",
    }


def agent_up(lab: Lab, url: str, python: str | None = None) -> int:
    token = lab.directory / "runner.token"
    if not token.exists():
        raise RuntimeError(
            "runner.token absent : lancez d'abord « manage.py seed_ansible_lab » (il crée le runner et son jeton)."
        )
    pid_file = lab.directory / "agent.pid"
    if pid_file.exists() and _alive(int(pid_file.read_text() or 0)):
        return int(pid_file.read_text())
    (lab.directory / "work").mkdir(exist_ok=True)
    log = open(lab.directory / "agent.log", "ab")  # noqa: SIM115 - ouvert pour toute la vie du processus
    proc = subprocess.Popen(  # noqa: S603
        [
            python or sys.executable,
            str(ROOT / "runner" / "agent.py"),
            "--url",
            url,
            "--token-file",
            str(token),
            "--concurrency",
            "2",
        ],
        stdout=log,
        stderr=log,
        env=agent_env(lab, url),
        start_new_session=True,
    )
    pid_file.write_text(str(proc.pid))
    return proc.pid


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return pid > 0


def agent_down(lab: Lab) -> bool:
    pid_file = lab.directory / "agent.pid"
    if not pid_file.exists():
        return False
    pid = int(pid_file.read_text() or 0)
    pid_file.unlink()
    if pid > 0 and _alive(pid):
        os.kill(pid, 15)
        return True
    return False


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    lab = Lab(
        directory=Path(os.environ.get("LAB_DIR", str(ROOT / "var" / "ansible-lab")))
    )
    url = os.environ.get("ORCH_URL", "http://127.0.0.1:8100")
    if cmd == "up":
        up(lab)
        print(
            f"Laboratoire prêt : {', '.join(lab.container(s) for s in lab.machines)} sur le réseau {lab.network}."
        )
        print(
            f"Étape suivante : (cd backend && ../.venv/bin/python manage.py seed_ansible_lab --lab-dir {lab.directory}) puis « scripts/ansible-lab.sh agent-up »."
        )
    elif cmd == "down":
        down(lab)
        print("Laboratoire arrêté.")
    elif cmd == "status":
        for name, state in status(lab):
            print(f"{name:20} {state}")
    elif cmd == "clean":
        agent_down(lab)
        down(lab)
        shutil.rmtree(lab.directory, ignore_errors=True)
        print("Laboratoire supprimé (clés comprises).")
    elif cmd == "agent-up":
        print(
            f"Agent lancé (pid {agent_up(lab, url)}) ; journal : {lab.directory / 'agent.log'}"
        )
    elif cmd == "agent-down":
        print("Agent arrêté." if agent_down(lab) else "Aucun agent en cours.")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
