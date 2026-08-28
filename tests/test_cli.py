"""Tests for the `book2audio generate` CLI entrypoint.

Covers the new --male-voice/--female-voice/--music-preset/--thumbnails flags:
that they parse, that they flow into the Config the pipeline actually runs
with, and that --thumbnails fails fast without freellmapi credentials. Uses
--dry-run throughout so nothing synthesizes audio or downloads a TTS model --
_dry_run only parses/attributes/casts (see Pipeline._load_and_prepare /
_cast_voices), matching the same boundary test_pipeline_batches.py relies on.
"""

from __future__ import annotations

import zipfile

import pytest

from book2audio.assemble.music import MUSIC_PRESETS
from book2audio.cli import _build_parser, main
from book2audio.tts.voices import FEMALE_VOICES, MALE_VOICES

# Same minimal fixture shape as test_epub_parser.py's _make_epub -- a cover
# page (skipped as front matter) plus one narratable chapter.
_CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

_OPF = """<?xml version="1.0"?>
<package version="2.0" unique-identifier="id"
    xmlns="http://www.idpf.org/2007/opf">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>Test Book</dc:title>
    <dc:creator opf:role="aut">Jane Author</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="c1" href="Text/ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="cover" href="Text/cover.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="cover"/>
    <itemref idref="c1"/>
  </spine>
</package>"""

_CH1 = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<title></title>
<h1>Chapter 1: The Start</h1>
<p>A frail young man sat on a rusty bench across from the old police station,
cradling a cup of coffee in his hands and thinking hard about what was to come
next, and whether any single part of it had ever truly mattered at all, in the
end, to anyone who might one day happen to remember his name.</p>
<p>"So it begins," he said, and stood.</p>
</body></html>"""

_COVER = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>Cover</h1><p>x</p></body></html>"""


