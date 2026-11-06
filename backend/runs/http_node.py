"""The « Requête HTTP » step: calls a service through a CONNECTION (base address + secret token set up by someone allowed
to manage connections). Nothing is called from the web process and nothing from the orchestrator: the orchestrator only
turns the step's templates into a checked request (`prepare`), a worker thread sends it (`execute`).

Safety rules, all server-side:
- the address is the connection's base address plus a path that must start with « / » (the host cannot be changed by the
  workflow author), every call goes through `core.netsec.safe_request` (public addresses only unless allowlisted by the
  platform administrator, no redirect followed, validated IP pinned);
- the token is read from the vault by the worker at send time, sent as `Authorization: Bearer …`, never stored on the
  step and masked in what comes back; headers that carry identity or framing cannot be set by the workflow;
- `mode: test` sends nothing: the output is marked `simulated`."""

import json
import logging
import time
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from audit.models import record
from core import netsec

from . import files
from .models import StepRun

log = logging.getLogger("fluxonata.http")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
FORBIDDEN_HEADERS = {
    "authorization",
    "host",
    "cookie",
    "set-cookie",
    "content-length",
    "transfer-encoding",
    "connection",
    "proxy-authorization",
    "x-forwarded-for",
}
MAX_BODY = 256 * 1024
MAX_RESPONSE = 256 * 1024
MAX_FILE = files.MAX_FILE
MAX_HEADERS = 20
MAX_ITEMS = 5000
MAX_PAGES = 50
LEASE = timedelta(seconds=150)
SIMULATED = {"status": 200, "ok": True, "headers": {}, "body": {}, "simulated": True}


class HttpStepError(Exception):
    def __init__(self, code: str, message: str, next_step: str = "Corrigez la configuration de l'étape."):
        super().__init__(message)
        self.code, self.message, self.next_step = code, message, next_step


def find_connection(workspace_id, environment_id, name):
    from vault.auth import HTTP_KINDS
    from vault.models import Connection

    return (
        Connection.objects.select_related("secret")
        .filter(workspace_id=workspace_id, name=name, kind__in=HTTP_KINDS)
        .filter(Q(environment_id=environment_id) | Q(environment__isnull=True))
        .order_by(F("environment_id").desc(nulls_last=True))
        .first()
    )


