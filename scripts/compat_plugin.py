"""Greffon pytest de scripts/compat-test.sh (AR0.5) — n'est chargé que par ce script, jamais par la suite normale.

Les tests `transaction=True` de l'ANCIEN code vident la base avec un TRUNCATE limité aux tables que l'ancien code connaît. Or le schéma
courant a des tables en plus qui référencent des tables anciennes (ex. `tenancy_usergroup_members` → `auth_user`) : PostgreSQL refuse
alors le TRUNCATE sans CASCADE. C'est une limite du nettoyage de test, pas un défaut de compatibilité du code : on autorise le CASCADE
pour ce seul nettoyage. Les écritures et lectures faites par l'ancien code, elles, ne sont pas modifiées.
"""

from django.core.management.commands import flush


def pytest_configure(config):
    original = flush.sql_flush

    def with_cascade(style, connection, **kwargs):
        kwargs["allow_cascade"] = True
        return original(style, connection, **kwargs)

    flush.sql_flush = with_cascade
