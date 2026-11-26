"""Harnais d'action Ansible de Fluxonata, protocole v1. S'exécute dans le conteneur jetable de l'image Ansible.

Lit input.json, vérifie chaque entrée, écrit l'inventaire et les variables dans un répertoire privé, lance `ansible-playbook` sur playbook.yml
(la « source » de l'action) et écrit UNE enveloppe JSON sur la sortie standard. Tout ce qu'Ansible affiche part vers stderr (journaux), après
masquage des secrets. Les secrets arrivent par l'environnement (FLX_SSH_KEY_B64, FLX_SSH_PASSWORD, FLX_BECOME_PASSWORD), jamais par les entrées.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
real_stdout = sys.stdout
sys.stdout = sys.stderr

SECRET_ENV = ("FLX_SSH_KEY_B64", "FLX_SSH_PASSWORD", "FLX_BECOME_PASSWORD")
MAX_INVENTORY = 256 * 1024
MAX_KNOWN_HOSTS = 64 * 1024
LIMIT_RE = re.compile(r"^[A-Za-z0-9_.:,!&*\[\]@-]{1,200}$")
TAG_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,31}$")
ALLOWED = {
    "inventory",
    "extra_vars",
    "limit",
    "tags",
    "skip_tags",
    "check",
    "diff",
    "forks",
    "user",
    "become",
    "become_user",
    "timeout",
    "host_key_checking",
    "known_hosts",
    "fail_on_error",
}


class InvalidInput(Exception):
    pass


def emit(envelope: dict) -> None:
    real_stdout.write(json.dumps({"protocol": 1, **envelope}) + "\n")
    real_stdout.flush()


def check_inputs(raw: object) -> dict:
    """Valide les entrées et leur applique les valeurs par défaut. Toute entrée inconnue est refusée (une faute de frappe ne passe pas en silence)."""
    if not isinstance(raw, dict):
        raise InvalidInput("Les entrées doivent être un objet.")
    unknown = sorted(set(raw) - ALLOWED)
    if unknown:
        raise InvalidInput(f"Entrée inconnue : {', '.join(unknown)}.")
    inv = raw.get("inventory")
    if not isinstance(inv, dict) or not inv:
        raise InvalidInput(
            "« inventory » est obligatoire : un objet d'inventaire Ansible (all / hosts / children / vars)."
        )
    if len(json.dumps(inv)) > MAX_INVENTORY:
        raise InvalidInput("L'inventaire dépasse 256 Ko.")
    extra = raw.get("extra_vars", {})
    if not isinstance(extra, dict):
        raise InvalidInput("« extra_vars » doit être un objet.")

    def flag(name: str, default: bool) -> bool:
        value = raw.get(name, default)
        if not isinstance(value, bool):
            raise InvalidInput(f"« {name} » doit être vrai ou faux.")
        return value

    def number(name: str, default: int, low: int, high: int) -> int:
        value = raw.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise InvalidInput(f"« {name} » doit être un entier entre {low} et {high}.")
        return value

    def words(name: str, pattern: re.Pattern) -> list[str]:
        value = raw.get(name, [])
        if (
            not isinstance(value, list)
            or len(value) > 20
            or not all(isinstance(v, str) and pattern.match(v) for v in value)
        ):
            raise InvalidInput(f"« {name} » : vingt mots au plus, lettres, chiffres, point, tiret et souligné.")
        return value

    def name(key: str) -> str | None:
        value = raw.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not USER_RE.match(value):
            raise InvalidInput(f"« {key} » : un nom d'utilisateur simple est attendu.")
        return value

    limit = raw.get("limit")
    if limit is not None and (not isinstance(limit, str) or not LIMIT_RE.match(limit)):
        raise InvalidInput("« limit » : un motif d'hôtes sans espace ni guillemet.")
    known = raw.get("known_hosts")
    if known is not None and (not isinstance(known, str) or len(known) > MAX_KNOWN_HOSTS):
        raise InvalidInput("« known_hosts » : un texte de 64 Ko au plus.")
    return {
        "inventory": inv,
        "extra_vars": extra,
        "limit": limit,
        "tags": words("tags", TAG_RE),
        "skip_tags": words("skip_tags", TAG_RE),
        "check": flag("check", False),
        "diff": flag("diff", False),
        "forks": number("forks", 5, 1, 10),
        "user": name("user"),
        "become": flag("become", False),
        "become_user": name("become_user"),
        "timeout": number("timeout", 30, 5, 120),
        "host_key_checking": flag("host_key_checking", True),
        "known_hosts": known,
        "fail_on_error": flag("fail_on_error", True),
    }


def secret_values() -> list[str]:
    """Les valeurs à masquer partout : chaque secret, la clé décodée, et chacune de ses lignes assez longues pour être reconnues."""
    values: list[str] = []
    for key in SECRET_ENV:
        raw = os.environ.get(key, "")
        if not raw:
            continue
        values.append(raw)
        if key == "FLX_SSH_KEY_B64":
            try:
                pem = base64.b64decode(raw, validate=True).decode()
            except Exception:  # noqa: BLE001 - une clé mal encodée est signalée plus loin
                continue
            values.append(pem)
            values.extend(line.strip() for line in pem.splitlines() if len(line.strip()) >= 8)
    return sorted({v for v in values if v}, key=len, reverse=True)


def redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        text = text.replace(s, "***")
    return text


def write_private(path: str, content: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(content)


def prepare(cfg: dict, work: str) -> tuple[list[str], dict, dict]:
    """Écrit les fichiers de la mission ; renvoie (commande, environnement du processus Ansible, secrets sous forme de variables)."""
    inventory = os.path.join(work, "inventory.json")
    write_private(inventory, json.dumps(cfg["inventory"]))
    variables: dict = dict(cfg["extra_vars"])
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV and not k.startswith("ANSIBLE_")}
    env.update(
        {
            "ANSIBLE_HOME": os.path.join(work, "home"),
            "ANSIBLE_LOCAL_TEMP": os.path.join(work, "tmp"),
            "ANSIBLE_NOCOLOR": "1",
            "ANSIBLE_FORCE_COLOR": "0",
            "ANSIBLE_RETRY_FILES_ENABLED": "0",
            "ANSIBLE_LOCALHOST_WARNING": "0",
            "ANSIBLE_INVENTORY_UNPARSED_WARNING": "0",
            "ANSIBLE_PIPELINING": "1",
            "ANSIBLE_ROLES_PATH": os.path.join(HERE, "roles"),
            "ANSIBLE_CALLBACK_PLUGINS": os.path.join(HERE, "callback_plugins"),
            "ANSIBLE_CALLBACKS_ENABLED": "fluxonata_callback",
            "ANSIBLE_SSH_CONTROL_PATH_DIR": os.path.join(work, "cp"),
            "ANSIBLE_COLLECTIONS_PATH": "",
            "ANSIBLE_ACTION_WARNINGS": "0",
            "FLX_RESULT_FILE": os.path.join(work, "result.json"),
        }
    )
    for sub in ("home", "tmp", "cp"):
        os.makedirs(os.path.join(work, sub), mode=0o700)
    key_b64 = os.environ.get("FLX_SSH_KEY_B64", "")
    if key_b64:
        try:
            pem = base64.b64decode(key_b64, validate=True).decode()
        except Exception as exc:  # noqa: BLE001
            raise InvalidInput("FLX_SSH_KEY_B64 n'est pas une clé privée encodée en base64.") from exc
        key_path = os.path.join(work, "id_key")
        write_private(key_path, pem if pem.endswith("\n") else pem + "\n")
        variables["ansible_ssh_private_key_file"] = key_path
    if os.environ.get("FLX_SSH_PASSWORD"):
        variables["ansible_password"] = os.environ["FLX_SSH_PASSWORD"]
    if os.environ.get("FLX_BECOME_PASSWORD"):
        variables["ansible_become_password"] = os.environ["FLX_BECOME_PASSWORD"]
    ssh_args = [
        "-o",
        "BatchMode=yes" if not os.environ.get("FLX_SSH_PASSWORD") else "BatchMode=no",
    ]
    if cfg["host_key_checking"]:
        known = os.path.join(work, "known_hosts")
        write_private(
            known,
            (cfg["known_hosts"] or "").rstrip("\n") + "\n" if cfg["known_hosts"] else "",
        )
        ssh_args += [
            "-o",
            f"UserKnownHostsFile={known}",
            "-o",
            "StrictHostKeyChecking=yes",
        ]
        env["ANSIBLE_HOST_KEY_CHECKING"] = "True"
    else:
        ssh_args += [
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "StrictHostKeyChecking=no",
        ]
        env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
    variables.setdefault("ansible_ssh_common_args", " ".join(ssh_args))
    # Connexion locale : le compte du conteneur (nobody) n'a pas de dossier personnel, où Ansible range sinon ses fichiers temporaires ; on lui donne le répertoire
    # privé de la mission. Les machines distantes gardent le réglage d'Ansible (leur propre dossier personnel).
    local_tmp = os.path.join(work, "rtmp")
    os.makedirs(local_tmp, mode=0o700)
    variables.setdefault(
        "ansible_remote_tmp",
        "{{ '" + local_tmp + "' if (ansible_connection | default('ssh')) == 'local' else '~/.ansible/tmp' }}",
    )
    varfile = os.path.join(work, "vars.json")
    write_private(varfile, json.dumps(variables))
    binary = shutil.which("ansible-playbook")
    if not binary:
        raise FileNotFoundError("ansible-playbook est introuvable dans cette image.")
    cmd = [
        binary,
        "-i",
        inventory,
        "-e",
        f"@{varfile}",
        "--forks",
        str(cfg["forks"]),
        "--timeout",
        str(cfg["timeout"]),
    ]
    if cfg["limit"]:
        cmd += ["--limit", cfg["limit"]]
    if cfg["tags"]:
        cmd += ["--tags", ",".join(cfg["tags"])]
    if cfg["skip_tags"]:
        cmd += ["--skip-tags", ",".join(cfg["skip_tags"])]
    if cfg["check"]:
        cmd.append("--check")
    if cfg["diff"]:
        cmd.append("--diff")
    if cfg["user"]:
        cmd += ["--user", cfg["user"]]
    if cfg["become"]:
        cmd.append("--become")
    if cfg["become_user"]:
        cmd += ["--become-user", cfg["become_user"]]
    cmd.append(os.path.join(HERE, "playbook.yml"))
    return cmd, env, variables


def totals(stats: dict) -> dict:
    """Le bilan de toutes les machines réunies (ok, changées, échecs, injoignables, ignorées) : de quoi tester « rien n'a changé » sans connaître les noms des machines."""
    keys = ("ok", "changed", "failures", "unreachable", "skipped", "rescued", "ignored")
    return {k: sum(int(s.get(k, 0)) for s in stats.values()) for k in keys}


