"""Rendu `docker compose config` de la pile LIVRÉE, sans dépendre du vrai `infra/.env.prod`.

Les fichiers Compose de production déclarent `env_file: .env.prod` (chemin relatif au dossier `infra/`) : sans ce fichier, `config` échoue. Le vrai
fichier contient des secrets, jamais versionnés : les tests ne le lisent, ne le copient ni ne l'écrasent. Ils fabriquent dans un dossier temporaire un
arbre dont chaque entrée de `infra/` est un lien vers l'original (les fichiers Compose réellement livrés, pas une copie à maintenir) SAUF `.env.prod`
(et tout autre `.env*` hors modèle), remplacé par un fichier à valeurs synthétiques déduit du modèle versionné `.env.prod.example`.

Prérequis : seulement le client `docker` et son plugin Compose (le rendu n'interroge aucun démon). Leur absence est une omission explicite ;
toute autre erreur (configuration invalide, JSON illisible, délai dépassé) est un ÉCHEC avec un diagnostic expurgé.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[2] / "infra"
TEMPLATE = ".env.prod.example"
AGENT_TEMPLATE = ".env.agent.example"
_SECRETISH = re.compile(
    r"^([A-Za-z0-9_]*(?:SECRET|TOKEN|PASSWORD|KEY)[A-Za-z0-9_]*)\s*[=:].*$", re.IGNORECASE | re.MULTILINE
)


def synthetic_env_text(template: str) -> str:
    """Valeurs synthétiques : chaque variable vide du modèle reçoit `synthetique-<nom>`, les autres gardent la valeur d'exemple."""
    lines = []
    for line in template.splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        lines.append(f"{m.group(1)}=synthetique-{m.group(1).lower()}" if m and not m.group(2).strip() else line)
    return "\n".join(lines) + "\n"


def build_tree(infra: Path, dest: Path) -> Path:
    """Arbre `dest/infra` : liens vers chaque entrée de `infra`, sauf les `.env*` (hors modèle), et un `.env.prod` synthétique. Ne lit aucun `.env` réel."""
    out = dest / "infra"
    out.mkdir()
    for entry in sorted(infra.iterdir()):
        if entry.name.startswith(".env") and entry.name != TEMPLATE:
            continue
        (out / entry.name).symlink_to(entry)
    (out / ".env.prod").write_text(synthetic_env_text((infra / TEMPLATE).read_text(encoding="utf-8")), encoding="utf-8")
    (out / ".env.prod").chmod(0o600)
    if (
        infra / AGENT_TEMPLATE
    ).exists():  # jeton de l'agent : fichier distinct, chargé par `exec-agent` seul (valeur synthétique reconnaissable dans les tests)
        (out / ".env.agent").write_text(
            synthetic_env_text((infra / AGENT_TEMPLATE).read_text(encoding="utf-8")), encoding="utf-8"
        )
        (out / ".env.agent").chmod(0o600)
    return out


def redact(text: str, limit: int = 800) -> str:
    """Diagnostic sans valeur de secret : les lignes `…SECRET/TOKEN/PASSWORD/KEY…=valeur` sont masquées, la taille bornée."""
    return _SECRETISH.sub(lambda m: f"{m.group(1)}=***", text)[:limit]


def compose_config(files, profiles=(), extra_env=None, infra: Path = INFRA) -> dict:
    """Configuration FUSIONNÉE exacte de `files` (noms dans `infra/`). Omission : client ou plugin Compose absent. Échec : tout le reste."""
    if not shutil.which("docker"):
        pytest.skip("client docker absent : rendu Compose impossible")
    probe = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True, timeout=30)
    if probe.returncode:
        pytest.skip("plugin « docker compose » absent : rendu Compose impossible")
    with tempfile.TemporaryDirectory(prefix="fluxonata-compose-") as tmp:
        tree = build_tree(infra, Path(tmp))
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", tmp),
            "ORCH_DB_PASSWORD": "synthetique",
            "ORCH_WORK_DIR": "/srv/work",
        }
        env.update(extra_env or {})
        cmd = ["docker", "compose"]
        for f in files:
            cmd += ["-f", str(tree / f)]
        cmd += ["--env-file", str(tree / TEMPLATE)]
        for p in profiles:
            cmd += ["--profile", p]
        try:
            out = subprocess.run(
                [*cmd, "config", "--format", "json"], capture_output=True, text=True, env=env, timeout=60
            )
        except subprocess.TimeoutExpired:
            pytest.fail("docker compose config : délai de 60 s dépassé")
        if out.returncode:
            pytest.fail(
                f"docker compose config a échoué (code {out.returncode}) pour {list(files)} :\n{redact(out.stderr)}"
            )
        try:
            return json.loads(out.stdout)
        except json.JSONDecodeError as e:
            pytest.fail(f"docker compose config : sortie JSON illisible ({e.msg}, ligne {e.lineno})")
