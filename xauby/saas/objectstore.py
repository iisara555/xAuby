"""Minimal S3-compatible upload for off-host backups. Roadmap P2.2.

The control-plane master key, the encrypted database and `/var/lib/xauby/backups`
all live on one VPS. Losing the host loses every tenant's exchange connection
permanently — the single largest availability risk in the deployment. The
operator's decision was S3-compatible object storage for the database bundle,
with the master key kept offline and deliberately *not* sent anywhere.

This is a hand-rolled SigV4 `PUT`/`HEAD` rather than boto3, for two reasons: it
adds no dependency to a host that trades real money, and the surface actually
used here is two requests. It speaks the dialect AWS S3, Backblaze B2 and
Cloudflare R2 all accept — SigV4 over path-style URLs.

What it deliberately does not do: multipart, retries with backoff beyond a
simple count, listing, or deletion. Retention off-host is the provider's job
(lifecycle rules), and this tool having no delete verb means a compromised host
cannot erase its own backups.
"""
from __future__ import annotations

import datetime as _datetime
import hashlib
import hmac
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

SERVICE = "s3"
ALGORITHM = "AWS4-HMAC-SHA256"


class ObjectStoreError(RuntimeError):
    """Upload or verification failed. Never raised for a successful write."""


@dataclass(frozen=True)
class S3Target:
    endpoint: str          # https://s3.us-west-004.backblazeb2.com
    bucket: str
    region: str
    access_key_id: str
    secret_access_key: str
    prefix: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.bucket and self.access_key_id
                    and self.secret_access_key)

    def missing(self) -> list[str]:
        """Which settings are absent, for an error an operator can act on."""
        names = {
            "XAUBY_BACKUP_S3_ENDPOINT": self.endpoint,
            "XAUBY_BACKUP_S3_BUCKET": self.bucket,
            "XAUBY_BACKUP_S3_ACCESS_KEY_ID": self.access_key_id,
            "XAUBY_BACKUP_S3_SECRET_ACCESS_KEY": self.secret_access_key,
        }
        return [name for name, value in names.items() if not value]

    def key_for(self, filename: str) -> str:
        prefix = self.prefix.strip("/")
        return f"{prefix}/{filename}" if prefix else filename

    def url_for(self, key: str) -> str:
        return f"{self.endpoint.rstrip('/')}/{quote(self.bucket)}/{quote(key)}"


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, date_stamp: str, region: str) -> bytes:
    key = _sign(f"AWS4{secret}".encode(), date_stamp)
    key = _sign(key, region)
    key = _sign(key, SERVICE)
    return _sign(key, "aws4_request")


def signed_headers(target: S3Target, method: str, key: str, payload_sha256: str,
                   *, now: _datetime.datetime | None = None,
                   extra: dict[str, str] | None = None) -> dict[str, str]:
    """SigV4 headers for one request. Pure, so it can be tested without a network."""
    moment = now or _datetime.datetime.now(_datetime.timezone.utc)
    amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = moment.strftime("%Y%m%d")
    host = target.endpoint.split("://", 1)[-1].rstrip("/")

    headers = {
        "host": host,
        "x-amz-content-sha256": payload_sha256,
        "x-amz-date": amz_date,
    }
    headers.update({k.lower(): v for k, v in (extra or {}).items()})

    names = sorted(headers)
    canonical_headers = "".join(f"{name}:{headers[name].strip()}\n" for name in names)
    signed = ";".join(names)
    canonical_request = "\n".join([
        method,
        f"/{quote(target.bucket)}/{quote(key)}",
        "",
        canonical_headers,
        signed,
        payload_sha256,
    ])
    scope = f"{date_stamp}/{target.region}/{SERVICE}/aws4_request"
    to_sign = "\n".join([
        ALGORITHM, amz_date, scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    signature = hmac.new(
        _signing_key(target.secret_access_key, date_stamp, target.region),
        to_sign.encode("utf-8"), hashlib.sha256,
    ).hexdigest()

    result = {name: headers[name] for name in names if name != "host"}
    result["Authorization"] = (
        f"{ALGORITHM} Credential={target.access_key_id}/{scope}, "
        f"SignedHeaders={signed}, Signature={signature}"
    )
    return result


def put_object(target: S3Target, key: str, body: bytes, *,
               session: Any = None, timeout: int = 300) -> dict[str, Any]:
    """Upload, then read the object back's metadata to confirm it landed.

    The confirming HEAD is the point. A backup you believe exists because a
    request returned 200 is the same class of claim as a certificate nobody
    reproduced — this checks the stored length and ETag against what was sent.
    """
    if not target.configured:
        raise ObjectStoreError(
            "object storage is not configured: " + ", ".join(target.missing()))

    import requests

    http = session or requests
    digest = hashlib.sha256(body).hexdigest()
    url = target.url_for(key)
    headers = signed_headers(target, "PUT", key, digest,
                             extra={"content-length": str(len(body))})
    response = http.put(url, data=body, headers=headers, timeout=timeout)
    if response.status_code not in (200, 201):
        raise ObjectStoreError(
            f"upload of {key} failed: HTTP {response.status_code} "
            f"{str(response.text or '')[:300]}")

    head = http.head(
        url,
        headers=signed_headers(target, "HEAD", key, hashlib.sha256(b"").hexdigest()),
        timeout=timeout,
    )
    if head.status_code != 200:
        raise ObjectStoreError(
            f"{key} uploaded but could not be read back: HTTP {head.status_code}")
    stored = int(head.headers.get("Content-Length", "-1"))
    if stored != len(body):
        raise ObjectStoreError(
            f"{key} stored {stored} bytes, expected {len(body)}")

    etag = str(head.headers.get("ETag", "")).strip('"')
    expected_md5 = hashlib.md5(body).hexdigest()  # noqa: S324 - S3 ETag, not a security digest
    # A single-part PUT gives an MD5 ETag on S3/B2; R2 and some gateways return
    # an opaque value. Only treat a *mismatching hex digest* as a failure, and
    # say so rather than silently accepting anything.
    verified_etag = False
    if len(etag) == 32 and all(c in "0123456789abcdef" for c in etag.lower()):
        if etag.lower() != expected_md5:
            raise ObjectStoreError(f"{key} stored with ETag {etag}, expected {expected_md5}")
        verified_etag = True

    return {
        "key": key, "url": url, "bytes": len(body), "sha256": digest,
        "etag": etag, "etag_verified": verified_etag,
    }
