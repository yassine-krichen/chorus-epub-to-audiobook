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