def prepare(node: dict, resolve, ctx: dict, workspace_id, environment_id) -> dict:
    """Template resolution + checks. Returns the job spec (no secret in it). Raises HttpStepError."""
    conn = find_connection(workspace_id, environment_id, node.get("connection", ""))
    base = str((conn.config or {}).get("base_url", "")).rstrip("/") if conn else ""
    if conn is None or not base:
        raise HttpStepError(
            "CONNECTION_MISSING",
            f"Connexion « {node.get('connection', '')} » introuvable pour cet environnement (type « Service HTTP »).",
            "Créez la connexion dans Connexions, avec son adresse de base.",
        )
    fields = _fields(node, resolve, ctx)
    method = str(fields["method"]).upper()
    if method not in METHODS:
        raise HttpStepError("HTTP_CONFIG", f"Méthode inconnue : {method}.")
    path = str(fields["path"])
    if not path.startswith("/") or any(c in path for c in "\\\r\n \t") or "@" in path.split("?")[0].split("/")[0]:
        raise HttpStepError(
            "HTTP_CONFIG", "Le chemin doit commencer par « / » et ne contenir ni espace ni retour ligne."
        )
    query = fields["query"]
    if not isinstance(query, dict):
        raise HttpStepError("HTTP_CONFIG", "Les paramètres de requête doivent former un objet.")
    url = (
        base if path == "/" and urlsplit(base).path not in ("", "/") else base + path
    )  # « / » = the connection address itself
    if query:
        url += ("&" if "?" in path else "?") + urlencode({str(k): str(v) for k, v in query.items()})
    if urlsplit(url).hostname != urlsplit(base).hostname:
        raise HttpStepError("HTTP_CONFIG", "Le chemin ne peut pas changer l'hôte de la connexion.")
    headers = fields["headers"]
    if not isinstance(headers, dict) or len(headers) > MAX_HEADERS:
        raise HttpStepError("HTTP_CONFIG", f"Les en-têtes doivent former un objet de {MAX_HEADERS} entrées au plus.")
    clean = {}
    for k, v in headers.items():
        name, value = str(k).strip(), str(v)
        if not name.replace("-", "").isalnum() or name.lower() in FORBIDDEN_HEADERS or "\n" in value or "\r" in value:
            raise HttpStepError("HTTP_CONFIG", f"En-tête interdit ou invalide : « {name} ».")
        clean[name] = value[:1000]
    body = fields["body"]
    raw, body_file, parts = None, None, None
    if fields["body_format"] == "file":
        body_file = files.file_id_of(fields.get("file"))
        if body_file is None:
            raise HttpStepError("HTTP_CONFIG", "Corps « fichier » : indiquez le fichier (sortie « file » d'une étape).")
    elif fields["body_format"] == "multipart" and any(
        isinstance(f, dict) and f.get("file") for f in (fields.get("files") or [])
    ):
        parts = {"fields": body, "files": _check_parts(fields.get("files"))}  # built by the worker: it reads the files
    elif fields["body_format"] == "multipart":
        boundary, raw = multipart(body, fields.get("files"))
        clean["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        if fields["body_format"] == "form" and isinstance(body, dict):
            raw = urlencode({k: str(v) for k, v in body.items()})
            clean.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif isinstance(body, (dict, list)):
            raw = json.dumps(body, ensure_ascii=False)
            clean.setdefault("Content-Type", "application/json")
        else:
            raw = str(body)
        if len(raw.encode()) > MAX_BODY:
            raise HttpStepError("HTTP_CONFIG", "Corps de requête trop volumineux (256 Ko au plus).")
    if node.get("response_file") and (node.get("pagination") or fields.get("pagination")):
        raise HttpStepError("HTTP_CONFIG", "Une réponse enregistrée comme fichier ne peut pas être paginée.")
    timeout = node.get("timeout_seconds")
    return {
        "pagination": _pagination(node.get("pagination") or fields.get("pagination")),
        "fail_if": fields.get("fail_if"),
        "sample": fields.get("sample"),
        "retry": _retry(node.get("retry")),
        "mode": "reel" if node.get("mode") == "reel" else "test",
        "connection": conn.name,
        "method": method,
        "url": url,
        "headers": clean,
        "body": raw,
        "body_file": body_file,
        "parts": parts,
        "save_file": _save_file(node),
        "timeout": timeout if isinstance(timeout, int) and 1 <= timeout <= 60 else 15,
        "fail_on_status": node.get("fail_on_status") is not False,
    }


def _fields(node: dict, resolve, ctx: dict) -> dict:
    """The request parts: written by hand in the step, or produced by an integration's operation from the filled-in
    parameters (the description lives in `catalog.integrations`, so a fix to it reaches every workflow)."""
    if node.get("integration"):
        from catalog import integrations

        _, op = integrations.find(str(node["integration"]), str(node.get("operation", "")))
        if op is None:
            raise HttpStepError("HTTP_CONFIG", "Intégration ou opération inconnue : mettez le workflow à jour.")
        try:
            return integrations.render(op, resolve(node.get("params") or {}, ctx))
        except ValueError as exc:
            raise HttpStepError("HTTP_CONFIG", str(exc)) from None
    body = resolve(node["body"], ctx) if node.get("body") not in (None, "") else None
    return {
        "method": node.get("method") or "GET",
        "path": resolve(node.get("path", "/"), ctx),
        "query": resolve(node.get("query") or {}, ctx),
        "headers": resolve(node.get("headers") or {}, ctx),
        "body": body,
        "body_format": node.get("body_format")
        if node.get("body_format") in ("json", "form", "multipart", "file")
        else "json",
        "file": resolve(node.get("file"), ctx) if node.get("file") else None,
        "files": resolve(node.get("files") or [], ctx),
        "pagination": None,
        "fail_if": None,
        "sample": None,
    }


def multipart(fields, files) -> tuple[str, str]:
    """A multipart/form-data body made of text fields and TEXT files (no binary content): `files` is a list of
    {field, filename, content, content_type}. Header-breaking characters are refused."""
    import uuid

    if fields is not None and not isinstance(fields, dict):
        raise HttpStepError("HTTP_CONFIG", "Les champs d'un envoi multipart doivent former un objet.")
    if not isinstance(files, list) or len(files) > 10 or not all(isinstance(f, dict) for f in files):
        raise HttpStepError("HTTP_CONFIG", "Fichiers : une liste de 10 objets au plus {field, filename, content}.")

    def ok(v: str) -> str:
        if any(c in v for c in '\r\n"'):
            raise HttpStepError("HTTP_CONFIG", "Nom de champ ou de fichier invalide (guillemet ou retour ligne).")
        return v

    boundary = "fluxonata-" + uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{ok(str(k))}"\r\n\r\n{v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}\r\n'
        for k, v in (fields or {}).items()
    ]
    for f in files:
        ctype = str(f.get("content_type") or "text/plain; charset=utf-8")
        if "\r" in ctype or "\n" in ctype:
            raise HttpStepError("HTTP_CONFIG", "Type de contenu invalide.")
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{ok(str(f.get("field", "file")))}"; filename="{ok(str(f.get("filename", "fichier.txt")))}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n{f.get('content', '')}\r\n"
        )
    return boundary, "".join(parts) + f"--{boundary}--\r\n"


