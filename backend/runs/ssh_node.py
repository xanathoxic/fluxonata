"""The « Commandes SSH » step: run commands on a server over SSH, directly or through ordered jump hosts.

Two session kinds, both agnostic (no device, prompt or error pattern is built in — commands and patterns come from the workflow):
- `exec`  : each command runs on its own channel; they share nothing (no working directory, no variables). stdout, stderr, the exit
            status (null when the server sent none) and the duration are collected.
- `shell` : one persistent session for the duration of the step (optional PTY): send / expect / respond (see `ssh_shell.py`).

Safety, all server-side (nothing runs in the web process or the orchestrator; the step is executed by a worker with the vault key and
an outgoing network, never by one holding the Docker socket — docs/ARCHITECTURE.md, invariant I1 is NOT changed by this step):
- every server's key is checked against the fingerprint(s) pinned in ITS connection (`host_key_sha256`, required); an unknown or different
  key is refused, there is no way to switch that off from a workflow;
- only credentials declared in Fluxonata are used: no key discovered in ~/.ssh, no inherited SSH agent, no agent forwarding;
- connections are fixed names looked up in the run's workspace and environment at execution time; jump hosts are bounded and may not repeat;
- the first hop is resolved and checked by `core.netsec` like every outgoing call and pinned to the validated address; the NEXT hops are
  reached through the previous one (`direct-tcpip`) and their names are resolved BY THE PREVIOUS HOP, not by the platform;
- secrets are never stored on the step: an interaction that needs one references a secret by alias (`secrets: {alias: secret name}`);
- `mode: test` opens no connection, resolves no name and reveals no secret; results are marked `simulated`.
After an interruption a remote command may have gone on: the result says so (`uncertain`) and nothing is replayed. Closing the
connection is NOT a guarantee that the remote command stopped."""

import logging
import re
import time

from audit.models import record
from vault.external import ExternalVaultError
from vault.injection import mask_text

from . import ssh_core as core
from .ssh_core import ID_RE, MAX_OP_TIMEOUT, SshStepError
from .ssh_core import int_in as _int
from .ssh_core import text_in as _text

log = logging.getLogger("fluxonata.ssh")

SESSIONS = ("exec", "shell")
MAX_JUMPS = 5
MAX_COMMANDS = 50
DEFAULT_TIMEOUT, MAX_TIMEOUT = 60, 600  # whole step, seconds
DEFAULT_OP_TIMEOUT = 30
DEFAULT_OUTPUT, MAX_OUTPUT = 65536, 524288  # per stream and per command / step
MAX_TOTAL_OUTPUT = 1048576  # all results of the step
MAX_CONNECT_RETRIES = 3
ALIAS_RE = ID_RE
FINGERPRINT_RE = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
MASKED = "<masqué>"


def check_connection_config(config: dict) -> None:
    """Settings of an `ssh` connection (also called when it is created). With a host, that server's key fingerprint is mandatory (no « trust on
    first use »). Without a host the connection is a reusable IDENTITY (user + private key): the step names the host and its fingerprint."""
    port = str(config.get("port") or "").strip()
    if port and (not re.fullmatch(r"[0-9]+", port) or not 1 <= int(port) <= 65535):
        raise SshStepError("SSH_CONFIG", "Port invalide.")
    host = str(config.get("host") or "").strip()
    if host and not core.valid_host(host):
        raise SshStepError("SSH_CONFIG", "Nom d'hôte ou adresse IP invalide (sans schéma, port ni chemin).")
    fps = core.pinned_fingerprints(config)
    if host and (not fps or any(not FINGERPRINT_RE.match(f) for f in fps)):
        raise SshStepError(
            "SSH_CONFIG",
            "Indiquez l'empreinte de la clé du serveur (« SHA256:… », 43 caractères ; plusieurs séparées par des espaces) : "
            "une connexion SSH sans clé serveur vérifiée est refusée.",
        )
    if not host and fps:
        raise SshStepError(
            "SSH_CONFIG",
            "Sans hôte, la connexion est une identité réutilisable (une clé pour plusieurs serveurs) : l'empreinte de la clé d'un serveur "
            "se donne avec cet hôte, dans l'étape.",
        )
    for key in ("connect_timeout_seconds", "auth_timeout_seconds"):
        raw = str(config.get(key) or "").strip()
        if raw and (not re.fullmatch(r"[0-9]+", raw) or not 1 <= int(raw) <= 60):
            raise SshStepError("SSH_CONFIG", f"« {key} » : de 1 à 60 secondes.")
    name = str(config.get("passphrase_secret") or "")
    if name and not ALIAS_RE.match(name):
        raise SshStepError("SSH_CONFIG", "« passphrase_secret » : nom d'un secret de l'espace de travail.")


