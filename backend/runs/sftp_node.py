"""The « Fichier SFTP » step: list, read, write or delete a file on an SFTP server, through a connection.

Safety, all server-side: the server address is checked like every outgoing call (`core.netsec`) and the connection is pinned
to the validated address; the server's key can be pinned in the connection (`host_key_sha256`), and the fingerprint seen is
returned in the output so it can be pinned afterwards; the password or private key is read from the vault by the worker at
send time and never stored on the step; text files only, 256 KB at most each way; `write` and `delete` have an effect, so they
are never retried. `mode: test` sends nothing."""

import base64
import hashlib
import io
import logging
import posixpath
import time

from audit.models import record
from core import netsec
from vault.external import ExternalVaultError

from . import files

log = logging.getLogger("fluxonata.sftp")
MAX_BYTES = 256 * 1024
MAX_LIST = 1000
OPERATIONS = ("list", "read", "write", "delete")


class SftpStepError(Exception):
    def __init__(self, code: str, message: str, next_step: str = "Corrigez la configuration de l'étape."):
        super().__init__(message)
        self.code, self.message, self.next_step = code, message, next_step


def find_connection(workspace_id, environment_id, name):
    from django.db.models import Q

    from vault.models import Connection

    return (
        Connection.objects.select_related("secret")
        .filter(workspace_id=workspace_id, name=name, kind="sftp")
        .filter(Q(environment_id=environment_id) | Q(environment__isnull=True))
        .order_by("-environment_id")
        .first()
    )


def clean_path(raw) -> str:
    p = str(raw or "").strip()
    if not p or "\x00" in p or len(p) > 500:
        raise SftpStepError("SFTP_CONFIG", "Indiquez un chemin (500 caractères au plus).")
    return posixpath.normpath(p) if p.startswith("/") else posixpath.normpath("/" + p)


def prepare(node: dict, resolve, ctx: dict, workspace_id, environment_id) -> dict:
    conn = find_connection(workspace_id, environment_id, node.get("connection", ""))
    if conn is None:
        raise SftpStepError(
            "CONNECTION_MISSING",
            f"Connexion « {node.get('connection', '')} » introuvable pour cet environnement (type « Serveur SFTP »).",
            "Créez la connexion dans Connexions.",
        )
    op = node.get("operation", "list")
    if op not in OPERATIONS:
        raise SftpStepError("SFTP_CONFIG", "Opération : list, read, write ou delete.")
    content, file_id = None, None
    if op == "write" and node.get("file"):  # binary: the bytes come from a file of the run
        file_id = files.file_id_of(resolve(node["file"], ctx))
        if file_id is None:
            raise SftpStepError("SFTP_CONFIG", "Fichier à écrire : indiquez la sortie « file » d'une étape.")
    elif op == "write":
        content = resolve(node.get("content", ""), ctx)
        content = content if isinstance(content, str) else str(content)
        if len(content.encode()) > MAX_BYTES:
            raise SftpStepError("SFTP_CONFIG", "Contenu trop volumineux (256 Ko au plus).")
    timeout = node.get("timeout_seconds")
    return {
        "mode": "reel" if node.get("mode") == "reel" else "test",
        "connection": conn.name,
        "operation": op,
        "path": clean_path(resolve(node.get("path", "/"), ctx)),
        "content": content,
        "file_id": file_id,
        "binary": op == "read" and bool(node.get("binary")),  # read as a file of the run (any bytes, 10 MB at most)
        "timeout": timeout if isinstance(timeout, int) and 1 <= timeout <= 60 else 20,
    }


def simulated_output(spec: dict) -> dict:
    base = {"operation": spec["operation"], "path": spec["path"], "simulated": True}
    if spec["operation"] == "read" and spec.get("binary"):
        stub = {"file_id": None, "name": posixpath.basename(spec["path"]), "size": 0, "content_type": "", "sha256": ""}
        return {**base, "content": None, "file": stub}
    return (
        {**base, "entries": [], "count": 0}
        if spec["operation"] == "list"
        else {**base, "content": "" if spec["operation"] == "read" else None}
    )


def _fingerprint(key) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def execute(sr) -> tuple[dict | None, dict | None]:
    return execute_spec(sr.http_job["spec"], sr.run.workspace, sr.run.environment_id, sr.run, step_id=sr.node_id)


