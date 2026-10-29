from pathlib import Path

from django.core.management.base import BaseCommand

from catalog import integrations

HEAD = """# Intégrations prêtes à l'emploi

Ce fichier est **généré** : `cd backend && ../.venv/bin/python manage.py integrations_doc` (ne pas l'éditer à la main ;
la source est `backend/catalog/integrations/catalog.py`).

Chaque intégration est une description (type de connexion, opérations, paramètres) utilisée par l'élément *Requête HTTP* :
dans l'éditeur, ouvrez « Intégrations » dans la palette, choisissez un service puis une opération, renseignez les paramètres
(valeurs fixes ou `{{ expressions }}`). Le **mode test** est actif par défaut (sortie d'exemple, rien n'est envoyé).

**État de vérification** : ces descriptions sont écrites d'après la documentation publique des services et validées contre un
faux serveur (assemblage de la requête, authentification, pagination, détection d'erreur). Elles n'ont **pas été exécutées
contre les vrais services** : testez chaque opération avec un compte de recette avant de l'utiliser en production.
Hors périmètre actuel (pas de requête HTTP) : bases SQL, LDAP, SFTP, S3 (signature SigV4).

"""


class Command(BaseCommand):
    help = "Writes docs/INTEGRATIONS.md from the integrations catalogue."

    def handle(self, *args, **opts):
        out = [
            HEAD,
            f"{len(integrations.INTEGRATIONS)} intégrations, {sum(len(i['operations']) for i in integrations.INTEGRATIONS)} opérations.\n",
        ]
        by_cat: dict = {}
        for i in integrations.INTEGRATIONS:
            by_cat.setdefault(i["category"], []).append(i)
        for cat in sorted(by_cat):
            out.append(f"\n## {cat}\n")
            for i in sorted(by_cat[cat], key=lambda x: x["name"]):
                c = i["connection"]
                out.append(
                    f"### {i['name']}\n\n{i['description']}  \nConnexion : `{c['kind']}` — `{c['config']['base_url']}`. {c['setup']}\n"
                )
                for o in i["operations"]:
                    req = ", ".join(p["label"] for p in o["params"] if p.get("required")) or "—"
                    out.append(
                        f"- **{o['name']}** (`{o['method']} {o['path']}`) — {o['description']} Obligatoire : {req}."
                    )
                out.append("")
        Path(__file__).resolve().parents[4].joinpath("docs", "INTEGRATIONS.md").write_text(
            "\n".join(out) + "\n", encoding="utf-8"
        )
        self.stdout.write("docs/INTEGRATIONS.md écrit")
