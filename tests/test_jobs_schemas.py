"""Tests for the JobConfig.filename_template validator wiring.

The allowlist logic itself is covered exhaustively in test_filenames.py;
these tests only confirm JobConfig actually calls validate_filename_template
on construction (the wiring, not the allowlist rules).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from book2audio.api.jobs.schemas import JobConfig


def test_job_config_accepts_a_valid_filename_template():
    config = JobConfig(filename_template="{title} - Part {part:02d}")
    assert config.filename_template == "{title} - Part {part:02d}"


def test_job_config_rejects_a_filename_template_with_a_disallowed_placeholder():
    with pytest.raises(ValidationError, match="Unsupported filename placeholder"):
        JobConfig(filename_template="{title.__class__}")


def test_job_config_allows_a_missing_filename_template():
    assert JobConfig().filename_template is None


# --- thumbnails wiring ------------------------------------------------------
#
# JobConfig is the actual POST /jobs wire contract; Config.thumbnails alone
# is unreachable from a client without a matching field here (worker's
# _build_config only pulls keys present in the persisted job.config JSON).


def test_job_config_defaults_thumbnails_to_false():
    assert JobConfig().thumbnails is False


def test_job_config_accepts_thumbnails_true():
    assert JobConfig(thumbnails=True).thumbnails is True


def test_job_config_defaults_enhanced_thumbnails_to_false():
    assert JobConfig().thumbnails_enhanced is False


def test_job_config_accepts_enhanced_thumbnails_together_with_thumbnails():
    assert JobConfig(thumbnails=True, thumbnails_enhanced=True).thumbnails_enhanced is True


def test_job_config_rejects_enhanced_thumbnails_without_thumbnails():
    with pytest.raises(ValidationError, match="thumbnails_enhanced requires thumbnails"):
        JobConfig(thumbnails=False, thumbnails_enhanced=True)


# --- male_voice / female_voice / music_preset validation --------------------
#
# These fields previously had no pydantic-level validation, so a bad value
# only surfaced as a job failure minutes later inside Config.validate() in
# the worker, instead of an immediate 422 at job-creation time.


def test_job_config_accepts_a_known_male_voice():
    assert JobConfig(male_voice="am_adam").male_voice == "am_adam"


def test_job_config_rejects_an_unknown_male_voice():
    with pytest.raises(ValidationError, match="Unknown male_voice"):
        JobConfig(male_voice="not-a-real-voice")


def test_job_config_accepts_a_missing_male_voice():
    assert JobConfig().male_voice is None


def test_job_config_accepts_a_known_female_voice():
    assert JobConfig(female_voice="af_heart").female_voice == "af_heart"


def test_job_config_rejects_an_unknown_female_voice():
    with pytest.raises(ValidationError, match="Unknown female_voice"):
        JobConfig(female_voice="not-a-real-voice")


def test_job_config_accepts_a_missing_female_voice():
    assert JobConfig().female_voice is None


def test_job_config_accepts_a_known_music_preset():
    assert JobConfig(music_preset="rain").music_preset == "rain"


def test_job_config_rejects_an_unknown_music_preset():
    with pytest.raises(ValidationError, match="Unknown music_preset"):
        JobConfig(music_preset="not-a-real-preset")


# --- attributor wiring ------------------------------------------------------


def test_job_config_defaults_attributor_to_heuristic():
    assert JobConfig().attributor == "heuristic"


@pytest.mark.parametrize("mode", ["heuristic", "llm", "hybrid"])
def test_job_config_accepts_every_known_attributor(mode):
    assert JobConfig(attributor=mode).attributor == mode


def test_job_config_rejects_an_unknown_attributor():
    with pytest.raises(ValidationError, match="Unknown attributor"):
        JobConfig(attributor="gpt")


# --- start_chapter ----------------------------------------------------------


def test_job_config_defaults_start_chapter_to_automatic():
    assert JobConfig().start_chapter is None


@pytest.mark.parametrize("value", [0, 1, 250])
def test_job_config_accepts_zero_and_positive_start_chapters(value):
    assert JobConfig(start_chapter=value).start_chapter == value


def test_job_config_rejects_a_negative_start_chapter():
    with pytest.raises(ValidationError, match="start_chapter must be >= 0"):
        JobConfig(start_chapter=-1)


def test_chapter_and_part_progress_load_from_jobs_stored_before_numbering_existed():
    from book2audio.api.jobs.schemas import ChapterProgress, PartProgress

    chapter = ChapterProgress(index=1, title="One", status="done")
    part = PartProgress(part_number=1, first_index=1, last_index=2, r2_key="k", status="done")

    assert chapter.number is None
    assert (part.first_number, part.last_number, part.first_title) == (None, None, None)


def test_job_config_accepts_chapter_title_when_one_file_per_chapter():
    config = JobConfig(filename_template="{chapter:03d} - {chapter_title}", batch_size=1)
    assert config.batch_size == 1


@pytest.mark.parametrize("batch_size", [0, 2, 10])
def test_job_config_rejects_chapter_title_unless_one_file_per_chapter(batch_size):
    with pytest.raises(ValidationError, match="one file per chapter"):
        JobConfig(filename_template="{chapter_title}", batch_size=batch_size)


def test_job_config_rejects_a_negative_batch_size():
    with pytest.raises(ValidationError):
        JobConfig(batch_size=-1)


def test_job_config_accepts_zero_batch_size_meaning_one_file_for_the_book():
    assert JobConfig(batch_size=0).batch_size == 0
