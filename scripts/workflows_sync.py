#!/usr/bin/env python3
"""« Workflows as code » : exporte les workflows d'un espace vers un dossier de fichiers JSON (à versionner avec Git) et les
réimporte. Bibliothèque standard uniquement ; le jeton de service est lu dans l'environnement, jamais en argument.

    export FLUXONATA_TOKEN=...            # jeton de service (droit de lecture pour export, d'édition pour import)
    scripts/workflows_sync.py export --url http://127.0.0.1:5173 --workspace demo --out workflows/
    scripts/workflows_sync.py import --url ... --workspace demo --from workflows/ [--overwrite] [--dry-run]

L'import crée ou remplace des BROUILLONS (rien n'est publié ni lancé). Un fichier = un workflow : `<slug>.json`.
Dans une CI : `import --dry-run` sur une demande de fusion, `import --overwrite` après fusion, puis publication par une
personne (la promotion en production reste soumise à revue)."""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def call(url: str, token: str, method: str = "GET", body=None):
    req = urllib.request.Request(
        url,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310  (the URL is the operator's own platform)
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        sys.exit(f"Erreur {exc.code} : {exc.read().decode()[:300]}")
    except urllib.error.URLError as exc:
        sys.exit(f"Plateforme injoignable : {exc.reason}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("export", "import"):
        p = sub.add_parser(name)
        p.add_argument("--url", required=True, help="adresse de la plateforme, ex. https://flux.exemple.org")
        p.add_argument("--workspace", required=True)
    sub.choices["export"].add_argument("--out", required=True)
    sub.choices["import"].add_argument("--from", dest="src", required=True)
    sub.choices["import"].add_argument("--overwrite", action="store_true")
    sub.choices["import"].add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    token = os.environ.get("FLUXONATA_TOKEN", "")
    if not token:
        sys.exit("Définissez FLUXONATA_TOKEN (jeton de service).")
    base = f"{args.url.rstrip('/')}/api/v1/workspaces/{args.workspace}/workflows"
    if args.cmd == "export":
        doc = call(f"{base}/export-all/", token)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        for wf in doc["workflows"]:
            (out / f"{wf['slug']}.json").write_text(json.dumps(wf, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{len(doc['workflows'])} workflow(s) écrit(s) dans {out}/")
        return
    files = sorted(Path(args.src).glob("*.json"))
    items = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    res = call(f"{base}/import-all/", token, "POST", {"format": "fluxonata-workflows/1", "workflows": items, "overwrite": args.overwrite, "dry_run": args.dry_run})
    for r in res["results"]:
        print(f"{r['status']:13} {r['slug']}" + (f" — {r['reason']}" if r.get("reason") else ""))
    if any(r["status"] == "refused" for r in res["results"]):
        sys.exit(1)


if __name__ == "__main__":
    main()
