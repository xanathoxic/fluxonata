"""Nœud « Cache » (ADR 0032) : lire, écrire ou supprimer une valeur partagée entre les workflows d'un espace.

S'exécute dans l'orchestrateur, comme « Transformer » : de la base de données seulement, jamais de réseau ni de code utilisateur.
Le cache est **explicite** (aucune sortie n'est mise en cache toute seule), **par espace et par environnement**, borné (taille d'une
valeur, nombre d'entrées) et chaque écriture augmente `version`. Ne jamais y placer un secret : la valeur est lisible par tout workflow de l'espace."""

import json
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from workflows.cache_spec import KEY_RE, MAX_TTL_SECONDS
from workflows.expressions import ExpressionError, resolve

from .models import CacheBuild, CacheEntry, WorkflowRun

MAX_VALUE_BYTES = 1_048_576  # 1 Mio de JSON par entrée
MAX_ENTRIES = 1000  # entrées valides par espace et environnement


ACTIVE_RUN = (WorkflowRun.Status.QUEUED, WorkflowRun.Status.RUNNING, WorkflowRun.Status.COMPENSATING)


class RebuildNeeded(Exception):
    """La valeur manque et l'étape demande de la reconstruire : l'orchestrateur lance (ou rejoint) le workflow producteur."""

    def __init__(self, key):
        super().__init__(key)
        self.key = key


class CacheStepError(Exception):
    def __init__(self, code, message, next_step="Corrigez la configuration puis relancez."):
        super().__init__(message)
        self.code, self.message, self.next_step = code, message, next_step


def _key(node, ctx) -> str:
    try:
        key = resolve(node.get("key", ""), ctx)
    except ExpressionError as exc:
        raise CacheStepError("CACHE_KEY", str(exc)) from None
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise CacheStepError(
            "CACHE_KEY",
            "La clé du cache est vide ou invalide (1 à 120 caractères : lettres, chiffres, . _ : / -).",
        )
    return key


def _live(run):
    """Les entrées non expirées de l'espace et de l'environnement de cette exécution."""
    now = timezone.now()
    return CacheEntry.objects.filter(workspace=run.workspace, environment=run.environment).exclude(expires_at__lte=now)


def _int_option(node, name, low, high):
    value = node.get(name)
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise CacheStepError("CACHE_OPTION", f"« {name} » : entier de {low} à {high}.")
    return value


def _fresh_entry(run, key, max_age):
    """L'entrée valable de la clé (non expirée, pas plus vieille que `max_age`), ou None."""
    entry = _live(run).filter(key=key).first()
    if entry is not None and max_age is not None and (timezone.now() - entry.updated_at).total_seconds() > max_age:
        return None
    return entry


def _found(entry, key) -> dict:
    return {
        "found": True,
        "value": entry.value,
        "version": entry.version,
        "age_seconds": int((timezone.now() - entry.updated_at).total_seconds()),
        "key": key,
        "rebuilt": False,
    }


def _read(run, node, ctx) -> dict:
    key, max_age = _key(node, ctx), _int_option(node, "max_age_seconds", 1, MAX_TTL_SECONDS)
    entry = _fresh_entry(run, key, max_age)
    if entry is not None:
        return _found(entry, key)
    mode = node.get("on_missing")
    if mode == "rebuild":
        raise RebuildNeeded(key)
    if mode == "fail":
        raise CacheStepError(
            "CACHE_MISS",
            f"Aucune valeur récente dans le cache sous « {key} ».",
            "Lancez d'abord le workflow qui alimente ce cache, ou choisissez « continuer sans valeur ».",
        )
    return {"found": False, "value": None, "version": 0, "age_seconds": None, "key": key, "rebuilt": False}


def producer_slug(run, key) -> str:
    """Le workflow de l'espace dont le réglage « publier le résultat » porte cette clé (un seul, publié)."""
    from workflows.models import Workflow

    slugs = list(
        Workflow.objects.filter(workspace=run.workspace, cache_key=key).values_list("slug", flat=True).order_by("slug")
    )
    if len(slugs) != 1:
        raise CacheStepError(
            "CACHE_NO_PRODUCER" if not slugs else "CACHE_AMBIGUOUS_PRODUCER",
            f"Aucun workflow ne publie la clé « {key} »."
            if not slugs
            else f"Plusieurs workflows publient la clé « {key} » : {', '.join(slugs)}.",
            "Réglez « Publier le résultat dans le cache » sur un seul workflow de l'espace.",
        )
    return slugs[0]


def _is_ancestor(run, build_run) -> bool:
    """Le lancement qui reconstruit est-il l'un de ceux qui attendent déjà (un producteur qui relit sa propre clé) ?"""
    cur, hops = run, 0
    while cur is not None and hops < 20:
        if cur.pk == build_run.pk:
            return True
        cur = cur.parent_step.run if cur.parent_step_id else None
        hops += 1
    return False


def join_or_claim(run, key):
    """Sous verrou : le lancement de reconstruction déjà actif de cette clé (à rejoindre), sinon None (c'est à l'appelant de le lancer puis de
    le déclarer avec `claim`). Un producteur qui attendrait sa propre reconstruction est refusé (aucune attente possible)."""
    lock, _ = CacheBuild.objects.select_for_update().get_or_create(
        workspace=run.workspace, environment=run.environment, key=key
    )
    build = lock.run
    if build is None or build.status not in ACTIVE_RUN:
        return None
    if _is_ancestor(run, build):
        raise CacheStepError(
            "CACHE_REBUILD_LOOP",
            f"Le workflow qui publie « {key} » relit sa propre clé : la reconstruction ne peut pas s'attendre elle-même.",
            "Lisez cette clé avec « continuer sans valeur » dans le workflow producteur.",
        )
    return build


