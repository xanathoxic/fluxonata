"""Ed25519 signatures for package provenance. The private key lives in the key directory (outside DB and repo)."""

import base64
import hashlib
import json
import os

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .crypto import key_dir


def _path():
    return key_dir() / "signing.key"


def _private() -> Ed25519PrivateKey:
    p = _path()
    if not p.exists():
        raw = Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
        )
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(base64.b64encode(raw))
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(p.read_bytes()))


def public_b64() -> str:
    raw = _private().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def key_id(public: str) -> str:
    return hashlib.sha256(base64.b64decode(public)).hexdigest()[:16]


def canonical(manifest: dict) -> bytes:
    body = {k: v for k, v in manifest.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign_manifest(manifest: dict) -> dict:
    pub = public_b64()
    return {
        "alg": "ed25519",
        "key_id": key_id(pub),
        "public_key": pub,
        "value": base64.b64encode(_private().sign(canonical(manifest))).decode(),
    }


def verify_manifest(manifest: dict) -> bool:
    sig = manifest.get("signature")
    try:
        if not isinstance(sig, dict) or sig.get("alg") != "ed25519":
            return False
        Ed25519PublicKey.from_public_bytes(base64.b64decode(sig["public_key"])).verify(
            base64.b64decode(sig["value"]), canonical(manifest)
        )
        return True
    except (InvalidSignature, ValueError, KeyError, TypeError):
        return False
