"""Voices: the curated, assignable pool, and the user's own cloned voices.

``GET /voices`` lists the pool ``VoiceCast`` casts from (``CHARACTER_VOICE_POOL`` plus
the default narrator voice) — it is NOT Kokoro's full upstream voice catalog; the Kokoro part is
built from constants and never loads the model. The signed-in user's cloned voices are
appended. ``/voices/clones`` enrols, lists and deletes those (see ``cloning.py``).
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from book2audio.api.auth.rate_limit import enforce_voice_enrolment_rate_limit
from book2audio.api.deps import get_current_user, get_db
from book2audio.api.voices import cloning
from book2audio.api.voices.reference_audio import (
    MAX_DURATION_S,
    MIN_DURATION_S,
    MIN_SAMPLE_RATE,
)
from book2audio.api.voices.schemas import (
    CloneOptions,
    ClonedVoiceInfo,
    CloneUploadUrlResponse,
    CreateClonedVoiceRequest,
    ReadingScript,
    VoiceInfo,
    VoicesResponse,
)
from book2audio.db.models import ClonedVoice, User
from book2audio.storage.r2 import object_exists, presign_get, presign_put
from book2audio.tts.voices import (
    CHARACTER_VOICE_POOL,
    DEFAULT_NARRATOR_VOICE,
    FEMALE_VOICES,
    MALE_VOICES,
    clone_voice_id,
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


def _clone_voice_info(voice: ClonedVoice, *, available: bool) -> VoiceInfo:
    return VoiceInfo(
        id=clone_voice_id(voice.id),
        gender="female" if voice.gender == "f" else "male",
        is_default_narrator=False,
        provider="clone",
        name=voice.name,
        available=available,
        sample_url=_recording_url(voice),
    )


def _recording_url(voice: ClonedVoice) -> str | None:
    """A short-lived link to the owner's own recording, so they can listen to it."""
    try:
        return presign_get(voice.ref_object_key, expires=_SAMPLE_URL_EXPIRY_SECONDS)
    except Exception:
        logger.warning("voice.recording_url_failed", extra={"voice_id": str(voice.id)}, exc_info=True)
        return None


@router.get("/voices", response_model=VoicesResponse)
def list_voices(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> VoicesResponse:
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
    online = cloning.clone_worker_online(db)
    # Only the signed-in user's own cloned voices are ever listed.
    voices += [
        _clone_voice_info(voice, available=online)
        for voice in cloning.list_cloned_voices(db, current_user.id)
    ]
    return VoicesResponse(
        narrator_voice=DEFAULT_NARRATOR_VOICE, voices=voices, clone_worker_online=online
    )


@router.get("/voices/clones/options", response_model=CloneOptions)
def clone_options(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> CloneOptions:
    return CloneOptions(
        scripts=[ReadingScript(id=k, text=v) for k, v in cloning.READING_SCRIPTS.items()],
        consent_version=cloning.CONSENT_VERSION,
        consent_text=cloning.CONSENT_TEXT,
        max_voices=cloning.MAX_CLONES_PER_USER,
        min_duration_s=MIN_DURATION_S,
        max_duration_s=MAX_DURATION_S,
        min_sample_rate=MIN_SAMPLE_RATE,
        clone_worker_online=cloning.clone_worker_online(db),
    )


@router.post("/voices/clones/upload-url", response_model=CloneUploadUrlResponse)
def create_clone_upload_url(
    current_user: User = Depends(get_current_user),
) -> CloneUploadUrlResponse:
    enforce_voice_enrolment_rate_limit(current_user.id)
    voice_id = uuid.uuid4()
    # Short-lived: the file is validated and copied by the server once it is enrolled.
    url = presign_put(cloning.staging_key(current_user.id, voice_id), expires=300)
    return CloneUploadUrlResponse(voice_id=voice_id, upload_url=url)


@router.post(
    "/voices/clones", response_model=ClonedVoiceInfo, status_code=status.HTTP_201_CREATED
)
def create_cloned_voice(
    payload: CreateClonedVoiceRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClonedVoiceInfo:
    enforce_voice_enrolment_rate_limit(current_user.id)
    voice = cloning.create_cloned_voice(
        db,
        current_user.id,
        voice_id=payload.voice_id,
        name=payload.name,
        gender=payload.gender,
        script_id=payload.script_id,
        consent=payload.consent,
        consent_version=payload.consent_version,
    )
    return _cloned_voice_info(voice)


@router.get("/voices/clones", response_model=list[ClonedVoiceInfo])
def list_cloned_voices(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[ClonedVoiceInfo]:
    return [_cloned_voice_info(v) for v in cloning.list_cloned_voices(db, current_user.id)]


@router.delete("/voices/clones/{voice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cloned_voice(
    voice_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    if not cloning.delete_cloned_voice(db, current_user.id, voice_id):
        # 404 for someone else's voice too: a non-owner cannot tell the difference.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Voice not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _cloned_voice_info(voice: ClonedVoice) -> ClonedVoiceInfo:
    return ClonedVoiceInfo(
        id=clone_voice_id(voice.id),
        name=voice.name,
        gender=voice.gender,
        duration_s=voice.duration_s,
        consent_at=voice.consent_at,
        created_at=voice.created_at,
        recording_url=_recording_url(voice),
    )
