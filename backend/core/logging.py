"""Structured JSON logs with correlation ids (request id, run id, execution id)."""

import contextvars
import json
import logging
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from django.utils.log import AdminEmailHandler
from django.views.debug import SafeExceptionReporterFilter

from .urlredact import PLACEHOLDER, redact_url_tokens

_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar("log_ctx", default=None)


@contextmanager
def bind(**fields):
    token = _ctx.set({**(_ctx.get() or {}), **{k: v for k, v in fields.items() if v is not None}})
    try:
        yield
    finally:
        _ctx.reset(token)


def current() -> dict:
    return dict(_ctx.get() or {})


class ContextFilter(logging.Filter):
    def filter(self, record):
        record.ctx = current()
        return True


class RedactUrlTokensFilter(logging.Filter):
    """Réécrit le message de chaque enregistrement pour masquer les jetons d'adresse (`core.urlredact`). Le message est formaté ici,
    puis ses arguments sont retirés : les gestionnaires voient le texte déjà masqué, quel que soit le formateur."""

    def filter(self, record):
        try:
            record.msg = redact_url_tokens(record.getMessage())
            record.args = ()
        except (
            TypeError,
            ValueError,
        ):  # message and arguments do not match: leave the record to logging's own error handling
            pass
        return True


class RedactingAdminEmailHandler(AdminEmailHandler):
    """Rapport d'erreur envoyé à `ADMINS` : objet, corps texte et corps HTML passent par `core.urlredact` (le rapport technique de Django
    contient l'adresse de la requête, « Request URL », `PATH_INFO`, `HTTP_REFERER`… : le filtre posé sur le gestionnaire ne voit que le message)."""

    def emit(self, record):
        # La valeur du paramètre d'adresse `token` (forms/<token>/, resume-forms/<token>/, hooks/resume/<token>/) figure aussi dans le rapport sous
        # une autre forme (« URL kwargs: {'token': '…'} », vue appelée) : on la retient pour la masquer partout. `emit` s'exécute sous le verrou du gestionnaire.
        match = getattr(getattr(record, "request", None), "resolver_match", None)
        value = (getattr(match, "kwargs", None) or {}).get("token")
        self._route_tokens = [value] if isinstance(value, str) and len(value) >= 8 else []
        try:
            super().emit(record)
        finally:
            self._route_tokens = []

    def _mask(self, text):
        text = redact_url_tokens(text)
        for value in getattr(self, "_route_tokens", []):
            text = text.replace(value, PLACEHOLDER)
        return text

    def send_mail(self, subject, message, *args, **kwargs):
        if kwargs.get("html_message"):
            kwargs["html_message"] = self._mask(kwargs["html_message"])
        super().send_mail(self._mask(subject), self._mask(message), *args, **kwargs)


class MaskedPostReporterFilter(SafeExceptionReporterFilter):
    """Dans les rapports d'erreur (e-mail à `ADMINS`, page technique de débogage), les NOMS des champs POST sont conservés, leurs VALEURS sont masquées :
    un formulaire public envoyé en `application/x-www-form-urlencoded` ou `multipart` aurait sinon ses saisies (et un champ « mot de passe »)
    recopiées dans le message. Les corps JSON, `Authorization` et les cookies n'y figurent déjà pas (vérifié)."""

    def get_post_parameters(self, request):
        if request is None:
            return {}
        return {name: self.cleansed_substitute for name in request.POST}


class JsonFormatter(logging.Formatter):
    def format(self, record):
        out = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **getattr(record, "ctx", {}),
        }
        if record.exc_info:
            out["exc"] = redact_url_tokens(
                self.formatException(record.exc_info)
            )  # le message d'une exception peut citer l'adresse
        if record.stack_info:
            out["stack"] = redact_url_tokens(self.formatStack(record.stack_info))
        return json.dumps(out, ensure_ascii=False)


class CorrelationMiddleware:
    """Accepts a caller-supplied X-Request-ID (sanitised) or creates one; echoes it back; binds it to every log line."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        rid = request.headers.get("X-Request-ID", "")
        rid = rid if rid and len(rid) <= 64 and rid.replace("-", "").replace("_", "").isalnum() else uuid.uuid4().hex
        request.correlation_id = rid
        with bind(correlation_id=rid):
            response = self.get_response(request)
        response["X-Request-ID"] = rid
        return response
