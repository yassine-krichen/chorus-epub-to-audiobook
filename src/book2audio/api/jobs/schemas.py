"""Pydantic request/response models for the jobs routes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


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
    speed: float = 1.0
    bitrate: str = "64k"
    chapter_limit: int = 0
    batch_size: int = 10
    chapter_gap: float = 1.8
    music: bool = True
    music_volume: float = 0.22
    expressive: bool = True


class CreateJobRequest(BaseModel):
    epub_object_key: str
    title: str = Field(min_length=1, max_length=255)
    config: JobConfig = Field(default_factory=JobConfig)


class CreateJobResponse(BaseModel):
    job_id: uuid.UUID
    status: str


class ChapterProgress(BaseModel):
    index: int
    title: str
    status: str


class PartProgress(BaseModel):
    part_number: int
    first_index: int
    last_index: int
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
