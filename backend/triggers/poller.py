"""Polling triggers, run by a thread of the WORKER process (never the web process)."""

import hashlib
import logging
import threading
from datetime import timedelta

from django.db import close_old_connections, transaction
from django.utils import timezone

from runs import engine, http_node
from runs.serializers import validate_inputs_against
from tenancy.models import Membership
from tenancy.permissions import ROLE_PERMISSIONS, RUN
from workflows.expressions import resolve

from .models import PollTrigger

log = logging.getLogger("fluxonata.poll")
MAX_SEEN = 5000
MAX_NEW_PER_POLL = 100
LEASE = timedelta(minutes=3)


def _dig(value, path: str):
    for key in [k for k in (path or "").split(".") if k]:
        if isinstance(value, dict):
            value = value.get(key)
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            return None
    return value


def claim_due() -> PollTrigger | None:
    now = timezone.now()
    with transaction.atomic():
        t = (
            PollTrigger.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("workspace", "workflow", "environment", "created_by")
            .filter(enabled=True, next_poll_at__lte=now)
            .exclude(claimed_until__gt=now)
            .order_by("next_poll_at")
            .first()
        )
        if t is None:
            return None
        t.claimed_until = now + LEASE
        t.save(update_fields=["claimed_until"])
        return t


def _fetch_mail(t: PollTrigger):
    from vault.models import Connection

    from . import mail

    req = t.request
    conn = (
        Connection.objects.select_related("secret")
        .filter(workspace=t.workspace, name=req.get("connection"), kind="imap")
        .first()
    )
    if conn is None or conn.secret is None:
        return None, "Connexion e-mail (IMAP) introuvable ou sans secret."
    try:
        user, password = conn.login()
        session = mail.connect({**conn.config, "username": user}, password)
        try:
            return mail.fetch_new(
                session,
                req.get("folder") or conn.config.get("folder", "INBOX"),
                req.get("from_contains", ""),
                req.get("subject_contains", ""),
            ), None
        finally:
            try:
                session.logout()
            except Exception:  # noqa: BLE001
                log.debug("imap logout failed", exc_info=True)
    except mail.MailError as exc:
        return None, str(exc)


def _fetch_sftp(t: PollTrigger):
    """Files of a folder (not sub-folders): `id` changes when a file is replaced (name + size + modification time)."""
    from . import sftp_watch

    return sftp_watch.list_files(t)


def _fetch(t: PollTrigger):
    """(items, error message). The request is prepared like an HTTP step (templates see no run data: inputs are empty)."""
    if t.source == "imap":
        return _fetch_mail(t)
    if t.source == "sftp":
        return _fetch_sftp(t)
    node = {**t.request, "type": "http", "mode": "reel"}
    try:
        spec = http_node.prepare(
            node, resolve, {"inputs": {}, "vars": {}, "steps": {}}, t.workspace_id, t.environment_id
        )
    except http_node.HttpStepError as exc:
        return None, exc.message
    result, error = http_node.execute_spec(spec, t.workspace, t.environment_id, t)
    if error:
        return None, error["message"]
    if not result["ok"]:
        return None, f"Le service a répondu {result['status']}."
    items = _dig(result["body"], t.items_path) if t.items_path else result["body"]
    if not isinstance(items, list):
        return None, "La réponse ne contient pas de liste à l'emplacement indiqué."
    return items, None


def _start(t: PollTrigger, inputs: dict, key: str):
    version = t.workflow.latest_version()
    owner = t.created_by
    member = Membership.objects.filter(user=owner, workspace=t.workspace).first() if owner else None
    if version is None or member is None or RUN not in ROLE_PERMISSIONS[member.role]:
        raise PermissionError("Le créateur du déclencheur n'a plus le droit de lancer ce workflow.")
    problems = validate_inputs_against(version.input_schema, inputs)
    if problems:
        raise ValueError(f"Entrées du workflow non satisfaites : {', '.join(problems)}")
    run, _ = engine.start_run(
        workspace=t.workspace,
        environment=t.environment,
        workflow_version=version,
        user=owner,
        inputs=inputs,
        idempotency_key=f"poll-{t.pk}-{key}"[:120],
        trigger="poll",
    )
    return run


def poll_once(t: PollTrigger) -> None:
    """One poll of one trigger; always leaves the trigger scheduled again."""
    now = timezone.now()
    note, ok = "", True
    try:
        items, error = _fetch(t)
        if error:
            ok, note = False, error
        else:
            ids = []
            for it in items:
                ident = _dig(it, t.id_path)
                ids.append(None if ident is None else str(ident)[:200])
            if any(i is None for i in ids):
                ok, note = False, "Certains éléments n'ont pas d'identifiant à l'emplacement indiqué."
            else:
                seen = set(t.seen_ids)
                fresh = [(i, it) for i, it in zip(ids, items, strict=True) if i not in seen]
                first = not t.baselined
                firing = (fresh if (not first or t.fire_existing) else [])[:MAX_NEW_PER_POLL]
                if t.preview_next:
                    note = f"Essai : {len(items)} élément(s), {len(fresh)} nouveau(x) ; {len(firing)} exécution(s) seraient lancées."
                else:
                    if t.mode == PollTrigger.Mode.BATCH and firing:
                        key = hashlib.sha256("|".join(i for i, _ in firing).encode()).hexdigest()[:24]
                        _start(t, {"items": [it for _, it in firing]}, key)
                    else:
                        for ident, it in firing:
                            _start(t, {"item": it}, hashlib.sha256(ident.encode()).hexdigest()[:24])
                    t.seen_ids = [*t.seen_ids, *[i for i, _ in fresh]][-MAX_SEEN:]
                    t.baselined = True
                    note = (
                        f"{len(items)} élément(s) mémorisé(s) (premier relevé : rien n'est lancé)."
                        if first and not t.fire_existing
                        else f"{len(items)} élément(s), {len(firing)} exécution(s) lancée(s)."
                    )
    except (PermissionError, ValueError) as exc:
        ok, note = False, str(exc)
    except Exception:  # noqa: BLE001
        log.exception("poll trigger %s failed", t.pk)
        ok, note = False, "Erreur interne pendant le relevé."
    PollTrigger.objects.filter(pk=t.pk).update(
        seen_ids=t.seen_ids,
        baselined=t.baselined,
        preview_next=False,
        last_poll_at=now,
        last_ok=ok,
        last_note=note[:300],
        next_poll_at=now + timedelta(seconds=t.interval_seconds),
        claimed_until=None,
    )


def process_one() -> bool:
    t = claim_due()
    if t is None:
        return False
    poll_once(t)
    return True


def run_loop(worker_id: str, stop=None, poll_seconds: float = 2.0) -> None:
    stop = stop or threading.Event()
    while not stop.is_set():
        close_old_connections()
        try:
            if process_one():
                continue
        except Exception:  # noqa: BLE001
            log.exception("poll loop iteration failed")
        stop.wait(poll_seconds)
