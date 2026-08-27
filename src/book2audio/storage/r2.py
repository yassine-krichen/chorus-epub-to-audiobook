"""Cloudflare R2 client wrapper (S3-compatible, via boto3).

R2 has no AWS-style regions; the endpoint is derived from the account id.
Credentials and bucket come from env vars so the API and worker share the
same config surface without importing each other.
"""

from __future__ import annotations

import os

import boto3
from botocore.client import Config as BotoConfig


def _bucket() -> str:
    bucket = os.environ.get("R2_BUCKET")
    if not bucket:
        raise RuntimeError("R2_BUCKET environment variable is not set")
    return bucket


def _client():
    account_id = os.environ.get("R2_ACCOUNT_ID")
    access_key = os.environ.get("R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
    if not (account_id and access_key and secret_key):
        raise RuntimeError(
            "R2_ACCOUNT_ID, R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY must all be set"
        )
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
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


def presign_get(key: str, expires: int = 600) -> str:
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": _bucket(), "Key": key},
        ExpiresIn=expires,
    )


def upload_file(local_path: str, key: str) -> None:
    _client().upload_file(local_path, _bucket(), key)


def download_file(key: str, local_path: str) -> None:
    _client().download_file(_bucket(), key, local_path)