def claim(run, key, build_run) -> None:
    CacheBuild.objects.filter(workspace=run.workspace, environment=run.environment, key=key).update(
        run=build_run, started_at=timezone.now()
    )


def after_rebuild(run, key, build_run) -> dict:
    """La valeur lue une fois la reconstruction terminée ; lève CacheStepError si le producteur a échoué ou n'a rien publié."""
    if build_run is None or build_run.status != WorkflowRun.Status.SUCCEEDED:
        state = build_run.get_status_display().lower() if build_run else "introuvable"
        raise CacheStepError(
            "CACHE_REBUILD_FAILED",
            f"La reconstruction de « {key} » n'a pas abouti (workflow producteur : {state}).",
            "Consultez l'exécution du workflow producteur, puis relancez.",
        )
    entry = _fresh_entry(run, key, None)
    if entry is None:
        raise CacheStepError(
            "CACHE_REBUILD_EMPTY",
            f"Le workflow producteur s'est terminé mais n'a rien publié sous « {key} ».",
            "Vérifiez le réglage « Publier le résultat dans le cache » de ce workflow.",
        )
    return {**_found(entry, key), "rebuilt": True}


def _size_of(value) -> int:
    try:
        size = len(json.dumps(value, ensure_ascii=False).encode())
    except (TypeError, ValueError) as exc:
        raise CacheStepError("CACHE_VALUE", f"Valeur impossible à enregistrer : {exc}") from None
    if size > MAX_VALUE_BYTES:
        raise CacheStepError(
            "CACHE_TOO_BIG",
            f"La valeur fait {size} octets ; le cache accepte {MAX_VALUE_BYTES} octets au plus.",
            "Réduisez la valeur (gardez l'essentiel) avant de l'écrire.",
        )
    return size


def store(run, key: str, value, ttl: int | None, sensitive: bool = False) -> dict:
    """Écrit `value` sous `key` dans le cache de l'espace et de l'environnement de `run` ; rend `{written, key, version, size_bytes, expires_at}`."""
    size = _size_of(value)
    now = timezone.now()
    fields = {
        "value": value,
        "size_bytes": size,
        "sensitive": bool(sensitive),
        "expires_at": now + timedelta(seconds=ttl) if ttl else None,
        "written_by_workflow": run.workflow_version.workflow.slug[:80],
        "written_by_run": run,
        "updated_at": now,
    }
    for attempt in (1, 2):  # la seconde passe couvre deux premières écritures simultanées de la même clé
        try:
            with transaction.atomic():
                version = _upsert(run, key, fields, now)
            break
        except IntegrityError:
            if attempt == 2:
                raise
    expires = fields["expires_at"]
    return {
        "written": True,
        "key": key,
        "version": version,
        "size_bytes": size,
        "expires_at": expires and expires.isoformat(),
    }


def _write(run, node, ctx) -> dict:
    key, ttl = _key(node, ctx), _int_option(node, "ttl_seconds", 1, MAX_TTL_SECONDS)
    if "value" not in node:
        raise CacheStepError("CACHE_VALUE", "Indiquez la valeur à écrire dans le cache.")
    try:
        value = resolve(node["value"], ctx)
    except ExpressionError as exc:
        raise CacheStepError("CACHE_VALUE", f"Valeur impossible à enregistrer : {exc}") from None
    return store(run, key, value, ttl, node.get("sensitive") is True)


def publish_run_result(run) -> str | None:
    """Réglage du workflow « publier son résultat dans le cache » : écrit les sorties d'une exécution réussie sous la clé du workflow.
    Rend un message pour le journal de l'exécution (ou None si le workflow ne publie pas) ; n'échoue jamais : la fin de l'exécution ne dépend pas du cache."""
    workflow = run.workflow_version.workflow
    if not workflow.cache_key:
        return None
    try:
        out = store(run, workflow.cache_key, run.outputs, workflow.cache_ttl_seconds, workflow.cache_sensitive)
    except CacheStepError as exc:
        return f"Résultat non publié dans le cache « {workflow.cache_key} » : {exc.message}"
    return f"Résultat publié dans le cache « {workflow.cache_key} » (version {out['version']})."


def _upsert(run, key, fields, now) -> int:
    scope = CacheEntry.objects.filter(workspace=run.workspace, environment=run.environment)
    entry = scope.select_for_update().filter(key=key).first()
    if entry is not None:  # même clé, même expirée : on la remplace, la version continue
        version = entry.version + 1
        scope.filter(pk=entry.pk).update(version=version, **fields)
        return version
    scope.filter(expires_at__lte=now).delete()
    if scope.count() >= MAX_ENTRIES:
        raise CacheStepError(
            "CACHE_QUOTA",
            f"Le cache de cet environnement contient déjà {MAX_ENTRIES} entrées.",
            "Supprimez des entrées inutiles (étape Cache, opération « supprimer »).",
        )
    CacheEntry.objects.create(workspace=run.workspace, environment=run.environment, key=key, created_at=now, **fields)
    return 1


def _delete(run, node, ctx) -> dict:
    key = _key(node, ctx)
    count, _ = CacheEntry.objects.filter(workspace=run.workspace, environment=run.environment, key=key).delete()
    return {"deleted": count > 0, "key": key}


_OPS = {"read": _read, "write": _write, "delete": _delete}


def apply(run, node, ctx) -> dict:
    """Exécute l'opération du nœud et rend sa sortie ; lève CacheStepError (code, message, étape suivante)."""
    return _OPS[node["operation"]](run, node, ctx)