def execute_spec(
    spec: dict, workspace, environment_id, audit_target, step_id: str = ""
) -> tuple[dict | None, dict | None]:
    import paramiko

    from .models import WorkflowRun

    run = audit_target if isinstance(audit_target, WorkflowRun) else None  # files exist only inside a run
    if (spec.get("file_id") or spec.get("binary")) and run is None:
        return None, {
            "code": "FILE_MISSING",
            "message": "Les fichiers ne sont utilisables que dans une exécution.",
            "next_step": "",
        }

    conn = find_connection(workspace.pk, environment_id, spec["connection"])
    if conn is None or conn.secret is None:
        return None, {
            "code": "CONNECTION_MISSING",
            "message": "Connexion introuvable ou sans secret.",
            "next_step": "Vérifiez la connexion.",
        }
    cfg = conn.config
    try:
        target = netsec.validate_url(f"https://{cfg.get('host', '')}:{int(cfg.get('port') or 22)}/")
    except netsec.UnsafeURL as exc:
        return None, {
            "code": "SFTP_BLOCKED",
            "message": f"Serveur refusé : {exc}",
            "next_step": "Vérifiez la connexion.",
        }
    try:
        user, secret = conn.login()
    except ExternalVaultError as exc:
        return None, {
            "code": exc.code,
            "message": exc.message,
            "next_step": "Vérifiez le coffre externe (Plate-forme → Coffre externe) et le secret de la connexion.",
        }
    started = time.monotonic()
    transport = None
    try:
        sock = netsec.open_connection(target, 10)
        sock.settimeout(spec["timeout"])
        transport = paramiko.Transport(sock)
        transport.start_client(timeout=10)
        seen = _fingerprint(transport.get_remote_server_key())
        pinned = str(cfg.get("host_key_sha256") or "").strip()
        if pinned and pinned != seen:
            return None, {
                "code": "SFTP_HOSTKEY",
                "message": "La clé du serveur ne correspond pas à l'empreinte enregistrée dans la connexion.",
                "next_step": "Vérifiez le serveur : c'est peut-être une usurpation.",
            }
        if secret.lstrip().startswith("-----BEGIN"):
            pkey = paramiko.PKey.from_private_key(io.StringIO(secret))
            transport.auth_publickey(user, pkey)
        else:
            transport.auth_password(user, secret)
        sftp = paramiko.SFTPClient.from_transport(transport)
        out = _operate(sftp, spec, run, step_id)
        out["host_fingerprint"] = seen
    except paramiko.AuthenticationException:
        return None, {
            "code": "SFTP_AUTH",
            "message": "Authentification refusée par le serveur SFTP.",
            "next_step": "Vérifiez l'identifiant et le secret de la connexion.",
        }
    except FileNotFoundError:
        return None, {
            "code": "SFTP_NOT_FOUND",
            "message": "Chemin introuvable sur le serveur.",
            "next_step": "Vérifiez le chemin.",
        }
    except PermissionError:
        return None, {
            "code": "SFTP_DENIED",
            "message": "Droits insuffisants sur ce chemin.",
            "next_step": "Vérifiez les droits du compte.",
        }
    except (SftpStepError, files.FileError) as exc:
        return None, {"code": exc.code, "message": exc.message, "next_step": exc.next_step}
    except (paramiko.SSHException, OSError, EOFError) as exc:
        return None, {
            "code": "SFTP_ERROR",
            "message": f"Le serveur SFTP n'a pas répondu correctement ({type(exc).__name__}).",
            "next_step": "Vérifiez que le serveur est joignable.",
        }
    finally:
        if transport is not None:
            transport.close()
    record(
        "sftp.operation",
        workspace=workspace,
        target=audit_target,
        host=target.host,
        operation=spec["operation"],
        ms=int((time.monotonic() - started) * 1000),
    )
    return out, None


def _operate(sftp, spec, run=None, step_id: str = "") -> dict:
    op, path = spec["operation"], spec["path"]
    base = {"operation": op, "path": path, "simulated": False}
    if op == "list":
        rows = [
            {"name": a.filename, "size": a.st_size, "is_dir": bool(a.st_mode & 0o040000), "modified": a.st_mtime}
            for a in sftp.listdir_attr(path)[:MAX_LIST]
        ]
        return {**base, "entries": rows, "count": len(rows)}
    if op == "read" and spec.get("binary"):
        with sftp.open(path, "rb") as fh:
            raw = fh.read(files.MAX_FILE + 1)
        return {**base, "content": None, "file": files.save(run, step_id, posixpath.basename(path), "", raw)}
    if op == "read":
        with sftp.open(path, "rb") as fh:
            raw = fh.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise SftpStepError(
                "SFTP_TOO_BIG", "Fichier trop volumineux (256 Ko au plus).", "Lisez un fichier plus petit."
            )
        try:
            return {**base, "content": raw.decode("utf-8")}
        except UnicodeDecodeError:
            raise SftpStepError(
                "SFTP_NOT_TEXT", "Ce fichier n'est pas du texte (UTF-8).", "Seuls les fichiers texte sont lus."
            ) from None
    if op == "write":
        data = bytes(files.load(run, spec["file_id"]).data) if spec.get("file_id") else spec["content"].encode()
        with sftp.open(path, "wb") as fh:
            fh.write(data)
        return {**base, "content": None, "size": len(data)}
    sftp.remove(path)
    return {**base, "content": None}
