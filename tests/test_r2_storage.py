"""Tests for the R2 (S3-compatible) storage wrapper. boto3 is mocked — no
real network call or R2 credentials are used or required.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import (
    ClientError,
    IncompleteReadError,
    ReadTimeoutError,
    ResponseStreamingError,
)

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
# assemble.filenames already sanitizes rendered filenames before they reach
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


@pytest.mark.parametrize("char", ["\u202e", "\u200f", "\u2066", "\u2028", "\x7f"])
def test_presign_get_rejects_a_download_filename_containing_a_bidi_or_separator_char(
    fake_client, char
):
    with pytest.raises(ValueError, match="Unsafe characters"):
        r2.presign_get("k", download_filename=f"a{char}b.m4b")

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


# --- object_size / read_object: small objects read into memory ----------------


def _not_found() -> ClientError:
    return ClientError(
        {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "HeadObject"
    )


def test_object_size_returns_the_content_length(fake_client):
    fake_client.head_object.return_value = {"ContentLength": 1234}

    assert r2.object_size("jobs/1/cast_report.json") == 1234


def test_object_size_returns_none_for_a_missing_object(fake_client):
    fake_client.head_object.side_effect = _not_found()

    assert r2.object_size("jobs/1/cast_report.json") is None


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def read(self, size: int) -> bytes:
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


def test_read_object_returns_the_body_bytes_pinned_to_the_etag_it_measured(fake_client):
    fake_client.head_object.return_value = {"ContentLength": 5, "ETag": '"abc"'}
    fake_client.get_object.return_value = {"Body": _Body(b"hello")}

    assert r2.read_object("jobs/1/cast_report.json", max_bytes=100) == b"hello"
    fake_client.get_object.assert_called_once_with(
        Bucket="test-bucket", Key="jobs/1/cast_report.json", IfMatch='"abc"'
    )


def test_read_object_returns_none_for_a_missing_object(fake_client):
    fake_client.head_object.side_effect = _not_found()

    assert r2.read_object("jobs/1/cast_report.json", max_bytes=100) is None
    fake_client.get_object.assert_not_called()


def test_read_object_refuses_an_object_over_the_limit_without_downloading_it(fake_client):
    fake_client.head_object.return_value = {"ContentLength": 101}

    with pytest.raises(ValueError, match="more than the 100 byte limit"):
        r2.read_object("jobs/1/cast_report.json", max_bytes=100)
    fake_client.get_object.assert_not_called()


def test_read_object_stops_when_the_stream_is_bigger_than_the_head_said(fake_client):
    # The object was replaced between the HEAD and the GET (or the HEAD lied): the cap still holds.
    fake_client.head_object.return_value = {"ContentLength": 10}
    fake_client.get_object.return_value = {"Body": _Body(b"x" * 5000)}

    with pytest.raises(ValueError, match="more than the 100 byte limit"):
        r2.read_object("voice-uploads/u/v.wav", max_bytes=100)


def test_read_object_reports_a_cut_connection_as_a_storage_error(fake_client):
    class _Broken:
        def read(self, size):
            raise ReadTimeoutError(endpoint_url="x")

    fake_client.head_object.return_value = {"ContentLength": 5}
    fake_client.get_object.return_value = {"Body": _Broken()}

    with pytest.raises(r2.StorageReadError):
        r2.read_object("k", max_bytes=100)


def test_presign_put_can_sign_the_content_length_and_type_into_the_url(fake_client):
    fake_client.generate_presigned_url.return_value = "https://signed"

    r2.presign_put("voice-uploads/u/v.wav", expires=300, content_length=1234, content_type="audio/wav")

    fake_client.generate_presigned_url.assert_called_once_with(
        "put_object",
        Params={
            "Bucket": "test-bucket",
            "Key": "voice-uploads/u/v.wav",
            "ContentLength": 1234,
            "ContentType": "audio/wav",
        },
        ExpiresIn=300,
    )


# --- download_capped: untrusted uploads, size enforced while streaming ---------------------


def test_download_capped_writes_the_object_and_pins_the_read_to_the_etag_it_measured(
    fake_client, tmp_path
):
    fake_client.head_object.return_value = {"ContentLength": 5, "ETag": '"abc"'}
    fake_client.get_object.return_value = {"Body": _Body(b"hello")}
    target = tmp_path / "book.epub"

    assert r2.download_capped("uploads/u/x.epub", str(target), max_bytes=100) is True

    assert target.read_bytes() == b"hello"
    fake_client.get_object.assert_called_once_with(
        Bucket="test-bucket", Key="uploads/u/x.epub", IfMatch='"abc"'
    )


def test_download_capped_returns_false_for_a_missing_object(fake_client, tmp_path):
    fake_client.head_object.side_effect = _not_found()

    assert r2.download_capped("uploads/u/x.epub", str(tmp_path / "b"), max_bytes=100) is False
    fake_client.get_object.assert_not_called()


def test_download_capped_refuses_an_object_that_is_declared_too_large(fake_client, tmp_path):
    fake_client.head_object.return_value = {"ContentLength": 101, "ETag": '"abc"'}

    with pytest.raises(ValueError):
        r2.download_capped("uploads/u/x.epub", str(tmp_path / "b"), max_bytes=100)
    fake_client.get_object.assert_not_called()


def test_download_capped_stops_and_removes_the_file_when_the_stream_exceeds_the_cap(
    fake_client, tmp_path
):
    # The object grew after the size check: the declared size said 5, the stream has more.
    fake_client.head_object.return_value = {"ContentLength": 5, "ETag": '"abc"'}
    fake_client.get_object.return_value = {"Body": _Body(b"x" * 500)}
    target = tmp_path / "book.epub"

    with pytest.raises(ValueError):
        r2.download_capped("uploads/u/x.epub", str(target), max_bytes=100)

    assert not target.exists()


class _BrokenBody:
    """Yields some bytes, then fails the way botocore does when a stream breaks off."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self._sent = False

    def read(self, size: int) -> bytes:
        if not self._sent:
            self._sent = True
            return b"partial"
        raise self._error


