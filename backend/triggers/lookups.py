"""Dynamic lists of public forms (a select whose options come from a web service).

The web process only queues a `FormLookup` and reads its outcome; a thread of `run_worker` does the call, through the
workspace's connection (credentials stay on the server) and `core.netsec` (no private address, no redirect, bounded
size and time). The visitor never chooses the address: only the values of the placeholders of the path set by the
designer, URL-encoded, so the host cannot change. Upstream errors are not relayed to anonymous visitors."""

import hashlib
import json
import logging
import threading
from datetime import timedelta
from urllib.parse import quote, urlsplit

from django.db import close_old_connections, transaction
from django.utils import timezone

from runs import http_node
from vault import auth

from . import shaping
from .models import FormLookup

log = logging.getLogger("fluxonata.forms.lookups")
CACHE_FOR = timedelta(seconds=60)
KEEP_FOR = timedelta(minutes=10)
STALE_AFTER = timedelta(minutes=2)
MAX_QUEUED_PER_FORM = 20
MAX_PARAMS, MAX_PARAM_LEN, MAX_OPTIONS = 5, 200, 200
LOOKUP_MAX_BYTES = 1024 * 1024  # a reply may be big (a policy file, a directory); beyond this it is refused whole
GENERIC_ERROR = "La liste n'a pas pu être chargée. Réessayez plus tard."


def clean_params(raw, allowed: list[str]) -> dict[str, str]:
    """Only the placeholders the designer declared, as short texts."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k in allowed[:MAX_PARAMS]:
        v = raw.get(k)
        if isinstance(v, (str, int, float)) and not isinstance(v, bool):
            out[k] = str(v)[:MAX_PARAM_LEN]
    return out


KINDS = ("options", "prefill")


def find_source(form, field_key: str, kind: str = "options") -> dict | None:
    """The data source of a field of a public form (its published definition)."""
    return source_in(form.published_definition, field_key, kind)


def field_in(definition, field_key: str) -> dict | None:
    for section in (definition or {}).get("sections", []):
        for f in section.get("fields", []):
            if f.get("key") == field_key:
                return f
    return None


def detail_keys(field: dict | None) -> list[str]:
    """The fields (by key) that the options of a custom control field open: the shared sub-fields of the control, calculated ones apart."""
    spec = ((field or {}).get("control") or {}).get("spec") or {}
    return [
        p["key"]
        for p in spec.get("parts", [])
        if p.get("type") not in ("heading", "paragraph", "divider") and not p.get("compute")
    ][:50]


def source_in(definition, field_key: str, kind: str = "options") -> dict | None:
    """Where a field of a form gets its data: a dynamic list has a `source`; a custom control field may have a `source` (its options,
    if the control accepts them) and a `prefill` (its whole value). The reading is the form field's: the control carries none."""
    f = field_in(definition, field_key)
    if f is None:
        return None
    found = f.get("source") if kind == "options" else (f.get("prefill") if f.get("type") == "custom" else None)
    return found if isinstance(found, dict) else None


def enqueue(form, field_key: str, params: dict, kind: str = "options") -> FormLookup | None:
    """The lookup to follow: a recent identical one when there is one, else a new queued one. None when the queue of
    this form is full (the caller answers 429)."""
    digest = hashlib.sha256(json.dumps([field_key, params, kind], sort_keys=True).encode()).hexdigest()
    now = timezone.now()
    recent = (
        form.lookups.filter(params_hash=digest, created_at__gte=now - CACHE_FOR)
        .exclude(status=FormLookup.Status.FAILED)
        .order_by("-created_at")
        .first()
    )
    if recent is not None:
        return recent
    if form.lookups.filter(status=FormLookup.Status.QUEUED).count() >= MAX_QUEUED_PER_FORM:
        return None
    return FormLookup.objects.create(form=form, field_key=field_key, params=params, params_hash=digest, kind=kind)


