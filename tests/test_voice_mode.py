"""voice_mode (cast / narrator_only) and gender_overrides: config, pipeline, CLI, API."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

import book2audio.pipeline as pipeline_module
from book2audio.api.jobs.schemas import JobConfig
from book2audio.cli import _build_parser, main
from book2audio.config import Config
from book2audio.epub import ParsedChapter
from book2audio.models import BookMetadata, SegmentKind
from book2audio.pipeline import Pipeline
from test_cli import _generate_argv, epub  # noqa: F401 - fixture reused from the CLI tests

_BOOK = BookMetadata(title="Book", author="Author")
_MALE_NARRATION = ["Sunny scratched his head and shrugged his shoulders."] * 8


def _parsed():
    return [
        ParsedChapter(
            index=1,
            title="Chapter 1",
            paragraphs=[
                '"So bitter!" said Sunny.',
                *_MALE_NARRATION,
                '"Quiet," said Nephis.',
                '"We move at dawn."',
            ],
        )
    ]


def _config(tmp_path, **overrides) -> Config:
    return Config(
        epub_path=str(tmp_path / "book.epub"),
        output_path=str(tmp_path / "book.m4b"),
        work_dir=str(tmp_path),
        music=False,
        **overrides,
    )


@pytest.fixture
def parsed_book(monkeypatch):
    monkeypatch.setattr(pipeline_module, "parse_epub", lambda path, cover_dir: (_BOOK, _parsed()))


# --- narrator_only -------------------------------------------------------------------


def test_narrator_only_gives_every_line_the_narrator_voice(tmp_path, parsed_book):
    pipeline = Pipeline(_config(tmp_path, voice_mode="narrator_only"))

    book = pipeline._load_and_prepare()
    cast = pipeline._cast_voices(book)

    speakers = {seg.speaker for seg in book.chapters[0].segments}
    voices = {cast.voice_for(seg.speaker or "") for seg in book.chapters[0].segments}
    assert speakers == {"Narrator"}
    assert voices == {cast.narrator_voice}
    assert any(s.kind == SegmentKind.DIALOGUE for s in book.chapters[0].segments)


def test_narrator_only_skips_speaker_attribution_entirely(tmp_path, parsed_book, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("attribution must not run in narrator_only mode")

    monkeypatch.setattr(pipeline_module, "build_attributor", fail)

    pipeline = Pipeline(_config(tmp_path, voice_mode="narrator_only"))
    pipeline._load_and_prepare()

    assert pipeline._characters == []


def test_cast_mode_attributes_and_casts_characters_by_gender(tmp_path, parsed_book):
    pipeline = Pipeline(_config(tmp_path))

    book = pipeline._load_and_prepare()
    cast = pipeline._cast_voices(book)

    assert pipeline._characters[0] == "Sunny"
    assert cast.voice_for("Sunny") != cast.narrator_voice
    assert cast.voice_for("Sunny")[1] == "m"
    # Nephis has no pronoun evidence at all, so the narrator voice reads her.
    assert cast.voice_for("Nephis") == cast.narrator_voice


def test_a_gender_override_casts_an_unresolved_character_from_that_gender_pool(
    tmp_path, parsed_book
):
    pipeline = Pipeline(_config(tmp_path, gender_overrides={"Nephis": "f"}))

    book = pipeline._load_and_prepare()
    cast = pipeline._cast_voices(book)

    assert cast.voice_for("Nephis")[1] == "f"


def test_a_gender_override_beats_what_the_text_says(tmp_path, parsed_book):
    pipeline = Pipeline(_config(tmp_path, gender_overrides={"Sunny": "f"}))

    book = pipeline._load_and_prepare()
    cast = pipeline._cast_voices(book)

    assert cast.voice_for("Sunny")[1] == "f"


def test_the_cast_report_is_written_to_the_work_dir(tmp_path, parsed_book):
    pipeline = Pipeline(_config(tmp_path))
    book = pipeline._load_and_prepare()
    cast = pipeline._cast_voices(book)

    pipeline.report_cast(book.chapters, cast)

    report = json.loads((tmp_path / "cast_report.json").read_text(encoding="utf-8"))
    assert report["voice_mode"] == "cast"
    assert {c["name"] for c in report["characters"]} == {"Sunny", "Nephis"}


def test_narrator_only_writes_an_empty_cast_report(tmp_path, parsed_book):
    pipeline = Pipeline(_config(tmp_path, voice_mode="narrator_only"))
    book = pipeline._load_and_prepare()
    cast = pipeline._cast_voices(book)

    pipeline.report_cast(book.chapters, cast)

    report = json.loads((tmp_path / "cast_report.json").read_text(encoding="utf-8"))
    assert report["voice_mode"] == "narrator_only"
    assert report["characters"] == []


def test_a_gender_override_for_an_unknown_name_is_reported(tmp_path, parsed_book, capsys):
    from rich.console import Console

    pipeline = Pipeline(_config(tmp_path, gender_overrides={"Nobody": "m"}), Console(width=200))
    book = pipeline._load_and_prepare()
    pipeline.report_cast(book.chapters, pipeline._cast_voices(book))

    assert "match no character: Nobody" in capsys.readouterr().out


# --- Config ---------------------------------------------------------------------------


def test_voice_mode_defaults_to_cast_with_no_gender_overrides(tmp_path):
    cfg = _config(tmp_path)

    assert cfg.voice_mode == "cast"
    assert cfg.gender_overrides == {}


def test_config_rejects_an_unknown_voice_mode(tmp_path):
    (tmp_path / "book.epub").write_bytes(b"x")

    with pytest.raises(ValueError, match="Unknown voice_mode"):
        _config(tmp_path, voice_mode="choir").validate()


def test_config_rejects_a_gender_other_than_m_or_f(tmp_path):
    (tmp_path / "book.epub").write_bytes(b"x")

    with pytest.raises(ValueError, match="must be 'm' or 'f'"):
        _config(tmp_path, gender_overrides={"Sunny": "x"}).validate()


# --- CLI ------------------------------------------------------------------------------


def test_cli_defaults_to_cast_mode_with_no_gender_overrides():
    args = _build_parser().parse_args(["generate", "book.epub"])

    assert args.narrator_only is False
    assert args.gender == []


def test_cli_parses_repeatable_gender_overrides_and_narrator_only():
    args = _build_parser().parse_args(
        ["generate", "b.epub", "--gender", "Cassie=f", "--gender", "Kai=m", "--narrator-only"]
    )

    assert args.gender == ["Cassie=f", "Kai=m"]
    assert args.narrator_only is True


def test_cli_passes_gender_overrides_and_voice_mode_into_the_config(monkeypatch, epub, tmp_path):
    seen = {}
    real_init = Pipeline.__init__

    def spy(self, config, *a, **kw):
        seen["config"] = config
        real_init(self, config, *a, **kw)

    monkeypatch.setattr(Pipeline, "__init__", spy)

    code = main(_generate_argv(epub, tmp_path, "--gender", "Sunny=m", "--narrator-only"))

    assert code == 0
    assert seen["config"].gender_overrides == {"Sunny": "m"}
    assert seen["config"].voice_mode == "narrator_only"


def test_cli_gender_without_equals_is_refused():
    with pytest.raises(SystemExit):
        main(["generate", "book.epub", "--dry-run", "--gender", "Cassie"])


def test_cli_gender_value_other_than_m_or_f_exits_non_zero(epub, tmp_path, capsys):
    code = main(_generate_argv(epub, tmp_path, "--gender", "Sunny=x"))

    assert code == 1
    assert "must be 'm' or 'f'" in capsys.readouterr().out


def test_dry_run_prints_the_cast_report_and_writes_it(epub, tmp_path, capsys):
    code = main(_generate_argv(epub, tmp_path))

    out = capsys.readouterr().out
    assert code == 0
    assert (tmp_path / "work" / "cast_report.json").is_file()
    assert "Cast" in out


def test_dry_run_narrator_only_says_the_narrator_reads_everything(epub, tmp_path, capsys):
    code = main(_generate_argv(epub, tmp_path, "--narrator-only"))

    assert code == 0
    assert "narrator only" in capsys.readouterr().out


def test_narrator_only_does_not_need_llm_credentials_for_an_llm_attributor(
    epub, tmp_path, monkeypatch
):
    for name in ("FREELLMAPI_BASE_URL", "FREELLMAPI_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    code = main(_generate_argv(epub, tmp_path, "--narrator-only", "--attributor", "llm"))

    assert code == 0


# --- API ------------------------------------------------------------------------------


def test_job_config_defaults_to_cast_mode_and_no_gender_overrides():
    config = JobConfig()

    assert config.voice_mode == "cast"
    assert config.gender_overrides == {}


def test_job_config_accepts_narrator_only():
    assert JobConfig(voice_mode="narrator_only").voice_mode == "narrator_only"


def test_job_config_rejects_an_unknown_voice_mode():
    with pytest.raises(ValidationError):
        JobConfig(voice_mode="choir")


def test_job_config_accepts_valid_gender_overrides():
    config = JobConfig(gender_overrides={"Cassie": "f", "Sunny": "m"})

    assert config.gender_overrides == {"Cassie": "f", "Sunny": "m"}


@pytest.mark.parametrize("value", ["x", "M", "female", ""])
def test_job_config_rejects_a_gender_other_than_m_or_f(value):
    with pytest.raises(ValidationError):
        JobConfig(gender_overrides={"Cassie": value})


def test_job_config_rejects_an_overlong_gender_override_name():
    with pytest.raises(ValidationError):
        JobConfig(gender_overrides={"x" * 200: "m"})


def test_job_config_rejects_too_many_gender_overrides():
    with pytest.raises(ValidationError):
        JobConfig(gender_overrides={f"name{i}": "m" for i in range(101)})


def test_the_worker_builds_its_config_with_voice_mode_and_gender_overrides(tmp_path):
    from book2audio.worker.render import _build_config

    config = _build_config(
        str(tmp_path / "book.epub"),
        str(tmp_path),
        {"voice_mode": "narrator_only", "gender_overrides": {"Cassie": "f"}},
    )

    assert config.voice_mode == "narrator_only"
    assert config.gender_overrides == {"Cassie": "f"}


def test_narrator_only_does_not_warn_that_gender_names_match_no_character(
    tmp_path, parsed_book, capsys
):
    from rich.console import Console

    config = _config(tmp_path, voice_mode="narrator_only", gender_overrides={"Sunny": "m"})
    pipeline = Pipeline(config, Console(width=200))
    book = pipeline._load_and_prepare()
    pipeline.report_cast(book.chapters, pipeline._cast_voices(book))

    assert "match no character" not in capsys.readouterr().out


def test_a_gender_override_spelled_as_a_merged_variant_is_not_reported_as_unmatched(
    tmp_path, monkeypatch, capsys
):
    from rich.console import Console

    paragraphs = ['"Hi," said Cassie.', '"Yes," said Cassia.']
    chapters = [
        ParsedChapter(index=1, title="Chapter 1", paragraphs=paragraphs[:1]),
        ParsedChapter(index=2, title="Chapter 2", paragraphs=paragraphs[1:]),
    ]
    monkeypatch.setattr(pipeline_module, "parse_epub", lambda path, cover_dir: (_BOOK, chapters))
    config = _config(tmp_path, gender_overrides={"cassia": "f", "Nobody": "m"})
    pipeline = Pipeline(config, Console(width=200))

    book = pipeline._load_and_prepare()
    cast = pipeline._cast_voices(book)
    pipeline.report_cast(book.chapters, cast)

    out = capsys.readouterr().out
    assert "match no character: Nobody" in out
    assert "cassia" not in out.split("match no character:")[1]
    assert cast.voice_for("Cassie")[1] == "f"
