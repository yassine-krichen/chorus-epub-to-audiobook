"""Pydantic response models for the voices route."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from book2audio.api.voices.reference_audio import MAX_UPLOAD_BYTES


class VoiceInfo(BaseModel):
    id: str
    gender: str
    is_default_narrator: bool
    sample_url: str | None = None
    # "kokoro" for the built-in voices, "clone" for the user's own recorded voices.
    provider: Literal["kokoro", "clone"] = "kokoro"
    # The user's name for a cloned voice; built-in voices are named by their id.
    name: str | None = None
    # False for a cloned voice while no local worker with the clone engine is online.
    available: bool = True


class VoicesResponse(BaseModel):
    narrator_voice: str
    voices: list[VoiceInfo]
    # Whether a local worker able to speak cloned voices has been seen recently.
    clone_worker_online: bool = False


class ReadingScript(BaseModel):
    id: str
    text: str


class CloneOptions(BaseModel):
    """What the recorder needs to know: the passages to read, the rules, the consent text."""

    scripts: list[ReadingScript]
    consent_version: str
    consent_text: str
    max_voices: int
    min_duration_s: float
    max_duration_s: float
    min_sample_rate: int
    clone_worker_online: bool


class CloneUploadUrlRequest(BaseModel):
    # The exact size of the WAV the browser will upload: signed into the URL, so the storage
    # service refuses any other length.
    size: int = Field(ge=1_000, le=MAX_UPLOAD_BYTES)


class CloneUploadUrlResponse(BaseModel):
    voice_id: uuid.UUID
    upload_url: str


class CreateClonedVoiceRequest(BaseModel):
    voice_id: uuid.UUID
    name: str = Field(min_length=1, max_length=80)
    gender: Literal["m", "f"]
    script_id: str = Field(min_length=1, max_length=40)
    consent: bool
    consent_version: str = Field(min_length=1, max_length=40)


class ClonedVoiceInfo(BaseModel):
    id: str  # clone:<uuid>, the id to use wherever a voice id is accepted
    name: str
    gender: Literal["m", "f"]
    duration_s: float
    consent_at: datetime
    created_at: datetime
    recording_url: str | None = None