def _make_epub(path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", _CONTAINER)
        zf.writestr("OEBPS/content.opf", _OPF)
        zf.writestr("OEBPS/Text/ch1.xhtml", _CH1)
        zf.writestr("OEBPS/Text/cover.xhtml", _COVER)


@pytest.fixture
def epub(tmp_path):
    path = tmp_path / "book.epub"
    _make_epub(path)
    return str(path)


def _generate_argv(epub_path, tmp_path, *extra):
    # --work-dir/--out are pinned inside tmp_path so a dry run never touches
    # the repo's real ./output directory.
    return [
        "generate",
        epub_path,
        "--dry-run",
        "--work-dir",
        str(tmp_path / "work"),
        "--out",
        str(tmp_path / "out.m4b"),
        *extra,
    ]


def _clear_freellmapi_env(monkeypatch):
    for name in ("FREELLMAPI_BASE_URL", "FREELLMAPI_API_KEY", "FREELLMAPI_IMAGE_MODEL"):
        monkeypatch.delenv(name, raising=False)


def _set_freellmapi_env(monkeypatch):
    monkeypatch.setenv("FREELLMAPI_BASE_URL", "https://freellmapi.example.com")
    monkeypatch.setenv("FREELLMAPI_API_KEY", "test-key")
    monkeypatch.setenv("FREELLMAPI_IMAGE_MODEL", "test-model")


# --- parser: flags parse with the right defaults and values ---------------


def test_parser_defaults_male_voice_female_voice_to_none():
    args = _build_parser().parse_args(["generate", "book.epub"])
    assert args.male_voice is None
    assert args.female_voice is None


def test_parser_defaults_music_preset_to_ambient():
    args = _build_parser().parse_args(["generate", "book.epub"])
    assert args.music_preset == "ambient"


def test_parser_defaults_thumbnails_to_false():
    args = _build_parser().parse_args(["generate", "book.epub"])
    assert args.thumbnails is False


def test_parser_parses_male_voice_female_voice_music_preset_and_thumbnails():
    args = _build_parser().parse_args(
        [
            "generate",
            "book.epub",
            "--male-voice",
            "am_adam",
            "--female-voice",
            "af_heart",
            "--music-preset",
            "forest",
            "--thumbnails",
        ]
    )
    assert args.male_voice == "am_adam"
    assert args.female_voice == "af_heart"
    assert args.music_preset == "forest"
    assert args.thumbnails is True


# --- flags flow into the constructed Config --------------------------------


def _run_and_capture_config(monkeypatch, argv):
    import book2audio.cli as cli_module

    captured: dict = {}
    real_config = cli_module.Config

    def _spy_config(**kwargs):
        captured.update(kwargs)
        return real_config(**kwargs)

    monkeypatch.setattr(cli_module, "Config", _spy_config)
    exit_code = main(argv)
    return exit_code, captured


def test_generate_passes_male_and_female_voice_through_to_config(monkeypatch, epub, tmp_path):
    exit_code, config_kwargs = _run_and_capture_config(
        monkeypatch,
        _generate_argv(
            epub, tmp_path, "--male-voice", MALE_VOICES[0], "--female-voice", FEMALE_VOICES[0]
        ),
    )

    assert exit_code == 0
    assert config_kwargs["male_voice"] == MALE_VOICES[0]
    assert config_kwargs["female_voice"] == FEMALE_VOICES[0]


def test_generate_passes_music_preset_through_to_config(monkeypatch, epub, tmp_path):
    preset = sorted(MUSIC_PRESETS)[0]

    exit_code, config_kwargs = _run_and_capture_config(
        monkeypatch, _generate_argv(epub, tmp_path, "--music-preset", preset)
    )

    assert exit_code == 0
    assert config_kwargs["music_preset"] == preset


def test_generate_passes_thumbnails_true_through_to_config_when_flag_is_set(
    monkeypatch, epub, tmp_path
):
    _set_freellmapi_env(monkeypatch)

    exit_code, config_kwargs = _run_and_capture_config(
        monkeypatch, _generate_argv(epub, tmp_path, "--thumbnails")
    )

    assert exit_code == 0
    assert config_kwargs["thumbnails"] is True


def test_generate_defaults_thumbnails_false_in_config_when_flag_is_omitted(
    monkeypatch, epub, tmp_path
):
    exit_code, config_kwargs = _run_and_capture_config(
        monkeypatch, _generate_argv(epub, tmp_path)
    )

    assert exit_code == 0
    assert config_kwargs["thumbnails"] is False


# --- --thumbnails fail-fast on missing freellmapi credentials -------------


def test_thumbnails_without_freellmapi_env_vars_exits_non_zero(monkeypatch, epub, tmp_path, capsys):
    _clear_freellmapi_env(monkeypatch)

    exit_code = main(_generate_argv(epub, tmp_path, "--thumbnails"))

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "FREELLMAPI" in output


def test_thumbnails_with_freellmapi_env_vars_set_does_not_fail_the_credentials_check(
    monkeypatch, epub, tmp_path
):
    _set_freellmapi_env(monkeypatch)

    exit_code = main(_generate_argv(epub, tmp_path, "--thumbnails"))

    assert exit_code == 0


def test_generate_without_thumbnails_succeeds_even_with_no_freellmapi_env_vars_set(
    monkeypatch, epub, tmp_path
):
    """The freellmapi check is gated on config.thumbnails -- it must not run
    (and must not block) a normal render that never opted into thumbnails."""
    _clear_freellmapi_env(monkeypatch)

    exit_code = main(_generate_argv(epub, tmp_path))

    assert exit_code == 0


# --- --music-preset invalid value fails via Config.validate() end-to-end ---


def test_generate_with_an_invalid_music_preset_fails_via_config_validate(
    monkeypatch, epub, tmp_path, capsys
):
    exit_code = main(_generate_argv(epub, tmp_path, "--music-preset", "not-a-real-preset"))

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "Unknown music_preset" in output
