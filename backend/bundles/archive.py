"""Package archive format (fluxonata-package/1): build, and hardened read/validation.

Reading never executes anything, never extracts to disk, and bounds everything: entry count, per-file and total
uncompressed size (checked on the header AND on bytes actually read), compression ratio, path shape, file types,
symlinks, undeclared files and checksums.
"""

import hashlib
import io
import json
import posixpath
import re
import stat
import zipfile
from datetime import UTC, datetime

FORMAT = "fluxonata-package/1"
MAX_ARCHIVE = 5 * 1024 * 1024
MAX_ENTRIES = 500
MAX_FILE = 1024 * 1024
MAX_TOTAL = 20 * 1024 * 1024
MAX_RATIO = 100
ALLOWED_EXT = {".json", ".py", ".mjs", ".ts", ".sh", ".ps1", ".md", ".txt"}
ALLOWED_PREFIX = ("actions/", "workflows/", "forms/", "docs/")
SOURCE_EXT = {"python": "py", "node": "mjs", "typescript": "ts", "shell": "sh", "powershell": "ps1"}


class PackageError(ValueError):
    pass


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def build(
    meta: dict,
    actions: list[dict],
    workflows: list[dict],
    readme: str = "",
    sign: bool = False,
    forms: list[dict] | None = None,
) -> tuple[bytes, dict]:
    """meta: name, slug, version, description, license. actions/workflows: dict payloads (see export view)."""
    files: dict[str, bytes] = {}
    contents = {"actions": [], "workflows": []}
    for a in actions:
        base = f"actions/{a['slug']}/{a['version']}"
        source = a.pop("source")
        src_path = f"{base}/source.{SOURCE_EXT[a['runtime']]}"
        files[src_path] = source.encode()
        files[f"{base}/action.json"] = json.dumps(a, indent=2, sort_keys=True, ensure_ascii=False).encode()
        contents["actions"].append(
            {"slug": a["slug"], "version": a["version"], "path": f"{base}/action.json", "source": src_path}
        )
    for w in workflows:
        path = f"workflows/{w['slug']}/{w['version']}/workflow.json"
        files[path] = json.dumps(w, indent=2, sort_keys=True, ensure_ascii=False).encode()
        contents["workflows"].append({"slug": w["slug"], "version": w["version"], "path": path})
    if forms:  # the key is absent when there is no form: packages without forms stay byte-for-byte what they were
        contents["forms"] = []
    for f in forms or []:
        path = f"forms/{f['slug']}/{f['version']}/form.json"
        files[path] = json.dumps(f, indent=2, sort_keys=True, ensure_ascii=False).encode()
        contents["forms"].append({"slug": f["slug"], "version": f["version"], "path": path})
    if readme:
        files["docs/README.md"] = readme.encode()
    manifest = {
        "format": FORMAT,
        **{k: meta[k] for k in ("name", "slug", "version", "description", "license")},
        "created_at": datetime.now(UTC).isoformat(),
        "contents": contents,
        "checksums": {p: sha(d) for p, d in sorted(files.items())},
        "notice": "Ce paquet ne contient aucun secret. L'import ne lance aucun code.",
    }
    if sign:
        from vault import signing

        manifest["signature"] = signing.sign_manifest(manifest)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False))
        for p, d in sorted(files.items()):
            z.writestr(p, d)
    return buf.getvalue(), manifest


def _check_path(name: str) -> None:
    if not name or "\\" in name or "\x00" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise PackageError(f"Chemin interdit dans l'archive : {name!r}")
    norm = posixpath.normpath(name)
    if norm != name or norm.startswith("../") or "/../" in f"/{norm}/" or norm == "..":
        raise PackageError(f"Chemin interdit dans l'archive : {name!r}")
    if name != "manifest.json" and not name.startswith(ALLOWED_PREFIX):
        raise PackageError(f"Dossier non autorisé dans l'archive : {name!r}")
    if posixpath.splitext(name)[1].lower() not in ALLOWED_EXT:
        raise PackageError(f"Type de fichier non autorisé : {name!r}")


def read(data: bytes) -> tuple[dict, dict[str, bytes]]:
    """Returns (manifest, files). Raises PackageError on anything suspicious."""
    if len(data) > MAX_ARCHIVE:
        raise PackageError("Archive trop volumineuse (5 Mo maximum).")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PackageError("Ce fichier n'est pas une archive valide.") from None
    infos = z.infolist()
    if len(infos) > MAX_ENTRIES:
        raise PackageError(f"Trop de fichiers dans l'archive ({MAX_ENTRIES} maximum).")
    files: dict[str, bytes] = {}
    total = 0
    for info in infos:
        if info.is_dir():
            continue
        _check_path(info.filename)
        if info.filename in files:
            raise PackageError(f"Entrée en double : {info.filename}")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise PackageError(f"Lien symbolique interdit : {info.filename}")
        if info.flag_bits & 0x1:
            raise PackageError("Archive chiffrée non prise en charge.")
        if info.file_size > MAX_FILE or (
            info.compress_size and info.file_size / info.compress_size > MAX_RATIO and info.file_size > 10_000
        ):
            raise PackageError(f"Fichier suspect (taille ou taux de compression excessif) : {info.filename}")
        with z.open(info) as fh:
            content = fh.read(MAX_FILE + 1)  # header sizes can lie: bound the bytes actually produced
        if len(content) > MAX_FILE:
            raise PackageError(f"Fichier trop volumineux : {info.filename}")
        total += len(content)
        if total > MAX_TOTAL:
            raise PackageError("Contenu décompressé trop volumineux (20 Mo maximum).")
        files[info.filename] = content
    if "manifest.json" not in files:
        raise PackageError("manifest.json absent.")
    try:
        manifest = json.loads(files.pop("manifest.json"))
    except ValueError:
        raise PackageError("manifest.json illisible.") from None
    _check_manifest(manifest, files)
    return manifest, files


def _check_manifest(m: dict, files: dict[str, bytes]) -> None:
    if not isinstance(m, dict) or m.get("format") != FORMAT:
        raise PackageError(f"Format de paquet non pris en charge (attendu : {FORMAT}).")
    for key in ("name", "slug", "version", "description", "license", "contents", "checksums"):
        if key not in m:
            raise PackageError(f"Manifeste incomplet : champ « {key} » manquant.")
    sums = m["checksums"]
    if not isinstance(sums, dict) or set(sums) != set(files):
        extra = sorted(set(files) - set(sums if isinstance(sums, dict) else {}))
        raise PackageError(
            f"Fichiers non déclarés dans le manifeste : {extra[:3]}"
            if extra
            else "Le manifeste référence des fichiers absents."
        )
    for path, expected in sums.items():
        if sha(files[path]) != expected:
            raise PackageError(f"Somme de contrôle invalide : {path}")
    declared = {
        p for sec in m["contents"].values() for item in sec for p in (item.get("path"), item.get("source")) if p
    }
    if not declared <= set(files):
        raise PackageError("Le contenu déclaré référence des fichiers absents.")


def signature_status(manifest: dict, trusted: dict[str, str]) -> dict:
    """trusted: {public_key_b64: name}. status: unsigned | invalid | untrusted | trusted."""
    from vault import signing

    if "signature" not in manifest:
        return {"status": "unsigned", "signer": None}
    if not signing.verify_manifest(manifest):
        return {"status": "invalid", "signer": None}
    pub = manifest["signature"]["public_key"]
    if pub in trusted:
        return {"status": "trusted", "signer": trusted[pub]}
    return {"status": "untrusted", "signer": None, "key_id": manifest["signature"].get("key_id")}