@pytest.mark.parametrize(
    "error",
    [
        ReadTimeoutError(endpoint_url="https://r2", error="timed out"),
        IncompleteReadError(actual_bytes=7, expected_bytes=100),
        ResponseStreamingError(error="connection broken"),
        ConnectionResetError("reset"),
    ],
)
def test_download_capped_cleans_up_and_reports_a_stream_that_breaks_off(
    fake_client, tmp_path, error
):
    fake_client.head_object.return_value = {"ContentLength": 100, "ETag": '"abc"'}
    fake_client.get_object.return_value = {"Body": _BrokenBody(error)}
    target = tmp_path / "book.epub"

    with pytest.raises(r2.StorageReadError):
        r2.download_capped("uploads/u/x.epub", str(target), max_bytes=1000)

    assert not target.exists()


def test_download_capped_does_not_hide_a_failed_etag_check(fake_client, tmp_path):
    fake_client.head_object.return_value = {"ContentLength": 5, "ETag": '"abc"'}
    fake_client.get_object.side_effect = ClientError(
        {"Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {"HTTPStatusCode": 412}},
        "GetObject",
    )

    with pytest.raises(ClientError):
        r2.download_capped("uploads/u/x.epub", str(tmp_path / "b"), max_bytes=100)


def test_list_object_ages_pages_through_the_listing_and_returns_last_modified(fake_client):
    from datetime import datetime, timezone

    first = datetime(2026, 11, 1, tzinfo=timezone.utc)
    second = datetime(2026, 11, 2, tzinfo=timezone.utc)
    fake_client.list_objects_v2.side_effect = [
        {"Contents": [{"Key": "voice-uploads/a", "LastModified": first}], "IsTruncated": True,
         "NextContinuationToken": "t"},
        {"Contents": [{"Key": "voice-uploads/b", "LastModified": second}], "IsTruncated": False},
    ]

    assert r2.list_object_ages("voice-uploads/") == [
        ("voice-uploads/a", first),
        ("voice-uploads/b", second),
    ]
    assert fake_client.list_objects_v2.call_args_list[1].kwargs["ContinuationToken"] == "t"
