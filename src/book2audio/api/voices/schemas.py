"""Pydantic response models for the voices route."""

from __future__ import annotations

from pydantic import BaseModel


class VoiceInfo(BaseModel):
    id: str
    gender: str
    is_default_narrator: bool
    sample_url: str | None = None


class VoicesResponse(BaseModel):
    narrator_voice: str
    voices: list[VoiceInfo]