def find_connection(workspace_id, environment_id, name, with_secret=True):
    """The SSH connection `name` of this workspace for this environment. `with_secret=False` never loads the secret row (preparation and
    simulation need the settings only: no secret is read or decrypted there)."""
    from django.db.models import Q

    from vault.models import Connection

    qs = Connection.objects.select_related("secret") if with_secret else Connection.objects.all()
    return (
        qs.filter(workspace_id=workspace_id, name=name, kind="ssh")
        .filter(Q(environment_id=environment_id) | Q(environment__isnull=True))
        .order_by("-environment_id")
        .first()
    )


def hop_items(node: dict) -> list[dict]:
    """The chain as written in the step, jumps first then the target: `{connection, host?, port?, host_key_sha256?}` for each. A jump is a plain
    connection name (its host comes from the connection) or an object (its host comes from the step)."""
    out: list[dict] = []
    jumps = node.get("jumps")
    for j in jumps if isinstance(jumps, list) else []:
        out.append(
            {"connection": j} if isinstance(j, str) else dict(j) if isinstance(j, dict) else {"connection": None}
        )
    out.append({k: node[k] for k in ("connection", "host", "port", "host_key_sha256") if node.get(k) is not None})
    return out


def chain_names(node: dict) -> list:
    """Jump connections then the target, as written in the step (fixed names; anything that is not a name is refused by the callers)."""
    return [h.get("connection") for h in hop_items(node)]


def unauthorized_connections(workspace, node: dict) -> list[str]:
    """Names, among the step's target and jumps, that are not an SSH connection OF THIS workspace (missing, of another type, of another
    workspace, or deleted since): one answer for all of them, so nothing is learned about a connection the caller cannot use."""
    from vault.models import Connection

    names = chain_names(node)
    bad = [str(n) for n in names if not isinstance(n, str) or not n or "{{" in n]
    wanted = {n for n in names if isinstance(n, str) and n and "{{" not in n}
    found = set(
        Connection.objects.filter(workspace=workspace, kind="ssh", name__in=wanted).values_list("name", flat=True)
    )
    return bad + sorted(wanted - found)


# Fields the SSH step defines (one source: the MCP path refuses anything else, nested structures included).
NODE_KEYS = {
    "id", "type", "name", "ui", "connection", "host", "port", "host_key_sha256", "jumps", "session", "mode", "commands", "steps", "pty", "stop_on_error",
    "timeout_seconds", "command_timeout_seconds", "max_output_bytes", "connect_retries", "secrets", "mocks",
}  # fmt: skip
COMMAND_KEYS = {"id", "run", "sensitive", "ignore_error", "expect_exit", "timeout_seconds"}
STEP_KEYS = {
    "id",
    "send",
    "send_secret",
    "eol",
    "expect",
    "success",
    "failure",
    "responses",
    "sensitive",
    "ignore_error",
    "timeout_seconds",
}
RESPONSE_KEYS = {"expect", "send", "send_secret", "eol", "max", "sensitive"}
PTY_KEYS = {"enabled", "term", "cols", "rows"}
MOCK_KEYS = {"id", "stdout", "stderr", "exit_status", "failure"}
JUMP_KEYS = {"connection", "host", "port", "host_key_sha256"}


def unknown_fields(node: dict) -> list[str]:
    """Paths of the fields of an SSH step (and of its nested commands, steps, responses, pty, mocks) that the step does not define."""
    out = [k for k in node if k not in NODE_KEYS]

    def walk(items, allowed, where):
        for i, item in enumerate(items if isinstance(items, list) else []):
            if isinstance(item, dict):
                out.extend(f"{where}[{i}].{k}" for k in item if k not in allowed)

    jumps = node.get("jumps")
    for i, j in enumerate(jumps if isinstance(jumps, list) else []):
        if isinstance(j, dict):
            out.extend(f"jumps[{i}].{k}" for k in j if k not in JUMP_KEYS)
    walk(node.get("commands"), COMMAND_KEYS, "commands")
    walk(node.get("mocks"), MOCK_KEYS, "mocks")
    steps = node.get("steps")
    walk(steps, STEP_KEYS, "steps")
    for i, s in enumerate(steps if isinstance(steps, list) else []):
        if isinstance(s, dict):
            walk(s.get("responses"), RESPONSE_KEYS, f"steps[{i}].responses")
    if isinstance(node.get("pty"), dict):
        out.extend(f"pty.{k}" for k in node["pty"] if k not in PTY_KEYS)
    return out


