"""GET /voices: book2audio's curated, assignable voice pool.

This lists the pool ``VoiceCast`` casts from (``CHARACTER_VOICE_POOL`` plus
the default narrator voice) — it is NOT Kokoro's full upstream voice catalog.
Built entirely from constants, so it never loads the Kokoro model.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from book2audio.api.deps import get_current_user
from book2audio.api.voices.schemas import VoiceInfo, VoicesResponse
from book2audio.db.models import User
from book2audio.storage.r2 import object_exists, presign_get
from book2audio.tts.voices import (
    CHARACTER_VOICE_POOL,
    DEFAULT_NARRATOR_VOICE,
    FEMALE_VOICES,
    MALE_VOICES,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voices"])

# Frontend caches the voice list for 5 minutes, so presigned sample URLs need
# to outlive that cache window comfortably.
_SAMPLE_URL_EXPIRY_SECONDS = 3600


def _sample_url(voice_id: str) -> str | None:
    # A sample may not exist yet (generate_voice_samples.py hasn't been run
    # for this voice) -- that's expected and not logged. Any other failure
    # (bad R2/MinIO config, auth, network) is unexpected and must not 500
    # the whole endpoint, but it is logged so a broken config isn't silent.
    key = f"voice-samples/{voice_id}.wav"
    try:
        if not object_exists(key):
            return None
        return presign_get(key, expires=_SAMPLE_URL_EXPIRY_SECONDS)
    except Exception:
        logger.warning("Failed to resolve sample_url for voice %s", voice_id, exc_info=True)
        return None


def _gender_of(voice_id: str) -> str:
    if voice_id in MALE_VOICES:
        return "male"
    if voice_id in FEMALE_VOICES:
        return "female"
    return "male" if voice_id[1] == "m" else "female"


@router.get("/voices", response_model=VoicesResponse)
def list_voices(current_user: User = Depends(get_current_user)) -> VoicesResponse:
    # The narrator voice is deliberately excluded from CHARACTER_VOICE_POOL
    # (so characters never collide with it) but clients still need to see it
    # to know which id is_default_narrator.
    all_voice_ids = (DEFAULT_NARRATOR_VOICE, *CHARACTER_VOICE_POOL)
    voices = [
        VoiceInfo(
            id=voice_id,
            gender=_gender_of(voice_id),
            is_default_narrator=voice_id == DEFAULT_NARRATOR_VOICE,
            sample_url=_sample_url(voice_id),
        )
        for voice_id in all_voice_ids
    ]
    return VoicesResponse(narrator_voice=DEFAULT_NARRATOR_VOICE, voices=voices)
