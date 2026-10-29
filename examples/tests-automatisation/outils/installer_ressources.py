#!/usr/bin/env python3
"""Installe sur une plate-forme vierge les actions et les skills IA nécessaires aux tests d'automatisation.

  - les actions : le paquet `ressources/paquet/` (actions de démonstration sans effet réel) est importé par l'API des paquets ;
  - les skills IA : `ressources/skills/*.json`, créés par l'API des skills.

Rien n'est publié ni lancé ; un élément déjà présent est laissé tel quel (relancer le script est sans danger).
Le jeton de service (droit de publication dans l'espace) se passe par l'environnement, jamais en argument :

  export FLUXONATA_TOKEN='…'
  python3 installer_ressources.py --url http://127.0.0.1:5173 --workspace tests-auto [--dry-run]

Sans le script : Bibliothèques → Actions → onglet « Paquets » → Importer (archive créée par `construire_paquet.py`),
puis Bibliothèques → Skills IA → Nouveau skill (copier le contenu des fichiers .json).
"""

import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

RESSOURCES = Path(__file__).resolve().parent.parent / "ressources"


def paquet_zip(directory: Path = RESSOURCES / "paquet") -> bytes:
    """Archive du paquet (même construction que la commande `package_import` pour un dossier)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(p for p in directory.rglob("*") if p.is_file()):
            z.write(f, f.relative_to(directory).as_posix())
    return buf.getvalue()


def appel(url: str, token: str, method: str, body: bytes | None = None, content_type: str = "application/json"):
    req = urllib.request.Request(
        url, method=method, data=body, headers={"Authorization": f"Bearer {token}", "Content-Type": content_type}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - l'adresse est celle de votre plate-forme
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except ValueError:
            return exc.code, {}
    except urllib.error.URLError as exc:
        sys.exit(f"Plate-forme injoignable : {exc.reason}")


def multipart(champs: dict[str, str], fichier: tuple[str, bytes]) -> tuple[bytes, str]:
    sep = uuid.uuid4().hex
    out = b""
    for k, v in champs.items():
        out += f'--{sep}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    out += f'--{sep}\r\nContent-Disposition: form-data; name="file"; filename="{fichier[0]}"\r\nContent-Type: application/zip\r\n\r\n'.encode()
    out += fichier[1] + f"\r\n--{sep}--\r\n".encode()
    return out, f"multipart/form-data; boundary={sep}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="adresse de la plate-forme (ex. http://127.0.0.1:5173)")
    ap.add_argument("--workspace", required=True, help="identifiant de l'espace (ex. tests-auto)")
    ap.add_argument("--dry-run", action="store_true", help="indique ce qui serait installé, sans rien écrire")
    args = ap.parse_args()
    token = os.environ.get("FLUXONATA_TOKEN", "")
    if not token:
        print("Définissez FLUXONATA_TOKEN (jeton de service avec le droit de publier).", file=sys.stderr)
        return 2
    base = f"{args.url.rstrip('/')}/api/v1/workspaces/{args.workspace}"
    echec = False

    # 1. actions (paquet) : « skip » laisse en place ce qui existe déjà
    corps, ctype = multipart({"mode": "preview" if args.dry_run else "apply", "strategy": "skip"}, ("tests-automatisation.zip", paquet_zip()))
    statut, rapport = appel(f"{base}/packages/import/", token, "POST", corps, ctype)
    if statut >= 400 and not rapport.get("items"):
        print(f"Paquet d'actions : refusé ({statut}) {'; '.join(rapport.get('errors', []))[:300]}")
        echec = True
    else:
        for it in rapport.get("items", []):
            print(f"[{it['status']}] action {it['slug']}@{it['version']} {' '.join(it.get('messages', []))}")
        for e in rapport.get("errors", []):
            print(f"  erreur : {e}")
            echec = echec or "déjà été importée" not in e  # relancer le script : version déjà installée = rien à faire

    # 2. skills IA
    statut, existants = appel(f"{base}/ai/skills/", token, "GET")
    deja = {s["slug"] for s in existants.get("results", [])} if statut == 200 else set()
    for f in sorted((RESSOURCES / "skills").glob("*.json")):
        skill = json.loads(f.read_text(encoding="utf-8"))
        if skill["slug"] in deja:
            print(f"[déjà présent] skill {skill['slug']}")
        elif args.dry_run:
            print(f"[à créer] skill {skill['slug']}")
        else:
            statut, rep = appel(f"{base}/ai/skills/", token, "POST", json.dumps(skill).encode())
            if statut == 201:
                print(f"[créé] skill {skill['slug']}")
            else:
                print(f"[refusé] skill {skill['slug']} ({statut}) {json.dumps(rep, ensure_ascii=False)[:300]}")
                echec = True
    return 1 if echec else 0


if __name__ == "__main__":
    sys.exit(main())
