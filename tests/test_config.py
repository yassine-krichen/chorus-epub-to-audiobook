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
