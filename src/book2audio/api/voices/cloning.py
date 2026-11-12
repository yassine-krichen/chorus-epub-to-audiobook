"""Enrolling, listing and deleting a user's cloned voices, and checking jobs that use them.

The flow: the browser asks for an upload URL, PUTs a normalized WAV recording to a staging
key, then asks to enrol it. Enrolment re-reads the staged object, validates it server-side,
stores a normalized copy under ``voice-refs/<user id>/<voice id>.wav`` (private, owner only)
and records the consent. Nothing the browser says about the recording is trusted.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from book2audio.api.voices.reference_audio import (
    MAX_UPLOAD_BYTES,
    ReferenceAudioError,
    analyze_reference_upload,
)
from book2audio.db.models import ClonedVoice
from book2audio.db.worker_heartbeat import online_capabilities
from book2audio.storage.r2 import delete_objects, read_object, upload_bytes
from book2audio.tts.clone_engine import CLONE_CAPABILITY
from book2audio.tts.voices import parse_clone_voice

logger = logging.getLogger(__name__)

MAX_CLONES_PER_USER = 5
# Bump when the consent text changes: enrolments record the version the user agreed to.
CONSENT_VERSION = "2026-11-v1"
CONSENT_TEXT = (
    "This is my own voice, and I am recording it myself. I will only use it to make "
    "audiobooks for my own use, and I will not clone anyone else's voice. I understand I can "
    "delete my voice and its recording at any time."
)

# What the user reads aloud. The text is stored with the voice: the engine needs it to line
# the recording up, so it must be exactly what was read. Each takes 20 to 30 seconds.
READING_SCRIPTS: dict[str, str] = {
    "lighthouse": (
        "The old lighthouse keeper climbed the spiral stairs every evening, counting each "
        "step the way his father had taught him. At the top he wiped the glass, lit the lamp, "
        "and watched the fog roll in from the sea. Somewhere out there a ship was listening "
        "for the horn, and he would not let it sound late. When the first beam swept across "
        "the water, he allowed himself a small smile, poured a cup of tea, and began his "
        "long night's watch."
    ),
    "market": (
        "On Saturday mornings the market square filled with the smell of warm bread and "
        "oranges. Vendors called out their prices, children darted between the stalls, and "
        "an accordion played somewhere near the fountain. She bought a loaf, two peaches, "
        "and a small bunch of lavender, then sat on the old stone steps to watch the crowd. "
        "It was an ordinary day, and she was quietly glad of it."
    ),
    "train": (
        "The night train pulled out of the station with a long, tired sigh. Rain streaked the "
        "windows, and the lights of the city slid away one by one until only the dark fields "
        "remained. He opened his notebook, read the first line twice, and wrote nothing. "
        "Somewhere ahead was a town he had not seen in ten years, and he still could not "
        "decide whether he was going home or running away."
    ),
}

_NAME_UNSAFE = re.compile(r"[\x00-\x1f\x7f]")


def staging_key(user_id: uuid.UUID, voice_id: uuid.UUID) -> str:
    """Where the browser uploads a recording before the server has validated it."""
    return f"voice-uploads/{user_id}/{voice_id}.wav"


def reference_key(user_id: uuid.UUID, voice_id: uuid.UUID) -> str:
    """Where a validated recording lives: private to its owner."""
    return f"voice-refs/{user_id}/{voice_id}.wav"


def clean_voice_name(name: str) -> str:
    cleaned = " ".join(_NAME_UNSAFE.sub(" ", name).split())
    if not cleaned:
        raise HTTPException(422, "Give your voice a name.")
    return cleaned[:80]


def list_cloned_voices(db: Session, user_id: uuid.UUID) -> list[ClonedVoice]:
    return list(
        db.scalars(
            select(ClonedVoice)
            .where(ClonedVoice.user_id == user_id)
            .order_by(ClonedVoice.created_at.asc(), ClonedVoice.id.asc())
        )
    )


def clone_worker_online(db: Session) -> bool:
    return CLONE_CAPABILITY in online_capabilities(db)


def create_cloned_voice(
    db: Session,
    user_id: uuid.UUID,
    *,
    voice_id: uuid.UUID,
    name: str,
    gender: str,
    script_id: str,
    consent: bool,
    consent_version: str,
) -> ClonedVoice:
    if not consent or consent_version != CONSENT_VERSION:
        raise HTTPException(
            422,
            "Confirm that this is your own voice before saving it.",
        )
    script = READING_SCRIPTS.get(script_id)
    if script is None:
        raise HTTPException(422, "Unknown reading script.")
    clean_name = clean_voice_name(name)
    count = db.scalar(
        select(func.count()).select_from(ClonedVoice).where(ClonedVoice.user_id == user_id)
    )
    if (count or 0) >= MAX_CLONES_PER_USER:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"You can keep up to {MAX_CLONES_PER_USER} cloned voices. Delete one to add another.",
        )
    if db.get(ClonedVoice, voice_id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This voice was already saved.")

    staged = staging_key(user_id, voice_id)
    try:
        data = read_object(staged, max_bytes=MAX_UPLOAD_BYTES)
    except ValueError:
        _discard(staged)
        raise HTTPException(
            422, "The recording file is too large."
        ) from None
    except (ClientError, BotoCoreError):
        logger.warning("voice.staging_unreadable", extra={"voice_id": str(voice_id)}, exc_info=True)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Could not read your recording. Try again."
        ) from None
    if data is None:
        raise HTTPException(
            422, "The recording was not uploaded. Try again."
        )
    try:
        analysis = analyze_reference_upload(data)
    except ReferenceAudioError as error:
        _discard(staged)
        raise HTTPException(422, str(error)) from None

    final = reference_key(user_id, voice_id)
    try:
        upload_bytes(analysis.wav_bytes, final, content_type="audio/wav")
    except (ClientError, BotoCoreError):
        logger.warning("voice.store_failed", extra={"voice_id": str(voice_id)}, exc_info=True)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Could not save your recording. Try again."
        ) from None
    voice = ClonedVoice(
        id=voice_id,
        user_id=user_id,
        name=clean_name,
        gender=gender,
        ref_object_key=final,
        ref_text=script,
        ref_sha256=analysis.sha256,
        duration_s=analysis.duration_s,
        consent_at=datetime.now(timezone.utc),
        consent_version=CONSENT_VERSION,
    )
    db.add(voice)
    try:
        db.commit()
    except Exception:
        db.rollback()
        _discard(final)
        raise
    _discard(staged)  # the validated copy is the only one that stays
    db.refresh(voice)
    return voice


def delete_cloned_voice(db: Session, user_id: uuid.UUID, voice_id: uuid.UUID) -> bool:
    """Remove the voice and its recording; ``False`` when the user has no such voice."""
    voice = db.scalar(
        select(ClonedVoice).where(ClonedVoice.id == voice_id, ClonedVoice.user_id == user_id)
    )
    if voice is None:
        return False
    try:
        delete_objects([voice.ref_object_key, staging_key(user_id, voice_id)])
    except (ClientError, BotoCoreError, RuntimeError):
        # The recording is personal data: keep the row so the user can retry, never orphan it.
        logger.warning("voice.delete_failed", extra={"voice_id": str(voice_id)}, exc_info=True)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Could not delete the recording. Try again."
        ) from None
    db.delete(voice)
    db.commit()
    return True


def _discard(key: str) -> None:
    try:
        delete_objects([key])
    except (ClientError, BotoCoreError, RuntimeError):
        logger.warning("voice.discard_failed", exc_info=True)


# --- jobs that use cloned voices ------------------------------------------------


def clone_voice_ids_in_config(config: dict) -> set[uuid.UUID]:
    """Every cloned voice a job's settings name (narrator and per-character voices)."""
    voices = [config.get("narrator_voice"), *(config.get("voice_overrides") or {}).values()]
    found = (parse_clone_voice(v) for v in voices if isinstance(v, str))
    return {voice_id for voice_id in found if voice_id is not None}


def require_usable_clone_voices(
    db: Session, user_id: uuid.UUID, voice_ids: set[uuid.UUID], *, local_worker: bool
) -> None:
    """Refuse a job that names a cloned voice the user does not own, or that nothing can run.

    A voice that is missing and one that belongs to someone else get the same answer, so
    the response reveals nothing about other users' voices.
    """
    if not voice_ids:
        return
    owned = set(
        db.scalars(
            select(ClonedVoice.id).where(
                ClonedVoice.user_id == user_id, ClonedVoice.id.in_(voice_ids)
            )
        )
    )
    if owned != voice_ids:
        raise HTTPException(
            422,
            "One of the cloned voices you picked no longer exists. Pick another voice.",
        )
    if not local_worker:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Cloned voices need the local worker, which this server does not use. "
            "Pick one of the built-in voices.",
        )
    if not clone_worker_online(db):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Cloned voices need your local worker with the voice-cloning engine, and it is "
            "not online right now. Start it, or pick one of the built-in voices.",
        )
