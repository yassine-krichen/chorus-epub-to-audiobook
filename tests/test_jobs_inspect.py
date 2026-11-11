"""POST /jobs/inspect: the chapter list of an uploaded EPUB, for the chapter picker."""

from __future__ import annotations

import uuid
import zipfile
from unittest.mock import MagicMock

import pytest

import book2audio.api.auth.rate_limit as rate_limit_module
from book2audio.api.jobs import inspection

_CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

_WORDS = "the old road wound past the river and over the hill toward a quiet town " * 3


def _section(title: str) -> str:
    return (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        f"<h1>{title}</h1><p>{_WORDS}</p></body></html>"
    )


def _make_epub(path, titles: list[str]) -> None:
    items = "".join(
        f'<item id="s{i}" href="Text/s{i}.xhtml" media-type="application/xhtml+xml"/>'
        for i in range(len(titles))
    )
    spine = "".join(f'<itemref idref="s{i}"/>' for i in range(len(titles)))
    opf = f"""<?xml version="1.0"?>
<package version="2.0" unique-identifier="id" xmlns="http://www.idpf.org/2007/opf">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Test Book</dc:title><dc:creator>Jane Author</dc:creator><dc:language>en</dc:language>
  </metadata>
  <manifest>{items}</manifest><spine>{spine}</spine>
</package>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", _CONTAINER)
        archive.writestr("OEBPS/content.opf", opf)
        for i, title in enumerate(titles):
            archive.writestr(f"OEBPS/Text/s{i}.xhtml", _section(title))


@pytest.fixture
def account(client):
    """A signed-in user: their auth headers and their id (which names their upload area)."""
    email = "reader@example.com"
    registered = client.post("/auth/register", json={"email": email, "password": "hunter2pass"})
    token = client.post("/auth/login", json={"email": email, "password": "hunter2pass"}).json()[
        "access_token"
    ]
    return {"headers": {"Authorization": f"Bearer {token}"}, "id": registered.json()["id"]}


@pytest.fixture
def stored_epub(monkeypatch):
    """Storage that serves a generated EPUB for any key (a stand-in for download_capped)."""
    state = {
        "titles": ["SYNOPSIS", "Chapter 1: Start", "Chapter 2: Middle", "Chapter 3: End"],
        "found": True,
        "error": None,
    }

    def download(key, local_path, *, max_bytes):
        if state["error"] is not None:
            raise state["error"]
        if not state["found"]:
            return False
        _make_epub(local_path, state["titles"])
        return True

    state["download"] = MagicMock(side_effect=download)
    monkeypatch.setattr(inspection, "download_capped", state["download"])
    return state


def _inspect(client, account, key):
    return client.post("/jobs/inspect", json={"epub_object_key": key}, headers=account["headers"])


def _own_key(account, name="book.epub"):
    return f"uploads/{account['id']}/{uuid.uuid4()}-{name}"


def test_the_owner_gets_every_section_with_number_front_matter_flag_and_word_count(
    client, account, stored_epub
):
    response = _inspect(client, account, _own_key(account))

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Test Book"
    assert body["author"] == "Jane Author"
    assert [(c["number"], c["title"], c["front_matter"]) for c in body["chapters"]] == [
        (0, "SYNOPSIS", True),
        (1, "Chapter 1: Start", False),
        (2, "Chapter 2: Middle", False),
        (3, "Chapter 3: End", False),
    ]
    assert [c["index"] for c in body["chapters"]] == [1, 2, 3, 4]
    assert all(c["word_count"] > 40 for c in body["chapters"])


def test_another_users_upload_is_a_404_and_is_never_downloaded(client, account, stored_epub):
    response = _inspect(client, account, "uploads/00000000-0000-0000-0000-000000000000/x.epub")

    assert response.status_code == 404
    stored_epub["download"].assert_not_called()


def test_a_key_outside_the_uploads_area_is_a_404(client, account, stored_epub):
    assert _inspect(client, account, "jobs/abc/part-01.m4b").status_code == 404
    assert _inspect(client, account, "voice-samples/am_adam.wav").status_code == 404
    stored_epub["download"].assert_not_called()


def test_an_upload_that_is_not_there_is_a_404(client, account, stored_epub):
    stored_epub["found"] = False

    response = _inspect(client, account, _own_key(account, "missing.epub"))

    assert response.status_code == 404


def test_a_file_over_the_upload_cap_is_a_413(
    client, account, stored_epub
):
    stored_epub["error"] = ValueError("too big")

    response = _inspect(client, account, _own_key(account, "big.epub"))

    assert response.status_code == 413


def test_a_file_that_is_not_an_epub_is_a_422_with_a_plain_message(
    client, account, stored_epub, monkeypatch
):
    def download_text(key, local_path, *, max_bytes):
        with open(local_path, "w", encoding="utf-8") as bad_file:
            bad_file.write("not a zip")
        return True

    monkeypatch.setattr(inspection, "download_capped", download_text)

    response = _inspect(client, account, _own_key(account, "bad.epub"))

    assert response.status_code == 422
    assert response.json()["detail"] == "This file could not be read as an EPUB"


def test_an_epub_with_no_narratable_chapters_is_a_422(client, account, stored_epub):
    stored_epub["titles"] = []

    response = _inspect(client, account, _own_key(account, "empty.epub"))

    assert response.status_code == 422


def test_an_archive_with_too_many_entries_is_refused_before_parsing(tmp_path, monkeypatch):
    epub = tmp_path / "bomb.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        for i in range(10):
            archive.writestr(f"f{i}.txt", "x")
    monkeypatch.setattr(inspection, "_MAX_ZIP_ENTRIES", 5)
    parse = MagicMock()
    monkeypatch.setattr(inspection, "parse_epub", parse)

    with pytest.raises(inspection.HTTPException) as refused:
        inspection.inspect_epub(str(epub), str(tmp_path))

    assert refused.value.status_code == 422
    parse.assert_not_called()


def test_an_archive_that_unpacks_to_too_much_is_refused_before_parsing(tmp_path, monkeypatch):
    epub = tmp_path / "bomb.epub"
    with zipfile.ZipFile(epub, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("big.txt", "a" * 10_000)
    monkeypatch.setattr(inspection, "_MAX_UNPACKED_BYTES", 1_000)
    parse = MagicMock()
    monkeypatch.setattr(inspection, "parse_epub", parse)

    with pytest.raises(inspection.HTTPException):
        inspection.inspect_epub(str(epub), str(tmp_path))

    parse.assert_not_called()


def test_an_entry_over_the_per_entry_cap_is_refused_before_parsing(tmp_path, monkeypatch):
    epub = tmp_path / "bomb.epub"
    with zipfile.ZipFile(epub, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("big.xhtml", "a" * 5_000)
        archive.writestr("small.xhtml", "a")
    monkeypatch.setattr(inspection, "_MAX_ENTRY_BYTES", 1_000)
    parse = MagicMock()
    monkeypatch.setattr(inspection, "parse_epub", parse)

    with pytest.raises(inspection.HTTPException) as refused:
        inspection.inspect_epub(str(epub), str(tmp_path))

    assert refused.value.status_code == 422
    parse.assert_not_called()


def test_an_entry_with_a_forged_size_is_refused_before_parsing(tmp_path, monkeypatch):
    epub = tmp_path / "forged.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("a.xhtml", "hello")
    # Claim 5 bytes of content but no stored data at all.
    original = zipfile.ZipFile.infolist

    def forged(self):
        entries = original(self)
        for entry in entries:
            entry.compress_size = 0
        return entries

    monkeypatch.setattr(zipfile.ZipFile, "infolist", forged)
    parse = MagicMock()
    monkeypatch.setattr(inspection, "parse_epub", parse)

    with pytest.raises(inspection.HTTPException) as refused:
        inspection.inspect_epub(str(epub), str(tmp_path))

    assert refused.value.status_code == 422
    parse.assert_not_called()


def test_an_unexpected_parser_failure_is_a_422_not_a_500(tmp_path, monkeypatch, caplog):
    epub = tmp_path / "book.epub"
    _make_epub(epub, ["Chapter 1: Start"])
    monkeypatch.setattr(inspection, "parse_epub", MagicMock(side_effect=RecursionError("deep")))

    with caplog.at_level("WARNING"), pytest.raises(inspection.HTTPException) as refused:
        inspection.inspect_epub(str(epub), str(tmp_path))

    assert refused.value.status_code == 422
    assert "inspect.unusable_epub" in caplog.text


def test_an_upload_replaced_while_it_is_read_is_a_409(client, account, stored_epub):
    from botocore.exceptions import ClientError

    stored_epub["error"] = ClientError(
        {"Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {"HTTPStatusCode": 412}},
        "GetObject",
    )

    response = _inspect(client, account, _own_key(account))

    assert response.status_code == 409


@pytest.mark.parametrize(
    "key",
    [
        "uploads/{id}/book.epub",  # not the shape upload-url hands out
        "uploads/{id}/{uuid}-a/b.epub",
        r"uploads/{id}/{uuid}-a\b.epub",
        "uploads/{id}/../{id}/{uuid}-book.epub",
        "uploads/{id}/{uuid}-book.epub\n",
    ],
)
def test_a_key_that_is_not_exactly_an_own_upload_is_a_404(client, account, stored_epub, key):
    response = _inspect(client, account, key.format(id=account["id"], uuid=uuid.uuid4()))

    assert response.status_code == 404
    stored_epub["download"].assert_not_called()


def test_reading_books_is_rate_limited_per_user(client, account, stored_epub, monkeypatch):
    monkeypatch.setattr(rate_limit_module._inspect_per_user_limiter, "max_attempts", 2)

    statuses = [_inspect(client, account, _own_key(account)).status_code for _ in range(3)]

    assert statuses == [200, 200, 429]


def test_a_missing_token_is_rejected(client):
    response = client.post("/jobs/inspect", json={"epub_object_key": "uploads/x/y.epub"})

    assert response.status_code == 401


def test_a_missing_key_is_a_422(client, account):
    assert client.post("/jobs/inspect", json={}, headers=account["headers"]).status_code == 422