def _check_parts(raw) -> list:
    if not isinstance(raw, list) or len(raw) > 10 or not all(isinstance(f, dict) for f in raw):
        raise HttpStepError(
            "HTTP_CONFIG", "Fichiers : une liste de 10 objets au plus {field, filename, content ou file}."
        )
    out = []
    for f in raw:
        item = {k: str(f[k])[:200] for k in ("field", "filename", "content_type") if f.get(k)}
        if f.get("file"):
            item["file_id"] = files.file_id_of(f["file"])
            if item["file_id"] is None:
                raise HttpStepError("HTTP_CONFIG", "Fichier joint : référence invalide (sortie « file » d'une étape).")
        else:
            item["content"] = str(f.get("content", ""))
        out.append(item)
    return out


def _save_file(node: dict) -> dict | None:
    """`response_file`: keep the answer's bytes as a file of the run (output `file`) instead of reading it as text."""
    raw = node.get("response_file")
    if not raw:
        return None
    return {"name": files.safe_name(raw.get("name") if isinstance(raw, dict) else None, "reponse")}


def multipart_bytes(fields, parts: list, load) -> tuple[str, bytes]:
    """A multipart/form-data body with text fields and files: a part has `content` (text) or `file_id` (read with `load`)."""
    import uuid

    if fields is not None and not isinstance(fields, dict):
        raise HttpStepError("HTTP_CONFIG", "Les champs d'un envoi multipart doivent former un objet.")

    def ok(v: str) -> str:
        if any(c in v for c in '\r\n"'):
            raise HttpStepError("HTTP_CONFIG", "Nom de champ ou de fichier invalide (guillemet ou retour ligne).")
        return v

    boundary = "fluxonata-" + uuid.uuid4().hex
    out = b""
    for k, v in (fields or {}).items():
        text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{ok(str(k))}"\r\n\r\n{text}\r\n'.encode()
    for p in parts:
        ctype, data, name = str(p.get("content_type") or ""), None, p.get("filename")
        if p.get("file_id"):
            f = load(p["file_id"])
            data, ctype, name = bytes(f.data), ctype or f.content_type, name or f.name
        else:
            data, ctype, name = (
                str(p.get("content", "")).encode(),
                ctype or "text/plain; charset=utf-8",
                name or "fichier.txt",
            )
        if "\r" in ctype or "\n" in ctype:
            raise HttpStepError("HTTP_CONFIG", "Type de contenu invalide.")
        out += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{ok(str(p.get("field", "file")))}"; '
            f'filename="{ok(str(name))}"\r\nContent-Type: {ctype}\r\n\r\n'.encode()
            + data
            + b"\r\n"
        )
    return boundary, out + f"--{boundary}--\r\n".encode()