def db_lookup(workspace_id, environment_id):
    """`name -> (name, host, port)` or None, read from the database (settings only, never the secret)."""

    def look(name):
        conn = find_connection(workspace_id, environment_id, name, with_secret=False)
        return (
            None
            if conn is None
            else (conn.name, str(conn.config.get("host", "")).lower(), str(conn.config.get("port") or "22"))
        )

    return look


def endpoints_of(workspace) -> dict:
    """Every SSH connection of a workspace as `name -> (name, host, port)`, in ONE query that never touches the secrets: for callers that
    cannot reach the database later (the dry run runs its scenarios in threads)."""
    from vault.models import Connection

    rows = (
        Connection.objects.filter(workspace=workspace, kind="ssh")
        .order_by("-environment_id")
        .values_list("name", "config")
    )
    return {
        name: (name, str((cfg or {}).get("host", "")).lower(), str((cfg or {}).get("port") or "22"))
        for name, cfg in rows
    }


def _hop_problems(item: dict, name, conn) -> tuple[list[tuple[str, str]], tuple | None]:
    """Problems of one hop and where it points (host, port) when that is known: the host comes from the connection (then the
    step names none) or from the step (then the connection has none and the step gives the fingerprint)."""
    cfg = conn.config or {}
    own = str(cfg.get("host") or "").strip().lower()
    host = item.get("host")
    if host in (None, ""):
        if not own:
            return [
                (
                    "bad_ssh",
                    f"La connexion « {name} » n'a pas d'hôte : indiquez l'hôte et l'empreinte de sa clé dans l'étape.",
                )
            ], None
        return [], (own, str(cfg.get("port") or "22"))
    if own:
        return [
            (
                "bad_ssh",
                f"La connexion « {name} » définit déjà son hôte : l'étape ne peut pas en indiquer un autre.",
            )
        ], None
    out: list[tuple[str, str]] = []
    literal = isinstance(host, str) and "{{" not in host
    if literal and not core.valid_host(host):
        out.append(("bad_ssh", f"Hôte « {host} » invalide (nom ou adresse IP, sans schéma, port ni chemin)."))
    fp = item.get("host_key_sha256")
    if not fp:
        out.append(("bad_ssh", f"Hôte « {host} » : indiquez l'empreinte de sa clé (« SHA256:… »)."))
    elif (
        isinstance(fp, str)
        and "{{" not in fp
        and any(not FINGERPRINT_RE.match(f) for f in core.pinned_fingerprints({"host_key_sha256": fp}))
    ):
        out.append(("bad_ssh", f"Hôte « {host} » : empreinte invalide (« SHA256:… », 43 caractères)."))
    port = item.get("port")
    if (
        port not in (None, "")
        and isinstance(port, (int, str))
        and "{{" not in str(port)
        and not re.fullmatch(r"[0-9]+", str(port))
    ):
        out.append(("bad_ssh", "Port de l'étape : un entier de 1 à 65535."))
    return out, ((host.strip().lower(), str(port or 22)) if literal else None)


def chain_problems(node: dict, workspace) -> list[tuple[str, str]]:
    """Static check of the chain (publication time): shapes, fixed names, connections of THIS workspace, and for each hop where its host comes
    from — the connection (then the step names none) or the step (then the connection has none, and the step gives the fingerprint). Values
    written as expressions are checked when the step starts."""
    from vault.models import Connection

    jumps = node.get("jumps")
    if jumps is not None and (not isinstance(jumps, list) or len(jumps) > MAX_JUMPS):
        return [("bad_ssh", f"Rebonds : une liste de {MAX_JUMPS} connexions au plus.")]
    items = hop_items(node)
    names = [i.get("connection") for i in items]
    if any(not isinstance(x, str) or not x for x in names):
        return [("missing_connection", "Choisissez la connexion cible (et des rebonds nommés).")]
    if any("{{" in str(x) for x in names):
        return [("bad_ssh", "Les connexions (cible et rebonds) sont des noms fixes, sans expression.")]
    out: list[tuple[str, str]] = []
    seen: set = set()
    for item, name in zip(items, names, strict=True):
        conn = Connection.objects.filter(workspace=workspace, name=name, kind="ssh").first()
        if conn is None:
            out.append(("unknown_connection", f"Connexion « Serveur SSH » introuvable : {name}."))
            continue
        found, where = _hop_problems(item, name, conn)
        out += found
        if where is not None:
            if where in seen:
                out.append(("bad_ssh", "Deux étapes de la chaîne désignent le même serveur (boucle refusée)."))
            seen.add(where)
    return out


