"""Tests for book2audio.config.Config.validate().

Config fails fast on bad settings so a render never gets partway through
before discovering a misconfiguration. Every check in validate() is covered
here, including the pre-existing (previously untested) numeric-range checks.
"""

from __future__ import annotations

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
# Single source of truth for combining start_chapter (1-based offset) with
# chapter_limit (count from that offset) into 0-based [start, end) slice
# bounds. Both pipeline.py and worker/render.py depend on this method
# agreeing with itself -- see test_worker_render.py's drift-regression test.


def test_selection_bounds_selects_from_start_chapter_to_the_end_with_no_chapter_limit(tmp_path):
    config = _config(tmp_path, start_chapter=10)
    assert config.selection_bounds(20) == (9, 20)


def test_selection_bounds_combines_start_chapter_and_chapter_limit(tmp_path):
    # start_chapter=10, chapter_limit=5 on a 20-chapter book selects
    # chapters 10-14 (5 chapters), i.e. 0-based bounds (9, 14).
    config = _config(tmp_path, start_chapter=10, chapter_limit=5)
    assert config.selection_bounds(20) == (9, 14)


def test_selection_bounds_caps_the_end_at_total_when_chapter_limit_overshoots(tmp_path):
    config = _config(tmp_path, start_chapter=18, chapter_limit=10)
    assert config.selection_bounds(20) == (17, 20)


def test_selection_bounds_at_the_books_last_chapter_selects_a_single_chapter(tmp_path):
    config = _config(tmp_path, start_chapter=20)
    assert config.selection_bounds(20) == (19, 20)


def test_selection_bounds_defaults_to_the_whole_book_when_start_chapter_is_1(tmp_path):
    config = _config(tmp_path)
    assert config.selection_bounds(20) == (0, 20)


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