def _pagination(raw) -> dict | None:
    if not raw:
        return None
    if not isinstance(raw, dict) or raw.get("type") not in ("page", "offset", "cursor", "link"):
        raise HttpStepError("HTTP_CONFIG", "Pagination : type « page », « offset », « cursor » ou « link ».")
    out = {"type": raw["type"]}
    pages = raw.get("max_pages", 10)
    out["max_pages"] = pages if isinstance(pages, int) and 1 <= pages <= MAX_PAGES else 10
    for key in ("items_path", "page_param", "offset_param", "cursor_param", "size_param", "next_path"):
        if isinstance(raw.get(key), str) and raw[key].strip():
            out[key] = raw[key].strip()[:80]
    for key in ("page_size", "page_start"):
        if isinstance(raw.get(key), int) and 0 <= raw[key] <= 10000:
            out[key] = raw[key]
    if out["type"] == "cursor" and "next_path" not in out:
        raise HttpStepError("HTTP_CONFIG", "Pagination par curseur : indiquez où lire le curseur suivant (next_path).")
    return out


def _retry(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    attempts, backoff = raw.get("max_attempts"), raw.get("backoff_seconds", 2)
    if not isinstance(attempts, int) or not 2 <= attempts <= 6:
        return None
    return {
        "max_attempts": attempts,
        "backoff_seconds": backoff if isinstance(backoff, int) and 1 <= backoff <= 300 else 2,
    }


RETRY_STATUS = {429, 503}
IDEMPOTENT = {"GET", "PUT", "DELETE"}


def retry_delay(spec: dict, attempt: int, error: dict | None, result: dict | None) -> float | None:
    """Seconds to wait before the next try, or None when this outcome is not retried. Network errors are retried only for
    idempotent methods (a POST may have been received); 429/503 are retried for every method (the service did not process)."""
    cfg = spec.get("retry")
    if not cfg or attempt >= cfg["max_attempts"]:
        return None
    if error is not None:
        ok = error.get("code") == "HTTP_ERROR" and spec["method"] in IDEMPOTENT
    else:
        ok = result is not None and (
            result["status"] in RETRY_STATUS or (result["status"] >= 500 and spec["method"] in IDEMPOTENT)
        )
    if not ok:
        return None
    wait = cfg["backoff_seconds"] * (2 ** (attempt - 1))
    return float(min(300, max(wait, (result or {}).get("retry_after", 0))))


def simulated_output(spec: dict) -> dict:
    sample = spec.get("sample")
    if spec.get("save_file"):
        stub = {"file_id": None, "name": spec["save_file"]["name"], "size": 0, "content_type": "", "sha256": ""}
        return {**SIMULATED, "body": None, "file": stub, "request": {"method": spec["method"], "url": spec["url"]}}
    return {
        **SIMULATED,
        "body": sample if sample is not None else {},
        "request": {"method": spec["method"], "url": spec["url"]},
    }


def queue(sr: StepRun, spec: dict) -> None:
    sr.status = StepRun.Status.WAITING
    sr.http_job = {"state": "queued", "spec": spec, "claimed_at": None, "result": None, "error": None}
    sr.save()


# ------------------------------------------------------------------------------------------------ worker side
def claim_next() -> StepRun | None:
    with transaction.atomic():
        sr = (
            StepRun.objects.select_for_update(skip_locked=True)
            .select_related("run__workspace")
            .filter(
                node_type__in=("http", "sql", "ldap", "sftp", "smtp"),
                status=StepRun.Status.WAITING,
                http_job__state="queued",
            )
            .filter(Q(http_job__not_before__isnull=True) | Q(http_job__not_before__lte=time.time()))
            .order_by("id")
            .first()
        )
        if sr is None:
            return None
        sr.http_job = {**sr.http_job, "state": "running", "claimed_at": timezone.now().isoformat()}
        sr.save(update_fields=["http_job"])
        return sr


def _mask(value, secrets: list):
    if isinstance(value, str):
        for secret in secrets:
            if secret and len(secret) >= 4:
                value = value.replace(secret, "***")
        return value
    if isinstance(value, dict):
        return {k: _mask(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v, secrets) for v in value]
    return value


def _with_query(url: str, extra: dict) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in extra]
    query += [(k, str(v)) for k, v in extra.items()]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _dig(value, path: str):
    for key in [k for k in (path or "").split(".") if k]:
        if isinstance(value, dict):
            value = value.get(key)
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            return None
    return value