def enqueue_for_version(
    version, environment, user, field_key: str, params: dict, kind: str = "options"
) -> FormLookup | None:
    """Same for a signed-in person launching a workflow: a recent identical lookup (same version, same environment)
    is reused whoever asked, since the options come from the designer's path and not from the person."""
    digest = hashlib.sha256(json.dumps([field_key, params, environment.pk, kind], sort_keys=True).encode()).hexdigest()
    rows = FormLookup.objects.filter(workflow_version=version)
    recent = (
        rows.filter(params_hash=digest, created_at__gte=timezone.now() - CACHE_FOR)
        .exclude(status=FormLookup.Status.FAILED)
        .order_by("-created_at")
        .first()
    )
    if recent is not None:
        return recent
    if rows.filter(status=FormLookup.Status.QUEUED).count() >= MAX_QUEUED_PER_FORM:
        return None
    return FormLookup.objects.create(
        workflow_version=version,
        environment=environment,
        requested_by=user,
        field_key=field_key,
        params=params,
        params_hash=digest,
        kind=kind,
    )


def _context(lookup: FormLookup) -> tuple[dict | None, int, int | None]:
    """(definition, workspace id, environment id) the lookup runs in; nothing for a form switched off meanwhile."""
    if lookup.form_id:
        form = lookup.form
        return (form.published_definition if form.enabled else None), form.workspace_id, form.environment_id
    version = lookup.workflow_version
    return (version.graph or {}).get("input_form"), version.workflow.workspace_id, lookup.environment_id


def build_url(conn, source: dict, params: dict) -> str:
    base = str((conn.config or {}).get("base_url", "")).rstrip("/")
    path = str(source.get("path", ""))
    if not base or not path.startswith("/") or any(c in path for c in "\\\r\n \t"):
        raise ValueError("path")
    for k in source.get("params", []):
        path = path.replace("{" + k + "}", quote(params.get(k, ""), safe=""))
    if "{" in path and "}" in path:
        raise ValueError("placeholder")
    url = base + path
    if urlsplit(url).hostname != urlsplit(base).hostname:
        raise ValueError("host")
    return url


MAX_DATA_TEXT = 500


def _data(item, keys) -> dict:
    """What an option carries for the fields it opens (flat, by field key): scalars only, they pre-fill the details when it is chosen."""
    if not isinstance(item, dict):
        return {}
    out = {}
    for key in keys:
        v = item.get(key)
        if isinstance(v, bool) or (isinstance(v, (int, float)) and abs(v) < 1e15):
            out[key] = v
        elif isinstance(v, str) and len(v) <= MAX_DATA_TEXT:
            out[key] = v
    return out


def to_options(body, source: dict, limit: int = MAX_OPTIONS, keys=(), params: dict | None = None) -> list[dict]:
    """The options a reply gives with a reading (see `shaping`): items, filter, value, label, group, disabled, sort, at most `limit`."""
    rows = shaping.items(body, source, params)
    where, label_expr = shaping.compiled(source)
    entries = source.get("items_as") == "entries"
    value_path = source.get("value_path") or ("key" if entries else "")
    out, seen = [], set()
    for item in rows:
        if not shaping.keeps(where, item):
            continue
        value = http_node._dig(item, value_path) if value_path else item
        label = shaping.label_of(
            label_expr, item, http_node._dig(item, source["label_path"]) if source.get("label_path") else value
        )
        if isinstance(value, bool) or not isinstance(value, (str, int, float)) or value == "":
            continue
        value = str(value)[:200]
        if value in seen:
            continue
        seen.add(value)
        option = {"value": value, "label": str(label if label is not None else value)[:200]}
        group = http_node._dig(item, source["group_path"]) if source.get("group_path") else None
        if isinstance(group, (str, int, float)) and not isinstance(group, bool) and str(group):
            option["group"] = str(group)[:120]
        if source.get("disabled_path") and http_node._dig(item, source["disabled_path"]) is True:
            option["disabled"] = True
        data = _data(item, keys)
        if data:
            option["data"] = data
        out.append(option)
        if len(out) >= limit and not source.get("sort"):
            break  # without a sort the first ones are the ones kept; with a sort every item is read first
    return shaping.ordered(out, source.get("sort", ""))[:limit]


MAX_PREFILL = 100_000


def to_value(body, cfg: dict, field: dict):
    """The value a service hands to a custom control, as the control keeps it. A reply that does not respect the control's
    description is refused whole: nothing partial is pre-filled."""
    from . import formcontrols

    value = http_node._dig(body, cfg["value_path"]) if cfg.get("value_path") else body
    if value is None or len(json.dumps(value, default=str)) > MAX_PREFILL:
        raise ValueError("value")
    kept, problem = formcontrols.process({**field, "required": False}, value)
    if problem:
        raise ValueError("shape")
    return kept