def summarize(stats: dict) -> tuple[int, int]:
    failed = sum(s.get("failures", 0) for s in stats.values())
    unreachable = sum(s.get("unreachable", 0) for s in stats.values())
    return failed, unreachable


def main() -> None:
    with open(os.path.join(HERE, "input.json")) as fh:
        payload = json.load(fh)
    try:
        cfg = check_inputs(payload.get("inputs"))
    except InvalidInput as exc:
        emit({"ok": False, "error": {"code": "INVALID_INPUT", "message": str(exc)[:300]}})
        return
    secrets = secret_values()
    work = tempfile.mkdtemp(prefix="flx-")
    try:
        try:
            cmd, env, _ = prepare(cfg, work)
        except (InvalidInput, FileNotFoundError) as exc:
            emit(
                {
                    "ok": False,
                    "error": {"code": "ANSIBLE_SETUP", "message": str(exc)[:300]},
                }
            )
            return
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=work,
        )  # noqa: S603 - liste d'arguments construite ci-dessus, sans shell
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stderr.write(redact(line, secrets))
        code = proc.wait()
        summary: dict = {}
        result_file = env["FLX_RESULT_FILE"]
        if os.path.exists(result_file):
            with open(result_file) as fh:
                summary = json.load(fh)
        stats = summary.get("stats", {})
        if not stats:
            emit(
                {
                    "ok": False,
                    "error": {
                        "code": "ANSIBLE_FAILED",
                        "message": f"ansible-playbook s'est arrêté (code {code}) avant d'avoir joué quoi que ce soit : voir les journaux.",
                    },
                }
            )
            return
        failed, unreachable = summarize(stats)
        custom = summary.get("custom") or {}
        result = json.loads(
            redact(
                json.dumps(
                    {
                        "ok": code == 0,
                        "check": cfg["check"],
                        "hosts": len(stats),
                        "stats": stats,
                        "tasks": summary.get("tasks", []),
                        "tasks_dropped": summary.get("dropped", 0), "totals": totals(stats),
                        "data": custom.get("_run", custom),
                    }
                ),
                secrets,
            )
        )
        if code != 0 and cfg["fail_on_error"]:
            bad = [t for t in result["tasks"] if t["status"] in ("failed", "unreachable") and not t.get("ignored")]
            first = f" Première erreur : {bad[0]['host']} — {bad[0]['task']} — {bad[0]['msg']}" if bad else ""
            emit(
                {
                    "ok": False,
                    "error": {
                        "code": "ANSIBLE_FAILED",
                        "message": f"{failed} échec(s), {unreachable} hôte(s) injoignable(s).{first}"[:300],
                    },
                }
            )
            return
        emit({"ok": True, "result": result})
    except BaseException as exc:  # noqa: BLE001 - toute panne du harnais est rapportée de façon structurée
        emit(
            {
                "ok": False,
                "error": {
                    "code": "ANSIBLE_ERROR",
                    "message": redact(f"{type(exc).__name__}: {exc}", secrets)[:300],
                },
            }
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