def _send(spec: dict, url: str, headers: dict, secrets: list, signer=None) -> tuple[dict | None, dict | None, dict]:
    """One call. Returns (result, error, response headers). `signer` signs this very request (SigV4)."""
    body_bytes = spec.get("_body_bytes") or (spec["body"].encode() if spec["body"] else None)
    limit = int(spec.get("max_bytes") or MAX_RESPONSE)  # a reading of a service for a form may allow a bigger reply
    if signer is not None:
        headers = signer(spec["method"], url, headers, body_bytes)
    try:
        status, resp_headers, raw = netsec.safe_request(
            spec["method"],
            url,
            headers=headers,
            body=body_bytes,
            timeout=spec["timeout"],
            max_bytes=(MAX_FILE if spec.get("save_file") else limit) + 1,
        )
    except netsec.UnsafeURL as exc:
        err = {"code": "HTTP_BLOCKED", "message": f"Adresse refusée : {exc}", "next_step": "Vérifiez la connexion."}
        return None, err, {}
    except OSError as exc:  # timeouts, refused connections, TLS errors, DNS
        err = {
            "code": "HTTP_ERROR",
            "message": f"Le service n'a pas répondu ({type(exc).__name__}).",
            "next_step": "Vérifiez que le service est joignable puis relancez.",
        }
        return None, err, {}
    lower = {k.lower(): v for k, v in resp_headers.items()}
    ctype = lower.get("content-type", "")
    if spec.get("save_file") and 200 <= status < 300:  # the bytes are stored by `execute_spec`, never put in the output
        if len(raw) > MAX_FILE:
            err = {
                "code": "FILE_TOO_BIG",
                "message": f"Réponse trop volumineuse ({MAX_FILE // 1024 // 1024} Mo au plus).",
                "next_step": "Demandez un fichier plus petit.",
            }
            return None, err, {}
        result = {
            "status": status,
            "ok": True,
            "headers": {"content-type": ctype[:200]},
            "body": None,
            "simulated": False,
        }
        return {**result, "_raw": raw}, None, lower
    text = raw[:limit].decode("utf-8", "replace").replace("\x00", "\ufffd")  # PostgreSQL JSON refuses NUL
    body: object = text
    if "json" in ctype.lower():
        try:
            body = json.loads(text)
        except ValueError:
            body = text
    elif "event-stream" in ctype.lower():
        body = sse_last_json(text) if sse_last_json(text) is not None else text
    elif "xml" in ctype.lower() and len(raw) <= limit:
        body = xml_to_data(text) if xml_to_data(text) is not None else text
    result = {
        "status": status,
        "ok": 200 <= status < 300,
        "headers": {"content-type": ctype[:200]},
        "body": _mask(body, secrets),
        "simulated": False,
    }
    if len(raw) > limit:
        result["truncated"] = True
    if lower.get("retry-after", "").isdigit():
        result["retry_after"] = int(lower["retry-after"])
    return result, None, lower


def sse_last_json(text: str):
    """The JSON of the last `data:` line of a server-sent-events answer (MCP servers may answer that way), or None."""
    for line in reversed(text.splitlines()):
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip())
            except ValueError:
                return None
    return None


def xml_to_data(text: str):
    """A small XML document as plain data (tags -> keys, repeated tags -> lists, attributes ignored); None when it is not
    safe or not valid XML. Documents with a DOCTYPE / entities are never parsed (entity-expansion attacks)."""
    from xml.etree import ElementTree  # noqa: S405  (DOCTYPE/ENTITY refused below)

    head = text[:2000].upper()
    if "<!DOCTYPE" in head or "<!ENTITY" in text.upper():
        return None
    try:
        root = ElementTree.fromstring(text)  # noqa: S314
    except ElementTree.ParseError:
        return None

    def conv(el):
        kids = list(el)
        if not kids:
            return (el.text or "").strip()
        out: dict = {}
        for k in kids:
            tag = k.tag.split("}")[-1]
            val = conv(k)
            if tag in out:
                out[tag] = out[tag] if isinstance(out[tag], list) else [out[tag]]
                out[tag].append(val)
            else:
                out[tag] = val
        return out

    return {root.tag.split("}")[-1]: conv(root)}