def _step_endpoint(item: dict, found_host: str, name: str, resolve, ctx) -> dict | None:
    """The endpoint a step chooses for a connection that has no host of its own (None when the connection brings its own). Evaluated values are
    validated strictly: host syntax, port, and a pinned server-key fingerprint — mandatory, a host named by a step is never trusted on first use."""
    raw = item.get("host")
    if raw in (None, ""):
        if not found_host:
            raise SshStepError(
                "SSH_CONFIG",
                f"La connexion « {name} » n'a pas d'hôte : indiquez l'hôte (adresse IP ou nom) et l'empreinte de sa clé dans l'étape.",
            )
        if item.get("port") not in (None, "") or item.get("host_key_sha256") not in (None, ""):
            raise SshStepError(
                "SSH_CONFIG", f"« {name} » définit son hôte : le port et l'empreinte se règlent dans la connexion."
            )
        return None
    if found_host:
        raise SshStepError(
            "SSH_CONFIG",
            f"La connexion « {name} » définit déjà son hôte : l'étape ne peut pas en indiquer un autre "
            "(utilisez une connexion sans hôte pour choisir l'hôte dans l'étape).",
        )
    host = str(resolve(raw, ctx) if isinstance(raw, str) else raw).strip().lower()
    if not core.valid_host(host):
        raise SshStepError("SSH_CONFIG", "Hôte de l'étape invalide (nom ou adresse IP, sans schéma, port ni chemin).")
    port_raw = resolve(item.get("port"), ctx) if isinstance(item.get("port"), str) else item.get("port")
    if port_raw in (None, ""):
        port = 22
    elif (
        isinstance(port_raw, bool)
        or not str(port_raw).isascii()
        or not str(port_raw).isdigit()
        or not 1 <= int(port_raw) <= 65535
    ):
        raise SshStepError("SSH_CONFIG", "Port de l'étape : un entier de 1 à 65535.")
    else:
        port = int(port_raw)
    fp_raw = item.get("host_key_sha256")
    fps = core.pinned_fingerprints({"host_key_sha256": resolve(fp_raw, ctx) if isinstance(fp_raw, str) else ""})
    if not fps or any(not FINGERPRINT_RE.match(f) for f in fps):
        raise SshStepError(
            "SSH_CONFIG",
            f"Hôte « {host} » : indiquez l'empreinte de sa clé (« SHA256:… », 43 caractères) : un hôte choisi par l'étape est toujours vérifié.",
        )
    return {"host": host, "port": port, "fingerprints": fps}


def _chain(node: dict, lookup, resolve, ctx) -> tuple[list[dict], list]:
    """Target + jump connections, in connection order [first jump, …, target]; fixed names, same workspace, no repeated SERVER (the same identity
    may serve several hosts). Returns the chain and, per hop, the endpoint chosen by the step (None when the connection has its own host)."""
    items = hop_items(node)
    names = [i.get("connection") for i in items]
    if len(names) - 1 > MAX_JUMPS:
        raise SshStepError("SSH_CONFIG", f"Trop de rebonds (au plus {MAX_JUMPS}).")
    if any(not isinstance(n, str) or not n or "{{" in n for n in names):
        raise SshStepError("SSH_CONFIG", "Les connexions (cible et rebonds) sont des noms fixes, sans expression.")
    chain, endpoints, seen = [], [], set()
    for item, name in zip(items, names, strict=True):
        found = lookup(name)  # settings only: no secret is loaded here
        if found is None:
            raise SshStepError(
                "CONNECTION_MISSING",
                f"Connexion « {name} » introuvable pour cet environnement (type « Serveur SSH »).",
                "Créez la connexion dans Connexions.",
            )
        endpoint = _step_endpoint(item, found[1], str(name), resolve, ctx)
        where = (endpoint["host"], str(endpoint["port"])) if endpoint else (found[1], found[2])
        if where in seen:
            raise SshStepError("SSH_CONFIG", "Deux étapes de la chaîne désignent le même serveur (boucle refusée).")
        seen.add(where)
        chain.append({"name": found[0]})
        endpoints.append(endpoint)
    return chain, endpoints


