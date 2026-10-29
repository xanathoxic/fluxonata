"""AWS Signature Version 4 (S3 and other AWS-style services), standard library only.

Verified against the public « get-vanilla » test vector of the signature documentation (see tests)."""

import hashlib
import hmac
from datetime import UTC, datetime
from urllib.parse import parse_qsl, quote, urlsplit


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sign(
    method: str,
    url: str,
    headers: dict,
    body: bytes | None,
    *,
    access_key: str,
    secret_key: str,
    region: str,
    service: str,
    now: datetime | None = None,
    content_sha_header: bool = False,
) -> dict:
    """Returns `headers` plus Host-independent signing headers (X-Amz-Date, optionally X-Amz-Content-Sha256, Authorization)."""
    now = now or datetime.now(UTC)
    amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    parts = urlsplit(url)
    payload_hash = hashlib.sha256(body or b"").hexdigest()
    out = dict(headers)
    out["X-Amz-Date"] = amz_date
    if content_sha_header:
        out["X-Amz-Content-Sha256"] = payload_hash
    host = parts.netloc.split("@")[-1]
    signed = {
        "host": host,
        **{
            k.lower(): " ".join(str(v).split())
            for k, v in out.items()
            if k.lower().startswith("x-amz-") or k.lower() == "content-type"
        },
    }
    names = sorted(signed)
    canonical_headers = "".join(f"{n}:{signed[n]}\n" for n in names)
    query = sorted(
        (quote(k, safe="-_.~"), quote(v, safe="-_.~")) for k, v in parse_qsl(parts.query, keep_blank_values=True)
    )
    canonical_request = "\n".join(
        [
            method.upper(),
            quote(parts.path or "/", safe="/-_.~%"),
            "&".join(f"{k}={v}" for k, v in query),
            canonical_headers,
            ";".join(names),
            payload_hash,
        ]
    )
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_key).encode(), day), region), service), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    out["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={';'.join(names)}, Signature={signature}"
    )
    return out
