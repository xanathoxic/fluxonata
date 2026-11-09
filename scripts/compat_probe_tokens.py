#!/usr/bin/env python3
"""Sonde de compatibilité « jeton personnel » de scripts/compat-test.sh (AR0.5).

Cas non couvert par la suite de l'ancien code : une ligne `tenancy_apitoken` créée par le code COURANT pour une personne (`user_id` renseigné,
`service_account_id` NULL, ADR 0025) est présentée à l'API de l'ANCIEN code, encore en service pendant une mise à jour ou après un retour d'image.
Attendu : refus propre (401) ou, si l'ancien code connaît déjà les jetons personnels, acceptation (200) SOUS L'IDENTITÉ DE LA PERSONNE. Jamais d'erreur
serveur ni d'exception. Un jeton de compte de service, créé de la même façon, sert de contrôle (200, identité du compte) ; un jeton inconnu doit être refusé (401).
L'identité est lue sur `/api/v1/auth/me/` : un 200 sur une route sans identité ne prouverait rien.

    compat_probe_tokens.py new BACKEND ETAT   code COURANT : crée les deux jetons (valeurs brutes dans ETAT, fichier 0600, jamais affichées)
    compat_probe_tokens.py old BACKEND ETAT   ANCIEN code  : les présente à l'API et juge
Codes de sortie : 0 conforme ou non applicable (ancien code sans jetons d'API), 1 écart, 2 usage. Données synthétiques, base jetable.
"""

import json
import os
import re
import sys
import traceback
from datetime import timedelta

SLUG = "compat-probe"
URL = "/api/v1/auth/me/"  # renvoie l'identité authentifiée ; présente dans les anciennes versions


Answer = tuple[
    int | None, str | None, str | None
]  # (statut HTTP ou None, nom d'utilisateur authentifié ou None, exception ou None)


def judge(expected: dict, control: Answer, personal: Answer, invalid: Answer) -> tuple[bool, str]:
    """(conforme, message). `expected` : noms d'utilisateur attendus ({"service": …, "personal": …}).

    Un 200 ne suffit pas : l'identité authentifiée (`/api/v1/auth/me/`) doit être celle du propriétaire du jeton, et un jeton inconnu doit être refusé."""
    for label, (_status, _, error) in (
        ("contrôle (compte de service)", control),
        ("personnel", personal),
        ("invalide", invalid),
    ):
        if error:
            return False, f"jeton {label} : exception de l'ancien code : {error}"
    if control[0] != 200 or control[1] != expected["service"]:
        return False, (
            f"jeton de contrôle (compte de service) : HTTP {control[0]}, identité {control[1]!r} (attendu 200 et {expected['service']!r}) : "
            "régression, ou route de la sonde inadaptée à cette version"
        )
    if invalid[0] != 401:
        return False, f"jeton inconnu : HTTP {invalid[0]} (attendu 401)"
    if personal[0] == 200:
        if personal[1] != expected["personal"]:
            return (
                False,
                f"jeton personnel accepté mais sous une AUTRE identité : {personal[1]!r} (attendu {expected['personal']!r})",
            )
        return (
            True,
            "jeton personnel accepté avec la bonne identité ; contrôle 200 (bonne identité) ; jeton inconnu 401",
        )
    if personal[0] != 401:
        return (
            False,
            f"jeton personnel : HTTP {personal[0]} (attendu 401, ou 200 avec la bonne identité si l'ancien code le gère)",
        )
    return True, "jeton personnel refusé proprement (401) ; contrôle 200 (bonne identité) ; jeton inconnu 401"


def settings_module(backend: str) -> str:
    """Module de réglages déclaré par le `manage.py` de CETTE version (le nom du projet a pu changer entre deux versions)."""
    try:
        with open(os.path.join(backend, "manage.py"), encoding="utf-8") as fh:
            found = re.search(r"DJANGO_SETTINGS_MODULE[\"'],\s*[\"']([\w.]+)[\"']", fh.read())
    except OSError:
        found = None
    return found.group(1) if found else "fluxonata.settings"


def _setup(backend: str):
    sys.path.insert(0, backend)
    os.chdir(backend)
    os.environ["DJANGO_SETTINGS_MODULE"] = settings_module(backend)
    import django

    django.setup()


def phase_new(backend: str, state: str) -> int:
    _setup(backend)
    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from tenancy.auth import new_token
    from tenancy.models import ApiToken, Membership, Organization, Role, ServiceAccount, Workspace

    users = get_user_model()
    org = Organization.objects.create(name="compat-probe", slug="org-compat-probe")
    ws = Workspace.objects.create(organization=org, name="compat-probe", slug=SLUG)
    person = users.objects.create_user("compat-probe-person", password=os.urandom(9).hex())
    Membership.objects.create(user=person, workspace=ws, role=Role.DESIGNER)
    sa_user = users.objects.create_user("compat-probe-sa")
    sa_user.set_unusable_password()
    sa_user.save()
    Membership.objects.create(user=sa_user, workspace=ws, role=Role.VIEWER)
    account = ServiceAccount.objects.create(user=sa_user, workspace=ws, name="compat-probe-sa")
    raws = {}
    for label, owner in (("personal", {"user": person}), ("service", {"service_account": account})):
        raw, prefix, digest = new_token()
        ApiToken.objects.create(
            workspace=ws,
            name=f"compat-probe-{label}",
            prefix=prefix,
            token_hash=digest,
            scopes=["view"],
            expires_at=timezone.now() + timedelta(hours=1),
            **owner,
        )
        raws[label] = raw
    fd = os.open(state, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"tokens": raws, "users": {"personal": person.username, "service": sa_user.username}}, fh)
    personal = ApiToken.objects.get(name="compat-probe-personal")
    print(
        f"sonde : jetons créés (personnel : user_id renseigné={personal.user_id is not None}, compte de service={personal.service_account_id})"
    )
    return 0


def phase_old(backend: str, state: str) -> int:
    _setup(backend)
    try:
        from tenancy.models import ApiToken  # noqa: F401
    except ImportError:
        print("sonde NON APPLICABLE : l'ancien code n'a pas de jetons d'API.")
        return 0
    from django.test.utils import setup_test_environment
    from rest_framework.test import APIClient

    setup_test_environment()  # ajoute le serveur de test aux hôtes autorisés, hors pytest
    with open(state) as fh:
        saved = json.load(fh)
    raws = saved["tokens"]
    raws["invalid"] = "orch_" + "x" * 43  # jamais créé : doit être refusé

    def call(raw: str) -> Answer:
        client = APIClient(raise_request_exception=True)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        try:
            response = client.get(URL)
            body = response.json() if response.status_code == 200 else {}
            return response.status_code, body.get("username"), None
        except Exception as exc:  # noqa: BLE001 — toute exception est un écart à rapporter
            frame = traceback.extract_tb(exc.__traceback__)[-1]
            return None, None, f"{type(exc).__name__} ({os.path.relpath(frame.filename, backend)}:{frame.lineno})"

    ok, message = judge(saved["users"], call(raws["service"]), call(raws["personal"]), call(raws["invalid"]))
    print(("sonde OK : " if ok else "sonde ÉCHEC : ") + message)
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] not in ("new", "old"):
        print(__doc__, file=sys.stderr)
        return 2
    return (phase_new if argv[0] == "new" else phase_old)(os.path.abspath(argv[1]), os.path.abspath(argv[2]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