def _command_items(raw, resolve, ctx) -> list:
    value = resolve(raw, ctx) if isinstance(raw, str) else raw
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise SshStepError("SSH_CONFIG", "Commandes : une liste (ou une expression qui produit une liste).")
    return value


def prepare(node: dict, resolve, ctx: dict, workspace_id, environment_id, lookup=None) -> dict:
    """Validates the step AFTER expressions are evaluated and builds the spec the worker runs (no secret in it)."""
    session = node.get("session", "exec")
    if session not in SESSIONS:
        raise SshStepError("SSH_CONFIG", "Session : « exec » ou « shell ».")
    chain, endpoints = _chain(node, lookup or db_lookup(workspace_id, environment_id), resolve, ctx)
    timeout = _int(node.get("timeout_seconds"), DEFAULT_TIMEOUT, 1, MAX_TIMEOUT, "Délai global")
    op_timeout = _int(node.get("command_timeout_seconds"), DEFAULT_OP_TIMEOUT, 1, MAX_OP_TIMEOUT, "Délai par opération")
    spec = {
        "kind": "ssh",
        "mode": "reel" if node.get("mode") == "reel" else "test",
        "session": session,
        "connection": chain[-1]["name"],
        "jumps": [c["name"] for c in chain[:-1]],
        "endpoints": endpoints,  # per hop (jumps then target): the host chosen by the step, or None
        "timeout": timeout,
        "op_timeout": min(op_timeout, timeout),
        "max_output": _int(node.get("max_output_bytes"), DEFAULT_OUTPUT, 1, MAX_OUTPUT, "Taille de sortie"),
        "stop_on_error": node.get("stop_on_error", True) is not False,
        "connect_retries": _int(node.get("connect_retries"), 0, 0, MAX_CONNECT_RETRIES, "Nouvelles tentatives"),
        "secrets": core.clean_secret_aliases(node.get("secrets")),
        "mocks": core.clean_mocks(node.get("mocks")),
    }
    if session == "exec":
        spec["commands"] = _prepare_commands(node.get("commands"), resolve, ctx)
    else:
        from . import ssh_shell

        spec["pty"] = ssh_shell.clean_pty(node.get("pty"))
        spec["steps"] = ssh_shell.prepare_steps(node.get("steps"), resolve, ctx, spec["secrets"])
    return spec


def _prepare_commands(raw, resolve, ctx) -> list[dict]:
    items = _command_items(raw if raw is not None else [], resolve, ctx)
    if not items:
        raise SshStepError("SSH_CONFIG", "Indiquez au moins une commande.")
    if len(items) > MAX_COMMANDS:
        raise SshStepError("SSH_CONFIG", f"Trop de commandes (au plus {MAX_COMMANDS}).")
    out, ids = [], set()
    for i, item in enumerate(items, 1):
        obj = {"run": item} if isinstance(item, str) else item
        if not isinstance(obj, dict):
            raise SshStepError("SSH_CONFIG", f"Commande {i} : un texte ou un objet {{id, run, …}}.")
        cid = str(obj.get("id") or f"c{i}")
        if not ID_RE.match(cid) or cid in ids:
            raise SshStepError("SSH_CONFIG", f"Commande {i} : identifiant invalide ou en double.")
        ids.add(cid)
        exits = obj.get("expect_exit", [0])
        if not isinstance(exits, list) or not exits or not all(isinstance(e, int) and 0 <= e <= 255 for e in exits):
            raise SshStepError("SSH_CONFIG", f"Commande {cid} : « expect_exit » est une liste de codes de 0 à 255.")
        out.append(
            {
                "id": cid,
                "run": _text(resolve(obj.get("run"), ctx), f"Commande {cid}"),
                "sensitive": bool(obj.get("sensitive")),
                "ignore_error": bool(obj.get("ignore_error")),
                "expect_exit": exits,
                "timeout": _int(obj.get("timeout_seconds"), None, 1, MAX_OP_TIMEOUT, f"Commande {cid} : délai"),
            }
        )
    return out


