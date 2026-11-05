"""Stockage d'artefacts sur un service compatible S3 (AR1.4) : MinIO, SeaweedFS, Ceph RGW, AWS S3…

Rôle : permettre à l'API et aux workers de tourner sur des hôtes différents et de lire les mêmes artefacts (le volume local de
`FileSystemStore` ne le permet pas). Même interface que `FileSystemStore` (put / get / delete), choisie par `ORCH_ARTIFACTS_STORE=s3`.

- Signature AWS SigV4 : `vault/sigv4.py` (bibliothèque standard, vérifiée sur les vecteurs de test publics).
- **Toute sortie réseau passe par `core/netsec.safe_request`** (résolution épinglée, pas de redirection) : l'hôte du service doit donc être
  autorisé (`ORCH_EGRESS_ALLOWLIST`) dès qu'il est interne ; ce n'est pas contournable ici.
- Adressage « path-style » (`endpoint/bucket/clé`), le plus largement accepté par les services auto-hébergés.
- Les clés d'accès viennent de l'environnement seulement ; elles n'apparaissent dans aucun message d'erreur ni journal.
- Un objet lu est **vérifié** (longueur annoncée = longueur reçue) : `safe_request` tronque sans le dire au-delà de `max_bytes`.
"""

import re
import time
from urllib.parse import quote, urlsplit

from django.core.exceptions import ImproperlyConfigured

from core import netsec
from vault import sigv4

KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.-]{0,400}$")
RETRY_DELAYS = (0.2, 0.6)  # deux nouvelles tentatives sur erreur réseau ou 5xx, puis on renonce


class ArtifactStoreError(RuntimeError):
    """Le service de stockage a refusé ou n'a pas répondu (message en français, sans secret)."""


class S3Store:
    def __init__(
        self,
        endpoint: str,
        bucket: str,
        *,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        prefix: str = "",
        timeout: float = 30.0,
        max_bytes: int = 64 * 1024 * 1024,
    ):
        parts = urlsplit(endpoint or "")
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username
            or parts.query
            or parts.fragment
        ):
            raise ImproperlyConfigured(
                "ORCH_S3_ENDPOINT : adresse http(s)://hôte[:port] attendue, sans identifiants ni paramètres."
            )
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket or ""):
            raise ImproperlyConfigured(
                "ORCH_S3_BUCKET : nom de bucket S3 valide attendu (minuscules, chiffres, points, tirets)."
            )
        if not access_key or not secret_key:
            raise ImproperlyConfigured(
                "ORCH_S3_ACCESS_KEY et ORCH_S3_SECRET_KEY sont obligatoires avec ORCH_ARTIFACTS_STORE=s3."
            )
        self.base = f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"
        self.bucket, self.region = bucket, region
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self.timeout, self.max_bytes = timeout, max_bytes
        self._access_key, self._secret_key = access_key, secret_key

    @classmethod
    def from_settings(cls) -> "S3Store":
        from django.conf import settings

        return cls(
            settings.ORCH_S3_ENDPOINT,
            settings.ORCH_S3_BUCKET,
            access_key=settings.ORCH_S3_ACCESS_KEY,
            secret_key=settings.ORCH_S3_SECRET_KEY,
            region=settings.ORCH_S3_REGION,
            prefix=settings.ORCH_S3_PREFIX,
            timeout=settings.ORCH_S3_TIMEOUT,
            max_bytes=settings.ORCH_S3_MAX_OBJECT_MB * 1024 * 1024,
        )

    # ------------------------------------------------------------------ requêtes
    def _object_path(self, key: str) -> str:
        if not KEY_RE.match(key) or ".." in key.split("/") or "//" in key:
            raise ValueError("Clé de stockage invalide.")
        return f"/{self.bucket}/{quote(self.prefix + key, safe='/-_.~')}"

    def _send(self, method: str, path: str, body: bytes | None = None, *, ok=(200,), missing_ok=False):
        url = self.base + path
        last = "aucune réponse"
        for attempt in range(len(RETRY_DELAYS) + 1):
            headers = {"Content-Type": "application/octet-stream"} if body is not None else {}
            signed = sigv4.sign(
                method,
                url,
                headers,
                body,
                access_key=self._access_key,
                secret_key=self._secret_key,
                region=self.region,
                service="s3",
                content_sha_header=True,
            )
            try:
                status, resp_headers, data = netsec.safe_request(
                    method, url, headers=signed, body=body, timeout=self.timeout, max_bytes=self.max_bytes + 1
                )
            except netsec.UnsafeURL as exc:  # hôte non autorisé : inutile de réessayer, et le message dit quoi faire
                raise ArtifactStoreError(
                    f"Stockage d'artefacts : adresse refusée par la protection réseau ({exc}). "
                    "Ajoutez l'hôte du service à ORCH_EGRESS_ALLOWLIST."
                ) from None
            except OSError as exc:
                last = f"réseau ({type(exc).__name__})"
            else:
                if status in ok or (missing_ok and status == 404):
                    return status, resp_headers, data
                last = f"HTTP {status}{_s3_code(data)}"
                if status < 500:
                    break
            if attempt < len(RETRY_DELAYS):
                time.sleep(RETRY_DELAYS[attempt])
        raise ArtifactStoreError(f"Stockage d'artefacts : {method} refusé ou impossible ({last}).")

    # ------------------------------------------------------------------ interface ArtifactStore
    def put(self, key: str, data: bytes) -> None:
        if len(data) > self.max_bytes:
            raise ArtifactStoreError("Stockage d'artefacts : objet plus grand que ORCH_S3_MAX_OBJECT_MB.")
        self._send("PUT", self._object_path(key), data, ok=(200, 201, 204))

    def get(self, key: str) -> bytes:
        status, headers, data = self._send("GET", self._object_path(key), missing_ok=True)
        if status == 404:
            raise FileNotFoundError(key)
        declared = {k.lower(): v for k, v in headers.items()}.get("content-length")
        if len(data) > self.max_bytes or (declared is not None and int(declared) != len(data)):
            raise ArtifactStoreError(
                "Stockage d'artefacts : objet tronqué ou plus grand que ORCH_S3_MAX_OBJECT_MB ; lecture refusée."
            )
        return data

    def delete(self, key: str) -> None:
        self._send("DELETE", self._object_path(key), ok=(200, 204), missing_ok=True)

    # ------------------------------------------------------------------ exploitation
    def create_bucket(self) -> bool:
        """Crée le bucket s'il n'existe pas. True = créé, False = déjà là. (Souvent fait par l'administrateur du service ; pratique en essai.)"""
        status, _, _ = self._send("PUT", f"/{self.bucket}", b"", ok=(200, 409))
        return status == 200

    def ping(self) -> None:
        """Écrit, relit et supprime un petit objet : vérifie réseau, identifiants et droits en une fois."""
        key = f"ping/{int(time.time() * 1000)}"
        self.put(key, b"ok")
        if self.get(key) != b"ok":
            raise ArtifactStoreError("Stockage d'artefacts : la relecture ne correspond pas à l'écriture.")
        self.delete(key)


def _s3_code(body: bytes) -> str:
    m = re.search(rb"<Code>([A-Za-z0-9]{1,60})</Code>", body or b"")
    return f" {m.group(1).decode()}" if m else ""
