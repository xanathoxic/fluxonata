"""SCIM 2.0 (Users) so an identity provider can provision and, above all, DEPROVISION accounts.

Enabled only when ORCH_SCIM_TOKEN is set (a long random value shared with the IdP, sent as a bearer token). A user is linked to
the IdP by `externalId`, which must equal the `sub` claim of its identity tokens (that is how an SSO login finds the account:
never by e-mail). Deleting a user in the IdP deactivates the local account (history and audit stay consistent); roles come from
the groups of the identity token at each login (ORCH_OIDC_GROUP_ROLES), not from SCIM."""

import re
import secrets as pysecrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import Http404
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record

from .models import OidcIdentity

User = get_user_model()
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
MAX_COUNT = 100


class ScimParser(JSONParser):
    media_type = "application/scim+json"


class ScimRenderer(JSONRenderer):
    media_type = "application/scim+json"


def _issuer() -> str:
    return settings.ORCH_OIDC_ISSUER.rstrip("/")


def _resource(user, identity) -> dict:
    return {
        "schemas": [USER_SCHEMA],
        "id": str(user.pk),
        "externalId": identity.subject if identity else None,
        "userName": user.username,
        "active": user.is_active,
        "emails": [{"value": user.email, "primary": True}] if user.email else [],
        "meta": {"resourceType": "User"},
    }


def _error(status: int, detail: str) -> Response:
    return Response({"schemas": [ERROR_SCHEMA], "status": str(status), "detail": detail}, status=status)


class _Base(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    parser_classes = [ScimParser, JSONParser]
    renderer_classes = [ScimRenderer, JSONRenderer]

    def initial(self, request, *args, **kwargs):
        expected = getattr(settings, "ORCH_SCIM_TOKEN", "")
        if not expected:
            raise Http404()  # disabled: the endpoint does not exist
        got = request.headers.get("Authorization", "")
        if not pysecrets.compare_digest(got, f"Bearer {expected}"):
            raise AuthenticationFailed("Jeton SCIM invalide.")
        super().initial(request, *args, **kwargs)

    def _identity(self, user):
        return OidcIdentity.objects.filter(issuer=_issuer(), user=user).first()


class ServiceProviderConfigView(_Base):
    def get(self, request):
        return Response(
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
                "patch": {"supported": True},
                "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
                "filter": {"supported": True, "maxResults": MAX_COUNT},
                "changePassword": {"supported": False},
                "sort": {"supported": False},
                "etag": {"supported": False},
                "authenticationSchemes": [
                    {"type": "oauthbearertoken", "name": "Jeton porteur", "description": "ORCH_SCIM_TOKEN"}
                ],
            }
        )


class UsersView(_Base):
    def get(self, request):
        qs = User.objects.filter(oidc_identities__issuer=_issuer()).distinct().order_by("pk")
        flt = request.query_params.get("filter", "")
        m = re.fullmatch(r'\s*userName\s+eq\s+"([^"]{1,150})"\s*', flt)
        if flt and not m:
            return _error(400, 'Filtre non pris en charge (seulement : userName eq "…").')
        if m:
            qs = qs.filter(username__iexact=m.group(1))
        start = max(
            1,
            int(request.query_params.get("startIndex", 1))
            if request.query_params.get("startIndex", "1").isdigit()
            else 1,
        )
        count = min(
            MAX_COUNT,
            int(request.query_params.get("count", MAX_COUNT))
            if request.query_params.get("count", "1").isdigit()
            else MAX_COUNT,
        )
        total = qs.count()
        rows = list(qs[start - 1 : start - 1 + count])
        return Response(
            {
                "schemas": [LIST_SCHEMA],
                "totalResults": total,
                "startIndex": start,
                "itemsPerPage": len(rows),
                "Resources": [_resource(u, self._identity(u)) for u in rows],
            }
        )

    @transaction.atomic
    def post(self, request):
        d = request.data if isinstance(request.data, dict) else {}
        user_name, ext = str(d.get("userName") or "").strip(), str(d.get("externalId") or "").strip()
        if not user_name or not ext:
            return _error(400, "userName et externalId (le « sub » des jetons d'identité) sont requis.")
        if OidcIdentity.objects.filter(issuer=_issuer(), subject=ext).exists():
            return _error(409, "Un utilisateur existe déjà pour cet externalId.")
        base = re.sub(r"[^A-Za-z0-9._-]", "", user_name.split("@")[0])[:30] or "sso"
        name, i = f"sso.{base}", 1
        while User.objects.filter(username=name).exists():
            i += 1
            name = f"sso.{base}{i}"
        emails = d.get("emails") if isinstance(d.get("emails"), list) else []
        email = str(next((e.get("value") for e in emails if isinstance(e, dict) and e.get("value")), ""))[:254]
        user = User(username=name, email=email, is_active=bool(d.get("active", True)))
        user.set_unusable_password()
        user.save()
        ident = OidcIdentity.objects.create(issuer=_issuer(), subject=ext, user=user)
        record("scim.user_create", actor=None, target=user, active=user.is_active)
        resp = Response(_resource(user, ident), status=201)
        resp["Location"] = f"/api/v1/scim/v2/Users/{user.pk}"
        return resp


class UserDetailView(_Base):
    def _get(self, pk):
        user = User.objects.filter(pk=pk if str(pk).isdigit() else 0, oidc_identities__issuer=_issuer()).first()
        if user is None:
            raise Http404()
        return user

    def get(self, request, pk):
        user = self._get(pk)
        return Response(_resource(user, self._identity(user)))

    def _set_active(self, user, active: bool, how: str):
        if user.is_active != active:
            user.is_active = active
            user.save(update_fields=["is_active"])
            record("scim.user_activate" if active else "scim.user_deactivate", actor=None, target=user, via=how)

    def put(self, request, pk):
        user = self._get(pk)
        d = request.data if isinstance(request.data, dict) else {}
        if "active" in d:
            self._set_active(user, bool(d["active"]), "put")
        emails = d.get("emails")
        if isinstance(emails, list) and emails and isinstance(emails[0], dict):
            user.email = str(emails[0].get("value", ""))[:254]
            user.save(update_fields=["email"])
        return Response(_resource(user, self._identity(user)))

    def patch(self, request, pk):
        user = self._get(pk)
        ops = request.data.get("Operations") if isinstance(request.data, dict) else None
        if not isinstance(ops, list):
            return _error(400, "Operations attendu (SCIM PatchOp).")
        for op in ops:
            if not isinstance(op, dict) or str(op.get("op", "")).lower() not in ("replace", "add"):
                return _error(400, "Seules les opérations replace et add sont prises en charge.")
            path, value = op.get("path"), op.get("value")
            if path == "active":
                self._set_active(user, str(value).lower() in ("true", "1"), "patch")
            elif path is None and isinstance(value, dict) and "active" in value:
                self._set_active(user, bool(value["active"]), "patch")
            elif path in ("emails", 'emails[type eq "work"].value') and value:
                user.email = str(value[0].get("value") if isinstance(value, list) else value)[:254]
                user.save(update_fields=["email"])
        return Response(_resource(user, self._identity(user)))

    def delete(self, request, pk):
        user = self._get(pk)
        self._set_active(user, False, "delete")  # never erased: runs, approvals and audit keep their author
        return Response(status=204)
