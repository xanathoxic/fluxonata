"""Structured API errors: {"error": {"code", "message", "details"}}."""

from rest_framework import exceptions
from rest_framework.views import exception_handler as drf_handler


def exception_handler(exc, context):
    response = drf_handler(exc, context)
    if response is None:
        return None
    code = getattr(exc, "default_code", "error")
    if isinstance(exc, exceptions.APIException) and not isinstance(exc, exceptions.ValidationError):
        code = (
            getattr(getattr(exc, "detail", None), "code", None) or code
        )  # an explicit code (e.g. password_change_required)
    if isinstance(exc, exceptions.ValidationError):
        message, details = "Les données envoyées sont invalides.", response.data
    elif isinstance(getattr(exc, "detail", None), dict):
        message, details = str(exc.detail.get("message", "Erreur.")), exc.detail
    else:
        message = str(getattr(exc, "detail", exc))
        details = None
    response.data = {"error": {"code": code, "message": message, "details": details}}
    return response