def _next_link(link_header: str) -> str | None:
    for part in link_header.split(","):
        bits = part.split(";")
        if len(bits) > 1 and any(b.strip().replace(" ", "") in ('rel="next"', "rel=next") for b in bits[1:]):
            return bits[0].strip().strip("<>")
    return None


def _paginate(spec: dict, url: str, headers: dict, secrets: list, signer=None) -> tuple[dict | None, dict | None]:
    """Reads successive pages and concatenates their items: body = list of items, `pages` = pages read, `truncated` when
    the page limit stopped the reading."""
    pg = spec["pagination"]
    kind, max_pages = pg["type"], pg.get("max_pages", 10)
    items: list = []
    page_no = int(pg.get("page_start", 1))
    offset, cursor, link = 0, None, None
    last: dict | None = None
    for n in range(1, max_pages + 1):
        extra: dict = {}
        if kind == "page":
            extra[pg.get("page_param", "page")] = page_no
        elif kind == "offset":
            extra[pg.get("offset_param", "offset")] = offset
        elif kind == "cursor" and cursor is not None:
            extra[pg.get("cursor_param", "cursor")] = cursor
        if kind != "link" and pg.get("page_size"):
            extra[pg.get("size_param", "limit")] = pg["page_size"]
        target = link if kind == "link" and link else _with_query(url, extra)
        result, error, lower = _send(spec, target, headers, secrets, signer)
        if error:
            return None, error
        last = result
        if not result["ok"]:
            return result, None  # the failing page is returned as it is (status, body)
        body = result["body"]
        got = _dig(body, pg["items_path"]) if pg.get("items_path") else body
        got = got if isinstance(got, list) else []
        items += got
        if len(items) > MAX_ITEMS:
            items, last["truncated"] = items[:MAX_ITEMS], True
            break
        if not got:
            break
        if kind == "page":
            page_no += 1
        elif kind == "offset":
            offset += len(got)
            if pg.get("page_size") and len(got) < pg["page_size"]:
                break
        elif kind == "cursor":
            cursor = _dig(body, pg["next_path"])
            if cursor in (None, "", False):
                break
        elif kind == "link":
            link = _next_link(lower.get("link", ""))
            if not link or urlsplit(link).hostname != urlsplit(url).hostname:
                break
        if n == max_pages:
            last["truncated"] = True
    assert last is not None
    return {**last, "body": items, "pages": n}, None


def _fail_if(rule: dict, result: dict) -> dict | None:
    """Some services answer 200 and put the failure in the body (e.g. Slack `ok: false`): turn it into a failed step."""
    value = _dig(result["body"], rule["path"])
    hit = (value is not None) if rule.get("present") else (value == rule.get("is") and value is not None)
    if not hit:
        return None
    why = _dig(result["body"], rule.get("message_path", ""))
    return {
        "code": "API_ERROR",
        "message": f"Le service signale une erreur : {str(why)[:200] if why else 'refus sans détail'}.",
        "next_step": "Consultez la réponse du service.",
    }


def execute(sr: StepRun) -> tuple[dict | None, dict | None]:
    """Sends the request of a step. Returns (result, error)."""
    return execute_spec(sr.http_job["spec"], sr.run.workspace, sr.run.environment_id, sr.run, step_id=sr.node_id)


def _file_error(exc: files.FileError) -> dict:
    return {"code": exc.code, "message": exc.message, "next_step": exc.next_step}


