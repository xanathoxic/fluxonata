"""Structured JSON logs with correlation ids (request id, run id, execution id)."""

import contextvars
import json
import logging
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

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
            out["exc"] = self.formatException(record.exc_info)
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
