"""Cloudflare R2 client wrapper (S3-compatible, via boto3).

R2 has no AWS-style regions; the endpoint is derived from the account id.
Credentials and bucket come from env vars so the API and worker share the
same config surface without importing each other.
"""

from __future__ import annotations

import os
import re

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

# Characters that could break out of the quoted filename="..." parameter or
# inject extra header/response content (CRLF etc). Callers (e.g.
# api.jobs.filenames.render_filename) are expected to have already
# sanitized the filename -- this is a second, independent layer so this
# module never trusts a caller's sanitization blindly.
_UNSAFE_DISPOSITION_CHARS = re.compile(r'["\\\x00-\x1f]')


def _bucket() -> str:
    bucket = os.environ.get("R2_BUCKET")
    if not bucket:
        raise RuntimeError("R2_BUCKET environment variable is not set")
    return bucket


def _endpoint_url() -> str:
    # Hybrid mode points this at a local MinIO instance (exposed via a
    # Cloudflare Tunnel) instead of R2 -- everything else in this module is
    # unaware of which backend it's talking to, since both are S3-compatible.
    override = os.environ.get("R2_ENDPOINT_URL")
    if override:
        return override
    account_id = os.environ.get("R2_ACCOUNT_ID")
    if not account_id:
        raise RuntimeError(
            "R2_ENDPOINT_URL or R2_ACCOUNT_ID environment variable must be set"
        )
    return f"https://{account_id}.r2.cloudflarestorage.com"


def _client():
    access_key = os.environ.get("R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
    if not (access_key and secret_key):
        raise RuntimeError("R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY must both be set")
    return boto3.client(
        "s3",
        endpoint_url=_endpoint_url(),
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=BotoConfig(signature_version="s3v4"),
        region_name="auto",
    )


def presign_put(key: str, expires: int = 600) -> str:
    return _client().generate_presigned_url(
        "put_object",
        Params={"Bucket": _bucket(), "Key": key},
        ExpiresIn=expires,
    )


def presign_get(key: str, expires: int = 600, *, download_filename: str | None = None) -> str:
    params: dict[str, str] = {"Bucket": _bucket(), "Key": key}
    if download_filename is not None:
        params["ResponseContentDisposition"] = _content_disposition(download_filename)
    return _client().generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=expires,
    )


def object_exists(key: str) -> bool:
    """Whether ``key`` exists in the configured bucket.

    Uses ``head_object`` rather than ``list_objects``/``get_object`` since it
    doesn't transfer any object data. Only a genuine "not found" response is
    swallowed into ``False`` -- matched on the HTTP status code rather than
    the boto3 error code, since S3-compatible services (e.g. MinIO in hybrid
    mode) don't always return the same error code string as real R2/S3 for a
    missing key, but they do agree on the 404 status. Any other error
    (auth, network, misconfiguration) propagates so callers don't mistake a
    broken config for a missing object.
    """
    try:
        _client().head_object(Bucket=_bucket(), Key=key)
    except ClientError as exc:
        if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
            return False
        raise
    return True


def _content_disposition(filename: str) -> str:
    """RFC 6266 ``Content-Disposition``, with an ASCII fallback for clients
    that only understand the legacy ``filename=`` parameter.

    Raises ``ValueError`` if ``filename`` contains characters that could
    break out of the quoted ``filename="..."`` parameter or inject extra
    header content -- defense in depth, independent of whatever sanitization
    the caller already did.
    """
    import urllib.parse

    if _UNSAFE_DISPOSITION_CHARS.search(filename):
        raise ValueError(f"Unsafe characters in download filename: {filename!r}")

    ascii_fallback = filename.encode("ascii", "replace").decode("ascii")
    encoded = urllib.parse.quote(filename)
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


def upload_file(local_path: str, key: str) -> None:
    _client().upload_file(local_path, _bucket(), key)


def upload_bytes(data: bytes, key: str, *, content_type: str | None = None) -> None:
    kwargs = {"Bucket": _bucket(), "Key": key, "Body": data}
    if content_type is not None:
        kwargs["ContentType"] = content_type
    _client().put_object(**kwargs)


def download_file(key: str, local_path: str) -> None:
    _client().download_file(_bucket(), key, local_path)


def list_objects(prefix: str) -> list[str]:
    """Return every object key under ``prefix``, paginating as needed."""
    client = _client()
    bucket = _bucket()
    keys: list[str] = []
    continuation_token: str | None = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if continuation_token is not None:
            kwargs["ContinuationToken"] = continuation_token
        response = client.list_objects_v2(**kwargs)
        keys.extend(obj["Key"] for obj in response.get("Contents", []))
        if not response.get("IsTruncated"):
            break
        continuation_token = response.get("NextContinuationToken")
    return keys


def delete_objects(keys: list[str]) -> None:
    """Batch-delete ``keys``, chunked to R2/S3's 1000-key-per-request limit.

    Raises ``RuntimeError`` if any chunk's response reports per-object
    ``Errors`` (e.g. a transient failure deleting one key out of a chunk) --
    ``delete_objects`` can partially fail without raising on its own, so
    callers must not assume every key was actually removed just because the
    call returned. Matches this module's existing convention of raising
    ``RuntimeError`` for failure conditions (see ``_bucket``/``_client``)
    rather than returning a status object.
    """
    if not keys:
        return
    client = _client()
    bucket = _bucket()
    for i in range(0, len(keys), 1000):
        chunk = keys[i : i + 1000]
        response = client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": key} for key in chunk]},
        )
        errors = response.get("Errors")
        if errors:
            failed_keys = [error.get("Key") for error in errors]
            raise RuntimeError(f"Failed to delete R2 object(s): {failed_keys}")
