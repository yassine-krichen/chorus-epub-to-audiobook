"""Tests for the R2 (S3-compatible) storage wrapper. boto3 is mocked — no
real network call or R2 credentials are used or required.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from book2audio.storage import r2


@pytest.fixture
def fake_client(monkeypatch):
    client = MagicMock()
    # Mirror the real delete_objects response shape (a dict, no "Errors" key
    # on full success) so delete_objects()'s response.get("Errors") check
    # doesn't misread an unconfigured MagicMock return value as an error.
    client.delete_objects.return_value = {}
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


# --- _endpoint_url: hybrid mode points this at a local MinIO instance ------
#
# R2_ENDPOINT_URL overrides the R2_ACCOUNT_ID-derived endpoint so the worker
# can talk to a local MinIO (via a Cloudflare Tunnel) instead of real R2 --
# see worker/local_daemon.py.


def test_endpoint_url_uses_the_override_verbatim_when_r2_endpoint_url_is_set(monkeypatch):
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://minio.tunnel.example.com")

    assert r2._endpoint_url() == "https://minio.tunnel.example.com"


def test_endpoint_url_falls_back_to_the_account_id_derived_url_when_no_override_is_set(
    monkeypatch,
):
    monkeypatch.delenv("R2_ENDPOINT_URL", raising=False)

    assert r2._endpoint_url() == "https://test-account.r2.cloudflarestorage.com"


def test_endpoint_url_raises_when_neither_override_nor_account_id_is_set(monkeypatch):
    monkeypatch.delenv("R2_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("R2_ACCOUNT_ID", raising=False)

    with pytest.raises(RuntimeError, match="R2_ENDPOINT_URL or R2_ACCOUNT_ID"):
        r2._endpoint_url()


def test_client_uses_the_r2_endpoint_url_override_when_set(monkeypatch):
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://minio.tunnel.example.com")
    boto_client = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(r2.boto3, "client", boto_client)

    r2.presign_put("k")

    _, kwargs = boto_client.call_args
    assert kwargs["endpoint_url"] == "https://minio.tunnel.example.com"


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


# --- list_objects: pages through list_objects_v2 ---------------------------


def test_list_objects_returns_all_keys_from_a_single_page(fake_client):
    fake_client.list_objects_v2.return_value = {
        "Contents": [{"Key": "jobs/1/part-01.m4b"}, {"Key": "jobs/1/part-02.m4b"}],
        "IsTruncated": False,
    }

    keys = r2.list_objects("jobs/1/")

    assert keys == ["jobs/1/part-01.m4b", "jobs/1/part-02.m4b"]
    fake_client.list_objects_v2.assert_called_once_with(
        Bucket="test-bucket", Prefix="jobs/1/"
    )


def test_list_objects_returns_an_empty_list_when_the_prefix_has_no_objects(fake_client):
    fake_client.list_objects_v2.return_value = {"IsTruncated": False}

    keys = r2.list_objects("jobs/missing/")

    assert keys == []


def test_list_objects_follows_the_continuation_token_across_multiple_pages(fake_client):
    fake_client.list_objects_v2.side_effect = [
        {
            "Contents": [{"Key": "jobs/1/a"}],
            "IsTruncated": True,
            "NextContinuationToken": "token-1",
        },
        {
            "Contents": [{"Key": "jobs/1/b"}],
            "IsTruncated": True,
            "NextContinuationToken": "token-2",
        },
        {
            "Contents": [{"Key": "jobs/1/c"}],
            "IsTruncated": False,
        },
    ]

    keys = r2.list_objects("jobs/1/")

    assert keys == ["jobs/1/a", "jobs/1/b", "jobs/1/c"]
    assert fake_client.list_objects_v2.call_count == 3
    second_call_kwargs = fake_client.list_objects_v2.call_args_list[1].kwargs
    assert second_call_kwargs["ContinuationToken"] == "token-1"
    third_call_kwargs = fake_client.list_objects_v2.call_args_list[2].kwargs
    assert third_call_kwargs["ContinuationToken"] == "token-2"


def test_list_objects_omits_continuation_token_on_the_first_request(fake_client):
    fake_client.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}

    r2.list_objects("jobs/1/")

    _, kwargs = fake_client.list_objects_v2.call_args
    assert "ContinuationToken" not in kwargs


# --- delete_objects: chunked batch deletion ---------------------------------


def test_delete_objects_is_a_no_op_for_an_empty_list(fake_client):
    r2.delete_objects([])

    fake_client.delete_objects.assert_not_called()


def test_delete_objects_issues_a_single_request_when_under_the_chunk_limit(fake_client):
    keys = ["jobs/1/a", "jobs/1/b", "jobs/1/c"]

    r2.delete_objects(keys)

    fake_client.delete_objects.assert_called_once_with(
        Bucket="test-bucket",
        Delete={"Objects": [{"Key": k} for k in keys]},
    )


def test_delete_objects_chunks_at_1000_keys_per_request(fake_client):
    keys = [f"jobs/1/thumbnails/chapter-{i:04d}.png" for i in range(2500)]

    r2.delete_objects(keys)

    assert fake_client.delete_objects.call_count == 3
    chunk_sizes = [
        len(call.kwargs["Delete"]["Objects"])
        for call in fake_client.delete_objects.call_args_list
    ]
    assert chunk_sizes == [1000, 1000, 500]


def test_delete_objects_uses_the_configured_bucket_for_every_chunk(fake_client):
    keys = [f"k{i}" for i in range(1500)]

    r2.delete_objects(keys)

    for call in fake_client.delete_objects.call_args_list:
        assert call.kwargs["Bucket"] == "test-bucket"


def test_delete_objects_raises_when_the_response_reports_partial_failures(fake_client):
    fake_client.delete_objects.return_value = {
        "Deleted": [{"Key": "jobs/1/a"}],
        "Errors": [{"Key": "jobs/1/b", "Code": "InternalError", "Message": "transient"}],
    }

    with pytest.raises(RuntimeError, match="jobs/1/b"):
        r2.delete_objects(["jobs/1/a", "jobs/1/b"])


def test_delete_objects_succeeds_silently_when_the_response_has_no_errors(fake_client):
    fake_client.delete_objects.return_value = {"Deleted": [{"Key": "jobs/1/a"}]}

    r2.delete_objects(["jobs/1/a"])  # no exception raised


# --- object_exists: head_object existence check -----------------------------


def test_object_exists_returns_true_when_head_object_succeeds(fake_client):
    fake_client.head_object.return_value = {}

    assert r2.object_exists("voice-samples/am_adam.wav") is True
    fake_client.head_object.assert_called_once_with(
        Bucket="test-bucket", Key="voice-samples/am_adam.wav"
    )


def test_object_exists_returns_false_when_head_object_reports_a_404(fake_client):
    fake_client.head_object.side_effect = ClientError(
        {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
        "HeadObject",
    )

    assert r2.object_exists("voice-samples/missing.wav") is False


def test_object_exists_re_raises_a_client_error_that_is_not_a_404(fake_client):
    fake_client.head_object.side_effect = ClientError(
        {"Error": {"Code": "403"}, "ResponseMetadata": {"HTTPStatusCode": 403}},
        "HeadObject",
    )

    with pytest.raises(ClientError):
        r2.object_exists("voice-samples/am_adam.wav")