def failure_output(node: dict, exc: SshStepError) -> dict:
    """Output of a step refused before anything ran (bad configuration, connection missing): same shape, nothing executed."""
    return {
        "status": "failed",
        "connected": False,
        "simulated": node.get("mode") != "reel",
        "session": node.get("session", "exec") if node.get("session", "exec") in SESSIONS else "exec",
        "duration_ms": 0,
        "hops": [],
        "results": [],
        "failed_step": None,
        "error_type": exc.error_type,
        "error": {"type": exc.error_type, "message": exc.message},
        "truncated": False,
        "uncertain": False,
        "failed_count": 0,
    }


def scrub_spec(spec: dict) -> dict:
    """What stays in the database once the step is over: the text of sensitive commands, sends and responses is dropped (a reference
    to a secret by alias is not a secret and stays)."""
    out = dict(spec)
    if out.get("commands"):
        out["commands"] = [{**c, "run": MASKED} if c.get("sensitive") else c for c in out["commands"]]
    if out.get("steps"):
        steps = []
        for s in out["steps"]:
            hide = bool(s.get("sensitive"))
            responses = [
                {**r, "send": MASKED} if (hide or r.get("sensitive")) and r.get("send") is not None else r
                for r in s.get("responses", [])
            ]
            step = {**s, "responses": responses}
            if hide and s.get("send") is not None:
                step["send"] = MASKED
            steps.append(step)
        out["steps"] = steps
    return out


def redact_node(node: dict) -> dict:
    """The step as an assistant (MCP, AI) may read it: the text of sensitive commands, sends and responses is hidden, and so are the
    simulated results of those items. Secret references are aliases and names, never values: they stay."""
    if node.get("type") != "ssh":
        return node
    out = dict(node)
    hidden_ids: set[str] = set()
    if isinstance(out.get("commands"), list):
        cmds = []
        for i, c in enumerate(out["commands"], 1):
            if isinstance(c, dict) and c.get("sensitive"):
                hidden_ids.add(str(c.get("id") or f"c{i}"))
                c = {**c, "run": MASKED}
            cmds.append(c)
        out["commands"] = cmds
    if isinstance(out.get("steps"), list):
        steps = []
        for i, s in enumerate(out["steps"], 1):
            if isinstance(s, dict):
                hide = bool(s.get("sensitive")) or s.get("send_secret") is not None
                if hide:
                    hidden_ids.add(str(s.get("id") or f"s{i}"))
                responses = [
                    {**r, "send": MASKED}
                    if isinstance(r, dict) and (hide or r.get("sensitive")) and r.get("send") is not None
                    else r
                    for r in s.get("responses") or []
                ]
                s = {
                    **s,
                    **({"send": MASKED} if hide and s.get("send") is not None else {}),
                    **({"responses": responses} if "responses" in s else {}),
                }
            steps.append(s)
        out["steps"] = steps
    if isinstance(out.get("mocks"), list):
        out["mocks"] = [m for m in out["mocks"] if not (isinstance(m, dict) and str(m.get("id")) in hidden_ids)]
    return out


def redact_graph(graph):
    """Copy of a workflow graph with every SSH step redacted (see `redact_node`)."""
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        return graph
    return {**graph, "nodes": [redact_node(n) if isinstance(n, dict) else n for n in graph["nodes"]]}


def ssh_nodes(graph) -> dict:
    return {
        n["id"]: n
        for n in (graph or {}).get("nodes", [])
        if isinstance(n, dict) and n.get("type") == "ssh" and isinstance(n.get("id"), str)
    }


def sensitive_texts(spec: dict) -> list[str]:
    """Texts the step itself marked sensitive: masked wherever the server may echo them back (a PTY echoes what it receives)."""
    texts = [c["run"] for c in spec.get("commands", []) if c.get("sensitive")]
    for s in spec.get("steps", []):
        if s.get("sensitive") and s.get("send"):
            texts.append(s["send"])
        texts += [
            r["send"] for r in s.get("responses", []) if (s.get("sensitive") or r.get("sensitive")) and r.get("send")
        ]
    return texts