_MISS = object()


def _reply_key(connection_id, url: str) -> str:
    return "lookup-reply:" + hashlib.sha256(f"{connection_id}|{url}".encode()).hexdigest()


def _cached_reply(connection_id, url: str):
    """The reply of this very request made a moment ago (the same connection, the same address, no credentials in the key): the
    two lists of a cascade often read the same big file, which is fetched once."""
    from django.core.cache import cache

    return cache.get(_reply_key(connection_id, url), _MISS)


def _keep_reply(connection_id, url: str, body) -> None:
    from django.core.cache import cache

    if isinstance(body, (dict, list)):
        cache.set(_reply_key(connection_id, url), body, int(CACHE_FOR.total_seconds()))


def run_lookup(lookup: FormLookup) -> None:
    definition, workspace_id, environment_id = _context(lookup)
    source = source_in(definition, lookup.field_key, lookup.kind)
    if source is None:
        raise ValueError("gone")
    conn = http_node.find_connection(workspace_id, environment_id, source.get("connection", ""))
    if conn is None:
        raise ValueError("connection")
    url = build_url(conn, source, lookup.params)
    url_sent = url
    creds = auth.credentials(conn)
    body = _cached_reply(conn.pk, url)
    if body is _MISS:
        if creds.query:
            url_sent = http_node._with_query(url, creds.query)
        spec = {"method": "GET", "body": None, "timeout": 10, "max_bytes": LOOKUP_MAX_BYTES}
        result, error, _ = http_node._send(
            spec, url_sent, {"Accept": "application/json", **creds.headers}, creds.secrets, creds.signer
        )
        if error is not None or result is None or not result["ok"] or result.get("truncated"):
            raise ValueError("upstream")
        body = result["body"]
        _keep_reply(conn.pk, url, body)
    if lookup.kind == "prefill":
        lookup.options = to_value(body, source, field_in(definition, lookup.field_key) or {})
    else:
        lookup.options = to_options(
            body, source, keys=detail_keys(field_in(definition, lookup.field_key)), params=lookup.params
        )


def claim_next(worker_id: str) -> FormLookup | None:
    with transaction.atomic():
        row = (
            FormLookup.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("form", "form__workspace", "workflow_version__workflow")
            .filter(status=FormLookup.Status.QUEUED)
            .order_by("created_at")
            .first()
        )
        if row is None:
            return None
        row.status, row.claimed_by, row.heartbeat_at = FormLookup.Status.RUNNING, worker_id, timezone.now()
        row.save(update_fields=["status", "claimed_by", "heartbeat_at"])
        return row


def process_one(worker_id: str) -> bool:
    lookup = claim_next(worker_id)
    if lookup is None:
        return False
    try:
        run_lookup(lookup)
        lookup.status = FormLookup.Status.DONE
    except Exception as exc:  # noqa: BLE001 - whatever happens, the visitor gets the same neutral message
        log.warning("form lookup %s failed: %s", lookup.pk, type(exc).__name__)
        lookup.status, lookup.error, lookup.options = FormLookup.Status.FAILED, GENERIC_ERROR, None
    lookup.save(update_fields=["status", "options", "error"])
    return True


def sweep() -> None:
    now = timezone.now()
    FormLookup.objects.filter(
        status__in=[FormLookup.Status.QUEUED, FormLookup.Status.RUNNING], heartbeat_at__lt=now - STALE_AFTER
    ).update(status=FormLookup.Status.FAILED, error=GENERIC_ERROR)
    FormLookup.objects.filter(status=FormLookup.Status.QUEUED, created_at__lt=now - STALE_AFTER).update(
        status=FormLookup.Status.FAILED, error=GENERIC_ERROR
    )
    FormLookup.objects.filter(created_at__lt=now - KEEP_FOR).delete()


def run_loop(worker_id: str, stop=None, poll_seconds: float = 0.5) -> None:
    stop = stop or threading.Event()
    ticks = 0
    while not stop.is_set():
        close_old_connections()
        try:
            if process_one(worker_id):
                continue
            ticks += 1
            if ticks % 20 == 0:
                sweep()
        except Exception:  # noqa: BLE001
            log.exception("form lookup loop iteration failed")
        stop.wait(poll_seconds)
