"""Outbound callback delivery from the outbox: SSRF-checked at send time, signed if a key exists, retried."""

import hashlib
import hmac
import json
import logging
import threading
from datetime import timedelta

from django.db import close_old_connections, transaction
from django.utils import timezone

from core import netsec
from vault.external import ExternalVaultError

from .models import CallbackDelivery

log = logging.getLogger("fluxonata.callbacks")
MAX_ATTEMPTS = 6


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(3600, 30 * 2 ** (attempts - 1)))


def _signing_secret(workspace) -> str | None:
    from vault.models import Secret

    s = Secret.objects.filter(workspace=workspace, name="callback-signing").first()
    return s.reveal() if s else None  # a Vault problem raises: the delivery is retried, never sent unsigned


def deliver_due(now=None) -> int:
    now = now or timezone.now()
    done = 0
    ids = list(
        CallbackDelivery.objects.filter(status="pending", next_attempt_at__lte=now).values_list("id", flat=True)[:50]
    )
    for cid in ids:
        with transaction.atomic():
            cb = (
                CallbackDelivery.objects.select_for_update(skip_locked=True, of=("self",))
                .select_related("run__workspace")
                .filter(pk=cid, status="pending")
                .first()
            )
            if cb is None:
                continue
            cb.attempts += 1
            body = json.dumps(cb.payload, separators=(",", ":")).encode()
            headers = {
                "Content-Type": "application/json",
                "X-Fluxonata-Delivery": f"cb-{cb.pk}",
                "X-Fluxonata-Event": cb.payload["event"],
            }
            try:
                secret = _signing_secret(cb.run.workspace)
            except ExternalVaultError as exc:  # never send unsigned because the vault is down: retry later
                cb.last_error = f"Coffre externe : {exc.message}"[:300]
                if cb.attempts >= MAX_ATTEMPTS:
                    cb.status = CallbackDelivery.Status.FAILED
                else:
                    cb.next_attempt_at = timezone.now() + backoff(cb.attempts)
                cb.save()
                continue
            if secret:
                ts = str(int(timezone.now().timestamp()))
                headers["X-Fluxonata-Timestamp"] = ts
                headers["X-Fluxonata-Signature"] = (
                    "v1=" + hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
                )
            try:
                status, _, _ = netsec.safe_request("POST", cb.url, headers=headers, body=body, timeout=5)
                ok, err, permanent = 200 <= status < 300, f"HTTP {status}", False
            except netsec.UnsafeURL as exc:
                ok, err, permanent = False, f"URL refusée : {exc}", True
            except OSError as exc:
                ok, err, permanent = False, f"Réseau : {type(exc).__name__}", False
            if ok:
                cb.status, cb.last_error = CallbackDelivery.Status.DELIVERED, ""
                done += 1
            else:
                cb.last_error = err[:300]
                if permanent or cb.attempts >= MAX_ATTEMPTS:
                    cb.status = CallbackDelivery.Status.FAILED
                else:
                    cb.next_attempt_at = timezone.now() + backoff(cb.attempts)
            cb.save()
    return done


def run_loop(worker_id: str, stop=None, poll_seconds: float = 1.0) -> None:
    """Livraison des rappels sortants, dans le rôle « aux » du worker (AR4.2).

    Elle était dans le tour de l'orchestrateur : jusqu'à 50 envois de 5 s en série retardaient l'avancement de TOUS les runs, et l'orchestrateur avait besoin de
    la clé du coffre (signature) et d'un réseau sortant. Désormais l'orchestrateur n'a plus ni l'un ni l'autre."""
    stop = stop or threading.Event()
    while not stop.is_set():
        close_old_connections()
        try:
            deliver_due()
        except Exception:  # noqa: BLE001
            log.exception("callbacks loop iteration failed")
        stop.wait(poll_seconds)
