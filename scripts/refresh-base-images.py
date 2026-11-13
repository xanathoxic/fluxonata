#!/usr/bin/env python3
"""Épingle par condensat (digest) les images de base des Dockerfiles et les images EXTERNES des fichiers Compose livrés, et vérifie
qu'elles ne sont pas périmées.

Pourquoi : `FROM python:3.13-slim` désigne ce que l'étiquette pointe AUJOURD'HUI ; avec un condensat, la construction est
reproductible et ne change que lorsque vous le décidez. Contrepartie : les correctifs de sécurité de l'image de base
n'arrivent plus tout seuls : il faut rafraîchir volontairement (procédure ci-dessous).

    python3 scripts/refresh-base-images.py            # état : épinglée / non épinglée / périmée, sans rien écrire
    python3 scripts/refresh-base-images.py --check    # idem, code de sortie 1 s'il y a un écart
    python3 scripts/refresh-base-images.py --write    # réécrit les lignes FROM avec le condensat actuel de l'étiquette

Procédure de rafraîchissement : `--write`, relire le diff, reconstruire (`docker compose build` ou `scripts/prod-smoke.sh`),
rejouer les tests, puis valider le commit. Sont ignorés : les étapes `FROM <étape>` (images internes), les images construites par le
projet (`:local`) et les images paramétrées (`${...}`). Une image déjà épinglée SANS étiquette (`depot/image@sha256:…`) ne se
rafraîchit pas par ce script (aucune étiquette à résoudre) : la changer à la main. Le condensat écrit est celui de l'INDEX multi-architecture :
les plateformes supportées de l'étiquette sont conservées. Nécessite Docker (buildx)
et un accès au registre en lecture ; aucun jeton n'est utilisé.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["infra/Dockerfile.backend", "infra/Dockerfile.web", "runner/Dockerfile"]
COMPOSE_FILES = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "infra").glob("docker-compose*.yml"))
COMPOSE = re.compile(
    r"^(?P<pre>\s*image:\s+)(?P<image>[^\s@#$]+)(?:@(?P<digest>sha256:[0-9a-f]{64}))?(?P<rest>\s*(?:#.*)?)$"
)
FROM = re.compile(
    r"^(FROM\s+)(?P<image>[^\s@]+)(?:@(?P<digest>sha256:[0-9a-f]{64}))?(?P<rest>(?:\s+AS\s+\S+)?\s*)$", re.I
)


def resolve(image: str) -> str:
    """Condensat de l'index (multi-architecture) que `image` désigne dans son registre."""
    out = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", image], capture_output=True, text=True, timeout=120
    )
    m = re.search(r"^Digest:\s+(sha256:[0-9a-f]{64})\s*$", out.stdout, re.M)
    if out.returncode != 0 or not m:
        raise RuntimeError(f"condensat introuvable pour {image} : {(out.stderr or out.stdout).strip()[:200]}")
    return m.group(1)


def scan_compose(text: str):
    """(numéro de ligne, image, condensat actuel ou None, suffixe, préfixe) des lignes `image:` qui visent une image externe à étiquette."""
    found = []
    for n, line in enumerate(text.splitlines()):
        m = COMPOSE.match(line)
        if not m or m.group("image").endswith(":local"):
            continue
        name = m.group("image").rsplit("/", 1)[-1]
        if ":" not in name:  # pas d'étiquette : rien à résoudre (déjà épinglée à la main, ou non épinglable ici)
            continue
        found.append((n, m.group("image"), m.group("digest"), m.group("rest"), m.group("pre")))
    return found


def scan(text: str):
    """(numéro de ligne, image, condensat actuel ou None, suffixe) des lignes FROM qui visent une image externe."""
    stages: set[str] = set()
    found = []
    for n, line in enumerate(text.splitlines()):
        m = FROM.match(line.strip())
        if not m:
            continue
        image = m.group("image")
        if image.lower() not in stages:  # `FROM runtime AS exec` : étape interne, rien à épingler
            found.append((n, image, m.group("digest"), m.group("rest")))
        alias = re.search(r"\sAS\s+(\S+)\s*$", line, re.I)
        if alias:
            stages.add(alias.group(1).lower())
    return found


def process(text: str, resolver=resolve, write: bool = False, compose: bool = False):
    """Retourne (texte éventuellement réécrit, liste de (image, état)) ; état : 'à jour', 'non épinglée' ou 'périmée'."""
    lines = text.splitlines()
    report = []
    found = scan_compose(text) if compose else [(n, i, c, r, "FROM ") for n, i, c, r in scan(text)]
    resolved: dict[str, str] = {}
    for n, image, current, rest, pre in found:
        if image not in resolved:  # une seule requête au registre par image, même si elle apparaît plusieurs fois
            resolved[image] = resolver(image)
        wanted = resolved[image]
        state = "à jour" if current == wanted else ("non épinglée" if current is None else "périmée")
        report.append((image, state))
        if write and state != "à jour":
            lines[n] = f"{pre}{image}@{wanted}{rest.rstrip()}" if compose else f"FROM {image}@{wanted}{rest.rstrip()}"
    return "\n".join(lines) + ("\n" if text.endswith("\n") else ""), report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="réécrit les FROM avec le condensat actuel")
    ap.add_argument("--check", action="store_true", help="code de sortie 1 s'il y a un écart")
    args = ap.parse_args(argv)
    gap = False
    for rel in [*FILES, *COMPOSE_FILES]:
        path = ROOT / rel
        new, report = process(path.read_text(encoding="utf-8"), write=args.write, compose=rel in COMPOSE_FILES)
        for image, state in report:
            print(f"{rel:<34} {image:<26} {state}")
            gap |= state != "à jour"
        if args.write and new != path.read_text(encoding="utf-8"):
            path.write_text(new, encoding="utf-8")
    if args.write:
        print("Lignes FROM réécrites : relisez le diff, reconstruisez et rejouez les tests avant de valider.")
        return 0
    return 1 if (args.check and gap) else 0


if __name__ == "__main__":
    sys.exit(main())
