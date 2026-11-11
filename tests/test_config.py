"""Tests for book2audio.config.Config.validate().

Config fails fast on bad settings so a render never gets partway through
before discovering a misconfiguration. Every check in validate() is covered
here, including the pre-existing (previously untested) numeric-range checks.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from book2audio.assemble.music import MUSIC_PRESETS
from book2audio.config import Config
from book2audio.tts.voices import FEMALE_VOICES, MALE_VOICES


def _config(tmp_path, **overrides) -> Config:
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"fake epub contents")
    defaults = dict(
        epub_path=str(epub),
        output_path=str(tmp_path / "out.m4b"),
        work_dir=str(tmp_path),
    )
    defaults.update(overrides)
    return Config(**defaults)


def test_validate_passes_for_a_default_config(tmp_path):
    _config(tmp_path).validate()  # must not raise


def test_validate_raises_when_the_epub_path_does_not_exist(tmp_path):
    config = _config(tmp_path, epub_path=str(tmp_path / "missing.epub"))
    with pytest.raises(ValueError, match="EPUB not found"):
        config.validate()


def test_validate_raises_for_an_unknown_narrator_voice(tmp_path):
    config = _config(tmp_path, narrator_voice="not_a_real_voice")
    with pytest.raises(ValueError, match="Unknown narrator voice"):
        config.validate()


def test_validate_raises_for_an_unknown_male_voice_bias(tmp_path):
    config = _config(tmp_path, male_voice="not_a_real_voice")
    with pytest.raises(ValueError, match="Unknown male_voice"):
        config.validate()


def test_validate_accepts_a_known_male_voice_bias(tmp_path):
    _config(tmp_path, male_voice=MALE_VOICES[0]).validate()  # must not raise


def test_validate_raises_when_male_voice_bias_is_actually_a_female_voice(tmp_path):
    config = _config(tmp_path, male_voice=FEMALE_VOICES[0])
    with pytest.raises(ValueError, match="Unknown male_voice"):
        config.validate()


def test_validate_raises_for_an_unknown_female_voice_bias(tmp_path):
    config = _config(tmp_path, female_voice="not_a_real_voice")
    with pytest.raises(ValueError, match="Unknown female_voice"):
        config.validate()


def test_validate_accepts_a_known_female_voice_bias(tmp_path):
    _config(tmp_path, female_voice=FEMALE_VOICES[0]).validate()  # must not raise


def test_validate_raises_for_an_unknown_music_preset(tmp_path):
    config = _config(tmp_path, music_preset="orchestral")
    with pytest.raises(ValueError, match="Unknown music_preset"):
        config.validate()


@pytest.mark.parametrize("preset", sorted(MUSIC_PRESETS))
def test_validate_accepts_every_known_music_preset(tmp_path, preset):
    _config(tmp_path, music_preset=preset).validate()  # must not raise


def test_validate_raises_when_speed_is_below_the_minimum(tmp_path):
    config = _config(tmp_path, speed=0.4)
    with pytest.raises(ValueError, match="speed must be between"):
        config.validate()


def test_validate_raises_when_speed_is_above_the_maximum(tmp_path):
    config = _config(tmp_path, speed=2.1)
    with pytest.raises(ValueError, match="speed must be between"):
        config.validate()


@pytest.mark.parametrize("speed", [0.5, 2.0])
def test_validate_accepts_speed_at_the_inclusive_boundaries(tmp_path, speed):
    _config(tmp_path, speed=speed).validate()  # must not raise


def test_validate_raises_for_a_negative_chapter_limit(tmp_path):
    config = _config(tmp_path, chapter_limit=-1)
    with pytest.raises(ValueError, match="chapter_limit cannot be negative"):
        config.validate()


def test_validate_accepts_a_zero_chapter_limit_meaning_the_whole_book(tmp_path):
    _config(tmp_path, chapter_limit=0).validate()  # must not raise


def test_validate_raises_for_a_negative_batch_size(tmp_path):
    config = _config(tmp_path, batch_size=-1)
    with pytest.raises(ValueError, match="batch_size cannot be negative"):
        config.validate()


def test_validate_accepts_a_zero_batch_size_meaning_one_file_for_the_book(tmp_path):
    _config(tmp_path, batch_size=0).validate()  # must not raise


def test_validate_raises_for_a_negative_chapter_gap(tmp_path):
    config = _config(tmp_path, chapter_gap=-0.1)
    with pytest.raises(ValueError, match="chapter_gap cannot be negative"):
        config.validate()


def test_validate_accepts_a_zero_chapter_gap(tmp_path):
    _config(tmp_path, chapter_gap=0.0).validate()  # must not raise


def test_validate_raises_when_music_volume_is_below_zero(tmp_path):
    config = _config(tmp_path, music_volume=-0.01)
    with pytest.raises(ValueError, match="music_volume must be between"):
        config.validate()


def test_validate_raises_when_music_volume_is_above_one(tmp_path):
    config = _config(tmp_path, music_volume=1.01)
    with pytest.raises(ValueError, match="music_volume must be between"):
        config.validate()


@pytest.mark.parametrize("volume", [0.0, 1.0])
def test_validate_accepts_music_volume_at_the_inclusive_boundaries(tmp_path, volume):
    _config(tmp_path, music_volume=volume).validate()  # must not raise


# --- Config.selection_bounds ------------------------------------------------
#
# Single source of truth for combining start_chapter (a book chapter number, or
# None = automatic) with chapter_limit (how many book numbers to take) into
# 0-based [start, end) slice bounds. Both pipeline.py and worker/render.py
# depend on this method agreeing with itself -- see test_worker_render.py's
# drift-regression test.


@dataclass
class _Section:
    number: int
    front_matter: bool = False


def _book_with_synopsis(chapters):
    """A synopsis (number 0, front matter) followed by Chapters 1..``chapters``."""
    return [_Section(0, front_matter=True)] + [_Section(n) for n in range(1, chapters + 1)]


def test_selection_bounds_skips_front_matter_by_default(tmp_path):
    config = _config(tmp_path)
    assert config.selection_bounds(_book_with_synopsis(20)) == (1, 21)


def test_selection_bounds_with_start_chapter_zero_includes_front_matter(tmp_path):
    config = _config(tmp_path, start_chapter=0)
    assert config.selection_bounds(_book_with_synopsis(20)) == (0, 21)


def test_selection_bounds_selects_from_start_chapter_to_the_end_with_no_chapter_limit(tmp_path):
    config = _config(tmp_path, start_chapter=10)
    assert config.selection_bounds(_book_with_synopsis(20)) == (10, 21)


def test_selection_bounds_counts_the_limit_in_book_chapter_numbers(tmp_path):
    # Chapters 10-14 sit after the synopsis, so positions 10..14 -> slice (10, 15).
    config = _config(tmp_path, start_chapter=10, chapter_limit=5)
    assert config.selection_bounds(_book_with_synopsis(20)) == (10, 15)


def test_selection_bounds_default_start_with_limit_10_is_chapters_1_to_10(tmp_path):
    config = _config(tmp_path, chapter_limit=10)
    sections = _book_with_synopsis(20)
    start, end = config.selection_bounds(sections)
    assert [s.number for s in sections[start:end]] == list(range(1, 11))


def test_selection_bounds_start_zero_with_limit_includes_the_synopsis_in_the_count(tmp_path):
    config = _config(tmp_path, start_chapter=0, chapter_limit=10)
    sections = _book_with_synopsis(20)
    start, end = config.selection_bounds(sections)
    assert [s.number for s in sections[start:end]] == list(range(0, 10))


def test_selection_bounds_caps_the_end_at_the_book_when_chapter_limit_overshoots(tmp_path):
    config = _config(tmp_path, start_chapter=18, chapter_limit=10)
    assert config.selection_bounds(_book_with_synopsis(20)) == (18, 21)


def test_selection_bounds_at_the_books_last_chapter_selects_a_single_chapter(tmp_path):
    config = _config(tmp_path, start_chapter=20)
    assert config.selection_bounds(_book_with_synopsis(20)) == (20, 21)


def test_selection_bounds_is_empty_when_start_chapter_is_past_the_last_chapter(tmp_path):
    config = _config(tmp_path, start_chapter=99)
    start, end = config.selection_bounds(_book_with_synopsis(20))
    assert start == end


def test_selection_bounds_selects_the_whole_book_when_nothing_is_front_matter(tmp_path):
    config = _config(tmp_path)
    assert config.selection_bounds([_Section(n) for n in range(1, 6)]) == (0, 5)


def test_selection_bounds_keeps_a_prologue_numbered_zero_as_the_automatic_start(tmp_path):
    sections = [_Section(0, front_matter=True), _Section(0), _Section(1), _Section(2)]
    assert _config(tmp_path).selection_bounds(sections) == (1, 4)


def test_selection_bounds_includes_unnumbered_sections_that_share_a_chapter_number(tmp_path):
    sections = [_Section(1), _Section(2), _Section(2), _Section(3)]
    config = _config(tmp_path, start_chapter=2, chapter_limit=1)
    assert config.selection_bounds(sections) == (1, 3)


def test_validate_accepts_a_zero_or_missing_start_chapter(tmp_path):
    _config(tmp_path, start_chapter=0).validate()  # must not raise
    _config(tmp_path, start_chapter=None).validate()  # must not raise


def test_validate_raises_for_a_negative_start_chapter(tmp_path):
    with pytest.raises(ValueError, match="start_chapter cannot be negative"):
        _config(tmp_path, start_chapter=-1).validate()


def test_attributor_defaults_to_heuristic_with_a_150_call_budget(tmp_path):
    config = _config(tmp_path)
    assert config.attributor == "heuristic"
    assert config.attribution_max_calls == 150


@pytest.mark.parametrize("mode", ["heuristic", "llm", "hybrid"])
def test_validate_accepts_every_known_attributor(tmp_path, mode):
    _config(tmp_path, attributor=mode).validate()  # must not raise


def test_validate_raises_for_an_unknown_attributor(tmp_path):
    with pytest.raises(ValueError, match="Unknown attributor"):
        _config(tmp_path, attributor="gpt").validate()


def test_validate_raises_for_a_negative_attribution_budget(tmp_path):
    with pytest.raises(ValueError, match="attribution_max_calls"):
        _config(tmp_path, attribution_max_calls=-1).validate()


def test_validate_accepts_a_zero_attribution_budget_for_cache_only_runs(tmp_path):
    _config(tmp_path, attributor="hybrid", attribution_max_calls=0).validate()


def test_selection_bounds_kept_prologue_does_not_consume_the_limit(tmp_path):
    sections = [_Section(0, front_matter=True), _Section(0)] + [_Section(n) for n in range(1, 21)]
    config = _config(tmp_path, chapter_limit=10)

    start, end = config.selection_bounds(sections)

    # The prologue plus Chapters 1 to 10, not the prologue plus 1 to 9.
    assert [s.number for s in sections[start:end]] == [0] + list(range(1, 11))