def static_problems(node: dict) -> list[str]:
    """Problems visible BEFORE evaluation (publication): shape, bounds, unique ids, patterns that are plain text, unknown mock ids.
    Values that are expressions are checked when the step starts."""
    out: list[str] = []
    ident = lambda value, _ctx: value  # noqa: E731 - expressions stay as written
    try:
        _int(node.get("timeout_seconds"), None, 1, MAX_TIMEOUT, "Délai global")
        _int(node.get("command_timeout_seconds"), None, 1, MAX_OP_TIMEOUT, "Délai par opération")
        _int(node.get("max_output_bytes"), None, 1, MAX_OUTPUT, "Taille de sortie")
        _int(node.get("connect_retries"), None, 0, MAX_CONNECT_RETRIES, "Nouvelles tentatives")
        aliases = core.clean_secret_aliases(node.get("secrets"))
        mocks = core.clean_mocks(node.get("mocks"))
        ids: list[str] = []
        if node.get("session", "exec") == "exec":
            raw = node.get("commands")
            if raw in (None, "", []):
                out.append("Indiquez au moins une commande.")
            elif isinstance(raw, list):
                ids = [c["id"] for c in _prepare_commands(raw, ident, {})]
            elif not isinstance(raw, str):
                out.append("Commandes : une liste (ou une expression qui produit une liste).")
        elif node.get("session") == "shell":
            from . import ssh_shell

            ssh_shell.clean_pty(node.get("pty"))
            raw = node.get("steps")
            if isinstance(raw, list):
                ids = [s["id"] for s in ssh_shell.prepare_steps(_untemplated(raw), ident, {}, aliases)]
            else:
                out.append("Indiquez au moins une étape de session.")
        unknown = sorted(set(mocks) - set(ids)) if ids else []
        if unknown:
            out.append(f"Simulation pour une commande ou étape inconnue : {', '.join(unknown)}.")
    except SshStepError as exc:
        out.append(exc.message)
    return out


def _untemplated(steps):
    """Patterns written as expressions (`{{ … }}`) cannot be compiled before they are evaluated: a neutral stand-in is checked instead."""

    def fix_patterns(step):
        if not isinstance(step, dict):
            return step
        s = dict(step)
        for key in ("expect", "success", "failure"):
            v = s.get(key)
            if isinstance(v, str) and "{{" in v:
                s[key] = "x"
            elif isinstance(v, list):
                s[key] = ["x" if isinstance(p, str) and "{{" in p else p for p in v]
        if isinstance(s.get("responses"), list):
            s["responses"] = [
                {**r, "expect": "x"}
                if isinstance(r, dict) and isinstance(r.get("expect"), str) and "{{" in r["expect"]
                else r
                for r in s["responses"]
            ]
        return s

    return [fix_patterns(s) for s in steps]


# ---------------------------------------------------------------------------------------------------- simulated
def _item_result(item: dict, mock: dict | None, kind: str, combined: bool = False) -> dict:
    mock = mock or {}
    stdout, stderr = str(mock.get("stdout", "")), str(mock.get("stderr", ""))
    status_code = mock.get("exit_status", 0 if kind == "command" else None)
    res = core.blank_result(item["id"], kind, combined)
    res.update(
        {
            "status": "ok",
            "command": (MASKED if item.get("sensitive") else item["run"]) if kind == "command" else None,
            "stdout": stdout,
            "stderr": stderr,
            "exit_status": status_code if isinstance(status_code, int) and not isinstance(status_code, bool) else None,
            "sensitive": bool(item.get("sensitive")),
            "simulated": True,
        }
    )
    if kind == "command" and res["exit_status"] is not None and res["exit_status"] not in item["expect_exit"]:
        res["status"], res["error"] = "failed", core.error_dict("remote_status", "Statut de sortie simulé non attendu.")
    if kind == "step":
        res["sent"] = None if item.get("sensitive") or item.get("send") is None else item["send"][:500]
    if kind == "step" and mock.get("failure"):
        res["status"], res["error"] = "failed", core.error_dict("remote_status", "Échec simulé.")
    core.redact_sensitive(res)
    return res


def simulated_output(spec: dict) -> dict:
    """Nothing is opened, resolved or revealed: the results come from `mocks` (by id) or are empty and successful, always `simulated`."""
    kind = "command" if spec["session"] == "exec" else "step"
    items = spec["commands"] if kind == "command" else spec["steps"]
    results, failed, stop = [], None, False
    combined = bool((spec.get("pty") or {}).get("enabled"))
    for item in items:
        if stop:
            results.append({**core.blank_result(item["id"], kind, combined), "status": "not_run", "simulated": True})
            continue
        res = _item_result(item, spec["mocks"].get(item["id"]), kind, combined)
        results.append(res)
        if res["status"] == "failed" and not item.get("ignore_error"):
            failed = failed or res
            stop = spec["stop_on_error"]
    return core.build_output(spec, results, connected=False, simulated=True, duration_ms=0, failed=failed)


