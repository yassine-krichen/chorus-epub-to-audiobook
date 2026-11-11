"""Pydantic request/response models for the jobs routes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from book2audio.assemble.filenames import (
    validate_chapter_title_placeholder,
    validate_filename_template,
)
from book2audio.assemble.music import MUSIC_PRESETS
from book2audio.attribution.base import ATTRIBUTOR_MODES, VOICE_MODES
from book2audio.attribution.gender import validate_gender_overrides
from book2audio.tts.voices import FEMALE_VOICES, MALE_VOICES


class UploadUrlRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)


class UploadUrlResponse(BaseModel):
    upload_url: str
    object_key: str


class JobConfig(BaseModel):
    """Subset of book2audio.config.Config exposed to API clients.

    Kept separate from the pipeline's own Config dataclass on purpose — this
    is the wire contract with clients, the pipeline's Config is an internal
    implementation detail the worker builds from this payload.
    """

    narrator_voice: str | None = None
    voice_overrides: dict[str, str] = Field(default_factory=dict)
    male_voice: str | None = None
    female_voice: str | None = None
    speed: float = 1.0
    bitrate: str = "64k"
    chapter_limit: int = 0
    # None = automatic (skip front matter), 0 = include front matter, N = book chapter N.
    start_chapter: int | None = None
    # Chapters per output file: 1 = one file per chapter, 0 = one file for the whole book.
    batch_size: int = Field(default=10, ge=0)
    chapter_gap: float = 1.8
    music: bool = True
    music_volume: float = 0.22
    music_preset: str = "ambient"
    expressive: bool = True
    filename_template: str | None = Field(default=None, max_length=200)
    thumbnails: bool = False
    # "Smarter thumbnails": a language model reads each chapter before its picture.
    thumbnails_enhanced: bool = False
    attributor: str = "heuristic"
    # "cast" = a voice per character; "narrator_only" = the narrator reads everything.
    voice_mode: str = "cast"
    # Character name -> "m" or "f"; the user's word beats the gender inference.
    gender_overrides: dict[str, str] = Field(default_factory=dict)

    @field_validator("voice_mode")
    @classmethod
    def _validate_voice_mode(cls, value: str) -> str:
        if value not in VOICE_MODES:
            raise ValueError(f"Unknown voice_mode {value!r}. Known: {', '.join(VOICE_MODES)}")
        return value

    @field_validator("gender_overrides")
    @classmethod
    def _validate_gender_overrides(cls, value: dict[str, str]) -> dict[str, str]:
        validate_gender_overrides(value)
        return value

    @field_validator("attributor")
    @classmethod
    def _validate_attributor(cls, value: str) -> str:
        if value not in ATTRIBUTOR_MODES:
            raise ValueError(
                f"Unknown attributor {value!r}. Known: {', '.join(ATTRIBUTOR_MODES)}"
            )
        return value

    @field_validator("filename_template")
    @classmethod
    def _validate_filename_template(cls, value: str | None) -> str | None:
        if value is not None:
            validate_filename_template(value)
        return value

    @model_validator(mode="after")
    def _validate_enhanced_thumbnails_need_thumbnails(self) -> "JobConfig":
        if self.thumbnails_enhanced and not self.thumbnails:
            raise ValueError("thumbnails_enhanced requires thumbnails to be on")
        return self

    @model_validator(mode="after")
    def _validate_chapter_title_needs_one_file_per_chapter(self) -> "JobConfig":
        validate_chapter_title_placeholder(self.filename_template, self.batch_size)
        return self

    @field_validator("start_chapter")
    @classmethod
    def _validate_start_chapter(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError(f"start_chapter must be >= 0, got {value}")
        return value

    @field_validator("male_voice")
    @classmethod
    def _validate_male_voice(cls, value: str | None) -> str | None:
        if value is not None and value not in MALE_VOICES:
            raise ValueError(
                f"Unknown male_voice {value!r}. Known: {', '.join(sorted(MALE_VOICES))}"
            )
        return value

    @field_validator("female_voice")
    @classmethod
    def _validate_female_voice(cls, value: str | None) -> str | None:
        if value is not None and value not in FEMALE_VOICES:
            raise ValueError(
                f"Unknown female_voice {value!r}. Known: {', '.join(sorted(FEMALE_VOICES))}"
            )
        return value

    @field_validator("music_preset")
    @classmethod
    def _validate_music_preset(cls, value: str) -> str:
        if value not in MUSIC_PRESETS:
            raise ValueError(
                f"Unknown music_preset {value!r}. Known: {', '.join(sorted(MUSIC_PRESETS))}"
            )
        return value


class CreateJobRequest(BaseModel):
    epub_object_key: str
    title: str = Field(min_length=1, max_length=255)
    config: JobConfig = Field(default_factory=JobConfig)


class CreateJobResponse(BaseModel):
    job_id: uuid.UUID
    status: str


class ChapterProgress(BaseModel):
    index: int
    # The book's own chapter number; absent on jobs rendered before numbering existed.
    number: int | None = None
    title: str
    status: str
    thumbnail_status: str | None = None
    thumbnail_url: str | None = None


class PartProgress(BaseModel):
    part_number: int
    first_index: int
    last_index: int
    first_number: int | None = None
    last_number: int | None = None
    first_title: str | None = None
    r2_key: str
    status: str
    download_url: str | None = None


class JobSummary(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    total_chapters: int | None
    progress_percent: float
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    artifacts_swept_at: datetime | None = None


class JobDetail(JobSummary):
    epub_object_key: str
    config: dict[str, Any]
    chapters: list[ChapterProgress]
    parts: list[PartProgress]
    error_message: str | None


class JobListResponse(BaseModel):
    jobs: list[JobSummary]
    total: int
    page: int
    page_size: int


class CastEvidence(BaseModel):
    """One reason the app believes a character is male or female."""

    layer: str = Field(max_length=40)
    gender: Literal["m", "f"]
    snippet: str = Field(max_length=300)


class CastCharacter(BaseModel):
    """One character who speaks, as the worker's cast report describes them."""

    name: str = Field(min_length=1, max_length=200)
    lines: int = Field(ge=0)
    # None = not sure enough: the narrator voice reads this character.
    gender: Literal["m", "f"] | None
    confidence: float = Field(ge=0, le=1)
    needs_review: bool
    voice: str = Field(max_length=40)
    voice_source: Literal["override", "gender", "narrator"]
    sample_line: str = Field(max_length=300)
    layer_counts: dict[str, int] = Field(default_factory=dict)
    top_evidence: list[CastEvidence] = Field(default_factory=list, max_length=10)


class CastReport(BaseModel):
    """Who speaks in a job's selected chapters, with the voice each one gets.

    Validated field by field from the stored cast_report.json: only these fields
    reach the client, whatever else the worker wrote into the file.
    """

    voice_mode: Literal["cast", "narrator_only"]
    narrator_voice: str = Field(max_length=40)
    confidence_threshold: float = Field(ge=0, le=1)
    characters: list[CastCharacter] = Field(max_length=500)
