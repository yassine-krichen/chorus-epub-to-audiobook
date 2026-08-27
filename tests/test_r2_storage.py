"""Tests for the R2 (S3-compatible) storage wrapper. boto3 is mocked — no
real network call or R2 credentials are used or required.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from book2audio.storage import r2


@pytest.fixture
def fake_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(r2.boto3, "client", MagicMock(return_value=client))
    return client


def test_presign_put_signs_a_put_object_url_for_the_configured_bucket(fake_client):
    fake_client.generate_presigned_url.return_value = "https://signed-put-url"

    url = r2.presign_put("uploads/book.epub", expires=120)

    assert url == "https://signed-put-url"
    fake_client.generate_presigned_url.assert_called_once_with(
        "put_object",
        Params={"Bucket": "test-bucket", "Key": "uploads/book.epub"},
        ExpiresIn=120,
    )


def test_presign_get_signs_a_get_object_url_for_the_configured_bucket(fake_client):
    fake_client.generate_presigned_url.return_value = "https://signed-get-url"

    url = r2.presign_get("jobs/1/part-01.m4b")

    assert url == "https://signed-get-url"
    fake_client.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "test-bucket", "Key": "jobs/1/part-01.m4b"},
        ExpiresIn=600,
    )


def test_upload_file_uploads_the_local_path_to_the_given_key(fake_client):
    r2.upload_file("/tmp/part-01.m4b", "jobs/1/part-01.m4b")

    fake_client.upload_file.assert_called_once_with(
        "/tmp/part-01.m4b", "test-bucket", "jobs/1/part-01.m4b"
    )


def test_download_file_downloads_the_key_to_the_given_local_path(fake_client):
    r2.download_file("jobs/1/book.epub", "/tmp/book.epub")

    fake_client.download_file.assert_called_once_with(
        "test-bucket", "jobs/1/book.epub", "/tmp/book.epub"
    )


def test_client_uses_the_r2_endpoint_derived_from_the_account_id(monkeypatch):
    boto_client = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(r2.boto3, "client", boto_client)

    r2.presign_put("k")

    _, kwargs = boto_client.call_args
    assert kwargs["endpoint_url"] == "https://test-account.r2.cloudflarestorage.com"
    assert kwargs["aws_access_key_id"] == "test-access-key-id"
    assert kwargs["aws_secret_access_key"] == "test-secret-access-key"


def test_bucket_raises_a_clear_error_when_r2_bucket_env_var_is_missing(monkeypatch):
    monkeypatch.delenv("R2_BUCKET", raising=False)
    with pytest.raises(RuntimeError, match="R2_BUCKET"):
        r2.presign_put("k")


def test_upload_bytes_puts_the_object_at_the_given_key(fake_client):
    r2.upload_bytes(b"raw-bytes", "jobs/1/thumbnails/chapter-0001.png")

    fake_client.put_object.assert_called_once_with(
        Bucket="test-bucket", Key="jobs/1/thumbnails/chapter-0001.png", Body=b"raw-bytes"
    )


def test_upload_bytes_sets_content_type_when_provided(fake_client):
    r2.upload_bytes(b"raw-bytes", "jobs/1/thumbnails/chapter-0001.png", content_type="image/png")

    fake_client.put_object.assert_called_once_with(
        Bucket="test-bucket",
        Key="jobs/1/thumbnails/chapter-0001.png",
        Body=b"raw-bytes",
        ContentType="image/png",
    )


def test_upload_bytes_omits_content_type_when_not_provided(fake_client):
    r2.upload_bytes(b"raw-bytes", "jobs/1/thumbnails/chapter-0001.png")

    _, kwargs = fake_client.put_object.call_args
    assert "ContentType" not in kwargs


def test_presign_get_sets_content_disposition_when_a_download_filename_is_given(fake_client):
    fake_client.generate_presigned_url.return_value = "https://signed-get-url"

    r2.presign_get("jobs/1/part-01.m4b", download_filename="My Book - Part 01.m4b")

    _, kwargs = fake_client.generate_presigned_url.call_args
    disposition = kwargs["Params"]["ResponseContentDisposition"]
    assert disposition.startswith('attachment; filename="My Book - Part 01.m4b"')
    assert "filename*=UTF-8''My%20Book%20-%20Part%2001.m4b" in disposition


def test_presign_get_omits_content_disposition_when_no_download_filename_is_given(fake_client):
    fake_client.generate_presigned_url.return_value = "https://signed-get-url"

    r2.presign_get("jobs/1/part-01.m4b")

    _, kwargs = fake_client.generate_presigned_url.call_args
    assert "ResponseContentDisposition" not in kwargs["Params"]


def test_presign_get_ascii_fallback_replaces_non_ascii_characters_in_the_filename(fake_client):
    fake_client.generate_presigned_url.return_value = "https://signed-get-url"

    r2.presign_get("k", download_filename="Café.m4b")

    _, kwargs = fake_client.generate_presigned_url.call_args
    disposition = kwargs["Params"]["ResponseContentDisposition"]
    assert 'filename="Caf?.m4b"' in disposition
    assert "filename*=UTF-8''Caf%C3%A9.m4b" in disposition


# --- _content_disposition: defense-in-depth against header injection ------
#
# api.jobs.filenames already sanitizes rendered filenames before they reach
# here, but this module must never trust a caller's sanitization blindly --
# a quote or a raw CRLF in the filename could otherwise break out of the
# quoted filename="..." header parameter.


def test_presign_get_rejects_a_download_filename_containing_a_double_quote(fake_client):
    with pytest.raises(ValueError, match="Unsafe characters"):
        r2.presign_get("k", download_filename='a"; foo="bar.m4b')

    fake_client.generate_presigned_url.assert_not_called()


def test_presign_get_rejects_a_download_filename_containing_crlf(fake_client):
    with pytest.raises(ValueError, match="Unsafe characters"):
        r2.presign_get("k", download_filename="a\r\nSet-Cookie: evil=1.m4b")

    fake_client.generate_presigned_url.assert_not_called()


def test_presign_get_rejects_a_download_filename_containing_a_backslash(fake_client):
    with pytest.raises(ValueError, match="Unsafe characters"):
        r2.presign_get("k", download_filename="a\\b.m4b")

    fake_client.generate_presigned_url.assert_not_called()