def dryrun_output(node: dict, ctx: dict, endpoints: dict, force_failure: bool = False) -> dict:
    """What the dry run (essai à blanc) gives for an SSH step: the SAME simulation as test mode (`prepare` + `simulated_output`), from the
    step's explicit `mocks`; no connection, no name resolution, no secret loaded, no database access (the connections' settings come from
    `endpoints_of`, read once by the caller). `force_failure` makes the first item fail so that the `error` branch is exercised with the
    partial results the real step would leave. A refused configuration gives the usual minimal output."""
    from workflows.expressions import resolve

    try:
        spec = prepare({**node, "mode": "test"}, resolve, ctx, None, None, lookup=endpoints.get)
    except SshStepError as exc:
        return failure_output(node, exc)
    if force_failure and spec["session"] == "exec" and spec["commands"]:
        first = spec["commands"][0]["id"]
        spec["mocks"] = {**spec["mocks"], first: {**spec["mocks"].get(first, {}), "exit_status": 1}}
    elif force_failure and spec.get("steps"):
        first = spec["steps"][0]["id"]
        spec["mocks"] = {**spec["mocks"], first: {**spec["mocks"].get(first, {}), "failure": True}}
    return simulated_output(spec)


# ---------------------------------------------------------------------------------------------------- worker side
def _fail(code: str, message: str, next_step: str = "", **details):
    return None, {"code": code, "message": message, "next_step": next_step, **({"details": details} if details else {})}


def execute(sr) -> tuple[dict | None, dict | None]:
    spec = sr.http_job["spec"]
    return execute_spec(spec, sr.run.workspace, sr.run.environment_id, sr.run, cancel_check=core.step_checker(sr))


def execute_spec(spec, workspace, environment_id, audit_target, cancel_check=None, connector=None):
    """Runs the step. Returns (result, error): `result` is also set when the step failed (partial results, kept bounded);
    `error` is None on success."""
    started = time.monotonic()
    cancel_check = cancel_check or (lambda: False)
    deadline = started + spec["timeout"]
    try:
        hops = core.load_hops(spec, workspace, environment_id, find_connection)
    except SshStepError as exc:
        return _fail(exc.code, exc.message, exc.next_step)
    except ExternalVaultError as exc:
        return _fail(exc.code, exc.message, "Vérifiez le coffre externe et le secret de la connexion.")
    secrets = [s for h in hops for s in h.secret_values] + list(hops.alias_values.values()) + sensitive_texts(spec)
    session = None
    try:
        try:
            session = core.open_chain(hops, spec, deadline, cancel_check, connector=connector)
        except SshStepError as exc:
            out = core.build_output(
                spec, [], connected=False, simulated=False, duration_ms=_ms(started), failed=None, error=exc
            )
            return _final(out, exc, secrets, spec, workspace, audit_target, started)
        if spec["session"] == "exec":
            out = core.run_exec(session, spec, deadline, cancel_check, started)
        else:
            from . import ssh_shell

            out = ssh_shell.run_shell(session, spec, hops.alias_values, deadline, cancel_check, started)
    except Exception:  # noqa: BLE001 - a bug must not leak details (they could hold a secret) nor leave a session open
        log.exception("ssh step crashed")
        err = SshStepError(
            "SSH_INTERNAL", "Erreur interne pendant la session SSH.", "Consultez les journaux du worker."
        )
        out = core.build_output(
            spec, [], connected=session is not None, simulated=False, duration_ms=_ms(started), failed=None, error=err
        )
        out["uncertain"] = session is not None  # a command may already have been sent
        return _final(out, err, secrets, spec, workspace, audit_target, started)
    finally:
        if session is not None:
            session.close()  # reverse order: channels, then each hop's transport from the last to the first
    return _final(out, None, secrets, spec, workspace, audit_target, started)


def _ms(started) -> int:
    return int((time.monotonic() - started) * 1000)


def _final(out, exc, secrets, spec, workspace, audit_target, started):
    out = core.mask_output(out, secrets, mask_text)
    record(
        "ssh.session",
        workspace=workspace,
        target=audit_target,
        hops=len(spec["jumps"]),
        session=spec["session"],
        items=len(out["results"]),
        status=out["status"],
        error_type=out.get("error_type"),
        secrets_used=sorted(
            spec.get("secrets", {}).values()
        ),  # names only: which secrets of the workspace this step could read
        ms=_ms(started),
    )
    if out["status"] == "succeeded":
        return out, None
    return out, core.engine_error(out)