def execute_spec(
    spec: dict, workspace, environment_id, audit_target, step_id: str = ""
) -> tuple[dict | None, dict | None]:
    """Sends a prepared request. The secret is read here and never leaves this function."""
    conn = find_connection(workspace.pk, environment_id, spec["connection"])
    from vault import auth

    if conn is None:
        return None, {
            "code": "CONNECTION_MISSING",
            "message": "Connexion introuvable.",
            "next_step": "Vérifiez la connexion.",
        }

    try:
        creds = auth.credentials(conn)
    except auth.AuthError as exc:
        return None, {"code": exc.code, "message": exc.message, "next_step": "Vérifiez la connexion dans Connexions."}
    headers = {**spec["headers"], **creds.headers}
    url = spec["url"]
    if creds.query:
        url = _with_query(url, creds.query)
    from .models import WorkflowRun

    run = audit_target if isinstance(audit_target, WorkflowRun) else None  # files exist only inside a run
    try:
        if spec.get("body_file") or spec.get("parts"):
            if run is None:
                raise files.FileError("FILE_MISSING", "Les fichiers ne sont utilisables que dans une exécution.")
            if spec.get("body_file"):
                f = files.load(run, spec["body_file"])
                spec = {**spec, "_body_bytes": bytes(f.data)}
                if not any(k.lower() == "content-type" for k in headers):
                    headers["Content-Type"] = f.content_type
            else:
                boundary, data = multipart_bytes(
                    spec["parts"]["fields"], spec["parts"]["files"], lambda fid: files.load(run, fid)
                )
                spec = {**spec, "_body_bytes": data}
                headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            if len(spec["_body_bytes"]) > files.MAX_FILE + 4096:
                raise files.FileError("FILE_TOO_BIG", "Corps de requête trop volumineux (10 Mo au plus).")
        if spec.get("save_file") and run is None:
            raise files.FileError("FILE_MISSING", "Les fichiers ne sont utilisables que dans une exécution.")
    except files.FileError as exc:
        return None, _file_error(exc)
    except HttpStepError as exc:
        return None, {"code": exc.code, "message": exc.message, "next_step": exc.next_step}
    started = time.monotonic()
    if spec.get("pagination"):
        result, error = _paginate(spec, url, headers, creds.secrets, creds.signer)
    else:
        result, error, _ = _send(spec, url, headers, creds.secrets, creds.signer)
    if result is not None and "_raw" in result:
        raw = result.pop("_raw")
        try:
            name = spec["save_file"]["name"]
            result["file"] = files.save(run, step_id, name, result["headers"].get("content-type", ""), raw)
        except files.FileError as exc:
            return None, _file_error(exc)
    if result is not None and error is None and spec.get("fail_if") and result["ok"]:
        error = _fail_if(spec["fail_if"], result)
    if result is not None:
        record(
            "http.request",
            workspace=workspace,
            target=audit_target,
            method=spec["method"],
            host=urlsplit(spec["url"]).hostname,
            status=result["status"],
            pages=result.get("pages", 1),
            ms=int((time.monotonic() - started) * 1000),
        )
    return result, error


def process_one() -> bool:
    sr = claim_next()
    if sr is None:
        return False
    try:
        if sr.node_type == "sql":
            from . import sql_node

            result, error = sql_node.execute(sr)
        elif sr.node_type == "smtp":
            from . import smtp_node

            result, error = smtp_node.execute(sr)
        elif sr.node_type == "sftp":
            from . import sftp_node

            result, error = sftp_node.execute(sr)
        elif sr.node_type == "ldap":
            from . import ldap_node

            result, error = ldap_node.execute(sr)
        else:
            result, error = execute(sr)
    except Exception:  # noqa: BLE001
        log.exception("http step %s crashed", sr.pk)
        result, error = None, {"code": "HTTP_ERROR", "message": "Erreur interne pendant l'appel.", "next_step": ""}
    # only a step still waiting for this very call takes the outcome (a cancelled run keeps its own state)
    StepRun.objects.filter(pk=sr.pk, status=StepRun.Status.WAITING, http_job__state="running").update(
        http_job={**sr.http_job, "state": "done", "result": result, "error": error}
    )
    return True


def lease_expired(job: dict) -> bool:
    at = job.get("claimed_at")
    return bool(at) and timezone.now() - datetime.fromisoformat(at) > LEASE


def run_loop(worker_id: str, stop=None, poll_seconds: float = 1.0) -> None:
    import threading

    from django.db import close_old_connections

    stop = stop or threading.Event()
    while not stop.is_set():
        close_old_connections()
        try:
            if process_one():
                continue
        except Exception:  # noqa: BLE001
            log.exception("http loop iteration failed")
        stop.wait(poll_seconds)
