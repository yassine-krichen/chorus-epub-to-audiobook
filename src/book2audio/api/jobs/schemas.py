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
from book2audio.attribution.gender import (
    GENDER_OVERRIDE_MAX_ENTRIES,
    GENDER_OVERRIDE_MAX_NAME_LENGTH,
    validate_gender_overrides,
)
from book2audio.api.jobs.uploads import (
    MAX_UPLOAD_FILENAME_LENGTH,
    MAX_UPLOAD_KEY_LENGTH,
    validate_upload_filename,
)
from book2audio.cast_review import validate_character_aliases
from book2audio.tts.voices import (
    CHARACTER_VOICE_POOL,
    DEFAULT_NARRATOR_VOICE,
    FEMALE_VOICES,
    MALE_VOICES,
)

# Every voice a client may pick, the narrator's included.
_KNOWN_VOICES = frozenset(CHARACTER_VOICE_POOL) | {DEFAULT_NARRATOR_VOICE}


class UploadUrlRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=MAX_UPLOAD_FILENAME_LENGTH)

    @field_validator("filename")
    @classmethod
    def _validate_filename(cls, value: str) -> str:
        return validate_upload_filename(value)


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
    # Detected name -> the name that takes over its lines; set by the cast review, not the wizard.
    character_aliases: dict[str, str] = Field(default_factory=dict)
    # Pause after speakers and genders are worked out, so the user can fix the cast before
    # any audio is made. On by default; a narrator-only job has no cast, so it never pauses.
    cast_review: bool = True

    @field_validator("voice_overrides")
    @classmethod
    def _validate_voice_overrides(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > GENDER_OVERRIDE_MAX_ENTRIES:
            raise ValueError(f"at most {GENDER_OVERRIDE_MAX_ENTRIES} voice overrides are allowed")
        for name, voice in value.items():
            if not name.strip() or len(name) > GENDER_OVERRIDE_MAX_NAME_LENGTH:
                raise ValueError(
                    f"voice override names must be 1 to {GENDER_OVERRIDE_MAX_NAME_LENGTH} characters"
                )
            if voice not in _KNOWN_VOICES:
                raise ValueError(f"Unknown voice {voice!r} for {name!r}")
        return value

    @field_validator("character_aliases")
    @classmethod
    def _validate_character_aliases(cls, value: dict[str, str]) -> dict[str, str]:
        validate_character_aliases(value)
        return value

    @model_validator(mode="after")
    def _narrator_only_has_nothing_to_review(self) -> "JobConfig":
        if self.voice_mode == "narrator_only":
            self.cast_review = False
        return self

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
    epub_object_key: str = Field(min_length=1, max_length=MAX_UPLOAD_KEY_LENGTH)
    title: str = Field(min_length=1, max_length=255)
    config: JobConfig = Field(default_factory=JobConfig)

    @model_validator(mode="after")
    def _merges_come_from_the_cast_review_only(self) -> "CreateJobRequest":
        # character_aliases are the user's decisions about a detected cast. A new job has no
        # cast yet, so a client that sends them is guessing at names or probing.
        if self.config.character_aliases:
            raise ValueError("character_aliases can only be set through the cast review")
        return self


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
    # Only while the job waits for its cast review: when the review lapses and the job is canceled.
    cast_review_expires_at: datetime | None = None


class JobListResponse(BaseModel):
    jobs: list[JobSummary]
    total: int
    page: int
    page_size: int


class InspectRequest(BaseModel):
    epub_object_key: str = Field(min_length=1, max_length=MAX_UPLOAD_KEY_LENGTH)


class ChapterInfo(BaseModel):
    """One narratable section of an uploaded book, as the chapter picker lists it."""

    index: int  # position in reading order (the parser's identity key)
    number: int  # the chapter number the book uses; 0 before Chapter 1
    title: str
    front_matter: bool  # synopsis, info pages: skipped unless the user asks
    word_count: int


class BookInspection(BaseModel):
    title: str
    author: str
    chapters: list[ChapterInfo]


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


class CastReviewEdit(BaseModel):
    """What the user decided about one character in the cast review.

    A character is left alone (no entry), given a gender and/or a voice, merged into
    another character, or handed to the narrator. Merging and the narrator replace the
    character entirely, so they cannot be combined with a gender or a voice.
    """

    name: str = Field(min_length=1, max_length=200)
    gender: Literal["m", "f"] | None = None
    voice: str | None = None
    merge_into: str | None = Field(default=None, min_length=1, max_length=200)
    read_by_narrator: bool = False

    @field_validator("voice")
    @classmethod
    def _validate_voice(cls, value: str | None) -> str | None:
        if value is not None and value not in _KNOWN_VOICES:
            raise ValueError(f"Unknown voice {value!r}. Known: {', '.join(sorted(_KNOWN_VOICES))}")
        return value

    @model_validator(mode="after")
    def _one_decision_per_character(self) -> "CastReviewEdit":
        replaced = self.merge_into is not None or self.read_by_narrator
        if self.merge_into is not None and self.read_by_narrator:
            raise ValueError("a character is either merged into another or read by the narrator")
        if replaced and (self.gender is not None or self.voice is not None):
            raise ValueError("a merged or narrator-read character takes no gender or voice")
        return self


class CastReviewRequest(BaseModel):
    """The user's fixes to the detected cast. An empty list confirms it as it is."""

    characters: list[CastReviewEdit] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _one_edit_per_character(self) -> "CastReviewRequest":
        names = [edit.name for edit in self.characters]
        if len(set(names)) != len(names):
            raise ValueError("each character can appear only once")
        return self


class CastReport(BaseModel):
    """Who speaks in a job's selected chapters, with the voice each one gets.

    Validated field by field from the stored cast_report.json: only these fields
    reach the client, whatever else the worker wrote into the file.
    """

    voice_mode: Literal["cast", "narrator_only"]
    narrator_voice: str = Field(max_length=40)
    confidence_threshold: float = Field(ge=0, le=1)
    characters: list[CastCharacter] = Field(max_length=500)
